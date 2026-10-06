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

# Number of retries ONLY for temporary 503-type failures.
# Keep this low because retries can consume API quota.
MAX_503_RETRIES = 1

# Maximum wait before retrying a temporary 503.
MAX_RETRY_WAIT = 10


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

    # Remove markdown JSON fences if Gemini adds them.
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

    # --------------------------------------------------------
    # Direct JSON
    # --------------------------------------------------------

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        pass

    # --------------------------------------------------------
    # JSON surrounded by other text
    # --------------------------------------------------------

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:

        raise RuntimeError(
            "Gemini returned an invalid script response."
        )

    try:

        return json.loads(
            text[start:end + 1]
        )

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "Gemini returned invalid JSON for the video script."
        ) from exc


# ============================================================
# CLASSIFY GEMINI ERROR
# ============================================================

def _gemini_error_type(exc: Exception) -> str:

    error_text = str(exc)

    upper = error_text.upper()

    # --------------------------------------------------------
    # QUOTA / RATE LIMIT
    # --------------------------------------------------------

    if (
        "429" in error_text
        or "RESOURCE_EXHAUSTED" in upper
        or "QUOTA EXCEEDED" in upper
        or "RATE LIMIT" in upper
    ):
        return "quota"

    # --------------------------------------------------------
    # TEMPORARY SERVER UNAVAILABLE
    # --------------------------------------------------------

    if (
        "503" in error_text
        or "UNAVAILABLE" in upper
        or "HIGH DEMAND" in error_text.lower()
        or "SERVICE UNAVAILABLE" in upper
    ):
        return "temporary"

    # --------------------------------------------------------
    # AUTHENTICATION
    # --------------------------------------------------------

    if (
        "401" in error_text
        or "403" in error_text
        or "UNAUTHENTICATED" in upper
        or "PERMISSION_DENIED" in upper
    ):
        return "authentication"

    # --------------------------------------------------------
    # MODEL NOT FOUND
    # --------------------------------------------------------

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
# GENERATE SCRIPT
# ============================================================

def generate_script(
    topic: str,
    duration: int = 30
):

    # --------------------------------------------------------
    # Validate topic
    # --------------------------------------------------------

    if not topic or not topic.strip():

        raise ValueError(
            "Video topic cannot be empty."
        )

    topic = topic.strip()

    # --------------------------------------------------------
    # Validate duration
    # --------------------------------------------------------

    if duration not in (30, 45, 60):

        raise ValueError(
            "Duration must be 30, 45, or 60 seconds."
        )

    word_count = target_word_count(duration)

    # --------------------------------------------------------
    # Create Gemini client
    # --------------------------------------------------------

    client = get_gemini_client()

    # --------------------------------------------------------
    # Prompt
    # --------------------------------------------------------

    prompt = f"""
You are the professional script-writing engine for
Clip Pirate .ai, an AI short-video generator.

Create a high-retention short-form video narration.

TOPIC:
{topic}

REQUESTED DURATION:
{duration} seconds

TARGET WORD COUNT:
Approximately {word_count} spoken words.

REQUIREMENTS:

- Start with a strong hook.
- Keep the viewer engaged.
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
- End naturally.
- Keep the narration close to the requested word count.

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

    # --------------------------------------------------------
    # Gemini request
    # --------------------------------------------------------
    #
    # IMPORTANT:
    #
    # We do NOT retry 429 quota errors.
    #
    # We allow only ONE retry for a temporary 503.
    #
    # This prevents a temporary Gemini problem from consuming
    # the remaining daily API quota.
    #

    response = None

    for attempt in range(MAX_503_RETRIES + 1):

        try:

            response = client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=500
                )
            )

            break

        except Exception as exc:

            error_type = _gemini_error_type(exc)

            # ------------------------------------------------
            # QUOTA
            # ------------------------------------------------
            #
            # NEVER retry quota errors.
            #

            if error_type == "quota":

                raise _friendly_gemini_error(
                    exc,
                    error_type
                ) from exc

            # ------------------------------------------------
            # TEMPORARY 503
            # ------------------------------------------------

            if error_type == "temporary":

                if attempt < MAX_503_RETRIES:

                    # One short retry only.
                    wait_time = min(
                        2 ** attempt,
                        MAX_RETRY_WAIT
                    )

                    time.sleep(wait_time)

                    continue

                raise _friendly_gemini_error(
                    exc,
                    error_type
                ) from exc

            # ------------------------------------------------
            # AUTHENTICATION
            # ------------------------------------------------

            if error_type == "authentication":

                raise _friendly_gemini_error(
                    exc,
                    error_type
                ) from exc

            # ------------------------------------------------
            # MODEL
            # ------------------------------------------------

            if error_type == "model":

                raise _friendly_gemini_error(
                    exc,
                    error_type
                ) from exc

            # ------------------------------------------------
            # OTHER ERROR
            # ------------------------------------------------

            raise _friendly_gemini_error(
                exc,
                error_type
            ) from exc

    # ========================================================
    # Make sure a response was received
    # ========================================================

    if response is None:

        raise RuntimeError(
            "Gemini did not return a response."
        )

    # ========================================================
    # Get response text
    # ========================================================

    content = getattr(
        response,
        "text",
        None
    )

    if not content:

        raise RuntimeError(
            "Gemini returned an empty script."
        )

    # ========================================================
    # Parse JSON
    # ========================================================

    data = extract_json(content)

    if not isinstance(data, dict):

        raise RuntimeError(
            "Gemini returned an invalid script structure."
        )

    # ========================================================
    # Extract script
    # ========================================================

    script_text = str(
        data.get(
            "script",
            ""
        )
    ).strip()

    if not script_text:

        raise RuntimeError(
            "Gemini returned no script text."
        )

    # ========================================================
    # Extract dialogue
    # ========================================================

    dialogue = data.get(
        "dialogue",
        []
    )

    if not isinstance(dialogue, list):

        raise RuntimeError(
            "Gemini returned an invalid dialogue format."
        )

    # --------------------------------------------------------
    # Clean dialogue
    # --------------------------------------------------------

    dialogue = [
        str(sentence).strip()
        for sentence in dialogue
        if str(sentence).strip()
    ]

    if not dialogue:

        raise RuntimeError(
            "Gemini returned no dialogue."
        )

    # ========================================================
    # Return result
    # ========================================================

    return script_text, dialogue