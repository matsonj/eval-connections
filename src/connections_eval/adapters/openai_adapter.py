"""OpenAI API adapter."""

import requests
from typing import Dict, List
from ..utils.retry import retry_with_backoff


@retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(requests.RequestException,))
def chat(messages: List[Dict], model: str, timeout: int = 60) -> Dict:
    """
    Call OpenAI Chat Completions API.
    
    Args:
        messages: List of message objects with 'role' and 'content'
        model: OpenAI model name (e.g., 'o3', 'o4-mini', 'gpt-4o')
        timeout: Request timeout in seconds
        
    Returns:
        Raw API response JSON
        
    Raises:
        requests.RequestException: On API errors
    """
    url = "https://api.openai.com/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }
    
    # Check if this is a reasoning model that doesn't support certain parameters
    is_reasoning_model = model.startswith(('o1', 'o3', 'o4'))
    
    payload = {
        "model": model,
        "messages": messages,
    }
    
    # Only add these parameters for non-reasoning models
    if not is_reasoning_model:
        payload.update({
            "max_tokens": 100,
            "temperature": 0.0,
        })
    
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    
    return response.json()


def _get_api_key() -> str:
    """Get OpenAI API key from environment."""
    import os
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    return api_key
