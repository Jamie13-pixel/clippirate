import os
import json
import re
import time

from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types


# ============================================================
# GEMINI CONFIGURATION
# ============================================================

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite"
)

# Retries ONLY for temporary 5xx/503-type failures.
MAX_503_RETRIES = 1
MAX_RETRY_WAIT = 10

VALID_NARRATION_MODES = ("solo", "dialogue")


# ============================================================
# GEMINI CLIENT
# ============================================================

def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured on the server. "
            "Add GEMINI_API_KEY to your deployment environment "
            "variables and restart the application."
        )

    return genai.Client(api_key=api_key)


# ============================================================
# WORD COUNT
# ============================================================

def target_word_count(duration: int) -> int:

    if duration == 30:
        return 70

    if duration == 45:
        return 105

    if duration == 60:
        return 140

    raise ValueError(
        "Duration must be 30, 45, or 60 seconds."
    )


# ============================================================
# CLEAN GEMINI RESPONSE
# ============================================================

def clean_response(text: str) -> str:

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
        flags=re.IGNORECASE
    )

    return text.strip()


# ============================================================
# EXTRACT JSON
# ============================================================

def extract_json(text: str):

    text = clean_response(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(
            "Gemini returned an invalid script response."
        )

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Gemini returned invalid JSON for the video script."
        ) from exc


# ============================================================
# CLASSIFY GEMINI ERROR
# ============================================================

def _gemini_error_type(exc: Exception) -> str:

    # (This block was sitting at module level in the old file,
    #  where `exc` doesn't exist. It belongs inside the function.)
    code = getattr(exc, "code", None)

    if code in (500, 502, 503, 504):
        return "temporary"

    error_text = str(exc)
    upper = error_text.upper()

    if (
        "429" in error_text
        or "RESOURCE_EXHAUSTED" in upper
        or "QUOTA EXCEEDED" in upper
        or "RATE LIMIT" in upper
    ):
        return "quota"

    if (
        "503" in error_text
        or "UNAVAILABLE" in upper
        or "HIGH DEMAND" in error_text.lower()
        or "SERVICE UNAVAILABLE" in upper
    ):
        return "temporary"

    if (
        "401" in error_text
        or "403" in error_text
        or "UNAUTHENTICATED" in upper
        or "PERMISSION_DENIED" in upper
    ):
        return "authentication"

    if (
        "404" in error_text
        or "NOT_FOUND" in upper
        or "MODEL NOT FOUND" in upper
    ):
        return "model"

    return "other"


# ============================================================
# FRIENDLY GEMINI ERROR
# ============================================================

def _friendly_gemini_error(
    exc: Exception,
    error_type: str
) -> RuntimeError:

    if error_type == "quota":
        return RuntimeError(
            "Gemini API quota has been exhausted for the current "
            "model/project. No additional retry was attempted. "
            "Check your Gemini API quota or billing plan, or try "
            "again after the quota resets."
        )

    if error_type == "temporary":
        return RuntimeError(
            "Gemini is temporarily unavailable. "
            "The server made only one retry to avoid unnecessary "
            "API requests. Please try generating the video again "
            "later."
        )

    if error_type == "authentication":
        return RuntimeError(
            "Gemini API authentication failed. "
            "Check your GEMINI_API_KEY."
        )

    if error_type == "model":
        return RuntimeError(
            f"Gemini model '{DEFAULT_MODEL}' is not available. "
            "Check the GEMINI_MODEL environment variable."
        )

    return RuntimeError(
        f"Gemini script generation failed: {str(exc)}"
    )


# ============================================================
# HELPERS
# ============================================================

def _clean_visual(text) -> str:
    """Keep a stock-footage search phrase short, lowercase and free of punctuation."""
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip().lower()[:60]


def _normalize_speaker(value, index, dialogue_mode):
    """Return "HOST" or "GUEST". Solo is always HOST. In dialogue mode a
    missing/unknown speaker falls back to strict alternation."""
    if not dialogue_mode:
        return "HOST"

    speaker = str(value or "").strip().upper()

    if speaker in ("HOST", "GUEST"):
        return speaker

    return "HOST" if index % 2 == 0 else "GUEST"


def _parse_visual_scenes(data, dialogue_mode=False):
    """Read {"scenes": [{"sentence", "visual", "speaker"?}, ...]}.

    Returns (script_text, sentences, visuals, speakers) or None.
    """
    scenes = data.get("scenes")

    if not isinstance(scenes, list):
        return None

    sentences = []
    visuals = []
    speakers = []

    for item in scenes:

        if isinstance(item, dict):
            sentence = str(item.get("sentence", "")).strip()
            visual = _clean_visual(item.get("visual", ""))
            raw_speaker = item.get("speaker")
        else:
            sentence = str(item).strip()
            visual = ""
            raw_speaker = None

        if sentence:
            speakers.append(
                _normalize_speaker(raw_speaker, len(sentences), dialogue_mode)
            )
            sentences.append(sentence)
            visuals.append(visual)

    if not sentences:
        return None

    return " ".join(sentences), sentences, visuals, speakers


# ============================================================
# PROMPTS
# ============================================================

def _build_prompt(topic, duration, word_count, with_visuals, dialogue_mode):

    if with_visuals and dialogue_mode:
        return f"""
You are the professional script-writing engine for
Clip Pirate .ai, an AI short-video generator.

Create a high-retention short-form video written as a natural
conversation between two speakers, HOST and GUEST, and, for every
line, a stock-footage search phrase for the matching visuals.

TOPIC:
{topic}

REQUESTED DURATION:
{duration} seconds

TARGET WORD COUNT:
Approximately {word_count} spoken words in total.

CONVERSATION REQUIREMENTS:

- HOST opens with a strong hook.
- Strictly alternate: HOST, GUEST, HOST, GUEST, and so on.
- HOST explains the facts. GUEST reacts, asks curious questions
  and sets up the next fact.
- Use curiosity loops: every GUEST line should make the viewer
  want the next HOST line.
- Each "sentence" must be exactly ONE short, complete sentence
  ending in a period, exclamation mark or question mark.
- Use natural spoken language, suitable for text-to-speech.
- Avoid filler. Do not include stage directions, timestamps,
  sound effects, speaker names inside the text, or production
  instructions.
- Do not mention AI.
- End with a strong final fact or a memorable reaction.
- Keep the total close to the requested word count.

VISUAL REQUIREMENTS:

- "visual" is a 2 to 4 word search phrase for a stock-video library
  such as Pixabay. It is never spoken.
- Describe something concrete that a camera can film, for example
  "soccer stadium crowd", "city skyline night", "stack of cash",
  "ocean waves", "person typing laptop".
- It must match what that line is about.
- Vary the visuals from line to line.
- Never use people's names, brand names, team names or on-screen text.
- Never use abstract ideas such as "success" or "rivalry".

Return ONLY valid JSON.

Use exactly this structure:

{{
    "scenes": [
        {{"speaker": "HOST", "sentence": "First spoken line.", "visual": "stadium crowd cheering"}},
        {{"speaker": "GUEST", "sentence": "Second spoken line?", "visual": "soccer ball grass"}}
    ]
}}
"""

    if with_visuals:
        return f"""
You are the professional script-writing engine for
Clip Pirate .ai, an AI short-video generator.

Create a high-retention short-form video narration and, for every
sentence, a stock-footage search phrase for the matching visuals.

TOPIC:
{topic}

REQUESTED DURATION:
{duration} seconds

TARGET WORD COUNT:
Approximately {word_count} spoken words in total.

NARRATION REQUIREMENTS:

- Start with a strong hook.
- Keep the viewer engaged.
- Use natural spoken language.
- Use short, clear sentences.
- Each "sentence" must be exactly ONE complete sentence
  ending in a period, exclamation mark or question mark.
- Make it suitable for text-to-speech.
- Make it informative and engaging.
- Avoid unnecessary filler.
- Do not include camera directions, scene directions, timestamps,
  sound effects or production instructions in the sentences.
- Do not mention AI.
- End naturally.
- Keep the narration close to the requested word count.

VISUAL REQUIREMENTS:

- "visual" is a 2 to 4 word search phrase for a stock-video library
  such as Pixabay. It is never spoken.
- Describe something concrete that a camera can film, for example
  "soccer stadium crowd", "city skyline night", "stack of cash",
  "ocean waves", "person typing laptop".
- It must match what that sentence is about.
- Vary the visuals from sentence to sentence.
- Never use people's names, brand names, team names or on-screen text.
- Never use abstract ideas such as "success" or "rivalry".
  Choose a filmable scene that suggests the idea instead.

Return ONLY valid JSON.

Use exactly this structure:

{{
    "scenes": [
        {{"sentence": "First spoken sentence.", "visual": "stadium crowd cheering"}},
        {{"sentence": "Second spoken sentence.", "visual": "soccer ball grass"}}
    ]
}}
"""

    if dialogue_mode:
        return f"""
You are the professional script-writing engine for
Clip Pirate .ai, an AI short-video generator.

Create a high-retention short-form video written as a natural
conversation between two speakers, HOST and GUEST.

TOPIC:
{topic}

REQUESTED DURATION:
{duration} seconds

TARGET WORD COUNT:
{word_count} words minimum. It may exceed the target by up to 15%.

REQUIREMENTS:

- HOST opens with a strong hook.
- Strictly alternate: HOST, GUEST, HOST, GUEST, and so on.
- HOST explains the facts. GUEST reacts and asks curious questions.
- Use curiosity loops and short sentences.
- Natural spoken language suitable for text-to-speech.
- No stage directions, timestamps, sound effects or speaker names
  inside the text.
- Do not mention AI.
- End with a strong final fact.
- Never return fewer than {word_count} words.

Return ONLY valid JSON.

Use exactly this structure:

{{
    "script": "Complete conversation as one string, without speaker names.",
    "dialogue": [
        {{"speaker": "HOST", "text": "First line."}},
        {{"speaker": "GUEST", "text": "Second line?"}},
        {{"speaker": "HOST", "text": "Third line."}}
    ]
}}
"""

    return f"""
You are the professional script-writing engine for
Clip Pirate .ai, an AI short-video generator.

Create a high-retention short-form video narration.

TOPIC:
{topic}

REQUESTED DURATION:
{duration} seconds

TARGET WORD COUNT:
{word_count} words minimum.

IMPORTANT:
The narration must contain AT LEAST {word_count} words.
It may exceed the target by up to 15%.

REQUIREMENTS:

- Start with a strong hook in the first sentence.
- Keep the viewer engaged from beginning to end.
- Use natural spoken language.
- Use short, clear sentences.
- Make it suitable for text-to-speech.
- Make it informative and engaging.
- Avoid unnecessary filler.
- Do not include camera directions.
- Do not include scene directions.
- Do not include timestamps.
- Do not include sound effects.
- Do not include production instructions.
- Do not mention AI.
- End with a memorable conclusion or takeaway.
- Never return fewer than {word_count} words.

Return ONLY valid JSON.

Use exactly this structure:

{{
    "script": "Complete narration as one string.",
    "dialogue": [
        "First spoken sentence.",
        "Second spoken sentence.",
        "Third spoken sentence."
    ]
}}
"""


# ============================================================
# TEMPLATE STYLE
# ============================================================

PACE_GUIDANCE = {
    "fast": "Fast pace: very short, punchy sentences and quick beats.",
    "slow": "Slow pace: slightly longer, atmospheric sentences with room "
            "to breathe (still one sentence per line).",
}


def _template_block(template, with_visuals):
    """Extra prompt text built from a template's private style fields.

    Returns "" when there is no template or it has nothing to add."""

    if not isinstance(template, dict):
        return ""

    lines = []

    style = str(template.get("script_style") or "").strip()
    if style:
        lines.append(f"- Script style: {style}")

    pace = PACE_GUIDANCE.get(str(template.get("pace") or "").lower())
    if pace:
        lines.append(f"- {pace}")

    keywords = str(template.get("footage_keywords") or "").strip()
    if with_visuals and keywords:
        lines.append(
            f"- Footage themes to favour when they fit the line: {keywords}. "
            "Every visual must still match what its line is about."
        )

    if not lines:
        return ""

    name = str(template.get("name") or "this template").strip()

    return (
        f"STYLE TEMPLATE ({name}):\n"
        + "\n".join(lines)
        + "\n\nFollow the style above, but the target word count, the "
        "one-sentence-per-line rule and the JSON structure below still apply.\n\n"
    )


# ============================================================
# GENERATE SCRIPT
# ============================================================

def generate_script(
    topic: str,
    duration: int = 30,
    with_visuals: bool = False,
    narration_mode: str = "solo",
    return_speakers: bool = False,
    template: dict = None,
):
    """Generate the narration script.

    Return shapes (backward compatible):

      default:                      (script_text, dialogue)
      with_visuals=True:            (script_text, sentences, visuals)
      return_speakers=True:         (script_text, sentences, visuals, speakers)

    narration_mode: "solo" (HOST only) or "dialogue" (HOST/GUEST).
    speakers[i] is "HOST" or "GUEST" and matches sentences[i].
    return_speakers=True implies with_visuals=True.
    """

    if not topic or not topic.strip():
        raise ValueError("Video topic cannot be empty.")

    topic = topic.strip()

    if duration not in (30, 45, 60):
        raise ValueError("Duration must be 30, 45, or 60 seconds.")

    narration_mode = (narration_mode or "solo").strip().lower()

    if narration_mode not in VALID_NARRATION_MODES:
        raise ValueError(
            "narration_mode must be 'solo' or 'dialogue'."
        )

    dialogue_mode = narration_mode == "dialogue"

    if return_speakers:
        with_visuals = True

    word_count = target_word_count(duration)

    client = get_gemini_client()

    prompt = _build_prompt(
        topic, duration, word_count, with_visuals, dialogue_mode
    )

    style_block = _template_block(template, with_visuals)

    if style_block:
        marker = "Return ONLY valid JSON."
        prompt = prompt.replace(marker, style_block + marker, 1)

    # --------------------------------------------------------
    # Gemini request
    #
    # 429 quota errors are never retried. Temporary 5xx/503
    # errors get ONE short retry so a blip can't burn the quota.
    # --------------------------------------------------------

    if with_visuals:
        max_tokens = 2000 if dialogue_mode else 1500
    else:
        max_tokens = 800 if dialogue_mode else 500

    response = None

    for attempt in range(MAX_503_RETRIES + 1):

        try:

            response = client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=max_tokens
                )
            )

            break

        except Exception as exc:

            error_type = _gemini_error_type(exc)

            if (
                error_type == "temporary"
                and attempt < MAX_503_RETRIES
            ):
                time.sleep(min(2 ** attempt, MAX_RETRY_WAIT))
                continue

            raise _friendly_gemini_error(exc, error_type) from exc

    if response is None:
        raise RuntimeError("Gemini did not return a response.")

    content = getattr(response, "text", None)

    if not content:
        raise RuntimeError("Gemini returned an empty script.")

    data = extract_json(content)

    if not isinstance(data, dict):
        raise RuntimeError("Gemini returned an invalid script structure.")

    # --------------------------------------------------------
    # Per-line visuals (and speakers)
    # --------------------------------------------------------

    if with_visuals:

        parsed = _parse_visual_scenes(data, dialogue_mode)

        if parsed:
            script_text, sentences, visuals, speakers = parsed

            if return_speakers:
                return script_text, sentences, visuals, speakers

            return script_text, sentences, visuals

        # Gemini ignored the "scenes" structure; fall through to the
        # plain script/dialogue parsing below, with no visuals.

    # --------------------------------------------------------
    # Plain script + dialogue
    # --------------------------------------------------------

    script_text = str(data.get("script", "")).strip()

    raw_dialogue = data.get("dialogue", [])

    if not isinstance(raw_dialogue, list):
        raise RuntimeError("Gemini returned an invalid dialogue format.")

    sentences = []
    speakers = []

    for item in raw_dialogue:

        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            raw_speaker = item.get("speaker")
        else:
            text = str(item).strip()
            raw_speaker = None

        if text:
            speakers.append(
                _normalize_speaker(raw_speaker, len(sentences), dialogue_mode)
            )
            sentences.append(text)

    if not sentences:
        raise RuntimeError("Gemini returned no dialogue.")

    if not script_text:
        script_text = " ".join(sentences)

    if return_speakers:
        return script_text, sentences, [""] * len(sentences), speakers

    if with_visuals:
        return script_text, sentences, []

    return script_text, sentences