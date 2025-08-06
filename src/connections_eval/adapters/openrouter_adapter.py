"""OpenRouter API adapter."""

import requests
from typing import Dict, List
from ..utils.retry import retry_with_backoff


# Model mappings are now loaded from inputs/model_mappings.yml


@retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(requests.RequestException,))
def chat(messages: List[Dict], model: str, timeout: int = 60) -> Dict:
    """
    Call OpenRouter Chat Completions API.
    
    Args:
        messages: List of message objects with 'role' and 'content'
        model: OpenRouter model ID (e.g., 'openai/o3', 'x-ai/grok-3')
        timeout: Request timeout in seconds
        
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
    
    # Check if this is a reasoning model that doesn't support certain parameters
    is_openai_reasoning_model = any(openrouter_model.endswith(m) for m in ['o1', 'o3', 'o4', 'o1-mini', 'o3-mini', 'o4-mini'])
    
    # Check if this is a Grok-4 reasoning model (doesn't support max_tokens, temperature, etc.)
    is_grok4_reasoning_model = openrouter_model == 'x-ai/grok-4'
    
    # Check if this is a GPT OSS reasoning model (doesn't support max_tokens, temperature, etc.)
    is_gpt_oss_reasoning_model = any(openrouter_model.endswith(m) for m in ['gpt-oss-120b', 'gpt-oss-20b'])
    
    # Check if this is a Gemini model with reasoning capabilities
    is_gemini_reasoning_model = openrouter_model.startswith('google/gemini-2.5')
    
    payload = {
        "model": openrouter_model,
        "messages": messages,
        "usage": {
            "include": True  # Request cost and usage information
        }
    }
    
    # Handle different model types
    if is_openai_reasoning_model or is_grok4_reasoning_model or is_gpt_oss_reasoning_model:
        # OpenAI, Grok-4, and GPT OSS reasoning models don't support max_tokens or temperature
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



