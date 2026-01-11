"""Movie search logic with actor detection, genre filtering, and quality scoring."""

import asyncio
import re
import time
from datetime import datetime
from typing import Optional, Dict, List
import httpx
from config import (
    TMDB_API_KEY, TMDB_BASE_URL, TMDB_TIMEOUT,
    GENRE_MAP, MIN_RATING, MIN_VOTE_COUNT,
    MIN_RATING_FALLBACK, MIN_VOTE_COUNT_FALLBACK,
    logger
)
from tmdb_client import search_person, get_person_movie_credits, fetch_poster_and_verify_movie, fetch_movie_videos
from memory_client import fetch_user_memories
from utils import validate_and_build_poster_url
from cache import get_cached_movie, set_cached_movie


def get_tmdb_genre_id(genre_name: str) -> Optional[int]:
    """Get TMDb genre ID from genre name."""
    genre_name_lower = genre_name.lower().strip()
    
    if genre_name_lower in GENRE_MAP:
        return GENRE_MAP[genre_name_lower]
    
    for key, genre_id in GENRE_MAP.items():
        if key in genre_name_lower or genre_name_lower in key:
            return genre_id
    
    return None


async def discover_trending_movies(genre_ids: Optional[List[int]] = None, excluded_ids: Optional[List[int]] = None) -> List[Dict]:
    """Discover trending/popular movies using TMDb discover API."""
    excluded_ids_set = set(int(id) for id in (excluded_ids or []) if id)
    results = []
    
    try:
        discover_url = f"{TMDB_BASE_URL}/discover/movie"
        params = {
            "api_key": TMDB_API_KEY,
            "sort_by": "popularity.desc",
            "vote_average.gte": MIN_RATING,
            "vote_count.gte": MIN_VOTE_COUNT,
            "primary_release_date.gte": "2020-01-01",
            "page": 1
        }
        
        if genre_ids:
            unique_genre_ids = list(dict.fromkeys(genre_ids))[:2]
            params["with_genres"] = ",".join([str(gid) for gid in unique_genre_ids])
        
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(discover_url, params=params)
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                logger.info(f"Discover API found {len(results)} trending movies")
    
    except Exception as e:
        logger.error(f"Error using discover API: {e}")
    
    if excluded_ids_set and results:
        filtered_results = [r for r in results if r.get("id") not in excluded_ids_set]
        results = filtered_results
    
    return results[:20]


def extract_actor_name(query: str) -> Optional[str]:
    """Extract actor name from query using pattern matching and heuristics."""
    query_lower = query.lower()
    
    # Known actor names
    known_actors = {
        "statham": "Jason Statham",
        "brad pitt": "Brad Pitt",
        "brad pit": "Brad Pitt",
        "tom hanks": "Tom Hanks",
        "dicaprio": "Leonardo DiCaprio",
        "leonardo": "Leonardo DiCaprio",
    }
    
    for key, full_name in known_actors.items():
        if key in query_lower:
            return full_name
    
    # Pattern matching
    patterns = [
        r"films?\s+(?:with|wth)\s+([a-zA-Z\s]+?)(?:\s+as\s+an?\s+actor|\s+actor|$|films|movies|for)",
        r"films?\s+starring\s+([a-zA-Z\s]+?)(?:\s+$|films|movies|for)",
        r"films?\s+([A-Z][a-z]+\s+[A-Z][a-z]+)(?:\s+$|films|movies|for)",
        r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+films?",
        r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+movies?",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 2:
                return name.title()
    
    # Heuristic: Look for capitalized words
    words = query.split()
    stop_words = {"find", "films", "film", "movies", "movie", "with", "starring", 
                  "featuring", "actor", "actress", "directed", "by", "as", "an", 
                  "a", "the", "for", "me", "wth", "give", "suggest"}
    clean_words = [w for w in words if w.lower() not in stop_words]
    
    cap_words = [(i, w) for i, w in enumerate(clean_words) 
                 if w and (w[0].isupper() or (len(w) > 4 and w.isalpha()))]
    
    if len(cap_words) >= 2:
        return " ".join([w for _, w in cap_words[-2:]])
    elif len(cap_words) == 1:
        return cap_words[0][1]
    elif len(clean_words) >= 2:
        return " ".join(clean_words[-2:]).title()
    
    return None


def is_actor_query(query: str) -> bool:
    """Check if query is requesting movies by a specific actor."""
    query_lower = query.lower()
    actor_keywords = ["with", "wth", "starring", "featuring", "actor", "actress", "by"]
    has_actor_keyword = any(kw in query_lower for kw in actor_keywords)
    
    actor_name_patterns = [
        r"films?\s+(?:with\s+|wth\s+|starring\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+films?",
        r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s+movies?",
    ]
    has_actor_name_pattern = any(re.search(pattern, query, re.IGNORECASE) for pattern in actor_name_patterns)
    
    # Known actor names (including common typos/variations)
    known_actor_names = ["pitt", "statham", "stathem", "hanks", "dicaprio", "cruise", "damon", 
                        "affleck", "smith", "reeves", "jolie", "depp", "bale", "hiddleston"]
    has_known_actor = any(name in query_lower for name in known_actor_names)
    
    # Also check for pattern: "films [Name]" where Name is capitalized
    if "films" in query_lower or "movies" in query_lower:
        words = query.split()
        # Look for capitalized words that might be actor names
        cap_words = [w for w in words if w and w[0].isupper() and len(w) > 3]
        if len(cap_words) >= 1 and has_actor_keyword:
            return True
    
    return has_actor_keyword or (("films" in query_lower or "movies" in query_lower) and 
                                 (has_actor_name_pattern or has_known_actor))


async def search_actor_movies(actor_name: str, excluded_ids: set) -> List[Dict]:
    """Search for movies by a specific actor."""
    person = await search_person(actor_name)
    if not person:
        return []
    
    person_id = person.get("id")
    movies_list = await get_person_movie_credits(person_id)
    
    if not movies_list:
        return []
    
    # Filter by quality
    filtered = []
    for movie in movies_list:
        movie_id = movie.get("id")
        if movie_id in excluded_ids:
            continue
        
        rating = movie.get("vote_average", 0)
        vote_count = movie.get("vote_count", 0)
        
        if rating >= MIN_RATING and vote_count >= MIN_VOTE_COUNT:
            filtered.append(movie)
        elif rating >= MIN_RATING_FALLBACK and vote_count >= MIN_VOTE_COUNT_FALLBACK:
            filtered.append(movie)
    
    filtered.sort(key=lambda x: (x.get("popularity", 0), x.get("vote_average", 0)), reverse=True)
    return filtered[:30]


def filter_by_quality(results: List[Dict], is_christmas: bool = False) -> List[Dict]:
    """Filter movies by quality criteria (rating, votes, recency)."""
    rated_results = []
    for r in results:
        rating = r.get("vote_average", 0)
        vote_count = r.get("vote_count", 0)
        
        if rating >= MIN_RATING and vote_count >= MIN_VOTE_COUNT:
            release_date = r.get("release_date", "")
            if release_date and not is_christmas:
                try:
                    year = int(release_date.split("-")[0])
                    if year < 2010 and (rating < 8.5 or vote_count < 10000):
                        continue
                except (ValueError, AttributeError):
                    pass
            rated_results.append(r)
    
    if rated_results:
        return rated_results
    
    # Fallback: relaxed criteria
    for r in results:
        rating = r.get("vote_average", 0)
        vote_count = r.get("vote_count", 0)
        if rating >= MIN_RATING_FALLBACK and vote_count >= MIN_VOTE_COUNT_FALLBACK:
            rated_results.append(r)
    
    return rated_results


def score_movie(movie: Dict, query: str, from_actor_search: bool = False) -> float:
    """Score movie based on rating, popularity, recency, and query relevance."""
    score = 0.0
    rating = movie.get("vote_average", 0)
    vote_count = movie.get("vote_count", 0)
    popularity = movie.get("popularity", 0)
    title = (movie.get("title", "") or "").lower()
    overview = (movie.get("overview", "") or "").lower()
    
    if from_actor_search:
        score += 50.0  # Prioritize actor search results
    
    score += rating * 6.0
    
    # Popularity scoring
    if vote_count >= 10000:
        score += 15.0
    elif vote_count >= 5000:
        score += 10.0
    elif vote_count >= 2000:
        score += 6.0
    elif vote_count < 1000:
        score -= 5.0
    
    if popularity >= 100:
        score += 12.0
    elif popularity >= 50:
        score += 8.0
    elif popularity < 5:
        score -= 3.0
    
    # Recency scoring
    release_date = movie.get("release_date", "")
    if release_date:
        try:
            year = int(release_date.split("-")[0])
            current_year = datetime.now().year
            if year < 2010:
                score -= 15.0
            elif year >= 2020:
                recency_bonus = min((year - 2020) * 2.0, 10.0)
                score += recency_bonus
            if year >= current_year - 2:
                score += 8.0
        except (ValueError, AttributeError):
            pass
    
    # Query relevance
    query_words = set(query.lower().split())
    title_matches = sum(1 for word in query_words if len(word) > 2 and word in title)
    score += title_matches * 10.0
    
    overview_matches = sum(1 for word in query_words if len(word) > 2 and word in overview)
    score += overview_matches * 3.0
    
    return score


async def fast_movie_search(query: str, user_id: str, excluded_ids: Optional[List[int]] = None) -> Optional[Dict]:
    """
    Fast movie search with actor detection, genre filtering, and quality scoring.
    
    Returns:
        Dict with "movies" key containing list of movie dicts, or None if no results
    """
    start_time = time.time()
    excluded_ids = excluded_ids or []
    excluded_ids_set = set(int(id) for id in excluded_ids if id)
    
    logger.info(f"Fast movie search: query='{query}', excluded={len(excluded_ids_set)}")
    
    query_lower = query.lower()
    is_christmas_query = any(kw in query_lower for kw in ["christmas", "xmas", "holiday"])
    results = []
    actor_search_attempted = False
    from_actor_search = False
    
    # Actor query detection and handling
    if is_actor_query(query):
        actor_search_attempted = True
        logger.info(f"Actor query detected: '{query}'")
        
        actor_name = extract_actor_name(query)
        if actor_name:
            logger.info(f"Extracted actor name: '{actor_name}'")
            actor_movies = await search_actor_movies(actor_name, excluded_ids_set)
            if actor_movies:
                results = actor_movies
                from_actor_search = True
                logger.info(f"Found {len(results)} movies for {actor_name}")
    
    # Genre-based discovery
    if not results:
        found_genres = []
        for keyword, genre_id in sorted(GENRE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            if keyword in query_lower:
                found_genres.append(genre_id)
                if len(found_genres) >= 2:
                    break
        
        if found_genres and not is_christmas_query:
            logger.info(f"Using discover API with genres {found_genres}")
            discover_results = await discover_trending_movies(genre_ids=found_genres, excluded_ids=excluded_ids)
            if discover_results:
                results = discover_results
    
    # Fallback to search API
    if not results and not actor_search_attempted:
        clean_query = "christmas" if is_christmas_query else " ".join(
            [w for w in query.lower().split() if w not in {"find", "a", "the", "an", "for", "me", "give"}][:5]
        )
        
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                search_url = f"{TMDB_BASE_URL}/search/movie"
                params = {"api_key": TMDB_API_KEY, "query": clean_query, "page": 1}
                response = await client.get(search_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])[:10]
        except Exception as e:
            logger.warning(f"Search API failed: {e}")
    
    if not results:
        return None
    
    # Filter by memory (watched/excluded)
    try:
        watched_ids, watched_titles, _, _ = await fetch_user_memories(user_id)
        all_excluded = watched_ids | excluded_ids_set
        watched_titles_set = {t.lower() for t in watched_titles}
        
        filtered = []
        for r in results:
            movie_id = r.get("id")
            movie_title = r.get("title", "").lower()
            
            if movie_id in all_excluded:
                continue
            if any(wt in movie_title or movie_title in wt for wt in watched_titles_set):
                continue
            
            filtered.append(r)
        
        results = filtered
        logger.info(f"Filtered to {len(results)} movies after excluding watched")
        
        if not results:
            return None
    except Exception:
        pass
    
    # Filter by quality
    results = filter_by_quality(results, is_christmas=is_christmas_query)
    
    if not results:
        return None
    
    # Filter Christmas movies
    if is_christmas_query:
        christmas_keywords = {"christmas", "xmas", "holiday", "santa", "elf", "scrooge", 
                             "home alone", "miracle on", "wonderful life"}
        filtered = []
        for r in results:
            text = f"{r.get('title', '')} {r.get('overview', '')}".lower()
            if any(kw in text for kw in christmas_keywords):
                filtered.append(r)
        if filtered:
            results = filtered
    
    # Score and sort
    if len(results) > 1:
        scored = [(score_movie(r, query, from_actor_search), r) for r in results]
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in scored]
    
    if not results:
        return None
    
    # Get first movie details
    first_movie = results[0]
    movie_id = first_movie.get("id")
    
    if not movie_id:
        return None
    
    # Check cache
    cached = await get_cached_movie(movie_id)
    if cached:
        logger.info(f"Fast search (cache hit) in {time.time() - start_time:.2f}s")
        return {"movies": [cached]}
    
    # Fetch movie details
    try:
        poster_url, movie_details = await fetch_poster_and_verify_movie(movie_id, first_movie.get("title", ""))
        if not movie_details:
            return None
        
        trailer_url = await fetch_movie_videos(movie_id)
        
        movie_data = {
            "id": movie_id,
            "title": movie_details.get("title", ""),
            "rating": movie_details.get("vote_average", 0),
            "poster_url": poster_url,
            "trailer_url": trailer_url,
            "overview": movie_details.get("overview", "")[:200],
            "genres": [g["name"] for g in movie_details.get("genres", [])],
            "runtime": movie_details.get("runtime", 0)
        }
        
        await set_cached_movie(movie_id, movie_data)
        
        # Build movie queue for actor queries
        if from_actor_search and len(results) > 1:
            movies_queue = [movie_data]
            for r in results[1:10]:
                movies_queue.append({
                    "id": r.get("id"),
                    "title": r.get("title", "Unknown"),
                    "rating": r.get("vote_average", 0),
                    "poster_url": validate_and_build_poster_url(r.get("poster_path", "")),
                    "trailer_url": "",
                    "overview": (r.get("overview", "") or "")[:200]
                })
            logger.info(f"Returning {len(movies_queue)} movies from actor search")
            return {"movies": movies_queue}
        
        logger.info(f"Fast search completed in {time.time() - start_time:.2f}s")
        return {"movies": [movie_data]}
    
    except Exception as e:
        logger.error(f"Error in fast movie search: {e}")
        return None
