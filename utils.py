"""Utility functions for the Film Recommendation Bot."""

import json
import re
from typing import Optional
from config import TMDB_IMAGE_BASE_URL, logger


def validate_and_build_poster_url(poster_path: str) -> str:
    """
    Validate and build a proper TMDb poster URL.
    Returns empty string if poster_path is invalid.
    """
    if not poster_path:
        return ""
    
    poster_path = str(poster_path).strip()
    
    if not poster_path or len(poster_path) < 5:
        return ""
    
    if not poster_path.startswith("/"):
        poster_path = "/" + poster_path
    
    valid_pattern = r'^/[a-zA-Z0-9_\-/]+\.(jpg|jpeg|png|webp)$'
    if not re.match(valid_pattern, poster_path, re.IGNORECASE):
        logger.warning(f"Invalid poster_path format: {poster_path[:50]}")
        return ""
    
    # Security: reject URLs or suspicious patterns
    suspicious_patterns = [
        r'http', r'https', r'<script', r'javascript', r'data:',
        r'\.com', r'\.org', r'\.net', r'www\.'
    ]
    for pattern in suspicious_patterns:
        if re.search(pattern, poster_path, re.IGNORECASE):
            logger.error(f"Security: Rejected suspicious poster_path: {poster_path[:50]}")
            return ""
    
    poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}"
    
    if not poster_url.startswith("https://image.tmdb.org/t/p/w"):
        logger.error(f"Invalid poster URL format: {poster_url[:100]}")
        return ""
    
    if not re.match(r'^https://image\.tmdb\.org/t/p/w\d+/[a-zA-Z0-9_\-/]+\.(jpg|jpeg|png|webp)$', poster_url, re.IGNORECASE):
        logger.error(f"Poster URL doesn't match TMDb format: {poster_url[:100]}")
        return ""
    
    return poster_url


def parse_json_response(response: str) -> Optional[dict]:
    """
    Parse JSON from agent response.
    Handles code blocks and plain JSON.
    """
    if not response:
        return None
    
    # Try to find JSON in code blocks first
    json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
    matches = re.findall(json_pattern, response, re.DOTALL)
    if matches:
        for match in reversed(matches):
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict) and "movies" in parsed:
                    return parsed
            except json.JSONDecodeError:
                continue
    
    # Try to find JSON in plain text
    json_start = response.find('{')
    if json_start != -1:
        brace_count = 0
        end_idx = json_start
        for i, char in enumerate(response[json_start:], start=json_start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break
        
        if end_idx > json_start:
            try:
                json_str = response[json_start:end_idx]
                parsed = json.loads(json_str)
                if isinstance(parsed, dict) and "movies" in parsed:
                    return parsed
            except json.JSONDecodeError:
                json_end = response.rfind('}') + 1
                if json_end > json_start:
                    try:
                        json_str = response[json_start:json_end]
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        pass
    
    return None
