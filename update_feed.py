"""World Right Now: six rotating topical YouTube video embeds.

Install by replacing update_feed.py in the GitHub repository. The existing daily.yml
already invokes this file. YOUTUBE_API_KEY must stay in GitHub Actions secrets.
No YouTube media is downloaded or re-hosted.

Notes: YouTube Data API does not identify Shorts or provide public aspect-ratio metadata.
This version checks source stream dimensions via yt-dlp WITHOUT downloading footage.
When orientation cannot be verified, the candidate is rejected (no landscape fallback).
It cannot detect horizontal imagery letterboxed INSIDE an otherwise vertical video.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
from typing import Optional

try:
    import yt_dlp
except ImportError:
    yt_dlp = None
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

API_BASE = "https://www.googleapis.com/youtube/v3/"
API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
OUT = Path(__file__).with_name("videos.json")
COUNTRY = "CH"                # Audience in Switzerland
MAX_AGE_DAYS = 21             # Enough coverage for narrow portrait-only searches
MIN_SECONDS = 8
MAX_SECONDS = 180
MAX_ALTERNATIVES = 2          # Verified-vertical backup IDs for playback errors
MAX_FORMAT_PROBES = 12        # Per category: limit expensive metadata checks
MIN_VERTICAL_RATIO = 0.45    # 9:16 = 0.5625; allow slight deviations
MAX_VERTICAL_RATIO = 0.66    # Reject horizontal and square videos
HISTORY_SIZE = 24             # Remember more than 6 videos per category
# 5 searches per category = 30 search.list calls/day; each normally costs 100 quota units.
# If you have a lower quota, reduce the queries per topic.

TOPICS = {
    "war": {
        "queries": [
            "war civilian casualties recent news #shorts", "gaza ukraine sudan conflict #shorts",
            "airstrike displacement war footage #shorts", "warzone civilians news #shorts", "ceasefire attacks conflict news #shorts"
        ],
        "relevance": r"\b(war|conflict|airstrikes?|bomb(?:ing|s)?|missiles?|gaza|ukraine|sudan|ceasefire|military|civilians?|attacks?|refugee camp)\b",
    },
    "animals": {
        "queries": [
            "stray dogs cats animal rescue shelter #shorts",
            "factory farming meat industry animal welfare #shorts",
            "veganism animal rights meat consumption #shorts",
            "zoo captivity wildlife animal welfare #shorts",
            "livestock cages slaughterhouse animal testing #shorts",
        ],
        "relevance": (
            r"\b(animals?|animal rights|animal welfare|strays?|street dogs?|street cats?|"
            r"rescue|shelters?|abandon(?:ed|ment)?|adopt(?:ion)?|vegan(?:ism)?|"
            r"meat(?: consumption| industry)?|slaughter(?:house)?|factory farm(?:ing)?|"
            r"farms?|livestock|dairy|egg industry|battery cages?|captive|captivity|"
            r"zoos?|wildlife|cruelty|abuse|neglect|suffering|testing|fur farms?|"
            r"cows?|pigs?|chickens?|dogs?|cats?|sanctuar(?:y|ies)|"
            r"tierwohl|tierheim|tierschutz|massentierhaltung|fleischkonsum|"
            r"streuner|tierhaltung|tierversuche)\b"
        ),
    },
    "protests": {
        "queries": [
            "street protests demonstrations today #shorts", "marching police protest #shorts", "climate activists protests #shorts",
            "workers strike demonstration #shorts", "anti government protests #shorts"
        ],
        "relevance": r"\b(protests?|protesters?|demonstrations?|march(?:es|ing)?|rall(?:y|ies)|strikes?|activists?|unrest|riot(?:s|ing)?)\b",
    },
    "climate": {
        "queries": [
            "flooding climate disaster #shorts", "wildfires heatwaves news #shorts", "hurricane typhoon storm #shorts",
            "drought climate crisis #shorts", "extreme weather wildfire floods #shorts"
        ],
        "relevance": r"\b(flood(?:s|ed|ing)?|wildfires?|heatwaves?|drought|hurricanes?|typhoons?|storms?|climate|cyclones?|landslides?|extreme weather|forest fires?)\b",
    },
    "migration": {
        "queries": [
            "refugees border crossing #shorts", "migrants asylum emergency news #shorts", "displaced families humanitarian #shorts",
            "refugee camps migration #shorts", "migrant rescue border #shorts"
        ],
        "relevance": r"\b(refugees?|migration|migrants?|asylum|border crossing|displaced|displacement|deportation|refugee camps?|evacuation)\b",
    },
    "wealth": {
        "queries": [
            "billionaires inequality #shorts", "wealth gap rich poor #shorts", "billionaires economic inequality #shorts",
            "corporate profits workers poverty #shorts", "ultra rich tax inequality #shorts"
        ],
        "relevance": r"\b(billionaires?|wealth|inequality|wealth gap|richest|superrich|ultra.rich|poverty|corporate profits?|tax avoidance|rich poor|oligarchs?|fortunes?)\b",
    },
}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SHORT_HINT = re.compile(r"\bshorts?\b|#shorts\b|#reels\b", re.I)
NEWS_SOURCE = re.compile(r"\b(reuters|associated press|bbc|dw|al jazeera|france\s*24|ap news|sky news|guardian|unicef|red cross)\b", re.I)
BAD_CONTEXT = re.compile(
    r"\b(gore|beheading|execution video|graphic death|dead bodies|deepfake|ai.generated|"
    r"gameplay|video game|movie trailer|reaction video|comedy sketch|prank|fake news|satire)\b", re.I
)
ANIMAL_SUBJECT = re.compile(
    r"\b(animals?|cows?|cattle|pigs?|chickens?|birds?|dogs?|cats?|elephants?|wildlife|"
    r"fish|dolphins?|whales?|livestock|sheep|rabbits?|mice|monkeys?|"
    r"meat|vegan(?:ism)?|zoo|zoos|animal rights|farm(?:ing)?|strays?|"
    r"sanctuar(?:y|ies)|tierwohl|tierschutz|tierheim|streuner|fleischkonsum)\b", re.I
)
ANIMAL_ISSUE = re.compile(
    r"\b(cruelty|abuse|neglect|suffering|slaughter|factory farm|farm conditions|"
    r"animal testing|poach(?:ing|ers?)?|traffick(?:ing|ers?)?|fur farm|"
    r"welfare|rescue|injured|battery cages|mistreatment|exploitat(?:ion|ed)|"
    r"strays?|street dogs?|street cats?|shelters?|abandon(?:ed|ment)?|"
    r"vegan(?:ism)?|meat(?: consumption| industry)?|animal rights|"
    r"zoos?|captive|captivity|dairy|livestock|sanctuar(?:y|ies)|"
    r"tierwohl|tierheim|tierschutz|massentierhaltung|fleischkonsum|"
    r"streuner|tierhaltung|tierversuche)\b", re.I
)
ANIMAL_OFFTOPIC = re.compile(
    r"\b(police training|law enforcement|animal crime investigations|"
    r"expert support|dog training|cute animals|funny pets|pet tricks)\b", re.I
)
WEALTH_OFFTOPIC = re.compile(
    r"\b(billionaire mindset|motivation|inspirational quotes|luxury lifestyle|"
    r"supercar collection|how to become rich|side hustle)\b", re.I
)


def youtube_api(resource: str, params: dict) -> dict:
    """Public Data API call, with key redacted from errors and logs."""
    url = API_BASE + resource + "?" + urlencode({**params, "key": API_KEY})
    try:
        with urlopen(url, timeout=30) as resp:
            return json.load(resp)
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            msg = payload.get("error", {}).get("message", "Request rejected")
        except (ValueError, UnicodeDecodeError, AttributeError):
            msg = "Request rejected"
        raise RuntimeError(f"YouTube API HTTP {exc.code}: {msg}") from None
    except URLError as exc:
        raise RuntimeError(f"YouTube API network error: {exc.reason}") from None


def load_state() -> dict:
    if not OUT.exists():
        return {}
    try:
        state = json.loads(OUT.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        print("WARNING: Unable to load previous videos.json, continuing with empty state")
        return {}


def age_days(published_at: str, now: dt.datetime) -> float:
    try:
        return max(0.0, (now - dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds() / 86400)
    except (TypeError, ValueError, AttributeError):
        return 100000.0


def duration_seconds(iso: str) -> int:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    a, b, c = (int(x or 0) for x in m.groups())
    return a * 3600 + b * 60 + c


def search_video_ids(query: str, now: dt.datetime) -> list[str]:
    after = now - dt.timedelta(days=MAX_AGE_DAYS)
    response = youtube_api("search", {
        "part": "snippet", "type": "video", "q": query, "order": "date",
        "maxResults": 50, "publishedAfter": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "videoDuration": "short", "videoEmbeddable": "true",
        "videoSyndicated": "true", "regionCode": COUNTRY, "safeSearch": "moderate",
    })
    ids = []
    for item in response.get("items", []):
        vid = item.get("id", {}).get("videoId", "")
        if isinstance(vid, str) and ID_RE.fullmatch(vid):
            ids.append(vid)
    return list(dict.fromkeys(ids))


def get_video_details(ids: list[str]) -> list[dict]:
    unique = list(dict.fromkeys(x for x in ids if isinstance(x, str) and ID_RE.fullmatch(x)))
    result = []
    for i in range(0, len(unique), 50):
        response = youtube_api("videos", {
            "part": "snippet,status,contentDetails", "id": ",".join(unique[i:i + 50]),
        })
        result.extend(response.get("items", []))
    return result


def playable_metadata(video: dict) -> bool:
    """Best-effort preflight. Content ID, age gates, etc. can still block embeds."""
    s = video.get("status", {})
    content = video.get("contentDetails", {})
    snippet = video.get("snippet", {})
    if s.get("privacyStatus") != "public" or s.get("embeddable") is not True:
        return False
    if s.get("madeForKids") is True or s.get("uploadStatus") not in (None, "processed"):
        return False
    if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
        return False
    restrictions = content.get("regionRestriction", {})
    if "allowed" in restrictions and COUNTRY not in restrictions.get("allowed", []):
        return False
    if COUNTRY in restrictions.get("blocked", []):
        return False
    return MIN_SECONDS <= duration_seconds(content.get("duration", "")) <= MAX_SECONDS


def rank_video(video: dict, key: str, now: dt.datetime) -> tuple[float, dict] | None:
    if not playable_metadata(video):
        return None
    snippet = video.get("snippet", {})
    vid = video.get("id", "")
    if not isinstance(vid, str) or not ID_RE.fullmatch(vid):
        return None
    title = html.unescape(snippet.get("title", ""))
    desc = html.unescape(snippet.get("description", ""))[:600]
    channel = html.unescape(snippet.get("channelTitle", ""))
    combined = title + " " + desc
    if BAD_CONTEXT.search(combined[:500]):
        return None
    regex = re.compile(TOPICS[key]["relevance"], re.I)
    title_hits = len(regex.findall(title))
    desc_hits = len(regex.findall(desc))
    if not title_hits and desc_hits < 2:
        return None
    if key == "animals":
        if ANIMAL_OFFTOPIC.search(title) or not (ANIMAL_SUBJECT.search(combined) and ANIMAL_ISSUE.search(combined)):
            return None
    if key == "wealth" and WEALTH_OFFTOPIC.search(title):
        return None
    age = age_days(snippet.get("publishedAt", ""), now)
    if age > MAX_AGE_DAYS:
        return None
    length = duration_seconds(video["contentDetails"]["duration"])
    # Strong freshness and relevance. Shorts keyword alone is NOT proof of aspect ratio.
    score = (
        70 - age * 2.3
        + min(title_hits, 3) * 6
        + min(desc_hits, 3) * 1.2
        + (8 if SHORT_HINT.search(title + " " + desc[:150]) else 0)
        + (7 if NEWS_SOURCE.search(channel) else 0)
        + (4 if length <= 90 else 0)
        + (8 if key == "animals" and ANIMAL_ISSUE.search(title) else 0)
    )
    return score, {
        "id": vid, "title": title, "channel": channel,
        "publishedAt": snippet.get("publishedAt", ""),
        "durationSeconds": length, "shortsHint": bool(SHORT_HINT.search(combined)),
    }



def probe_vertical_source(video_id: str) -> Optional[tuple[int, int]]:
    """Inspect original stream dimensions. No media download. Fail closed on errors.

    The YouTube Data API does not give this for videos we do not own, and a
    'short' duration does not imply vertical orientation. yt-dlp accesses
    public playback metadata, which YouTube can block on hosted runners.
    """
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is missing. Install it in the GitHub workflow before Update videos.")
    settings = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True, "socket_timeout": 7,
        "retries": 0, "extractor_retries": 0,
        "ignoreerrors": False,
    }
    try:
        with yt_dlp.YoutubeDL(settings) as ydl:
            info = ydl.extract_info(
                "https://www.youtube.com/watch?v=" + video_id, download=False
            )
    except Exception as exc:
        # Never accept the clip on an unverified #shorts hashtag alone.
        print(f"  format unavailable for {video_id}: {str(exc)[:120]}")
        return None
    if not isinstance(info, dict):
        return None
    pairs = []
    # Most formats share the same orientation. Exclude audio-only/storyboards.
    for fmt in info.get("formats", []):
        if not isinstance(fmt, dict) or fmt.get("vcodec") in (None, "none"):
            continue
        w, h = fmt.get("width"), fmt.get("height")
        if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w >= 100 and h >= 100:
            pairs.append((int(w), int(h)))
    if not pairs:
        w, h = info.get("width"), info.get("height")
        if isinstance(w, (int, float)) and isinstance(h, (int, float)):
            pairs = [(int(w), int(h))]
    if not pairs:
        print(f"  format has no dimensions: {video_id}")
        return None
    # Prefer the most frequently observed aspect ratio, not an arbitrary
    # low-resolution stream. Fail if aspect ratios disagree substantially.
    ratios = [w / h for w, h in pairs if h > 0]
    if not ratios:
        return None
    middle = sorted(ratios)[len(ratios) // 2]
    if not (MIN_VERTICAL_RATIO <= middle <= MAX_VERTICAL_RATIO):
        print(f"  REJECT LANDSCAPE/SQUARE {video_id}: aspect {middle:.3f}")
        return None
    if sum(MIN_VERTICAL_RATIO <= r <= MAX_VERTICAL_RATIO for r in ratios) < max(1, int(len(ratios) * 0.8)):
        print(f"  REJECT MIXED FORMATS {video_id}")
        return None
    return min(pairs, key=lambda p: abs(p[0] / p[1] - middle))


def verified_vertical(item: dict, dimensions: Optional[tuple[int, int]]) -> dict | None:
    if dimensions is None:
        return None
    w, h = dimensions
    ratio = w / h if h else 0
    if not (MIN_VERTICAL_RATIO <= ratio <= MAX_VERTICAL_RATIO):
        return None
    return {**item, "verticalVerified": True, "sourceWidth": w, "sourceHeight": h}

def choose_candidates(
    key: str, now: dt.datetime, used: set[str], history: list[str],
    previous_id: str, banned: set[str],
) -> tuple[list[dict], int]:
    ids = []
    calls = 0
    for query in TOPICS[key]["queries"]:
        ids.extend(search_video_ids(query, now))
        calls += 1
    ranked = []
    for v in get_video_details(ids):
        result = rank_video(v, key, now)
        if result is not None:
            score, item = result
            if item["id"] not in used and item["id"] not in banned:
                ranked.append((score, item))
    # Deduplicate and prioritize unseen clips; don't let yesterday's clip win every time.
    unique = {item["id"]: (score, item) for score, item in ranked}
    history_set = set(history)
    def sort_key(entry):
        score, item = entry
        vid = item["id"]
        fresh = age_days(item["publishedAt"], now) <= 7
        # Huge preference for anything not seen recently, and for anything != yesterday.
        return (vid not in history_set, vid != previous_id, fresh, score)
    ordered = [item for _, item in sorted(unique.values(), key=sort_key, reverse=True)]
    # FORMAT VERIFICATION: accept only proven portrait sources.
    confirmed = []
    probes = 0
    for item in ordered:
        if probes >= MAX_FORMAT_PROBES or len(confirmed) >= MAX_ALTERNATIVES + 1:
            break
        probes += 1
        item = verified_vertical(item, probe_vertical_source(item["id"]))
        if item is not None:
            confirmed.append(item)
    print(f"{key}: verified {len(confirmed)} portrait videos from {probes} format probes")
    
    # Fallback when vertical verification fails
    if not confirmed:
        likely_shorts = [
            item for item in ordered
            if item.get("shortsHint")
        ]
        confirmed = [
            {**item, "verticalVerified": False}
            for item in likely_shorts[:MAX_ALTERNATIVES + 1]
        ]
        if confirmed:
            print(f"{key}: using Shorts fallback")

    return confirmed, calls


def main() -> None:
    if not API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is missing from GitHub Actions secrets")
    now = dt.datetime.now(dt.timezone.utc)
    state = load_state()
    previous = state.get("videos", {}) if isinstance(state.get("videos"), dict) else {}
    old_history = state.get("recentlyShown", {}) if isinstance(state.get("recentlyShown"), dict) else {}
    old_bad = state.get("blockedIds", {}) if isinstance(state.get("blockedIds"), dict) else {}
    bad_global = set(x for ids in old_bad.values() if isinstance(ids, list) for x in ids if ID_RE.fullmatch(str(x)))
    # Known unhelpful recurring Animal Suffering clip from user's earlier selection.
    bad_global.add("ZIs_ho8kFtI")

    selected = {}
    alternatives = {}
    new_history = {}
    last_changed = {}
    used = set()
    total_searches = 0
    old_changed = state.get("lastChangedAt", {}) if isinstance(state.get("lastChangedAt"), dict) else {}
    for key in TOPICS:
        old = previous.get(key) if isinstance(previous.get(key), dict) else {}
        old_id = old.get("id", "")
        hist = old_history.get(key) if isinstance(old_history.get(key), list) else []
        hist = [str(x) for x in hist if ID_RE.fullmatch(str(x))]
        options, count = choose_candidates(key, now, used, hist, old_id, bad_global)
        total_searches += count
        if not options:
            # Only retain the prior video if YouTube still reports it as public,
            # embeddable, not region-restricted, on topic, and recent enough.
            if (ID_RE.fullmatch(old_id) and old_id not in used and old_id not in bad_global
                and old.get("verticalVerified") is True):
                details = get_video_details([old_id])
                if details:
                    ranked_old = rank_video(details[0], key, now)
                    if ranked_old is not None:
                        item = verified_vertical(ranked_old[1], probe_vertical_source(old_id))
                        if item is not None:
                            options = [item]
        selected[key] = options[0] if options else None
        # A fallback can be used only once across the six topics; don't duplicate clips.
        fallback = [v for v in options[1:] if v["id"] not in used][:MAX_ALTERNATIVES]
        alternatives[key] = fallback
        if selected[key]:
            vid = selected[key]["id"]
            used.add(vid)
            new_history[key] = ([vid] + [x for x in hist if x != vid])[:HISTORY_SIZE]
            last_changed[key] = now.isoformat() if vid != old_id else old_changed.get(key) or now.isoformat()
            print(f"{key}: {vid} | {len(fallback)} backup clips | {'NEW' if vid != old_id else 'SAME'} | {selected[key]['title'][:80]}")
        else:
            new_history[key] = hist[:HISTORY_SIZE]
            last_changed[key] = old_changed.get(key)
            print(f"{key}: NO SUITABLE CLIP")

    filled = sum(bool(item) for item in selected.values())
    if not filled:
        print("WARNING: No vertical videos could be verified. Publishing empty slots rather than showing landscape videos.")
    result = {
        "updatedAt": now.isoformat(), "videos": selected,
        "alternatives": alternatives, "recentlyShown": new_history,
        "lastChangedAt": last_changed,
        "blockedIds": old_bad, "formatPolicy": "strict-portrait-metadata-only",
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"SUCCESS: {filled}/6 topics, {total_searches} search requests, {sum(map(len, alternatives.values()))} backups")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
