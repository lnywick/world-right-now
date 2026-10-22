"""Select up to six fresh, embeddable YouTube clips for World Right Now.

Run with: YOUTUBE_API_KEY=<secret> python update_feed.py
GitHub Actions supplies the API key via repository secrets.

Important: YouTube's public Data API cannot guarantee 9:16 orientation,
authenticity, safety, or editorial significance. #Shorts is only a hint.
No footage is downloaded, cropped, re-hosted or modified here.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

API_BASE = "https://www.googleapis.com/youtube/v3/"
API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
OUT = Path(__file__).with_name("videos.json")
MAX_AGE_DAYS = 14
MIN_SECONDS = 12
MAX_SECONDS = 180

# Three searches at most for each category; 18 search requests/day maximum.
# The second and third query are only used when necessary.
TOPICS = {
    "war": {
        "queries": ["war civilians news shorts", "ukraine gaza sudan war footage shorts", "war conflict breaking news #shorts"],
        "relevance": r"\b(war|conflict|airstrike|bombing|missile|gaza|ukraine|sudan|ceasefire|soldiers|military|civilians?|attacks?)\b",
    },
    "animals": {
        "queries": ["animal cruelty investigation shorts", "factory farming animal welfare #shorts", "wildlife poaching rescue news shorts"],
        "relevance": r"\b(animals?|cruelty|slaughter|factory farm|farming|livestock|cattle|pigs?|chickens?|wildlife|poaching|rescue|welfare|abuse)\b",
    },
    "protests": {
        "queries": ["protest march demonstration news shorts", "police protesters breaking news #shorts", "mass protest strike news short"],
        "relevance": r"\b(protests?|protesters?|demonstrations?|demonstrators?|march|rally|strikes?|police|unrest)\b",
    },
    "climate": {
        "queries": ["flood wildfire disaster news shorts", "extreme weather climate news #shorts", "drought heatwave storm footage shorts"],
        "relevance": r"\b(flood(?:s|ing)?|wildfires?|fires?|heatwaves?|drought|hurricanes?|typhoons?|storms?|climate|disaster|extreme weather|cyclone|landslide)\b",
    },
    "migration": {
        "queries": ["refugees border migration news shorts", "migrants asylum humanitarian crisis shorts", "refugee displacement news #shorts"],
        "relevance": r"\b(refugees?|migration|migrants?|asylum|border|displaced|displacement|crossing|evacuation|deportation)\b",
    },
    "wealth": {
        "queries": ["billionaire wealth inequality news shorts", "billionaires money power #shorts", "wealth gap rich poor economic news shorts"],
        "relevance": r"\b(billionaires?|wealth|inequality|millionaires?|rich|poverty|corporate|ceo|fortunes?|income|wealth gap|oligarchs?)\b",
    },
}

# Helps avoid exploitative content and clearly off-topic gameplay/reactions.
BLOCK = re.compile(
    r"\b(gore|uncensored|beheading|execution|dead bodies|deepfake|"
    r"ai.generated|parody|gameplay|reaction video|compilation|"
    r"movie trailer|video game|graphic footage)\b", re.I
)
SHORT_HINT = re.compile(r"(?:#shorts\b|\bshorts\b|#reels\b)", re.I)
NEWS_HINT = re.compile(r"\b(news|reuters|associated press|ap news|bbc|dw|al jazeera|france 24|guardian|abc news|sky news)\b", re.I)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_api(endpoint: str, params: dict) -> dict:
    """Make an API request; keep the secret out of logs and exceptions."""
    url = API_BASE + endpoint + "?" + urlencode({**params, "key": API_KEY})
    try:
        with urlopen(url, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        # Do not output the URL (it contains the API key).
        try:
            error = json.loads(exc.read().decode("utf-8"))
            message = error.get("error", {}).get("message", "API request rejected")
        except (ValueError, UnicodeDecodeError, AttributeError):
            message = "API request rejected"
        raise RuntimeError(f"YouTube API HTTP {exc.code}: {message}") from None
    except URLError as exc:
        raise RuntimeError(f"YouTube API network failure: {exc.reason}") from None


def duration_seconds(duration: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(x or 0) for x in match.groups())
    return hours * 3600 + minutes * 60 + seconds

