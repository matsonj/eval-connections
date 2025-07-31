"""Anthropic API adapter."""

import requests
from typing import Dict, List
from ..utils.retry import retry_with_backoff


@retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(requests.RequestException,))
def chat(messages: List[Dict], model: str, timeout: int = 60) -> Dict:
    """
    Call Anthropic Messages API.
    
    Args:
        messages: List of message objects with 'role' and 'content'
        model: Anthropic model name (e.g., 'sonnet-4', 'opus-4')
        timeout: Request timeout in seconds
        
    Returns:
        Raw API response JSON (normalized to OpenAI format)
        
    Raises:
        requests.RequestException: On API errors
    """
    url = "https://api.anthropic.com/v1/messages"
    
    headers = {
        "x-api-key": _get_api_key(),
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    
    # Convert messages to Anthropic format
    system_message = None
    anthropic_messages = []
    
    for msg in messages:
        if msg["role"] == "system":
            system_message = msg["content"]
        else:
            anthropic_messages.append(msg)
    
    payload = {
        "model": model,
        "max_tokens": 100,
        "temperature": 0.0,
        "messages": anthropic_messages,
    }
    
    if system_message:
        payload["system"] = system_message
    
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    
    data = response.json()
    
    # Normalize to OpenAI format
    normalized = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": data.get("content", [{}])[0].get("text", "")
            }
        }],
        "usage": {
            "prompt_tokens": data.get("usage", {}).get("input_tokens"),
            "completion_tokens": data.get("usage", {}).get("output_tokens"),
        }
    }
    
    return normalized


def _get_api_key() -> str:
    """Get Anthropic API key from environment."""
    import os
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    return api_key
