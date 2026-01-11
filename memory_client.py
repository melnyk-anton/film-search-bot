"""Memory management using Mem0AI for user preferences and watched films."""

import asyncio
import re
from typing import Tuple
from mem0 import MemoryClient
from config import MEM0_API_KEY, MEMORY_TIMEOUT, logger


def get_memory_client() -> MemoryClient:
    """Get a Mem0 memory client instance."""
    return MemoryClient(api_key=MEM0_API_KEY)


async def fetch_user_memories(user_id: str) -> Tuple[set, list, set, set]:
    """
    Fetch user memories from Mem0AI.
    
    Returns:
        Tuple of (watched_ids, watched_titles, disliked_genres, preferred_genres)
    """
    watched_ids = set()
    watched_titles = []
    disliked_genres = set()
    preferred_genres = set()
    
    try:
        memory_client = get_memory_client()
        
        # Fetch watched films
        memories = await asyncio.wait_for(
            asyncio.to_thread(
                memory_client.search,
                query="watched already",
                filters={"user_id": str(user_id)}
            ),
            timeout=MEMORY_TIMEOUT
        )
        
        mem_list = []
        if memories:
            if isinstance(memories, dict):
                mem_list = memories.get("results", [])
            elif isinstance(memories, list):
                mem_list = memories
        
        for mem in mem_list[:15]:
            mem_text = str(mem.get("memory", mem.get("content", mem.get("text", ""))))
            # Extract movie IDs
            id_patterns = [
                r'TMDb\s*ID[:\s]+(\d+)',
                r'\(ID[:\s]+(\d+)\)',
                r'watched.*?(\d{5,})',
                r'id[:\s]+(\d+)'
            ]
            for pattern in id_patterns:
                for id_str in re.findall(pattern, mem_text, re.IGNORECASE):
                    watched_ids.add(int(id_str))
            
            # Extract movie titles
            for title in re.findall(r'watched\s+(?:film[:\s]+)?([^\(\)]+)', mem_text, re.IGNORECASE)[:3]:
                cleaned = title.strip().split('(')[0].strip()
                if len(cleaned) > 3:
                    watched_titles.append(cleaned)
        
        # Fetch preferences
        try:
            pref_memories = await asyncio.wait_for(
                asyncio.to_thread(
                    memory_client.search,
                    query="doesn't like avoid dislike rating",
                    filters={"user_id": str(user_id)}
                ),
                timeout=MEMORY_TIMEOUT
            )
            pref_list = pref_memories.get("results", []) if isinstance(pref_memories, dict) else (
                pref_memories if isinstance(pref_memories, list) else []
            )
            
            for mem in pref_list[:10]:
                mem_text = str(mem.get("memory", mem.get("content", mem.get("text", "")))).lower()
                if any(x in mem_text for x in ["doesn't like", "avoid", "dislike"]):
                    for match in re.findall(r'genres?[:\s]+([^\.]+)', mem_text):
                        disliked_genres.update([g.strip() for g in match.split(",")])
                if "rated" in mem_text and any(x in mem_text for x in ["8", "9", "10", "excellent", "likes"]):
                    for match in re.findall(r'genres?[:\s]+([^\.]+)', mem_text):
                        preferred_genres.update([g.strip() for g in match.split(",")])
        except Exception as e:
            logger.debug(f"Error fetching preferences: {e}")
    except Exception as e:
        logger.debug(f"Error fetching memories: {e}")
    
    return watched_ids, watched_titles, disliked_genres, preferred_genres


async def save_memory(user_id: str, memory_text: str, timeout: float = 2.0) -> bool:
    """
    Save a memory to Mem0AI.
    
    Args:
        user_id: User ID
        memory_text: Memory text to save
        timeout: Timeout in seconds
        
    Returns:
        True if successful, False otherwise
    """
    try:
        memory_client = get_memory_client()
        message = [{"role": "user", "content": memory_text}]
        await asyncio.wait_for(
            asyncio.to_thread(
                memory_client.add,
                message,
                user_id=user_id
            ),
            timeout=timeout
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to save memory: {e}")
        return False
