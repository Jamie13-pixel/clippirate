"""
Live templates for Clip Pirate.

The Templates tab loads from GET /templates. This module fetches the template
list from an external source you control (a published Google Sheet CSV or a
JSON file), validates it, caches it, and falls back to the built-in templates
if the source is missing or unreachable.

Set the source in your .env file:

    TEMPLATES_SOURCE_URL=https://docs.google.com/spreadsheets/d/e/XXXX/pub?output=csv

Or use a Notion database instead (it wins if both are set):

    NOTION_TOKEN=secret_xxx            # the integration's token
    NOTION_DATABASE_ID=xxxxxxxx...     # the database id or its full link

Optional:

    TEMPLATES_CACHE_SECONDS=60     # how long a fetched list is reused (default 60)
"""

import csv
import hashlib
import io
import json
import os
import re
import threading
import time

import requests


# ------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------

CACHE_TTL_SECONDS = int(os.getenv("TEMPLATES_CACHE_SECONDS", "60"))
FAILURE_BACKOFF_SECONDS = 60      # don't retry a broken source on every request
FETCH_TIMEOUT_SECONDS = 6
MAX_SOURCE_BYTES = 200_000
MAX_TEMPLATES = 24

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
NOTION_MAX_PAGES = 3          # 100 rows per page

# Must match what /generate accepts.
ALLOWED_DURATIONS = {30, 45, 60}
ALLOWED_RATIOS = {"9:16", "16:9", "1:1", "4:3"}
ALLOWED_VOICES = {"professional", "energetic", "calm"}

ALLOWED_PACES = {"slow", "normal", "fast"}

# "solo" = one narrator, "dialogue" = Host + Guest (Starter plan and above).
# Blank = keep whatever the user picked.
ALLOWED_NARRATION_MODES = {"solo", "dialogue"}

# Look of the template card and the Create page. Both are optional.
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
COVER_URL = re.compile(r"^https://[^\s\"'()<>\\]{4,290}$")

# These steer script writing, stock footage and voice-over. They stay on the
# server (never sent to the browser); /generate looks them up by template id.
PRIVATE_FIELDS = ("script_style", "footage_keywords", "pace")

INACTIVE_VALUES = {"false", "0", "no", "off", "inactive", "disabled", "hidden"}


# ------------------------------------------------------------
# BUILT-IN TEMPLATES (used when no source is configured or it fails)
#
# duration / ratio / voice are None = "use the user's own preferences".
# ------------------------------------------------------------

def _builtin(id_, name, icon, description, prompt,
             script_style, footage_keywords, pace, narration_mode=None,
             accent=None, cover_url=None):
    return {
        "id": id_,
        "name": name,
        "icon": icon,
        "description": description,
        "prompt": prompt,
        "duration": None,
        "ratio": None,
        "voice": None,
        "narration_mode": narration_mode,
        "accent": accent,
        "cover_url": cover_url,
        "script_style": script_style,
        "footage_keywords": footage_keywords,
        "pace": pace,
    }


BUILTIN_TEMPLATES = [
    _builtin("viral-facts", "Viral Facts", "⚡",
             "Fast-paced fact videos with strong hooks.",
             "Amazing facts about",
             "Open with a shocking hook in the first sentence. Use short, "
             "punchy sentences, one surprising fact per beat, and end with a "
             "question or cliffhanger that makes viewers want more.",
             "dramatic, close-up, fast motion",
             "fast",
             "solo",
             accent="#f5b942"),
    _builtin("amazing-places", "Amazing Places", "🌍",
             "Create engaging destination and travel videos.",
             "Amazing places in",
             "Write like a travel guide painting a scene: sensory detail, "
             "sense of wonder, one standout place or fact per beat.",
             "aerial, landscape, travel, scenic",
             "normal",
             accent="#31c48d"),
    _builtin("did-you-know", "Did You Know?", "🧠",
             "Educational short-form videos built around surprising facts.",
             "Did you know facts about",
             "Start with 'Did you know' style curiosity. Explain each fact "
             "simply and clearly, with a brief reason it is true.",
             "science, documentary, close-up",
             "normal",
             accent="#5cc8ff"),
    _builtin("storytelling", "Storytelling", "📖",
             "Build short narratives with a strong opening and conclusion.",
             "A fascinating short story about",
             "Tell it as a story with a clear beginning, tension, and a "
             "satisfying ending. Use vivid, emotional language.",
             "cinematic, moody, people, slow motion",
             "slow",
             accent="#c77dff"),
    _builtin("motivation", "Motivation", "💡",
             "Short motivational and inspirational content.",
             "An inspiring story about",
             "Speak directly to the viewer in an uplifting, confident tone. "
             "Build momentum and finish with one memorable takeaway.",
             "sunrise, running, mountains, determination",
             "normal",
             accent="#ff8a5c"),
    _builtin("top-5-list", "Top 5 List", "🎯",
             "Create countdown-style videos and ranked lists.",
             "Top 5 facts about",
             "Structure as a countdown from number 5 to number 1, announcing "
             "each number, with the most impressive item saved for last.",
             "variety, bold, dynamic",
             "fast",
             accent="#ef5b6b"),
    _builtin("podcast-style", "Podcast Style", "🎙️",
             "A Host and a Guest talk through your topic like a short podcast clip.",
             "A podcast conversation about",
             "Write as a relaxed two-person podcast. The HOST opens with a "
             "hook and asks questions; the GUEST answers with surprising "
             "details. Keep turns short, natural and conversational.",
             "podcast, studio, microphone, conversation, city",
             "normal",
             "dialogue",
             accent="#7c5cff"),
    _builtin("news-reporter", "News Reporter", "📰",
             "An anchor and a correspondent break down the story.",
             "Breaking news report on",
             "Write as a news broadcast. The HOST is the anchor introducing "
             "the story and asking follow-ups; the GUEST is the "
             "correspondent reporting clear, factual details. Keep a "
             "confident, professional tone.",
             "news, city, crowd, newsroom, headlines",
             "fast",
             "dialogue",
             accent="#4f8cff"),
]


# ------------------------------------------------------------
# CACHE
# ------------------------------------------------------------

_lock = threading.Lock()

_cache = {
    "templates": None,
    "fetched_at": 0.0,
    "failed_at": 0.0,
}


# ------------------------------------------------------------
# VALIDATION
# ------------------------------------------------------------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value, max_len):
    if value is None:
        return ""
    value = _CONTROL_CHARS.sub("", str(value)).replace("<", "").replace(">", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len]


def _slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:40]


def _is_active(raw):
    if "active" not in raw:
        return True
    return str(raw["active"]).strip().lower() not in INACTIVE_VALUES


def _clean_template(raw, index):
    """Return a safe template dict, or None if the row is unusable."""

    if not isinstance(raw, dict):
        return None

    raw = {str(k).strip().lower(): v for k, v in raw.items() if k is not None}

    if not _is_active(raw):
        return None

    name = _text(raw.get("name"), 40)

    if not name:
        return None

    duration = None
    try:
        candidate = int(float(str(raw.get("duration", "")).strip()))
        if candidate in ALLOWED_DURATIONS:
            duration = candidate
    except (ValueError, TypeError):
        pass

    ratio = _text(raw.get("ratio"), 10)
    ratio = ratio if ratio in ALLOWED_RATIOS else None

    voice = _text(raw.get("voice"), 20).lower()
    voice = voice if voice in ALLOWED_VOICES else None

    try:
        order = float(str(raw.get("sort", "")).strip())
    except (ValueError, TypeError):
        order = float(index)

    pace = _text(raw.get("pace"), 10).lower()
    pace = pace if pace in ALLOWED_PACES else None

    narration_mode = _text(raw.get("narration_mode"), 10).lower()
    narration_mode = narration_mode if narration_mode in ALLOWED_NARRATION_MODES else None

    accent = str(raw.get("accent") or "").strip()
    accent = accent.lower() if HEX_COLOR.match(accent) else None

    cover_url = str(raw.get("cover_url") or "").strip()
    cover_url = cover_url if COVER_URL.match(cover_url) else None

    return {
        "id": _slug(raw.get("id") or name) or f"template-{index}",
        "name": name,
        "icon": _text(raw.get("icon"), 4) or "🎬",
        "description": _text(raw.get("description"), 140),
        "prompt": _text(raw.get("prompt"), 200),
        "duration": duration,
        "ratio": ratio,
        "voice": voice,
        "narration_mode": narration_mode,
        "accent": accent,
        "cover_url": cover_url,
        "script_style": _text(raw.get("script_style"), 400),
        "footage_keywords": _text(raw.get("footage_keywords"), 120),
        "pace": pace,
        "_order": order,
    }


def _parse_templates(text):

    text = text.lstrip("\ufeff").strip()

    if text.startswith("[") or text.startswith("{"):

        data = json.loads(text)

        rows = (
            data.get("templates")
            if isinstance(data, dict)
            else data
        )

    else:

        rows = list(csv.DictReader(io.StringIO(text)))

    if not isinstance(rows, list):
        raise ValueError("Template source must be a list of templates.")

    return _clean_rows(rows)


def _clean_rows(rows):

    cleaned = []
    seen = set()

    for index, raw in enumerate(rows):

        template = _clean_template(raw, index)

        if not template or template["id"] in seen:
            continue

        seen.add(template["id"])
        cleaned.append(template)

    cleaned.sort(key=lambda t: t["_order"])

    for template in cleaned:
        template.pop("_order", None)

    return cleaned[:MAX_TEMPLATES]


# ------------------------------------------------------------
# NOTION
# ------------------------------------------------------------

_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")


def _notion_database_id():
    """Accepts a bare id, a dashed id, or the database's full Notion link."""

    raw = os.getenv("NOTION_DATABASE_ID", "").strip().split("?")[0]

    found = _UUID.search(raw)

    if found:
        return found.group(0).replace("-", "")

    tail = raw.rstrip("/").split("/")[-1].replace("-", "")[-32:]

    return tail if _HEX32.fullmatch(tail) else ""


def _notion_enabled():
    return bool(os.getenv("NOTION_TOKEN", "").strip() and _notion_database_id())


def _notion_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _notion_value(prop):
    """Flatten one Notion property into a plain value."""

    kind = prop.get("type")
    value = prop.get(kind)

    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in value or [])

    if kind in ("number", "checkbox", "url"):
        return value

    if kind in ("select", "status"):
        return (value or {}).get("name", "")

    if kind == "multi_select":
        return ", ".join(item.get("name", "") for item in value or [])

    if kind == "formula":
        inner = value or {}
        return inner.get(inner.get("type"))

    return ""


def _fetch_notion():
    """Return the database rows as a list of plain dicts."""

    token = os.getenv("NOTION_TOKEN", "").strip()
    database_id = _notion_database_id()

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    rows = []
    cursor = None

    for _ in range(NOTION_MAX_PAGES):

        body = {"page_size": 100}

        if cursor:
            body["start_cursor"] = cursor

        response = requests.post(
            f"{NOTION_API}/databases/{database_id}/query",
            headers=headers,
            json=body,
            timeout=FETCH_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:

            try:
                message = response.json().get("message", "")
            except ValueError:
                message = ""

            raise ValueError(f"Notion returned {response.status_code}: {message}")

        data = response.json()

        for page in data.get("results", []):

            if page.get("archived") or page.get("in_trash"):
                continue

            rows.append({
                _notion_key(name): _notion_value(prop)
                for name, prop in (page.get("properties") or {}).items()
            })

        if not data.get("has_more"):
            break

        cursor = data.get("next_cursor")

    return rows


# ------------------------------------------------------------
# FETCHING
# ------------------------------------------------------------

def _download(url):

    with requests.get(
        url,
        timeout=FETCH_TIMEOUT_SECONDS,
        stream=True,
        headers={"User-Agent": "ClipPirate/1.0"},
    ) as response:

        response.raise_for_status()

        raw = b""

        for chunk in response.iter_content(8192):

            raw += chunk

            if len(raw) > MAX_SOURCE_BYTES:
                raise ValueError("Template source is too large.")

    return raw.decode("utf-8-sig", errors="replace")


def _load_templates():
    """
    Return (templates, source) where source is "remote" | "cache" |
    "stale-cache" | "builtin". Never raises: there is always a usable list.
    """

    url = os.getenv("TEMPLATES_SOURCE_URL", "").strip()
    use_notion = _notion_enabled()

    if not url and not use_notion:
        return BUILTIN_TEMPLATES, "builtin"

    label = "Notion" if use_notion else url

    now = time.time()

    with _lock:

        cached = _cache["templates"]

        if cached is not None and now - _cache["fetched_at"] < CACHE_TTL_SECONDS:
            return cached, "cache"

        recently_failed = now - _cache["failed_at"] < FAILURE_BACKOFF_SECONDS

    if not recently_failed:

        try:

            templates = (
                _clean_rows(_fetch_notion())
                if use_notion
                else _parse_templates(_download(url))
            )

            if not templates:
                raise ValueError("No valid, active templates found.")

            with _lock:
                _cache["templates"] = templates
                _cache["fetched_at"] = time.time()
                _cache["failed_at"] = 0.0

            return templates, "remote"

        except Exception as error:

            print(f"[templates] Could not load {label}: {error}")

            with _lock:
                _cache["failed_at"] = time.time()

    with _lock:
        stale = _cache["templates"]

    if stale:
        return stale, "stale-cache"

    return BUILTIN_TEMPLATES, "builtin"


# ------------------------------------------------------------
# PUBLIC API
# ------------------------------------------------------------

def _public_view(template):
    return {
        key: value
        for key, value in template.items()
        if key not in PRIVATE_FIELDS
    }


def get_templates():
    """
    What GET /templates returns: the list the browser may see (without the
    private script/footage/pace instructions), where it came from, and a
    version that changes whenever the visible list changes.
    """

    templates, source = _load_templates()

    public = [_public_view(t) for t in templates]

    version = hashlib.sha1(
        json.dumps(public, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]

    return {"templates": public, "source": source, "version": version}


def get_template(template_id):
    """Full template (including private fields) by id, or None."""

    template_id = _slug(template_id or "")

    if not template_id:
        return None

    templates, _ = _load_templates()

    for template in templates:
        if template["id"] == template_id:
            return dict(template)

    return None
