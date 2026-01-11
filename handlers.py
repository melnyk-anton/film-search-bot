"""Telegram bot handlers for message, button callbacks, and commands."""

import asyncio
import json
import re
from datetime import datetime
from typing import Optional

import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import TMDB_API_KEY, TMDB_BASE_URL, GENRE_MAP, AGENT_TIMEOUT, FAST_SEARCH_TIMEOUT, logger
from agent_tools import get_agent_response
from movie_search import fast_movie_search, discover_trending_movies, extract_actor_name, is_actor_query
from memory_client import get_memory_client, save_memory
from tmdb_client import fetch_poster_and_verify_movie, fetch_movie_videos, search_person, get_person_movie_credits
from utils import parse_json_response, validate_and_build_poster_url
from cache import get_cache, get_cache_lock


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    welcome_message = (
        "🎬 Welcome to the Film Recommendation Bot!\n\n"
        "I can help you discover amazing films based on your preferences.\n\n"
        "Just tell me what kind of films you like, or ask for recommendations!\n\n"
        "I'll remember your preferences to give you better suggestions over time."
    )
    await update.message.reply_text(welcome_message)


async def ask_for_rating(context: ContextTypes.DEFAULT_TYPE):
    """Ask user for film rating after watching."""
    job = context.job
    chat_id = job.chat_id
    movie_id = job.movie_id
    movie_title = job.movie_title
    
    try:
        watching_key = f"watching_movie_{movie_id}"
        if watching_key in context.user_data:
            keyboard = []
            row = []
            for i in range(1, 11):
                row.append(InlineKeyboardButton(f"{i}/10", callback_data=f"rate_{movie_id}_{i}"))
                if len(row) == 5:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎬 How was *{movie_title}*? Please rate it from 1 to 10:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error asking for rating: {e}")


async def handle_rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle rating callback."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(update.effective_user.id)
    data = query.data
    
    parts = data.split('_')
    if len(parts) != 3 or parts[0] != "rate":
        return
    
    movie_id = parts[1]
    rating = int(parts[2])
    
    try:
        memory_client = get_memory_client()
        watching_key = f"watching_movie_{movie_id}"
        movie_info = context.user_data.get(watching_key, {})
        movie_title = movie_info.get("title", "this film")
        
        if watching_key in context.user_data:
            del context.user_data[watching_key]
        
        # Get movie genres for preference analysis
        try:
            url = f"{TMDB_BASE_URL}/movie/{movie_id}"
            params = {"api_key": TMDB_API_KEY}
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    movie_details = response.json()
                    genres = [g["name"] for g in movie_details.get("genres", [])]
                    genres_str = ", ".join(genres) if genres else ""
                    
                    if rating >= 8:
                        memory_text = f"User rated {movie_title} {rating}/10 (Excellent!). User likes films with genres: {genres_str}."
                    elif rating >= 6:
                        memory_text = f"User rated {movie_title} {rating}/10 (Good). User enjoyed films with genres: {genres_str}."
                    else:
                        memory_text = f"User rated {movie_title} {rating}/10 (Didn't like it). Avoid films with genres: {genres_str}."
                else:
                    memory_text = f"User rated {movie_title} {rating}/10."
        except Exception:
            memory_text = f"User rated {movie_title} {rating}/10."
        
        await save_memory(user_id, memory_text, timeout=2.0)
        
        if rating >= 8:
            response_text = f"Excellent! Thanks for the {rating}/10 rating for *{movie_title}*. I'll remember you liked it! 🎬"
        elif rating >= 6:
            response_text = f"Thanks for the {rating}/10 rating for *{movie_title}*! I'll keep your preferences in mind. 🎬"
        else:
            response_text = f"Thanks for the {rating}/10 rating for *{movie_title}*. I'll avoid suggesting similar films. 🎬"
        
        await query.edit_message_text(response_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error handling rating callback: {e}")
        await query.edit_message_text("Sorry, I encountered an error saving your rating.")


async def prefetch_next_movie(context: ContextTypes.DEFAULT_TYPE, user_id: str):
    """Pre-fetch the next movie in the background."""
    try:
        original_prompt = context.user_data.get("original_prompt", "")
        suggested_movies = context.user_data.get("suggested_movies", [])
        
        if not original_prompt:
            return
        
        logger.debug("Pre-fetching next movie in background")
        
        if suggested_movies:
            suggested_list = ", ".join([str(id) for id in suggested_movies[:5]])
            prompt = f"Based on my original request: '{original_prompt}', suggest another film. Excluded IDs: {suggested_list}."
        else:
            prompt = f"Based on my original request: '{original_prompt}', suggest another film."
        
        response = await get_agent_response(user_id, prompt)
        if response:
            movie_data = parse_json_response(response)
            if movie_data and isinstance(movie_data, dict) and "movies" in movie_data:
                movies = movie_data.get("movies", [])
                if movies and isinstance(movies, list) and len(movies) > 0:
                    for movie in movies:
                        if isinstance(movie, dict) and movie.get("id") and movie.get("title"):
                            movie_id = int(movie.get("id"))
                            movie_title = movie.get("title", "Unknown")
                            
                            poster_url, movie_details = await fetch_poster_and_verify_movie(movie_id, movie_title)
                            
                            if movie_details:
                                actual_title = movie_details.get("title", movie_title)
                                movie_with_poster = movie.copy()
                                movie_with_poster["title"] = actual_title
                                movie_with_poster["poster_url"] = poster_url
                                
                                context.user_data["prefetched_next_movie"] = movie_with_poster
                                context.user_data["prefetched_query"] = original_prompt
                                logger.debug(f"Pre-fetched next movie: {actual_title}")
                                return
    except Exception as e:
        logger.debug(f"Pre-fetch failed: {e}")


async def send_movie_suggestion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, movie: dict):
    """Send a formatted movie suggestion with image and buttons."""
    try:
        movie_id = movie.get('id')
        movie_title = movie.get('title', 'Unknown')
        
        message_text = f"🎬 *{movie_title}*\n\n"
        message_text += f"⭐ Rating: {movie.get('rating', 'N/A')}/10\n"
        
        if movie.get('trailer_url'):
            message_text += f"🎥 [Watch Trailer]({movie.get('trailer_url')})\n\n"
        
        overview = movie.get('overview', 'No description available.')
        if len(overview) > 200:
            overview = overview[:200] + "..."
        message_text += f"_{overview}_"
        
        keyboard = [
            [InlineKeyboardButton("✅ I'll watch this", callback_data=f"watch_{movie_id}")],
            [
                InlineKeyboardButton("👎 I don't like it", callback_data=f"dislike_{movie_id}"),
                InlineKeyboardButton("✅ Already watched", callback_data=f"watched_{movie_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Always fetch poster fresh to ensure correctness
        poster_url = ""
        if movie_id:
            try:
                movie_id = int(movie_id)
                poster_url, verified_details = await fetch_poster_and_verify_movie(movie_id, movie_title)
                
                if verified_details:
                    verified_title = verified_details.get("title", "")
                    if verified_title and movie_title.lower() != verified_title.lower():
                        movie_title = verified_title
                        message_text = f"🎬 *{movie_title}*\n\n"
                        message_text += f"⭐ Rating: {movie.get('rating', 'N/A')}/10\n"
                        if movie.get('trailer_url'):
                            message_text += f"🎥 [Watch Trailer]({movie.get('trailer_url')})\n\n"
                        message_text += f"_{overview}_"
            except (ValueError, TypeError):
                logger.warning(f"Invalid movie_id: {movie_id}")
        
        if not poster_url:
            poster_url = movie.get('poster_url', '')
            if poster_url and not validate_and_build_poster_url(poster_url.split('/')[-1] if '/' in poster_url else ''):
                poster_url = ""
        
        if poster_url:
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=poster_url,
                    caption=message_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                logger.info(f"Sent movie poster: {movie_title} (ID: {movie_id})")
                asyncio.create_task(prefetch_next_movie(context, str(chat_id)))
                return
            except Exception as e:
                logger.warning(f"Error sending photo: {e}, sending text only")
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        asyncio.create_task(prefetch_next_movie(context, str(chat_id)))
    except Exception as e:
        logger.error(f"Error sending movie suggestion: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Sorry, I couldn't send the movie suggestion. Please try again."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages with intelligent query analysis."""
    if update.message.chat.type in ['group', 'supergroup']:
        if update.message.text and not update.message.text.startswith('/'):
            bot_username = context.bot.username
            if bot_username and f"@{bot_username}" not in update.message.text:
                return
    
    user_id = str(update.message.chat_id)
    user_message = update.message.text
    
    # Clear context if new query
    old_prompt = context.user_data.get("original_prompt", "")
    is_new_query = (old_prompt.lower().strip() != user_message.lower().strip())
    
    if is_new_query:
        context.user_data["movie_queue"] = []
        context.user_data["prefetched_next_movie"] = None
        context.user_data["prefetched_query"] = None
        context.user_data["suggested_movies"] = []
    
    await context.bot.send_chat_action(chat_id=update.message.chat_id, action="typing")
    
    try:
        context.user_data["original_prompt"] = user_message
        if "suggested_movies" not in context.user_data:
            context.user_data["suggested_movies"] = []
        
        # For actor queries, use fast_movie_search directly (more reliable than agent)
        # This ensures we get correct movie IDs from person credits API
        if is_actor_query(user_message):
            logger.info("Actor query detected - using fast_movie_search for reliable results")
            fast_result = await fast_movie_search(user_message, user_id, excluded_ids=[])
            if fast_result and fast_result.get("movies"):
                context.user_data["movie_queue"] = fast_result["movies"]
                if "suggested_movies" not in context.user_data:
                    context.user_data["suggested_movies"] = []
                context.user_data["suggested_movies"].append(fast_result["movies"][0]["id"])
                await send_movie_suggestion(context, update.effective_chat.id, fast_result["movies"][0])
                return
        
        # For non-actor queries, try agent first (intelligent analysis)
        try:
            response = await asyncio.wait_for(
                get_agent_response(user_id, user_message),
                timeout=AGENT_TIMEOUT
            )
        except asyncio.TimeoutError:
            response = None
        
        # Fallback to fast search if agent fails
        if not response:
            fast_result = await fast_movie_search(user_message, user_id, excluded_ids=[])
            if fast_result and fast_result.get("movies"):
                context.user_data["movie_queue"] = fast_result["movies"]
                if "suggested_movies" not in context.user_data:
                    context.user_data["suggested_movies"] = []
                context.user_data["suggested_movies"].append(fast_result["movies"][0]["id"])
                await send_movie_suggestion(context, update.effective_chat.id, fast_result["movies"][0])
                return
            
            # Try discover API as final fallback
            is_christmas = any(kw in user_message.lower() for kw in ["christmas", "xmas", "holiday"])
            if not is_christmas:
                user_lower = user_message.lower()
                found_genres = [gid for kw, gid in GENRE_MAP.items() if kw in user_lower]
                discover_results = await discover_trending_movies(
                    genre_ids=found_genres[:2] if found_genres else None,
                    excluded_ids=[]
                )
                if discover_results:
                    first_discover = discover_results[0]
                    movie_id = first_discover.get("id")
                    if movie_id:
                        poster_url, movie_details = await fetch_poster_and_verify_movie(
                            movie_id, first_discover.get("title", "")
                        )
                        if poster_url and movie_details:
                            movie_data = {
                                "id": movie_id,
                                "title": movie_details.get("title", ""),
                                "rating": movie_details.get("vote_average", 0),
                                "poster_url": poster_url,
                                "trailer_url": "",
                                "overview": movie_details.get("overview", "")[:200] or "No description available."
                            }
                            context.user_data["movie_queue"] = [movie_data]
                            context.user_data["suggested_movies"] = [movie_id]
                            await send_movie_suggestion(context, update.effective_chat.id, movie_data)
                            return
            
            await update.message.reply_text(
                "I couldn't find any movies for that request. Please try a different search term. 🎬"
            )
            return
        
        # Parse agent response
        movie_data = parse_json_response(response)
        if movie_data and isinstance(movie_data, dict) and "movies" in movie_data:
            movies = movie_data.get("movies", [])
            if movies and isinstance(movies, list) and len(movies) > 0:
                valid_movies = []
                for movie in movies:
                    if isinstance(movie, dict) and movie.get("id") and movie.get("title"):
                        movie_id = int(movie.get("id"))
                        agent_title = movie.get("title", "Unknown")
                        
                        poster_url, movie_details = await fetch_poster_and_verify_movie(movie_id, agent_title)
                        if not movie_details or not poster_url:
                            logger.warning(f"Skipping movie {movie_id}: Could not fetch valid details or poster")
                            continue
                        
                        actual_title = movie_details.get("title", agent_title)
                        actual_rating = movie_details.get("vote_average", movie.get("rating", 0))
                        vote_count = movie_details.get("vote_count", 0)
                        
                        # Additional validation: Verify title matches (redundant check after fetch_poster_and_verify_movie)
                        if agent_title and actual_title:
                            agent_norm = agent_title.lower().strip()
                            actual_norm = actual_title.lower().strip()
                            agent_words = set(agent_norm.split()[:4])
                            actual_words = set(actual_norm.split()[:4])
                            common_words = {"the", "a", "an", "and", "of", "in", "on", "at", "to", "for"}
                            agent_words = {w for w in agent_words if w not in common_words}
                            actual_words = {w for w in actual_words if w not in common_words}
                            
                            if not agent_words.intersection(actual_words) and agent_norm != actual_norm and len(agent_words) > 0 and len(actual_words) > 0:
                                logger.error(f"Skipping movie {movie_id}: Title mismatch! Agent said '{agent_title}' but TMDb says '{actual_title}'")
                                continue
                        
                        # Quality filter
                        if actual_rating < 7.0 or vote_count < 500:
                            logger.debug(f"Skipping movie {actual_title}: Rating {actual_rating} < 7.0 or votes {vote_count} < 500")
                            continue
                        
                        valid_movies.append({
                            "id": movie_id,
                            "title": actual_title,
                            "rating": actual_rating,
                            "poster_url": poster_url,
                            "trailer_url": movie.get("trailer_url", ""),
                            "overview": (movie_details.get("overview", "") or "")[:200] or "No description available."
                        })
                
                if valid_movies:
                    context.user_data["movie_queue"] = valid_movies
                    if "suggested_movies" not in context.user_data:
                        context.user_data["suggested_movies"] = []
                    context.user_data["suggested_movies"].append(valid_movies[0]["id"])
                    await send_movie_suggestion(context, update.effective_chat.id, valid_movies[0])
                    return
        
        # Fallback to fast search if parsing failed
        fallback_result = await fast_movie_search(user_message, user_id)
        if fallback_result and fallback_result.get("movies"):
            context.user_data["movie_queue"] = fallback_result["movies"]
            if "suggested_movies" not in context.user_data:
                context.user_data["suggested_movies"] = []
            context.user_data["suggested_movies"].append(fallback_result["movies"][0]["id"])
            await send_movie_suggestion(context, update.effective_chat.id, fallback_result["movies"][0])
            return
        
        await update.message.reply_text(
            "Sorry, I couldn't find any movies. Please try a different request. 🎬"
        )
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        await update.message.reply_text("Sorry, I encountered an error. Please try again later.")


async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks for movie actions (watch, dislike, watched)."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(update.effective_user.id)
    data = query.data
    
    parts = data.split('_', 1)
    if len(parts) != 2:
        return
    
    action = parts[0]
    movie_id = parts[1]
    
    try:
        memory_client = get_memory_client()
        movie_queue = context.user_data.get("movie_queue", [])
        current_movie = next((m for m in movie_queue if str(m.get("id")) == movie_id), None)
        
        if not current_movie:
            await query.edit_message_text("Sorry, I couldn't find that movie. Please ask for a new suggestion.")
            return
        
        movie_title = current_movie.get("title", "Unknown")
        
        # Handle actions
        if action == "watch":
            try:
                # Get runtime for scheduling rating request
                runtime_minutes = 120
                try:
                    movie_id_int = int(movie_id)
                    cache = get_cache()
                    cache_lock = get_cache_lock()
                    async with cache_lock:
                        if movie_id_int in cache:
                            runtime_minutes = cache[movie_id_int].get("runtime", 120) or 120
                        else:
                            url = f"{TMDB_BASE_URL}/movie/{movie_id_int}"
                            params = {"api_key": TMDB_API_KEY}
                            async with httpx.AsyncClient(timeout=3.0) as client:
                                response = await client.get(url, params=params)
                                if response.status_code == 200:
                                    movie_details = response.json()
                                    runtime_minutes = movie_details.get("runtime", 120) or 120
                except (ValueError, TypeError, Exception):
                    pass
                
                delay_seconds = (15 + runtime_minutes) * 60
                context.user_data[f"watching_movie_{movie_id}"] = {
                    "title": movie_title,
                    "id": movie_id,
                    "timestamp": datetime.now().isoformat()
                }
                
                context.job_queue.run_once(
                    ask_for_rating,
                    delay_seconds,
                    chat_id=update.effective_chat.id,
                    user_id=user_id,
                    movie_id=movie_id,
                    movie_title=movie_title
                )
                
                await save_memory(user_id, f"User wants to watch film: {movie_title}", timeout=2.0)
                
                hours = runtime_minutes // 60
                minutes = runtime_minutes % 60
                runtime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                response_text = f"Great choice! Enjoy watching *{movie_title}* ({runtime_str}). I'll check in with you after the film! 🎬"
            except Exception as e:
                logger.error(f"Error scheduling rating request: {e}")
                await save_memory(user_id, f"User wants to watch film: {movie_title}", timeout=2.0)
                response_text = f"Great choice! I've saved that you want to watch *{movie_title}*. Enjoy! 🎬"
        
        elif action == "dislike":
            # Get genres for preference analysis
            genres_str = None
            try:
                url = f"{TMDB_BASE_URL}/movie/{movie_id}"
                params = {"api_key": TMDB_API_KEY}
                async with httpx.AsyncClient(timeout=3.0) as client:
                    response = await client.get(url, params=params)
                    if response.status_code == 200:
                        movie_details = response.json()
                        genres = [g["name"] for g in movie_details.get("genres", [])]
                        genres_str = ", ".join(genres) if genres else None
            except Exception:
                pass
            
            memory_text = f"User doesn't like film: {movie_title}"
            if genres_str:
                memory_text += f" (Genres: {genres_str}). Avoid suggesting similar films."
            else:
                memory_text += ". Avoid suggesting similar films."
            
            await save_memory(user_id, memory_text, timeout=2.0)
            response_text = f"Noted! I won't suggest *{movie_title}* or similar films again. Let me find something different... 🎬"
        
        elif action == "watched":
            await save_memory(user_id, f"User already watched film: {movie_title} (TMDb ID: {movie_id})", timeout=2.0)
            response_text = f"Got it! I've saved that you've already watched *{movie_title}*. Let me find another film... 🎬"
        else:
            response_text = "Unknown action. Please try again."
        
        try:
            await query.edit_message_text(response_text, parse_mode='Markdown')
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=response_text,
                parse_mode='Markdown'
            )
        
        # Remove current movie from queue
        if movie_queue:
            context.user_data["movie_queue"] = [m for m in movie_queue if str(m.get("id")) != movie_id]
        
        # Add to excluded list
        if "suggested_movies" not in context.user_data:
            context.user_data["suggested_movies"] = []
        try:
            movie_id_int = int(movie_id)
            if movie_id_int not in context.user_data["suggested_movies"]:
                context.user_data["suggested_movies"].append(movie_id_int)
        except (ValueError, TypeError):
            pass
        
        # Get next movie (only for dislike/watched)
        if action in ["dislike", "watched"]:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            
            # Check prefetched movie
            original_prompt = context.user_data.get("original_prompt", "")
            prefetched_movie = context.user_data.get("prefetched_next_movie")
            prefetched_query = context.user_data.get("prefetched_query", "")
            
            if prefetched_movie and prefetched_query.lower().strip() == original_prompt.lower().strip():
                prefetched_id = prefetched_movie.get("id")
                if prefetched_id and prefetched_id not in context.user_data.get("suggested_movies", []):
                    del context.user_data["prefetched_next_movie"]
                    del context.user_data["prefetched_query"]
                    context.user_data["suggested_movies"].append(prefetched_id)
                    context.user_data["movie_queue"] = [prefetched_movie]
                    await send_movie_suggestion(context, update.effective_chat.id, prefetched_movie)
                    return
            
            # Check queue
            movie_queue = context.user_data.get("movie_queue", [])
            suggested_movies_set = set(context.user_data.get("suggested_movies", []))
            filtered_queue = [m for m in movie_queue if m.get("id") not in suggested_movies_set]
            
            if filtered_queue:
                context.user_data["movie_queue"] = filtered_queue
                await asyncio.sleep(1)
                await send_movie_suggestion(context, update.effective_chat.id, filtered_queue[0])
                return
            
            # Fetch more movies
            excluded_ids = context.user_data.get("suggested_movies", [])
            
            try:
                fast_result = await asyncio.wait_for(
                    fast_movie_search(original_prompt, user_id, excluded_ids=excluded_ids),
                    timeout=FAST_SEARCH_TIMEOUT
                )
            except asyncio.TimeoutError:
                fast_result = None
            except Exception as e:
                logger.error(f"Fast search error: {e}")
                fast_result = None
            
            if fast_result and fast_result.get("movies"):
                context.user_data["movie_queue"] = fast_result["movies"]
                if "suggested_movies" not in context.user_data:
                    context.user_data["suggested_movies"] = []
                first_movie = fast_result["movies"][0]
                context.user_data["suggested_movies"].append(first_movie["id"])
                await send_movie_suggestion(context, update.effective_chat.id, first_movie)
                return
            
            # Try discover API fallback
            original_lower = original_prompt.lower()
            is_actor_query_btn = is_actor_query(original_prompt)
            
            if not is_actor_query_btn:
                found_genres = [gid for kw, gid in GENRE_MAP.items() if kw in original_lower]
                discover_results = await discover_trending_movies(
                    genre_ids=found_genres[:2] if found_genres else None,
                    excluded_ids=excluded_ids
                )
                if discover_results:
                    movie_id = discover_results[0].get("id")
                    if movie_id:
                        poster_url, movie_details = await fetch_poster_and_verify_movie(
                            movie_id, discover_results[0].get("title", "")
                        )
                        if poster_url and movie_details:
                            movie_data = {
                                "id": movie_id,
                                "title": movie_details.get("title", ""),
                                "rating": movie_details.get("vote_average", 0),
                                "poster_url": poster_url,
                                "trailer_url": "",
                                "overview": movie_details.get("overview", "")[:200]
                            }
                            context.user_data["movie_queue"] = [movie_data]
                            context.user_data["suggested_movies"].append(movie_id)
                            await send_movie_suggestion(context, update.effective_chat.id, movie_data)
                            return
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="I'm having trouble finding more movies. Please try asking again with a specific genre or theme. 🎬"
            )
    
    except Exception as e:
        logger.error(f"Error handling button callback: {e}", exc_info=True)
        await query.edit_message_text("Sorry, I encountered an error. Please try again.")
