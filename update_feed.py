"""Daily selector for six topical, embeddable YouTube clips.

Requires GitHub repository secret YOUTUBE_API_KEY. Uses only Python stdlib.

Limits: YouTube Data API does NOT expose reliable video orientation or the
actual contents of footage. Entries must be editorially reviewed if accuracy,
portrait-only footage or sensitivity guarantees are required.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

API_BASE = "https://www.googleapis.com/youtube/v3/"
API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
OUT = Path(__file__).with_name("videos.json")

# Three fairly broad searches for each category. The script also checks
# metadata and duration after searching. These queries are not assertions
# that videos are accurate, original, vertical, or important.
TOPICS = {
    "war": {
        "queries": ["war news shorts", "conflict civilians news short", "war footage news #shorts"],
        "relevance": r"\b(war|wars|conflict|attack|airstrike|bombing|combat|ceasefire|gaza|ukraine|sudan|civilian|military)\b",
    },
    "animals": {
        "queries": ["animal cruelty news shorts", "factory farming animals shorts", "animal welfare investigation shorts"],
        "relevance": r"\b(animal|animals|farm|farming|livestock|welfare|cruelty|slaughter|cattle|pigs|chickens|factory farm|wildlife)\b",
    },
    "protests": {
        "queries": ["protests demonstration news shorts", "protesters march news #shorts", "police protest footage shorts"],
        "relevance": r"\b(protest|protests|protester|demonstration|demonstrators|march|rally|strike|police)\b",
    },
    "climate": {
        "queries": ["flood wildfire climate news shorts", "extreme weather disaster shorts", "climate crisis flooding news shorts"],
        "relevance": r"\b(flood|flooding|floods|wildfire|fire|fires|heatwave|drought|hurricane|typhoon|storm|climate|extreme weather|environment)\b",
    },
    "migration": {
        "queries": ["refugees border news shorts", "migration crisis news short", "migrants journey humanitarian #shorts"],
        "relevance": r"\b(refugee|refugees|migration|migrant|migrants|asylum|border|displaced|crossing|evacuation)\b",
    },
    "wealth": {
        "queries": ["billionaire wealth inequality shorts", "billionaires money power news shorts", "wealth gap poverty rich #shorts"],
        "relevance": r"\b(billionaire|billionaires|wealth|rich|poverty|inequality|millionaire|money|corporate|ceo|fortune|income)\b",
    },
}

# Avoid obviously inappropriate categories for an unreviewed auto feed.
BLOCK = re.compile(
    r"\b(gore|graphic|uncensored|beheading|execution|dead bodies|deepfake|fake news|"
    r"ai generated|ai-generated|parody|gameplay|reaction video|compilation|"
    r"movie trailer|video game)\b",
    re.I,
)
SHORT_HINT = re.compile(r"(?:#shorts\b|\bshorts\b|#reels\b)", re.I)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def api(endpoint: str, params: dict) -> dict:
    q = urlencode({**params, "key": API_KEY})
    try:
        with urlopen(API_BASE + endpoint + "?" + q, timeout=30) as response:
            return json.load(response)
    except HTTPError as e:
        # Never print an URL containing the API key.
        detail = e.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"YouTube API HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"YouTube API connection error: {e.reason}") from e


def duration_seconds(value: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    h, m, s = (int(part or 0) for part in match.groups())
    return 3600 * h + 60 * m + s


def published_age_hours(iso_time: str, now: dt.datetime) -> float:
    try:
        parsed = dt.datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return max(0, (now - parsed).total_seconds() / 3600)
    except (ValueError, AttributeError):
        return 10**9

