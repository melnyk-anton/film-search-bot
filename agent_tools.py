"""Claude Agent tools and response handling."""

import asyncio
import json
import time
from typing import Any, Dict, List
import httpx
from claude_agent_sdk import tool, ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server

from config import (
    TMDB_API_KEY, TMDB_BASE_URL, TMDB_TIMEOUT, AGENT_TIMEOUT,
    logger
)
from tmdb_client import fetch_poster_and_verify_movie, fetch_movie_videos
from memory_client import get_memory_client
from utils import validate_and_build_poster_url
from cache import get_cache, get_cache_lock


async def create_agent_tools(user_id: str):
    """Create tools for the Claude agent."""
    
    @tool("search_movies", "Search for movies by title, keywords, or plot description using TMDb search API.", {
        "query": str,
        "page": {"type": "integer", "default": 1}
    })
    async def search_movies(args: Dict[str, Any]) -> Dict[str, Any]:
        """Search for movies on TMDb."""
        try:
            query = args.get("query", "")
            page = args.get("page", 1)
            
            url = f"{TMDB_BASE_URL}/search/movie"
            params = {"api_key": TMDB_API_KEY, "query": query, "page": page}
            
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            
            results = data.get("results", [])[:15]
            movies = [{
                "id": m.get("id"),
                "title": m.get("title"),
                "overview": m.get("overview", ""),
                "release_date": m.get("release_date", ""),
                "rating": m.get("vote_average", 0),
                "vote_count": m.get("vote_count", 0),
                "popularity": m.get("popularity", 0),
                "poster_path": m.get("poster_path")
            } for m in results]
            
            movies_str = "\n".join([
                f"- {m['title']} ({m['release_date'][:4] if m['release_date'] else 'N/A'}) - "
                f"Rating: {m['rating']}/10 ({m['vote_count']} votes)\n  {m['overview'][:200]}"
                for m in movies
            ])
            
            return {
                "content": [{
                    "type": "text",
                    "text": f"Found {len(movies)} movies:\n\n{movies_str}" if movies else "No movies found."
                }]
            }
        except Exception as e:
            logger.error(f"Error searching movies: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    @tool("search_person", "Search for actors, directors, or other people in movies.", {
        "name": str,
        "page": {"type": "integer", "default": 1}
    })
    async def search_person_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        """Search for people on TMDb."""
        try:
            name = args.get("name", "")
            url = f"{TMDB_BASE_URL}/search/person"
            params = {"api_key": TMDB_API_KEY, "query": name, "page": 1}
            
            async with httpx.AsyncClient(timeout=TMDB_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            
            results = data.get("results", [])[:5]
            people = [{
                "id": p.get("id"),
                "name": p.get("name", ""),
                "known_for_department": p.get("known_for_department", ""),
                "known_for": [m.get("title", "") for m in p.get("known_for", [])[:3]]
            } for p in results]
            
            people_str = "\n".join([
                f"- {p['name']} (ID: {p['id']}, {p['known_for_department']}) - "
                f"Known for: {', '.join(p['known_for'])}"
                for p in people
            ])
            
            return {
                "content": [{
                    "type": "text",
                    "text": f"Found {len(people)} people:\n\n{people_str}" if people else f"No people found matching '{name}'."
                }]
            }
        except Exception as e:
            logger.error(f"Error searching person: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    @tool("get_person_movies", "Get movies starring or directed by a specific person.", {
        "person_id": int,
        "department": {"type": "string", "default": "cast"}
    })
    async def get_person_movies(args: Dict[str, Any]) -> Dict[str, Any]:
        """Get movies by a person."""
        try:
            person_id = args.get("person_id")
            department = args.get("department", "cast")
            
            url = f"{TMDB_BASE_URL}/person/{person_id}/movie_credits"
            params = {"api_key": TMDB_API_KEY}
            
            async with httpx.AsyncClient(timeout=TMDB_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            
            movies_list = data.get("crew", []) if department == "crew" else data.get("cast", [])
            
            filtered = [{
                "id": m.get("id"),
                "title": m.get("title"),
                "overview": m.get("overview", ""),
                "release_date": m.get("release_date", ""),
                "rating": m.get("vote_average", 0),
                "vote_count": m.get("vote_count", 0),
                "popularity": m.get("popularity", 0)
            } for m in movies_list if m.get("vote_average", 0) >= 7.0 and m.get("vote_count", 0) >= 500]
            
            filtered.sort(key=lambda x: (x.get("popularity", 0), x.get("rating", 0)), reverse=True)
            filtered = filtered[:15]
            
            # Return structured format with IDs prominently displayed
            movies_list = []
            for m in filtered:
                movies_list.append(
                    f"Movie ID: {m['id']} | Title: {m['title']} | "
                    f"Rating: {m['rating']}/10 | Votes: {m['vote_count']} | "
                    f"Release: {m['release_date'][:4] if m['release_date'] else 'N/A'}"
                )
            
            movies_str = "\n".join(movies_list)
            
            # Include the exact JSON structure agent should return
            movies_json = json.dumps({"movies": [{"id": m["id"], "title": m["title"], "rating": m["rating"]} for m in filtered[:5]]}, indent=2)
            
            return {
                "content": [{
                    "type": "text",
                    "text": f"Found {len(filtered)} movies ({department}). CRITICAL: Use these EXACT IDs:\n\n{movies_str}\n\n"
                    f"Return this EXACT format with these IDs:\n{movies_json}"
                    if filtered else f"No high-rated popular movies found ({department})."
                }]
            }
        except Exception as e:
            logger.error(f"Error getting person movies: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    @tool("discover_movies_by_genre", "Discover trending/popular movies by genre.", {
        "genre_ids": {"type": "array", "items": {"type": "integer"}},
        "page": {"type": "integer", "default": 1}
    })
    async def discover_movies_by_genre(args: Dict[str, Any]) -> Dict[str, Any]:
        """Discover movies by genre."""
        try:
            genre_ids = args.get("genre_ids", [])
            if not genre_ids:
                return {"content": [{"type": "text", "text": "Error: genre_ids required."}]}
            
            genre_ids = genre_ids[:2]
            url = f"{TMDB_BASE_URL}/discover/movie"
            params = {
                "api_key": TMDB_API_KEY,
                "sort_by": "popularity.desc",
                "vote_average.gte": 7.0,
                "vote_count.gte": 500,
                "primary_release_date.gte": "2020-01-01",
                "with_genres": ",".join([str(gid) for gid in genre_ids]),
                "page": 1
            }
            
            async with httpx.AsyncClient(timeout=TMDB_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            
            results = data.get("results", [])[:15]
            movies = [{
                "id": m.get("id"),
                "title": m.get("title"),
                "overview": m.get("overview", ""),
                "release_date": m.get("release_date", ""),
                "rating": m.get("vote_average", 0),
                "vote_count": m.get("vote_count", 0),
                "popularity": m.get("popularity", 0)
            } for m in results]
            
            movies_str = "\n".join([
                f"- {m['title']} ({m['release_date'][:4] if m['release_date'] else 'N/A'}) - "
                f"Rating: {m['rating']}/10 ({m['vote_count']} votes)"
                for m in movies
            ])
            
            return {
                "content": [{
                    "type": "text",
                    "text": f"Found {len(movies)} trending movies (genres: {genre_ids}):\n\n{movies_str}"
                    if movies else f"No movies found for genres {genre_ids}."
                }]
            }
        except Exception as e:
            logger.error(f"Error discovering movies: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    @tool("get_movie_details", "Get detailed information about a specific movie by TMDb ID.", {
        "movie_id": int
    })
    async def get_movie_details(args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed movie information."""
        try:
            movie_id = args.get("movie_id")
            
            cache = get_cache()
            cache_lock = get_cache_lock()
            async with cache_lock:
                if movie_id in cache:
                    cached = cache[movie_id]
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Movie details (JSON):\n{json.dumps(cached, indent=2)}"
                        }]
                    }
            
            async with httpx.AsyncClient(timeout=1.0) as client:
                url = f"{TMDB_BASE_URL}/movie/{movie_id}"
                videos_url = f"{TMDB_BASE_URL}/movie/{movie_id}/videos"
                params = {"api_key": TMDB_API_KEY}
                
                try:
                    movie_resp, videos_resp = await asyncio.wait_for(
                        asyncio.gather(
                            client.get(url, params=params),
                            client.get(videos_url, params=params),
                            return_exceptions=True
                        ),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    raise Exception("Request timeout")
                
                if isinstance(movie_resp, Exception):
                    raise movie_resp
                
                movie_resp.raise_for_status()
                movie = movie_resp.json()
                
                trailer_url = ""
                if not isinstance(videos_resp, Exception) and videos_resp.status_code == 200:
                    videos_data = videos_resp.json()
                    for video in videos_data.get("results", []):
                        if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                            trailer_url = f"https://www.youtube.com/watch?v={video.get('key')}"
                            break
                
                poster_path = movie.get("poster_path", "")
                poster_url = validate_and_build_poster_url(poster_path)
                
                movie_data = {
                    "id": movie_id,
                    "title": movie.get("title", ""),
                    "rating": movie.get("vote_average", 0),
                    "release_date": movie.get("release_date", ""),
                    "overview": movie.get("overview", ""),
                    "poster_url": poster_url,
                    "trailer_url": trailer_url,
                    "genres": [g["name"] for g in movie.get("genres", [])],
                    "runtime": movie.get("runtime", 0)
                }
                
                async with cache_lock:
                    cache[movie_id] = movie_data
                    if len(cache) > 100:
                        oldest_key = next(iter(cache))
                        del cache[oldest_key]
                
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Movie details (JSON):\n{json.dumps(movie_data, indent=2)}"
                    }]
                }
        except Exception as e:
            logger.error(f"Error getting movie details: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    @tool("get_user_memories", "Retrieve user's memories from memory system.", {
        "query": {"type": "string", "default": ""}
    })
    async def get_user_memories(args: Dict[str, Any]) -> Dict[str, Any]:
        """Get user memories from Mem0."""
        try:
            query = args.get("query", "")
            memory_client = get_memory_client()
            
            search_query = query if query else "user preferences watched films"
            results = memory_client.search(query=search_query, filters={"user_id": str(user_id)})
            
            if isinstance(results, dict):
                memories = results.get("results", results.get("data", []))
            elif isinstance(results, list):
                memories = results
            else:
                memories = []
            
            if not memories:
                return {"content": [{"type": "text", "text": "No memories found."}]}
            
            memories_str = "\n".join([
                f"- {mem.get('memory', mem.get('content', mem.get('text', str(mem))))}"
                for mem in memories[:10]
            ])
            
            return {"content": [{"type": "text", "text": f"User memories:\n\n{memories_str}"}]}
        except Exception as e:
            logger.error(f"Error getting memories: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    @tool("save_watched_film", "Save a film that the user has watched.", {
        "film_title": str,
        "film_id": {"type": "integer", "default": None},
        "rating": {"type": "float", "default": None},
        "notes": {"type": "string", "default": ""}
    })
    async def save_watched_film(args: Dict[str, Any]) -> Dict[str, Any]:
        """Save watched film to memory."""
        try:
            film_title = args.get("film_title")
            film_id = args.get("film_id")
            rating = args.get("rating")
            notes = args.get("notes", "")
            
            memory_client = get_memory_client()
            memory_text = f"User watched film: {film_title}"
            if film_id:
                memory_text += f" (TMDb ID: {film_id})"
            if rating:
                memory_text += f". User rating: {rating}/10"
            if notes:
                memory_text += f". Notes: {notes}"
            
            message = [{"role": "user", "content": memory_text}]
            await asyncio.wait_for(
                asyncio.to_thread(memory_client.add, message, user_id=str(user_id)),
                timeout=2.0
            )
            
            return {"content": [{"type": "text", "text": f"Saved: {film_title}"}]}
        except Exception as e:
            logger.error(f"Error saving watched film: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    @tool("save_user_preference", "Save user's film preferences.", {
        "preference": str
    })
    async def save_user_preference(args: Dict[str, Any]) -> Dict[str, Any]:
        """Save user preference to memory."""
        try:
            preference = args.get("preference")
            memory_client = get_memory_client()
            message = [{"role": "user", "content": f"User preference: {preference}"}]
            
            await asyncio.wait_for(
                asyncio.to_thread(memory_client.add, message, user_id=str(user_id)),
                timeout=2.0
            )
            
            return {"content": [{"type": "text", "text": f"Preference saved: {preference}"}]}
        except Exception as e:
            logger.error(f"Error saving preference: {e}")
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}
    
    return [
        search_movies, get_movie_details, search_person_tool, get_person_movies,
        discover_movies_by_genre, get_user_memories, save_watched_film, save_user_preference
    ]


async def get_agent_response(user_id: str, user_message: str) -> Optional[str]:
    """Get response from Claude agent with intelligent query analysis."""
    from memory_client import fetch_user_memories
    
    start_time = time.time()
    
    try:
        watched_ids, watched_titles, disliked_genres, preferred_genres = await fetch_user_memories(user_id)
        
        watched_info = ""
        if watched_ids:
            watched_info += f"\n\nBLOCKED IDs: {sorted(list(watched_ids))[:20]}"
        if watched_titles:
            watched_info += f"\nBLOCKED TITLES: {', '.join(watched_titles[:10])}"
        
        preference_info = ""
        if disliked_genres:
            preference_info += f"\n\nAVOID genres: {', '.join(list(disliked_genres)[:10])}"
        if preferred_genres:
            preference_info += f"\nPREFER genres: {', '.join(list(preferred_genres)[:10])}"
        
        tools = await create_agent_tools(user_id)
        tools_server = create_sdk_mcp_server(
            name="films_memory",
            version="1.0.0",
            tools=tools
        )
        
        system_prompt = f"""You are an intelligent film recommendation assistant. User {user_id}.{watched_info}{preference_info}

Analyze the user's query and choose the best search strategy, then recommend ONE HIGH-QUALITY movie.

QUALITY REQUIREMENTS:
- Rating >= 7.0 (prefer 8.0+)
- Vote count >= 500 (prefer 5000+)
- Recent (2020+) preferred, but allow classics (8.5+) if requested
- NOT in BLOCKED IDs/TITLES
- High popularity = trending films

SEARCH STRATEGY:
1. ACTOR/DIRECTOR MENTIONED? → Use search_person → get_person_movies
   CRITICAL: When get_person_movies returns movie IDs, use those EXACT IDs in your response. Do NOT search by title or make up IDs.
2. GENRE REQUEST? → Use discover_movies_by_genre (except Christmas - use search_movies("christmas"))
3. PLOT DESCRIPTION? → Use search_movies with keywords
4. SPECIFIC TITLE? → Use search_movies

IMPORTANT: Always use the exact movie IDs returned by the tools. If a tool returns "ID: 12345" for a movie, use 12345 in your JSON response. Never search by title and guess IDs.

Return ONLY JSON:
{{"movies": [{{"id": <EXACT_ID_FROM_TOOL>, "title": "<title>", "rating": <rating>, "poster_url": "<url>", "trailer_url": "<url>", "overview": "<200 chars>"}}]}}"""
        
        options = ClaudeAgentOptions(
            model="claude-haiku-4-5-20251001",
            mcp_servers={"films_memory": tools_server},
            allowed_tools=[
                "mcp__films_memory__search_movies",
                "mcp__films_memory__get_movie_details",
                "mcp__films_memory__search_person",
                "mcp__films_memory__get_person_movies",
                "mcp__films_memory__discover_movies_by_genre",
            ],
            system_prompt=system_prompt
        )
        
        async def run_agent():
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt=user_message)
                async for msg in client.receive_response():
                    if hasattr(msg, "result") and msg.result:
                        return msg.result
            return None
        
        try:
            result_text = await asyncio.wait_for(run_agent(), timeout=AGENT_TIMEOUT)
            logger.info(f"Agent: {time.time() - start_time:.2f}s")
            return result_text
        except asyncio.TimeoutError:
            logger.warning("Agent timed out")
            return None
        except Exception as e:
            logger.error(f"Agent error: {e}")
            return None
    
    except Exception as e:
        logger.error(f"Error in get_agent_response: {e}")
        return None
