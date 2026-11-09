"""World Right Now: daily YouTube video selection for GitHub Pages / Readymag.

Replace only update_feed.py in the existing repository. Requires the existing
YOUTUBE_API_KEY GitHub Actions secret; no third-party Python packages required.

Uses one YouTube search per topic per run (about 600 search quota units), keeps
backups, and falls back to saved selections if YouTube is unavailable.
YouTube Data API cannot verify aspect ratio; Shorts are preferred, not guaranteed.
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

API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
API_ROOT = "https://www.googleapis.com/youtube/v3/"
OUTPUT = Path(__file__).with_name("videos.json")
COUNTRY = "CH"
MAX_AGE_DAYS = 45
MAX_SECONDS = 180
BACKUPS = 5
HISTORY_SIZE = 25
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SHORT_RE = re.compile(r"(?:#shorts?\b|\bshorts?\b|#reels\b)", re.I)
AVOID_RE = re.compile(
    r"\b(gameplay|video game|movie trailer|prank|comedy sketch|satire|"
    r"ai.generated|deepfake|graphic death|execution video|reaction video)\b", re.I
)
SOURCE_RE = re.compile(r"\b(reuters|associated press|bbc|dw|ap news|al jazeera|france 24|sky news|unicef)\b", re.I)

# One query per topic per run; queries rotate daily to vary sources.
TOPICS = {
    "war": {
        "queries": ["war civilians conflict news shorts", "gaza ukraine sudan war news shorts", "war displacement airstrike footage news short"],
        "match": r"\b(war|conflict|airstrike|bombing|missile|gaza|ukraine|sudan|military|ceasefire|refugee|civilians?)\b",
    },
    "animals": {
        "queries": ["animal cruelty rescue shorts news", "factory farming animal welfare shorts", "wildlife poaching animal abuse news shorts"],
        "match": r"\b(animals?|cruelty|abuse|welfare|slaughter|factory farming|poaching|wildlife|rescue|livestock|pigs?|cows?|chickens?)\b",
    },
    "protests": {
        "queries": ["protest demonstration street marches news shorts", "protesters rally police news short", "activists strike demonstration news shorts"],
        "match": r"\b(protests?|protesters?|demonstrations?|march|rally|strikes?|activists?|unrest|riots?)\b",
    },
    "climate": {
        "queries": ["climate floods extreme weather news shorts", "wildfires heatwave climate crisis shorts", "storm hurricane flooding news shorts"],
        "match": r"\b(climate|floods?|flooding|wildfires?|heatwave|storms?|hurricanes?|drought|cyclone|typhoon|extreme weather)\b",
    },
    "migration": {
        "queries": ["refugees migration border news shorts", "migrants asylum humanitarian news short", "refugee camps displacement news shorts"],
        "match": r"\b(refugees?|migration|migrants?|asylum|border|displacement|displaced|deportation|evacuation)\b",
    },
    "wealth": {
        "queries": ["billionaires wealth inequality news shorts", "rich poor wealth gap economy news short", "corporate profits poverty billionaire news shorts"],
        "match": r"\b(billionaires?|wealth|inequality|rich|poverty|corporate profits|tax avoidance|oligarchs?|fortune|economic gap)\b",
    },
}


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def old_state() -> dict:
    try:
        result = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def youtube_api(resource: str, params: dict) -> dict:
    url = API_ROOT + resource + "?" + urlencode({**params, "key": API_KEY})
    try:
        with urlopen(url, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
            reason = payload.get("error", {}).get("message", "unknown API error")
        except (ValueError, UnicodeDecodeError, AttributeError):
            reason = "unknown API error"
        raise RuntimeError(f"YouTube API HTTP {error.code}: {reason}") from None
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"YouTube connection failed: {error}") from None


def duration_seconds(value: object) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(value or ""))
    if not match:
        return 0
    h, m, s = (int(part or 0) for part in match.groups())
    return h * 3600 + m * 60 + s


def age_days(value: str, now: dt.datetime) -> float:
    try:
        return max(0, (now - dt.datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds() / 86400)
    except (ValueError, TypeError, AttributeError):
        return 9999


def search_topic(key: str, now: dt.datetime) -> list[dict]:
    topic = TOPICS[key]
    day = now.date().toordinal()
    query = topic["queries"][day % len(topic["queries"])]
    after = now - dt.timedelta(days=MAX_AGE_DAYS)
    response = youtube_api("search", {
        "part": "snippet", "type": "video", "q": query,
        "maxResults": 50, "order": "date", "videoDuration": "short",
        "videoEmbeddable": "true", "regionCode": COUNTRY,
        "safeSearch": "moderate",
        "publishedAfter": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    ids = list(dict.fromkeys(
        item.get("id", {}).get("videoId") for item in response.get("items", [])
        if valid_id(item.get("id", {}).get("videoId"))
    ))
    if not ids:
        return []
    results = youtube_api("videos", {
        "part": "snippet,status,contentDetails", "id": ",".join(ids[:50]),
    })
    return results.get("items", [])


def scored_video(video: dict, key: str, now: dt.datetime, history: set[str]) -> tuple[float, dict] | None:
    video_id = video.get("id")
    if not valid_id(video_id):
        return None
    status = video.get("status") or {}
    details = video.get("contentDetails") or {}
    snippet = video.get("snippet") or {}
    if status.get("privacyStatus") != "public" or status.get("embeddable") is not True:
        return None
    if status.get("madeForKids") is True or snippet.get("liveBroadcastContent") in ("live", "upcoming"):
        return None
    restriction = details.get("regionRestriction") or {}
    if COUNTRY in restriction.get("blocked", []):
        return None
    if "allowed" in restriction and COUNTRY not in restriction.get("allowed", []):
        return None
    seconds = duration_seconds(details.get("duration"))
    if not 8 <= seconds <= MAX_SECONDS:
        return None
    title = html.unescape(snippet.get("title", ""))
    description = html.unescape(snippet.get("description", ""))[:500]
    channel = html.unescape(snippet.get("channelTitle", ""))
    all_text = title + " " + description
    if AVOID_RE.search(all_text):
        return None
    matches = re.compile(TOPICS[key]["match"], re.I)
    in_title = len(matches.findall(title))
    in_description = len(matches.findall(description))
    if in_title == 0 and in_description < 2:
        return None
    if key == "animals" and not re.search(r"\b(cruelty|abuse|welfare|rescue|suffering|poaching|slaughter|factory farming|neglect|testing|injured)\b", all_text, re.I):
        return None
    if key == "wealth" and re.search(r"\b(billionaire mindset|how to get rich|luxury lifestyle|supercar|motivation)\b", title, re.I):
        return None
    age = age_days(snippet.get("publishedAt", ""), now)
    if age > MAX_AGE_DAYS:
        return None
    shorts = bool(SHORT_RE.search(all_text))
    score = (
        130 - age * 1.5 + 8 * min(in_title, 3) + 2 * min(in_description, 3)
        + (30 if shorts else 0) + (9 if SOURCE_RE.search(channel) else 0)
        + (6 if seconds <= 70 else 0) + (100 if video_id not in history else 0)
    )
    return score, {
        "id": video_id, "title": title, "channel": channel,
        "publishedAt": snippet.get("publishedAt", ""),
        "durationSeconds": seconds, "shortsHint": shorts,
        "verticalVerified": False,
    }


def saved_candidates(key: str, state: dict, blocked: set[str]) -> list[dict]:
    """Keep the page populated when YouTube's API is temporarily unavailable."""
    result = []
    existing = state.get("videos") or {}
    backups = state.get("alternatives") or {}
    history = state.get("recentlyShown") or {}
    entries = [existing.get(key)] + list(backups.get(key) or []) + list(history.get(key) or [])
    seen = set()
    for entry in entries:
        v = entry if isinstance(entry, dict) else {"id": entry}
        video_id = v.get("id")
        if not valid_id(video_id) or video_id in blocked or video_id in seen:
            continue
        seen.add(video_id)
        result.append({
            "id": video_id,
            "title": v.get("title") or "Previously selected video",
            "channel": v.get("channel") or "",
            "publishedAt": v.get("publishedAt") or "",
            "durationSeconds": v.get("durationSeconds") or 0,
            "shortsHint": bool(v.get("shortsHint", False)),
            "verticalVerified": bool(v.get("verticalVerified", False)),
        })
    return result


def run() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    state = old_state()
    old_videos = state.get("videos") if isinstance(state.get("videos"), dict) else {}
    old_history = state.get("recentlyShown") if isinstance(state.get("recentlyShown"), dict) else {}
    old_changed = state.get("lastChangedAt") if isinstance(state.get("lastChangedAt"), dict) else {}
    old_blocked = state.get("blockedIds") if isinstance(state.get("blockedIds"), dict) else {}
    blocked = {v for group in old_blocked.values() if isinstance(group, list) for v in group if valid_id(v)}
    blocked.add("ZIs_ho8kFtI")  # Previously identified repetitive animal clip.
    selected, alternatives, histories, changed = {}, {}, {}, {}
    used = set()
    api_available = bool(API_KEY)
    api_errors = []
    new_count = 0

    for key in TOPICS:
        history = [v for v in old_history.get(key, []) if valid_id(v)]
        old = old_videos.get(key) if isinstance(old_videos.get(key), dict) else {}
        previous_id = old.get("id")
        ranked = []
        if api_available:
            try:
                for video in search_topic(key, now):
                    result = scored_video(video, key, now, set(history))
                    if result and result[1]["id"] not in used and result[1]["id"] not in blocked:
                        ranked.append(result)
            except RuntimeError as error:
                api_errors.append(str(error))
                api_available = False
                print(f"WARNING: {error}. Using saved videos for remaining topics.")
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        candidates = [item for _, item in ranked]
        saved = saved_candidates(key, state, blocked)
        # Add saved items only as backups, or as primary when searches found nothing.
        candidates.extend(saved)
        candidates = [v for v in candidates if v["id"] not in used and v["id"] not in blocked]
        unique, seen = [], set()
        for item in candidates:
            if item["id"] not in seen:
                unique.append(item)
                seen.add(item["id"])
        primary = unique[0] if unique else None
        selected[key] = primary
        if primary:
            used.add(primary["id"])
            histories[key] = ([primary["id"]] + [x for x in history if x != primary["id"]])[:HISTORY_SIZE]
            if primary["id"] != previous_id:
                new_count += 1
                changed[key] = now.isoformat()
            else:
                changed[key] = old_changed.get(key) or now.isoformat()
            print(f"{key}: {primary['id']} | {'new selection' if primary['id'] != previous_id else 'previous selection'}")
        else:
            histories[key] = history[:HISTORY_SIZE]
            changed[key] = old_changed.get(key)
            print(f"{key}: NO VIDEO IN SEARCH OR SAVED HISTORY")
        alternatives[key] = [v for v in unique[1:] if not primary or v["id"] != primary["id"]][:BACKUPS]

    filled = sum(bool(v) for v in selected.values())
    if not filled and isinstance(state.get("videos"), dict) and any(state["videos"].values()):
        print("WARNING: refusing to overwrite the existing populated feed with empty results")
        return
    output = {
        "updatedAt": now.isoformat(), "videos": selected,
        "alternatives": alternatives, "recentlyShown": histories,
        "lastChangedAt": changed, "blockedIds": old_blocked,
        "formatPolicy": "shorts-preferred-unverified",
        "feedStatus": "saved-fallback" if api_errors or not API_KEY else "updated",
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(f"SUCCESS: {filled}/6 topics populated, {new_count} changed selections")
    if api_errors or not API_KEY:
        print("WARNING: Fresh videos unavailable. The feed will retry during the next scheduled run.")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
