"""Google Gemini API adapter."""

import requests
from typing import Dict, List
from ..utils.retry import retry_with_backoff


@retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(requests.RequestException,))
def chat(messages: List[Dict], model: str, timeout: int = 60) -> Dict:
    """
    Call Google Gemini API.
    
    Args:
        messages: List of message objects with 'role' and 'content'
        model: Gemini model name (e.g., 'gemini-2.5-pro')
        timeout: Request timeout in seconds
        
    Returns:
        Raw API response JSON (normalized to OpenAI format)
        
    Raises:
        requests.RequestException: On API errors
    """
    api_key = _get_api_key()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    headers = {
        "Content-Type": "application/json",
    }
    
    # Convert messages to Gemini format
    contents = []
    for msg in messages:
        role = "user" if msg["role"] in ["user", "system"] else "model"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}]
        })
    
    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 100,
            "temperature": 0.0,
        }
    }
    
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    
    data = response.json()
    
    # Normalize to OpenAI format
    text_content = ""
    if "candidates" in data and data["candidates"]:
        candidate = data["candidates"][0]
        if "content" in candidate and "parts" in candidate["content"]:
            parts = candidate["content"]["parts"]
            if parts and "text" in parts[0]:
                text_content = parts[0]["text"]
    
    normalized = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": text_content
            }
        }],
        "usage": {
            "prompt_tokens": data.get("usageMetadata", {}).get("promptTokenCount"),
            "completion_tokens": data.get("usageMetadata", {}).get("candidatesTokenCount"),
        }
    }
    
    return normalized


def _get_api_key() -> str:
    """Get Gemini API key from environment."""
    import os
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    return api_key
