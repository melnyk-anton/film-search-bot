"""Configuration and constants for the Film Recommendation Bot."""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# TMDb API configuration
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
if not TMDB_API_KEY:
    raise ValueError("TMDB_API_KEY not set in environment variables")

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# Mem0 API configuration
MEM0_API_KEY = os.getenv("MEM0_API_KEY")
if not MEM0_API_KEY:
    raise ValueError("MEM0_API_KEY not set in environment variables")

# Telegram Bot configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set in environment variables")

# Anthropic API configuration (for Claude Agent)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not set in environment variables")

# Genre mapping for TMDb
GENRE_MAP = {
    "detective": 80,
    "crime": 80,
    "mystery": 9648,
    "thriller": 53,
    "horror": 27,
    "comedy": 35,
    "action": 28,
    "romance": 10749,
    "drama": 18,
    "sci-fi": 878,
    "science fiction": 878,
    "fantasy": 14,
    "christmas": 10751,
    "xmas": 10751,
    "holiday": 10751,
    "noir": 80,
    "western": 37,
    "adventure": 12,
    "war": 10752,
    "animation": 16,
    "documentary": 99,
}

# Movie quality filters
MIN_RATING = 7.0
MIN_VOTE_COUNT = 500
MIN_RATING_FALLBACK = 6.5
MIN_VOTE_COUNT_FALLBACK = 300

# Timeouts (in seconds)
TMDB_TIMEOUT = 3.0
AGENT_TIMEOUT = 30.0
FAST_SEARCH_TIMEOUT = 10.0
MEMORY_TIMEOUT = 0.5

# Cache configuration
MAX_CACHE_SIZE = 100
