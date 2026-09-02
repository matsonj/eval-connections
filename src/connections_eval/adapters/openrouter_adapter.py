"""OpenRouter API adapter."""

import requests
import os
import logging
from typing import Dict, List, Optional, Set
from ..utils.retry import retry_with_backoff, get_last_backoff_sec
from ..utils.rate_limiter import get_default as get_rate_limiter

logger = logging.getLogger(__name__)


class InsufficientCreditsError(RuntimeError):
    """OpenRouter returned 402 — credits exhausted or pre-auth too large.

    Never resolves within a retry window, so it skips retries and aborts
    the whole run (a credit wall poisons every subsequent puzzle)."""
    non_retryable = True


class PartialResponseError(requests.RequestException):
    """OpenRouter returned HTTP 200 with a `usage` block whose completion_tokens
    is 0 despite `choices` being present — a transient upstream fault (the
    provider was killed/restarted mid-generation) rather than a real model
    answer. `content` may be empty or a partial generation cut off mid-sentence
    with no closing tag. Re-running the identical request typically succeeds,
    so this is raised as a RequestException to feed the existing retry loop —
    it is not a rate-limit signal, so callers must not treat it like a 429."""


# Cache of OpenRouter's live model catalog (fetched once per process)
_MODEL_CATALOG: Optional[Set[str]] = None


def assert_model_exists(model_id: str) -> None:
    """
    Fail fast when a model ID isn't in OpenRouter's live catalog.

    A bad slug otherwise burns the full retry budget on every puzzle (6
    attempts x 20 puzzles of 400s) before finishing with zeros. Raises
    ValueError with a clear message. If the catalog fetch itself fails
    (network hiccup), logs a warning and skips the check rather than
    blocking an otherwise-valid run.
    """
    global _MODEL_CATALOG
    if _MODEL_CATALOG is None:
        try:
            resp = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {_get_api_key()}"},
                timeout=30,
            )
            resp.raise_for_status()
            _MODEL_CATALOG = {m["id"] for m in resp.json().get("data", [])}
        except Exception as e:
            logger.warning(f"Could not fetch OpenRouter model catalog ({e}); skipping model preflight")
            return
    base = model_id.split(":")[0]
    if model_id in _MODEL_CATALOG or base in _MODEL_CATALOG:
        return
    raise ValueError(
        f"Model ID '{model_id}' not found in OpenRouter's catalog — "
        f"check the mapping in inputs/model_mappings.yml"
    )

# Mapping from OpenRouter model ID prefix to provider slug for pinning.
# Only includes providers where the slug is known and prompt caching benefits.
# Provider slugs do NOT always match model ID prefixes (e.g. x-ai/ -> "xai").
# Models hosted by third parties (deepseek, meta-llama, qwen) are omitted
# because their provider slug varies by hosting provider.
_PROVIDER_SLUG_MAP = {
    "anthropic/": "anthropic",
    "openai/": "openai",
    "google/": "google-ai-studio",
    "x-ai/": "xai",
}

# Per-model overrides that take precedence over the prefix map above.
# TEMPORARY: claude-sonnet-5 is a day-0 model that deprecated `top_p`, but
# OpenRouter's first-party Anthropic route still injects `top_p` when it
# translates `reasoning.effort`, so any thinking request pinned to "anthropic"
# 400s ("`top_p` is deprecated for this model."). The Amazon Bedrock route
# handles the same request (reasoning included) fine, so we pin there instead.
# Trade-off: forgoes first-party Anthropic prompt caching (the cache_control
# branch in chat() only fires for provider == "anthropic"). Remove this entry
# once OpenRouter stops sending top_p on the Anthropic route.
_PROVIDER_SLUG_OVERRIDES = {
    "anthropic/claude-sonnet-5": "amazon-bedrock",
}


def extract_provider_slug(model: str) -> Optional[str]:
    """
    Extract the OpenRouter provider slug from a model ID.

    Args:
        model: OpenRouter model ID (e.g., 'anthropic/claude-sonnet-4')

    Returns:
        Provider slug (e.g., 'anthropic') or None for unrecognized prefixes
    """
    if model in _PROVIDER_SLUG_OVERRIDES:
        return _PROVIDER_SLUG_OVERRIDES[model]
    for prefix, slug in _PROVIDER_SLUG_MAP.items():
        if model.startswith(prefix):
            return slug
    return None


# Output cap applied only when a response_format schema is sent (see chat()).
STRUCTURED_OUTPUT_MAX_TOKENS = 12000


def _chat_base_delay(messages: List[Dict], model: str, timeout: int = 300,
                     provider: Optional[str] = None, **_kwargs) -> float:
    # Free-tier endpoints (model IDs ending in `:free`) hit aggressive rate limits;
    # double the base delay so the exponential schedule actually clears their cooldown.
    return 4.0 if model.endswith(":free") else 2.0


@retry_with_backoff(max_retries=5, base_delay=_chat_base_delay, exceptions=(requests.RequestException,))
def chat(messages: List[Dict], model: str, timeout: int = 300, provider: Optional[str] = None,
         session_id: Optional[str] = None, reasoning_effort: Optional[str] = None,
         thinking: bool = False, response_format: Optional[Dict] = None) -> Dict:
    """
    Call OpenRouter Chat Completions API.

    Args:
        messages: List of message objects with 'role' and 'content'
        model: OpenRouter model ID (e.g., 'openai/o3', 'x-ai/grok-3')
        timeout: Request timeout in seconds
        provider: Optional provider slug to pin requests to (e.g., 'anthropic').
            When set, forces OpenRouter to route to this provider with no fallbacks,
            enabling prompt caching across calls.
        session_id: Optional stable identifier shared by all calls in one
            conversation. OpenRouter uses it as the sticky-routing key, pinning
            every request in the session to the same upstream provider — from the
            first call, before any cache hit. This is the only caching lever for
            cloaked/third-party-hosted models that have no pinnable provider slug.
        reasoning_effort: Reasoning effort for thinking models (e.g. 'minimal',
            'low', 'medium', 'high'). Defaults to 'minimal' when unset — cheapest
            solves score best. Ignored for non-thinking models.
        thinking: Whether `model` is a reasoning/thinking model — the caller
            (core.py) decides this from the model's presence in the YAML's
            `models.thinking` mapping. Governs whether a `reasoning` block is
            sent, the extended 600s timeout floor, and skipping the default
            max_tokens/temperature.
        response_format: Optional OpenRouter/OpenAI `response_format` block (e.g.
            a `{"type": "json_schema", ...}` from connections_eval.structured).
            Sent verbatim so the provider constrains the model's output. Omitted
            from the payload entirely when unset.

    Returns:
        Raw API response JSON

    Raises:
        requests.RequestException: On API errors
    """
    # Model ID is already the full OpenRouter model ID from YAML mapping
    openrouter_model = model

    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/matsonj/eval-connections",
        "X-Title": "Connections Eval"
    }

    # For Anthropic models, add cache_control breakpoints to enable prompt
    # caching via OpenRouter.  We mark the last assistant message with
    # cache_control so the entire conversation prefix up to that point is
    # cached between turns.  Anthropic requires the prefix to be >= 1024
    # tokens; after the first long thinking response this is easily met.
    # Limited to 4 breakpoints per request.
    request_messages = messages
    if provider == "anthropic":
        # Find the index of the last assistant message (the reusable prefix end)
        last_assistant_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "assistant":
                last_assistant_idx = i
                break

        request_messages = []
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            # Add cache_control to the last assistant message
            if i == last_assistant_idx and isinstance(content, str):
                request_messages.append({
                    "role": msg["role"],
                    "content": [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                })
            else:
                request_messages.append(msg)

    payload = {
        "model": openrouter_model,
        "messages": request_messages,
        "usage": {
            "include": True  # Request cost and usage information
        }
    }

    # Structured output: make the provider constrain the response to a JSON
    # schema (opt-in; callers that don't pass one get an unchanged payload).
    if response_format:
        payload["response_format"] = response_format
        # The thinking-model path deliberately sets no max_tokens, but under a
        # JSON schema some small models degenerate into runaway output (granite-
        # 4.2-8b: one 15.8k-token, 233s generation, then requests that never
        # returned before the 600s timeout and were retried for most of an
        # hour). The structured payload is short, so a cap costs nothing on a
        # healthy response and bounds the failure. setdefault keeps the
        # non-thinking path's existing 25000.
        payload.setdefault("max_tokens", STRUCTURED_OUTPUT_MAX_TOKENS)

    # Pin to a specific provider for prompt caching
    if provider:
        payload["provider"] = {
            "order": [provider],
            "allow_fallbacks": False,
        }

    # Sticky-routing key. Keeps all calls in one puzzle conversation on the same
    # upstream provider so prompt caching can take effect — especially for cloaked
    # or third-party-hosted models that have no pinnable provider slug above.
    if session_id:
        payload["session_id"] = session_id

    # Handle different model types
    if thinking:
        if timeout < 600:
            timeout = 600
        payload["reasoning"] = {"effort": reasoning_effort or "minimal"}
    else:
        # Standard models
        payload.update({
            "max_tokens": 25000,
            "temperature": 0.0,
        })

    # Wait our turn at the shared rate limiter before hitting the network.
    # Each retry attempt acquires a fresh permit so the in-flight cap stays accurate.
    limiter = get_rate_limiter()
    limiter.acquire(openrouter_model)
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)

        # 429 → feed the AIMD signal so the bucket halves before the retry decorator
        # backs off; all other workers on this model see the new (slower) rate too.
        if response.status_code == 429:
            ra_raw = response.headers.get("Retry-After")
            try:
                ra = float(ra_raw) if ra_raw is not None else None
            except ValueError:
                ra = None
            limiter.on_429(openrouter_model, retry_after=ra)
            # Let the retry decorator handle the actual sleep + retry loop.
            response.raise_for_status()

        # 402 = insufficient credits. Retrying can't fix it and every subsequent
        # puzzle would fail the same way — abort the run with the API's own
        # explanation (it includes the exact affordable token count).
        if response.status_code == 402:
            try:
                detail = response.json().get("error", {}).get("message", "")
            except Exception:
                detail = ""
            raise InsufficientCreditsError(
                f"OpenRouter credits exhausted (402): {detail or 'Payment Required'} — "
                f"top up at https://openrouter.ai/settings/credits"
            )

        # Check for OpenRouter-specific errors before raising
        if not response.ok:
            try:
                error_data = response.json()
                error_msg = error_data.get("error", {}).get("message", "")

                # Check for data policy configuration error
                if "data policy" in error_msg.lower() and response.status_code == 404:
                    logger.error(f"[OpenRouter] Data policy configuration required for model: {openrouter_model}")
                    logger.error(f"[OpenRouter] Error details: {error_msg}")
                    detailed_msg = (
                        f"OpenRouter data policy error for model '{openrouter_model}': {error_msg}\n"
                        f"Configure your data policy settings at: https://openrouter.ai/settings/privacy"
                    )
                    error = requests.HTTPError(detailed_msg)
                    error.response = response
                    raise error
            except (ValueError, KeyError):
                # If we can't parse the error JSON, fall through to default handling
                pass

        response.raise_for_status()

        response_data = response.json()

        # OpenRouter occasionally returns HTTP 200 with an error body (no `choices`)
        # when an upstream provider is throttled or misbehaving. Raise as a
        # RequestException so retry_with_backoff gets another attempt instead of
        # letting a KeyError('choices') escape upstream.
        if not response_data.get("choices"):
            err = response_data.get("error") or response_data
            # Treat upstream-throttled 200s the same as 429 for the AIMD loop —
            # the symptom (provider can't serve us right now) is identical.
            limiter.on_429(openrouter_model, retry_after=None)
            raise requests.RequestException(f"OpenRouter 200 OK but no 'choices' in body: {err}")

        choice = response_data["choices"][0]
        message = choice.get("message", {})
        content = message.get("content", "") or ""
        raw_usage = response_data.get("usage")

        # OpenRouter occasionally returns HTTP 200 with `choices` present, a real
        # (but partial, cut off mid-sentence with no closing tag) or empty
        # `content`, finish_reason "stop", and a `usage` block whose token counts
        # are all zero. That's a transient upstream fault (re-running the same
        # request usually succeeds), not a real model answer — not a rate signal
        # either, so this doesn't touch the AIMD limiter beyond releasing the
        # in-flight slot. completion_tokens > 0 (including a genuine max_tokens
        # truncation with finish_reason "length") is never retried here.
        if raw_usage is not None and (raw_usage.get("completion_tokens") or 0) == 0:
            finish_reason = choice.get("finish_reason")
            native_finish_reason = choice.get("native_finish_reason")
            response_provider = response_data.get("provider")
            tail = content[-120:] if content else ""
            raise PartialResponseError(
                "OpenRouter returned zero completion_tokens with finish_reason="
                f"{finish_reason!r}, native_finish_reason={native_finish_reason!r}, "
                f"provider={response_provider!r}, content_length={len(content)}, "
                f"content_tail={tail!r}"
            )

        limiter.on_success(openrouter_model)

        # DEBUG: Log if content is missing but tokens were used
        usage = raw_usage or {}
        completion_tokens = usage.get("completion_tokens", 0)

        if (not content or content.strip() == "") and completion_tokens > 0:
            logger.warning(f"[OpenRouter] Model generated {completion_tokens} tokens but content is empty!")
            logger.warning(f"[OpenRouter] finish_reason: {choice.get('finish_reason')}")
            logger.warning(f"[OpenRouter] Message keys: {list(message.keys())}")

        response_data["_backoff_sec"] = get_last_backoff_sec()
        return response_data
    finally:
        limiter.release(openrouter_model)


def _get_api_key() -> str:
    """Get OpenRouter API key from environment."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable not set")
    return api_key
