"""
Live templates for Clip Pirate.

The Templates tab loads from GET /templates. This module fetches the template
list from an external source you control (a published Google Sheet CSV or a
JSON file), validates it, caches it, and falls back to the built-in templates
if the source is missing or unreachable.

Set the source in your .env file:

    TEMPLATES_SOURCE_URL=https://docs.google.com/spreadsheets/d/e/XXXX/pub?output=csv

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

# Must match what /generate accepts.
ALLOWED_DURATIONS = {30, 45, 60}
ALLOWED_RATIOS = {"9:16", "16:9", "1:1", "4:3"}
ALLOWED_VOICES = {"professional", "energetic", "calm"}

ALLOWED_PACES = {"slow", "normal", "fast"}

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
             script_style, footage_keywords, pace):
    return {
        "id": id_,
        "name": name,
        "icon": icon,
        "description": description,
        "prompt": prompt,
        "duration": None,
        "ratio": None,
        "voice": None,
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
             "fast"),
    _builtin("amazing-places", "Amazing Places", "🌍",
             "Create engaging destination and travel videos.",
             "Amazing places in",
             "Write like a travel guide painting a scene: sensory detail, "
             "sense of wonder, one standout place or fact per beat.",
             "aerial, landscape, travel, scenic",
             "normal"),
    _builtin("did-you-know", "Did You Know?", "🧠",
             "Educational short-form videos built around surprising facts.",
             "Did you know facts about",
             "Start with 'Did you know' style curiosity. Explain each fact "
             "simply and clearly, with a brief reason it is true.",
             "science, documentary, close-up",
             "normal"),
    _builtin("storytelling", "Storytelling", "📖",
             "Build short narratives with a strong opening and conclusion.",
             "A fascinating short story about",
             "Tell it as a story with a clear beginning, tension, and a "
             "satisfying ending. Use vivid, emotional language.",
             "cinematic, moody, people, slow motion",
             "slow"),
    _builtin("motivation", "Motivation", "💡",
             "Short motivational and inspirational content.",
             "An inspiring story about",
             "Speak directly to the viewer in an uplifting, confident tone. "
             "Build momentum and finish with one memorable takeaway.",
             "sunrise, running, mountains, determination",
             "normal"),
    _builtin("top-5-list", "Top 5 List", "🎯",
             "Create countdown-style videos and ranked lists.",
             "Top 5 facts about",
             "Structure as a countdown from number 5 to number 1, announcing "
             "each number, with the most impressive item saved for last.",
             "variety, bold, dynamic",
             "fast"),
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

    return {
        "id": _slug(raw.get("id") or name) or f"template-{index}",
        "name": name,
        "icon": _text(raw.get("icon"), 4) or "🎬",
        "description": _text(raw.get("description"), 140),
        "prompt": _text(raw.get("prompt"), 200),
        "duration": duration,
        "ratio": ratio,
        "voice": voice,
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

    if not url:
        return BUILTIN_TEMPLATES, "builtin"

    now = time.time()

    with _lock:

        cached = _cache["templates"]

        if cached is not None and now - _cache["fetched_at"] < CACHE_TTL_SECONDS:
            return cached, "cache"

        recently_failed = now - _cache["failed_at"] < FAILURE_BACKOFF_SECONDS

    if not recently_failed:

        try:

            templates = _parse_templates(_download(url))

            if not templates:
                raise ValueError("No valid, active templates found.")

            with _lock:
                _cache["templates"] = templates
                _cache["fetched_at"] = time.time()
                _cache["failed_at"] = 0.0

            return templates, "remote"

        except Exception as error:

            print(f"[templates] Could not load {url}: {error}")

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
