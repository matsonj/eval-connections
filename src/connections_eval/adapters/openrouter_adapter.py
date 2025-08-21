"""OpenRouter API adapter."""

import requests
import yaml
import os
from typing import Dict, List, Set
from ..utils.retry import retry_with_backoff


def _load_thinking_models() -> Set[str]:
    """Load the set of thinking model IDs from model_mappings.yml."""
    # Get the path to the yaml file relative to this module
    current_dir = os.path.dirname(__file__)
    yaml_path = os.path.join(current_dir, '../../../inputs/model_mappings.yml')
    yaml_path = os.path.abspath(yaml_path)
    
    try:
        with open(yaml_path, 'r') as f:
            config = yaml.safe_load(f)
            thinking_models = config['models']['thinking']
            return set(thinking_models.values())
    except (FileNotFoundError, KeyError, yaml.YAMLError):
        # Fallback to empty set if config loading fails
        return set()


# Cache the thinking models set
_THINKING_MODELS = _load_thinking_models()


@retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(requests.RequestException,))
def chat(messages: List[Dict], model: str, timeout: int = 300) -> Dict:
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
    
    # Check if this is a thinking model
    is_thinking_model = openrouter_model in _THINKING_MODELS
    
    # Special handling for Gemini reasoning models (keeps temperature)
    is_gemini_thinking_model = openrouter_model == 'google/gemini-2.5-pro'
    
    payload = {
        "model": openrouter_model,
        "messages": messages,
        "usage": {
            "include": True  # Request cost and usage information
        }
    }
    
    # Handle different model types
    if is_thinking_model:
        # Thinking models don't support max_tokens or temperature and need longer timeout
        if timeout < 600:
            timeout = 600
            
        # Special case: Gemini thinking models keep temperature
        if is_gemini_thinking_model:
            payload.update({
                "temperature": 0.0,
            })
    else:
        # Standard models
        payload.update({
            "max_tokens": 1000,
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



