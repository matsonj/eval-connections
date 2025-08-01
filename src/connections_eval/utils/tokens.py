"""Token counting utilities."""

import tiktoken
from typing import Optional


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
    
    # Upstream cost (for BYOK requests)
    cost_details = usage.get("cost_details", {})
    upstream_cost = cost_details.get("upstream_inference_cost")
    
    return total_cost, upstream_cost
