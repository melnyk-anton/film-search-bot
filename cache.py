"""In-memory cache for movie details."""

import asyncio
from typing import Dict, Optional
from config import MAX_CACHE_SIZE, logger

_movie_cache: Dict[int, dict] = {}
_cache_lock = asyncio.Lock()


def get_cache() -> Dict[int, dict]:
    """Get the cache dictionary (for internal use)."""
    return _movie_cache


def get_cache_lock():
    """Get the cache lock (for internal use)."""
    return _cache_lock


async def get_cached_movie(movie_id: int) -> Optional[dict]:
    """Get movie from cache."""
    async with _cache_lock:
        return _movie_cache.get(movie_id)


async def set_cached_movie(movie_id: int, movie_data: dict):
    """Cache movie data."""
    async with _cache_lock:
        _movie_cache[movie_id] = movie_data
        if len(_movie_cache) > MAX_CACHE_SIZE:
            oldest_key = next(iter(_movie_cache))
            del _movie_cache[oldest_key]


async def clear_cache():
    """Clear the movie cache."""
    async with _cache_lock:
        _movie_cache.clear()
