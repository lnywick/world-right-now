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

# Three searches for each category; 18 searches/day = 1,800 search quota units.
# Searching each query gives categories with fewer matches more options.
TOPICS = {
    "war": {
        "queries": ["war civilians news shorts", "ukraine gaza sudan war footage shorts", "war conflict breaking news #shorts"],
        "relevance": r"\b(war|conflict|airstrike|bombing|missile|gaza|ukraine|sudan|ceasefire|soldiers|military|civilians?|attacks?)\b",
    },
    "animals": {
        "queries": ["animal cruelty exposed news shorts", "factory farming animal welfare investigation shorts", "animal abuse wildlife poaching news #shorts"],
        "relevance": r"\b(animals?|cruelty|slaughter|factory farm|farming|livestock|cattle|pigs?|chickens?|wildlife|poaching|welfare|abuse|neglect|suffering|animal testing)\b",
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
ANIMAL_HARM = re.compile(
    r"\b(cruelty|abuse|neglect|suffering|slaughter|factory farm|animal testing|"
    r"poach(?:ing|ers?)?|injured|trafficking|fur farm|animal welfare|"
    r"livestock|meat industry|dairy farm|chicken farm|torture)\b", re.I
)
ANIMAL_SUBJECT = re.compile(
    r"\b(animals?|cattle|cows?|pigs?|chickens?|sheep|wildlife|dogs?|cats?|"
    r"birds?|elephants?|horses?|dolphins?|whales?|livestock)\b", re.I
)


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


def load_state() -> dict:
    if not OUT.exists():
        return {"videos": {}, "recentlyShown": {}, "lastChangedAt": {}}
    try:
        raw = json.loads(OUT.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Top-level JSON must be an object")
        for key in ("videos", "recentlyShown", "lastChangedAt"):
            if not isinstance(raw.get(key), dict):
                raw[key] = {}
        return raw
    except (OSError, ValueError, TypeError) as exc:
        print(f"WARNING: Existing videos.json cannot be parsed: {exc}")
        return {"videos": {}, "recentlyShown": {}, "lastChangedAt": {}}


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
    # Avoid unrelated police/training stories or cute pet videos for animal suffering.
    # Metadata alone cannot verify what the footage actually shows.
    if topic is TOPICS["animals"]:
        combined = title + " " + description[:500]
        if not ANIMAL_SUBJECT.search(combined) or not ANIMAL_HARM.search(combined):
            return None
        if re.search(r"law enforcement|police training|crime investigations", title, re.I):
            return None

    age = published_age_hours(snippet.get("publishedAt", ""), now)
    if age > MAX_AGE_DAYS * 24:
        return None
    short_bonus = 2 if SHORT_HINT.search(title + " " + description[:150]) else 0
    news_bonus = 3 if NEWS_HINT.search(channel) else 0
    title_bonus = 4 if title_hits else 0
    harm_bonus = 5 if topic is TOPICS["animals"] and ANIMAL_HARM.search(title) else 0
    freshness = 20 * max(0, 1 - age / (MAX_AGE_DAYS * 24))
    score = short_bonus + news_bonus + title_bonus + harm_bonus + freshness
    return score, {
        "id": video_id,
        "title": title,
        "channel": channel,
        "publishedAt": snippet.get("publishedAt", ""),
        "durationSeconds": length,
        "shortsHint": bool(short_bonus),
    }


def choose_for_topic(
    topic: dict, now: dt.datetime, used: set[str],
    previous_id: str = "", recently_shown: list[str] | None = None,
) -> tuple[dict | None, int]:
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

    if not candidates:
        return None, calls

    pool = list(candidates.values())
    history = set(recently_shown or [])
    # First prefer unseen videos, then any video different from yesterday's.
    unseen = [x for x in pool if x[1]["id"] != previous_id and x[1]["id"] not in history]
    different = [x for x in pool if x[1]["id"] != previous_id]
    pool = unseen or different or pool
    # Avoid resurfacing an old 14-day clip when a recent alternative exists.
    recent = [x for x in pool if published_age_hours(x[1]["publishedAt"], now) <= 7 * 24]
    if recent:
        pool = recent
    return max(pool, key=lambda x: x[0])[1], calls


def main() -> None:
    if not API_KEY:
        raise RuntimeError("Missing YOUTUBE_API_KEY GitHub repository secret")

    now = dt.datetime.now(dt.timezone.utc)
    state = load_state()
    previous = state["videos"]
    recent_history = state["recentlyShown"]
    last_changed = state["lastChangedAt"]
    selected: dict[str, dict | None] = {}
    new_history: dict[str, list[str]] = {}
    new_changed: dict[str, str | None] = {}
    used: set[str] = set()
    search_calls = 0
    changed_count = 0

    for key, topic in TOPICS.items():
        old = previous.get(key)
        old_id = str(old.get("id", "")) if isinstance(old, dict) else ""
        history = recent_history.get(key, [])
        if not isinstance(history, list):
            history = []
        history = [x for x in history if isinstance(x, str) and ID_RE.fullmatch(x)]
        chosen, calls = choose_for_topic(topic, now, used, old_id, history)
        search_calls += calls
        if chosen is None and isinstance(old, dict) and ID_RE.fullmatch(old_id) and old_id not in used:
            chosen = old
            print(f"{key}: NO ALTERNATIVE FOUND; keeping {old_id}")
        elif chosen is None:
            print(f"{key}: NO CLIP AVAILABLE")

        selected[key] = chosen
        if chosen is not None:
            chosen_id = chosen["id"]
            used.add(chosen_id)
            if chosen_id != old_id:
                changed_count += 1
                new_changed[key] = now.isoformat()
                print(f"{key}: CHANGED {old_id or '-'} -> {chosen_id} | {chosen['title'][:80]}")
            else:
                new_changed[key] = last_changed.get(key) or state.get("updatedAt") or now.isoformat()
                print(f"{key}: UNCHANGED ({chosen_id}); no suitable different video found")
            new_history[key] = ([chosen_id] + [x for x in history if x != chosen_id])[:6]
        else:
            new_history[key] = history[:6]
            new_changed[key] = last_changed.get(key)

    count = sum(isinstance(x, dict) for x in selected.values())
    print(f"RESULT: {count}/6 categories filled, {changed_count} changed; {search_calls} searches")
    if count == 0:
        raise RuntimeError("No videos found. Leaving existing videos.json unchanged.")

    output = {
        "updatedAt": now.isoformat(),
        "videos": selected,
        "recentlyShown": new_history,
        "lastChangedAt": new_changed,
    }
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

