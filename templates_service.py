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
PRIVATE_FIELDS = (
    "script_style",
    "footage_keywords",
    "pace",
    "hook_style",
    "ending_style",
    "dialogue_style",
    "retention_strategy"
)

INACTIVE_VALUES = {"false", "0", "no", "off", "inactive", "disabled", "hidden"}


# ------------------------------------------------------------
# BUILT-IN TEMPLATES (used when no source is configured or it fails)
#
# duration / ratio / voice are None = "use the user's own preferences".
# ------------------------------------------------------------

def _builtin(
    id_,
    name,
    icon,
    description,
    prompt,
    script_style,
    footage_keywords,
    pace,
    narration_mode=None,
    accent=None,
    cover_url=None,
    hook_style=None,
    ending_style=None,
    dialogue_style=None,
    retention_strategy=None
):
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
        "hook_style": hook_style,
        "ending_style": ending_style,
        "dialogue_style": dialogue_style,
        "retention_strategy": retention_strategy
    }


BUILTIN_TEMPLATES = [
    
    _builtin(
        "amazing-places",
        "Amazing Places",
        "🌍",
        "Cinematic destination videos that reveal breathtaking places, "
        "remarkable natural wonders, fascinating cultures, and unforgettable "
        "travel experiences.",
        "Amazing places in",
        "Write like an exceptional travel documentary narrator. Open with a "
        "vivid image, surprising destination detail, or compelling reason to "
        "explore the place. Build the narration around specific sights, "
        "landmarks, landscapes, local traditions, or verified facts. Use "
        "sensory language sparingly but effectively, helping viewers imagine "
        "the scenery, atmosphere, sounds, and scale. Give each beat one "
        "clear visual focus and connect the scenes naturally. Distinguish "
        "verified facts from subjective descriptions. Do not invent "
        "locations, attractions, cultural practices, prices, or travel "
        "conditions. End with a memorable image, meaningful observation, "
        "or a final verified detail that rewards the viewer's curiosity. "
        "Avoid generic travel slogans and forced calls to action.",
        "aerial, sweeping landscape, cinematic travel documentary, "
        "natural wonders, architecture, local culture, scenic vistas, "
        "golden hour, immersive establishing shots",
        "normal",
        hook_style="descriptive",
        ending_style="memorable",
        dialogue_style="curiosity_loop",
        retention_strategy="high",
        accent="#31c48d"
    ),

    _builtin(
        "did-you-know",
        "Did You Know?",
        "🧠",
        "Fast, fascinating educational videos that explain surprising facts "
        "and make complex ideas easy to understand.",
        "Did you know facts about",
        "Start with a concise 'Did you know?' hook or an equally compelling "
        "question about the topic. Present accurate, genuinely interesting "
        "facts in a logical sequence. Explain why each fact is true using "
        "simple language, a useful comparison, or a short example. Keep "
        "scientific and technical explanations accessible without making "
        "them misleading. Prioritize a few well-explained facts over a long "
        "list of shallow claims. Use reliable, verifiable information and "
        "never invent statistics, quotations, studies, or scientific "
        "explanations. Build curiosity between beats, but resolve the main "
        "question before the video ends. Finish with the most useful "
        "takeaway or a surprising implication supported by the facts.",
        "science documentary, macro close-ups, laboratory details, "
        "nature footage, space imagery, educational animation, "
        "scientific visualization, clean explanatory shots",
        "normal",
        hook_style="intriguing",
        ending_style="memorable",
        dialogue_style="curiosity_loop",
        retention_strategy="high",
        accent="#5cc8ff"
    ),

    _builtin(
        "storytelling",
        "Storytelling",
        "📖",
        "Immersive short stories with compelling openings, rising tension, "
        "meaningful turning points, and satisfying conclusions.",
        "A fascinating short story about",
        "Tell a coherent story with a clear beginning, middle, and end. "
        "Open with an intriguing situation, an unusual event, or a "
        "character facing a meaningful challenge. Establish only the "
        "details needed to understand the characters and their goals. "
        "Build tension through obstacles, choices, discoveries, and "
        "consequences rather than repetitive dramatic statements. Use "
        "vivid but concise language, natural dialogue when appropriate, "
        "and clear transitions between scenes. Make every beat advance "
        "the plot or reveal character. Deliver a believable resolution, "
        "a meaningful revelation, or an earned emotional conclusion. "
        "For fictional stories, creativity is welcome; for stories "
        "presented as true, do not fabricate real-world evidence or "
        "present invented events as verified history. Avoid introducing "
        "unresolved plot threads solely to force another view.",
        "cinematic storytelling, expressive characters, atmospheric "
        "locations, dramatic lighting, meaningful close-ups, "
        "environmental details, visual symbolism, film stills",
        "slow",
        hook_style="descriptive",
        ending_style="resolution",
        dialogue_style="curiosity_loop",
        retention_strategy="high",
        accent="#c77dff"
    ),

    _builtin(
        "motivation",
        "Motivation",
        "💡",
        "Uplifting short videos that turn inspiring ideas, practical lessons, "
        "and personal growth stories into actionable takeaways.",
        "An inspiring story about",
        "Speak directly to the viewer in a confident, encouraging, "
        "grounded tone. Open with a relatable challenge, an insightful "
        "observation, or a concise statement that earns attention. Build "
        "momentum through a realistic example, a useful lesson, or a "
        "credible story of persistence and growth. Use strong, natural "
        "language without exaggeration, shame, or empty promises. Make "
        "the message specific enough to be useful rather than relying "
        "on generic slogans. Distinguish real people and events from "
        "fictional illustrations. End with one memorable takeaway and "
        "a practical action the viewer can realistically take. Use a "
        "call to action only when it fits naturally; never promise that "
        "success is guaranteed.",
        "sunrise, mountain landscapes, focused work, training montage, "
        "everyday perseverance, progress over time, determined expressions, "
        "hopeful cinematic lighting, meaningful visual transitions",
        "normal",
        hook_style="inspirational",
        ending_style="call_to_action",
        dialogue_style="curiosity_loop",
        retention_strategy="high",
        accent="#ff8a5c"
    ),

    _builtin(
        "top-5-list",
        "Top 5 List",
        "🎯",
        "Punchy countdown videos featuring five interesting, useful, "
        "surprising, or carefully ranked entries on a topic.",
        "Top 5 facts about",
        "Structure the video as a clear countdown from number five "
        "to number one. Announce each number explicitly and give every "
        "entry a concise explanation of why it belongs on the list. "
        "Use consistent criteria when ranking items and explain the "
        "basis of a ranking when it is subjective or debatable. Build "
        "anticipation through meaningful contrasts and progressively "
        "interesting reveals, while keeping every entry informative. "
        "Ensure all five entries are distinct, relevant to the requested "
        "topic, and factually defensible. Do not invent rankings, "
        "statistics, prices, records, or superlatives. Save the strongest "
        "entry for number one when the topic supports that choice. "
        "Finish with a short conclusion that reinforces the most "
        "interesting takeaway. Do not introduce a sixth item in the "
        "closing or delay the answer with an unrelated cliffhanger.",
        "dynamic countdown graphics, bold numbers, varied establishing "
        "shots, comparison visuals, close-up details, fast visual cuts, "
        "clean transitions, striking subject imagery",
        "fast",
        hook_style="shocking",
        ending_style="memorable",
        dialogue_style="curiosity_loop",
        retention_strategy="high",
        accent="#ef5b6b"
    ),

    _builtin(
        "podcast-style",
        "Podcast Style",
        "🎙️",
        "Natural two-person podcast clips with engaging questions, "
        "thoughtful answers, useful insights, and authentic conversation.",
        "A podcast conversation about",
        "Write a natural, concise conversation between a HOST and a GUEST. "
        "The HOST opens with a relevant hook, introduces the topic, and "
        "asks focused questions. The GUEST answers directly, adding "
        "useful explanations, examples, or surprising but supportable "
        "insights. Give both speakers distinct conversational roles "
        "without exaggerated personalities or artificial disagreement. "
        "Keep turns brief enough for short-form video, but allow an "
        "answer to finish before changing topics. Use follow-up questions "
        "that respond to what was actually said rather than repeating "
        "the same question in different words. Maintain a relaxed, "
        "respectful rhythm and avoid making the conversation sound "
        "like two alternating monologues. Do not invent personal "
        "experiences or attribute unsupported claims to real people. "
        "Close with the GUEST's final insight when useful, followed "
        "by a natural HOST summary or thank-you. Avoid abrupt endings, "
        "repetitive greetings, and forced promotional requests.",
        "podcast studio, two microphones, host and guest, natural "
        "conversation, studio close-ups, attentive listening, "
        "subtle camera changes, professional recording environment",
        "normal",
        "dialogue",
        hook_style="podcast",
        ending_style="discussion",
        dialogue_style="curiosity_loop",
        retention_strategy="high",
        accent="#7c5cff"
    ),

    _builtin(
        "news-reporter",
        "News Reporter",
        "📰",
        "Professional anchor-and-correspondent news videos with clear "
        "story structure, factual reporting, and broadcast-style delivery.",
        "Breaking news report on",
        "Write a credible news broadcast with a HOST acting as the studio "
        "anchor and a GUEST acting as the correspondent. The HOST opens "
        "with a concise, newsworthy lead, identifies the central "
        "development, and introduces the correspondent when appropriate. "
        "The GUEST reports the confirmed details, relevant background, "
        "known consequences, and important context in a clear, neutral "
        "tone. Keep questions purposeful and answers direct. Distinguish "
        "confirmed facts, official statements, allegations, estimates, "
        "and unverified reports. Attribute claims to their sources when "
        "known, acknowledge uncertainty, and never fabricate names, "
        "quotes, dates, locations, statistics, sources, or news outlets. "
        "Do not call a story breaking unless the information warrants it. "
        "Conclude with the latest confirmed development or the next "
        "known step. Use a professional correspondent sign-off or a "
        "studio handoff only when appropriate. For standalone short-form "
        "news, finish with a complete factual closing sentence. Do not "
        "withhold essential information to create suspense, insert "
        "personal opinions, or add an unrelated engagement request.",
        "newsroom, professional broadcast studio, city establishing "
        "shots, relevant event footage, public press conferences, "
        "reporter on location, news graphics, documentary-style b-roll",
        "fast",
        "dialogue",
        hook_style="breaking_news",
        ending_style="broadcast_sign_off",
        dialogue_style="curiosity_loop",
        retention_strategy="high",
        accent="#4f8cff"
    ),

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

    "hook_style"= _text(raw.get("hook_style"), 40),

    "ending_style"= _text(raw.get("ending_style"), 40),

    "dialogue_style"= _text(raw.get("dialogue_style"), 40),

    "retention_strategy"= _text(raw.get("retention_strategy"), 60),

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
        "hook_style": _text(raw.get("hook_style"), 100),
        "ending_style": _text(raw.get("ending_style"), 100),
        "dialogue_style": _text(raw.get("dialogue_style"), 100),
        "retention_strategy": _text(raw.get("retention_strategy"), 100),
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
