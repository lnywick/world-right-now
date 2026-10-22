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


def published_age_hours(value: str, now: dt.datetime) -> float:
    try:
        timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0, (now - timestamp).total_seconds() / 3600)
    except (ValueError, TypeError, AttributeError):
        return float("inf")


def load_previous() -> dict:
    if not OUT.exists():
        return {}
    try:
        raw = json.loads(OUT.read_text(encoding="utf-8"))
        return raw.get("videos", {}) if isinstance(raw.get("videos"), dict) else {}
    except (OSError, ValueError, TypeError) as exc:
        print(f"WARNING: Existing videos.json cannot be parsed: {exc}")
        return {}


def search(query: str, now: dt.datetime) -> list[str]:
    since = now - dt.timedelta(days=MAX_AGE_DAYS)
    data = youtube_api("search", {
        "part": "snippet",
        "type": "video",
        "q": query,
        "order": "date",
        "maxResults": 40,
        "videoDuration": "short",  # Official meaning: shorter than four minutes.
        "videoEmbeddable": "true",
        "publishedAfter": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "safeSearch": "moderate",
    })
    ids = []
    for item in data.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        if isinstance(video_id, str) and ID_RE.fullmatch(video_id):
            ids.append(video_id)
    return list(dict.fromkeys(ids))


def details(video_ids: list[str]) -> list[dict]:
    results = []
    # Maximum 50 IDs per videos.list request.
    for i in range(0, len(video_ids), 50):
        response = youtube_api("videos", {
            "part": "snippet,contentDetails,status",
            "id": ",".join(video_ids[i:i + 50]),
            "maxResults": 50,
        })
        results.extend(response.get("items", []))
    return results


def rank(video: dict, topic: dict, now: dt.datetime) -> tuple[float, dict] | None:
    snippet = video.get("snippet", {})
    status = video.get("status", {})
    video_id = video.get("id", "")
    if not isinstance(video_id, str) or not ID_RE.fullmatch(video_id):
        return None
    if status.get("embeddable") is not True or status.get("privacyStatus") != "public":
        return None
    if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
        return None
    length = duration_seconds(video.get("contentDetails", {}).get("duration", ""))
    if not MIN_SECONDS <= length <= MAX_SECONDS:
        return None

    title = html.unescape(snippet.get("title", ""))
    description = html.unescape(snippet.get("description", ""))
    channel = html.unescape(snippet.get("channelTitle", ""))
    if BLOCK.search(title + " " + description[:300]):
        return None
    rx = re.compile(topic["relevance"], re.I)
    # Relevant title preferred; allow a strongly relevant description as fallback.
    title_hits = len(rx.findall(title))
    description_hits = len(rx.findall(description[:500]))
    if title_hits == 0 and description_hits < 2:
        return None

    age = published_age_hours(snippet.get("publishedAt", ""), now)
    if age > MAX_AGE_DAYS * 24:
        return None
    short_bonus = 9 if SHORT_HINT.search(title + " " + description[:150]) else 0
    news_bonus = 4 if NEWS_HINT.search(channel) else 0
    title_bonus = 5 if title_hits else 0
    freshness = 6 * max(0, 1 - age / (MAX_AGE_DAYS * 24))
    score = short_bonus + news_bonus + title_bonus + freshness
    return score, {
        "id": video_id,
        "title": title,
        "channel": channel,
        "publishedAt": snippet.get("publishedAt", ""),
        "durationSeconds": length,
        "shortsHint": bool(short_bonus),
    }


def choose_for_topic(topic: dict, now: dt.datetime, used: set[str]) -> tuple[dict | None, int]:
    candidates: dict[str, tuple[float, dict]] = {}
    calls = 0
    for query in topic["queries"]:
        video_ids = search(query, now)
        calls += 1
        for item in details(video_ids):
            ranked = rank(item, topic, now)
            if ranked is not None:
                score, video = ranked
                if video["id"] not in used:
                    old = candidates.get(video["id"])
                    if old is None or score > old[0]:
                        candidates[video["id"]] = (score, video)
        # Stop as soon as a decent pool is found: saves daily API calls.
        if len(candidates) >= 4:
            break
    if not candidates:
        return None, calls
    return max(candidates.values(), key=lambda pair: pair[0])[1], calls


def main() -> None:
    if not API_KEY:
        raise RuntimeError("Missing YOUTUBE_API_KEY GitHub repository secret")

    now = dt.datetime.now(dt.timezone.utc)
    previous = load_previous()
    selected: dict[str, dict | None] = {}
    used: set[str] = set()
    search_calls = 0

    for key, topic in TOPICS.items():
        chosen, calls = choose_for_topic(topic, now, used)
        search_calls += calls
        if chosen:
            selected[key] = chosen
            used.add(chosen["id"])
            print(f"{key}: NEW {chosen['id']} | {chosen['channel']} | {chosen['durationSeconds']}s")
        else:
            old = previous.get(key)
            if isinstance(old, dict) and ID_RE.fullmatch(str(old.get("id", ""))) and old["id"] not in used:
                selected[key] = old
                used.add(old["id"])
                print(f"{key}: previous clip kept ({old['id']}); no matching new clip")
            else:
                selected[key] = None
                print(f"{key}: NO CLIP (none found, no previous clip to preserve)")

    count = sum(isinstance(x, dict) for x in selected.values())
    print(f"RESULT: {count}/6 categories filled; {search_calls} search calls this run")
    if count == 0:
        raise RuntimeError("No videos found. Leaving existing videos.json unchanged.")

    output = {"updatedAt": now.isoformat(), "videos": selected}
    temp = OUT.with_name("videos.json.tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(OUT)
    print("Saved videos.json successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail GitHub Actions visibly rather than silently publishing stale data.
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
