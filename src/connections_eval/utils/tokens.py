"""Token counting utilities."""

import tiktoken
from typing import Dict, Optional


def count_tokens(text: str, model_name: str = "gpt-4") -> int:
    """
    Count tokens in text using tiktoken.
    
    Args:
        text: Text to count tokens for
        model_name: Model name for encoding selection
        
    Returns:
        Number of tokens
    """
    try:
        # Use cl100k_base encoding as default for most modern models
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        # Fallback to rough word-based estimation
        return len(text.split()) * 1.3  # rough approximation


def extract_token_usage(response_data: dict) -> tuple[Optional[int], Optional[int], str]:
    """
    Extract token usage from API response.
    
    Args:
        response_data: Raw API response data
        
    Returns:
        Tuple of (prompt_tokens, completion_tokens, method)
        method is either "API" or "APPROXIMATE"
    """
    usage = response_data.get("usage", {})
    
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    
    if prompt_tokens is not None and completion_tokens is not None:
        return prompt_tokens, completion_tokens, "API"
    
    return None, None, "APPROXIMATE"


def extract_cache_info(response_data: dict) -> Dict[str, Optional[int]]:
    """
    Extract prompt cache information from API response.

    Args:
        response_data: Raw API response data

    Returns:
        Dict with 'cached_tokens' and 'cache_discount' keys (values may be None)
    """
    usage = response_data.get("usage", {})
    prompt_details = usage.get("prompt_tokens_details", {})

    cached_tokens = prompt_details.get("cached_tokens")
    cache_discount = usage.get("cache_discount")

    return {
        "cached_tokens": cached_tokens,
        "cache_discount": cache_discount,
    }


def extract_cost_info(response_data: dict) -> tuple[Optional[float], Optional[float]]:
    """
    Extract cost information from API response.
    
    Args:
        response_data: Raw API response data
        
    Returns:
        Tuple of (total_cost, upstream_cost)
        Costs are in USD or None if not available
    """
    usage = response_data.get("usage", {})

    # Total cost charged by OpenRouter
    total_cost = usage.get("cost")

    # Upstream cost and BYOK flag
    cost_details = usage.get("cost_details", {})
    upstream_cost = cost_details.get("upstream_inference_cost")
    is_byok = usage.get("is_byok", False)

    # Only record upstream_cost for BYOK requests. For BYOK, cost is
    # OpenRouter's routing fee and upstream_inference_cost is the provider
    # charge (additive). For non-BYOK, cost already includes the upstream
    # charge so upstream_inference_cost is informational only.
    if not is_byok:
        upstream_cost = None

    return total_cost, upstream_cost
