"""OpenRouter API adapter."""

import requests
from typing import Dict, List
from ..utils.retry import retry_with_backoff


# Model mappings from short names to OpenRouter model IDs
MODEL_MAPPINGS = {
    # OpenAI models (updated with correct OpenRouter model IDs)
    "o3": "openai/o3",
    "o3-pro": "openai/o3-pro",
    "o3-mini": "openai/o3-mini", 
    "o4-mini": "openai/o4-mini",
    "gpt4": "openai/gpt-4",
    "gpt4-turbo": "openai/gpt-4-turbo",
    "gpt4o": "openai/gpt-4o",
    "gpt4o-mini": "openai/gpt-4o-mini",
    
    # xAI models (corrected prefix: x-ai not xai)
    "grok3": "x-ai/grok-3",
    "grok3-mini": "x-ai/grok-3-mini",
    "grok4": "x-ai/grok-4",
    
    # Anthropic models (using stable versions)
    "sonnet": "anthropic/claude-3.5-sonnet",
    "sonnet-4": "anthropic/claude-sonnet-4",
    "opus": "anthropic/claude-3-opus",
    "opus-4": "anthropic/claude-opus-4",
    "haiku": "anthropic/claude-3.5-haiku",
    
    # Google models
    "gemini": "google/gemini-2.5-pro",
    "gemini-flash": "google/gemini-2.0-flash-001",
}


@retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(requests.RequestException,))
def chat(messages: List[Dict], model: str, timeout: int = 60) -> Dict:
    """
    Call OpenRouter Chat Completions API.
    
    Args:
        messages: List of message objects with 'role' and 'content'
        model: Short model name (e.g., 'o3', 'grok3') or full OpenRouter model ID
        timeout: Request timeout in seconds
        
    Returns:
        Raw API response JSON
        
    Raises:
        requests.RequestException: On API errors
        ValueError: If model is not supported
    """
    # Map short name to full OpenRouter model ID
    if model in MODEL_MAPPINGS:
        openrouter_model = MODEL_MAPPINGS[model]
    elif "/" in model:
        # Already a full model ID
        openrouter_model = model
    else:
        raise ValueError(f"Unsupported model: {model}")
    
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/matsonj/eval-connections",
        "X-Title": "Connections Eval"
    }
    
    # Check if this is a reasoning model that doesn't support certain parameters
    is_openai_reasoning_model = any(openrouter_model.endswith(m) for m in ['o1', 'o3', 'o4', 'o1-mini', 'o3-mini', 'o4-mini'])
    
    # Check if this is a Gemini model with reasoning capabilities
    is_gemini_reasoning_model = openrouter_model.startswith('google/gemini-2.5')
    
    payload = {
        "model": openrouter_model,
        "messages": messages,
    }
    
    # Handle different model types
    if is_openai_reasoning_model:
        # OpenAI reasoning models don't support max_tokens or temperature
        pass
    elif is_gemini_reasoning_model:
        # Gemini reasoning models: no max_tokens limit (let them use what they need)
        payload.update({
            "temperature": 0.0,
        })
    else:
        # Standard models
        payload.update({
            "max_tokens": 100,
            "temperature": 0.0,
        })
    
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    
    return response.json()


def _get_api_key() -> str:
    """Get OpenRouter API key from environment."""
    import os
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable not set")
    return api_key


def get_supported_models() -> List[str]:
    """Get list of supported model short names."""
    return list(MODEL_MAPPINGS.keys())
