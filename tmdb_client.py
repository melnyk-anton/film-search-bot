"""TMDb API client for movie data retrieval."""

import asyncio
import httpx
from typing import Tuple, Optional
from config import TMDB_API_KEY, TMDB_BASE_URL, TMDB_TIMEOUT, logger
from utils import validate_and_build_poster_url
from typing import Tuple, Optional, List, Dict


async def fetch_poster_and_verify_movie(movie_id: int, movie_title: str = "") -> Tuple[str, dict]:
    """
    Fetch poster URL and full movie details from TMDb API.
    
    Args:
        movie_id: TMDb movie ID
        movie_title: Optional movie title for verification
        
    Returns:
        Tuple of (poster_url, movie_details_dict)
    """
    try:
        movie_id = int(movie_id)
    except (ValueError, TypeError):
        logger.error(f"Invalid movie_id type: {movie_id}")
        return "", {}
    
    try:
        async with httpx.AsyncClient(timeout=TMDB_TIMEOUT) as client:
            detail_url = f"{TMDB_BASE_URL}/movie/{movie_id}"
            detail_resp = await client.get(detail_url, params={"api_key": TMDB_API_KEY})
            
            if detail_resp.status_code == 200:
                movie_details = detail_resp.json()
                returned_id = movie_details.get("id")
                
                # Verify movie ID matches
                if int(returned_id) != int(movie_id):
                    logger.error(f"Movie ID mismatch! Requested {movie_id}, got {returned_id}")
                    return "", {}
                
                # Verify title if provided - reject if completely different
                if movie_title and movie_details.get("title"):
                    returned_title = movie_details.get("title", "")
                    expected_norm = movie_title.lower().strip()
                    returned_norm = returned_title.lower().strip()
                    
                    # If titles are completely different (no shared words), this is likely the wrong movie
                    if expected_norm != returned_norm:
                        expected_words = set(expected_norm.split()[:4])
                        returned_words = set(returned_norm.split()[:4])
                        # Remove common words for comparison
                        common_words = {"the", "a", "an", "and", "of", "in", "on", "at", "to", "for"}
                        expected_words = {w for w in expected_words if w not in common_words}
                        returned_words = {w for w in returned_words if w not in common_words}
                        
                        if not expected_words.intersection(returned_words) and len(expected_words) > 0 and len(returned_words) > 0:
                            logger.error(f"Title mismatch for ID {movie_id}: Expected '{movie_title}', got '{returned_title}' - REJECTING")
                            return "", {}  # Return empty to indicate invalid movie
                
                poster_path = movie_details.get("poster_path", "")
                poster_url = validate_and_build_poster_url(poster_path)
                
                if poster_url:
                    logger.debug(f"Fetched poster for movie {movie_id}: {returned_title}")
                    return poster_url, movie_details
                else:
                    logger.warning(f"No valid poster_path for movie {movie_id}: {returned_title or movie_title}")
                    return "", movie_details
            else:
                logger.warning(f"Failed to fetch movie details for {movie_id}: HTTP {detail_resp.status_code}")
    except Exception as e:
        logger.error(f"Error fetching poster for movie {movie_id}: {e}")
    
    return "", {}


async def fetch_movie_details(movie_id: int) -> Optional[dict]:
    """Fetch full movie details from TMDb."""
    _, details = await fetch_poster_and_verify_movie(movie_id)
    return details if details else None


async def fetch_movie_videos(movie_id: int) -> str:
    """
    Fetch trailer URL for a movie.
    
    Returns:
        Trailer URL or empty string
    """
    try:
        async with httpx.AsyncClient(timeout=TMDB_TIMEOUT) as client:
            videos_url = f"{TMDB_BASE_URL}/movie/{movie_id}/videos"
            videos_resp = await asyncio.wait_for(
                client.get(videos_url, params={"api_key": TMDB_API_KEY}),
                timeout=0.8
            )
            
            if videos_resp.status_code == 200:
                videos_data = videos_resp.json()
                for video in videos_data.get("results", []):
                    if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                        return f"https://www.youtube.com/watch?v={video.get('key')}"
    except Exception:
        pass
    
    return ""


async def search_person(name: str) -> Optional[dict]:
    """
    Search for a person (actor/director) on TMDb.
    
    Returns:
        Person details dict or None
    """
    try:
        async with httpx.AsyncClient(timeout=TMDB_TIMEOUT) as client:
            url = f"{TMDB_BASE_URL}/search/person"
            params = {"api_key": TMDB_API_KEY, "query": name, "page": 1}
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                if results:
                    return results[0]
    except Exception as e:
        logger.error(f"Error searching for person {name}: {e}")
    
    return None


async def get_person_movie_credits(person_id: int) -> list:
    """
    Get movie credits for a person.
    
    Returns:
        List of movies the person has worked on
    """
    try:
        async with httpx.AsyncClient(timeout=TMDB_TIMEOUT) as client:
            url = f"{TMDB_BASE_URL}/person/{person_id}/movie_credits"
            params = {"api_key": TMDB_API_KEY}
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("cast", []) + data.get("crew", [])
    except Exception as e:
        logger.error(f"Error fetching credits for person {person_id}: {e}")
    
    return []
