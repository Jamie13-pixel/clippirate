import os
import json
import re

from google import genai
from google.genai import types


# ============================================================
# GEMINI CONFIG
# ============================================================

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite"
)


# ============================================================
# GEMINI CLIENT
# ============================================================

def get_gemini_client():

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY missing."
        )

    return genai.Client(
        api_key=api_key
    )


# ============================================================
# CLEAN RESPONSE
# ============================================================

def clean_response(text):

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```$",
        "",
        text,
        flags=re.IGNORECASE
    )

    return text.strip()


# ============================================================
# PARSE JSON
# ============================================================

def extract_json(text):

    text = clean_response(text)

    try:
        return json.loads(text)

    except Exception:

        start = text.find("[")
        end = text.rfind("]")

        if start == -1:
            raise RuntimeError(
                "Scene planner returned invalid JSON."
            )

        return json.loads(
            text[start:end + 1]
        )


# ============================================================
# BUILD SCENE PLAN
# ============================================================

def build_scene_plan(
    topic,
    script,
    timeline,
    style="viral"
):

    if not timeline:

        return []

    client = get_gemini_client()

    timeline_text = json.dumps(
        timeline,
        indent=2
    )

    prompt = f"""
You are a professional short-video director.

TOPIC:
{topic}

VIDEO STYLE:
{style}

NARRATION:
{script}

TIMELINE:
{timeline_text}

Your job is to convert narration into
high-quality stock footage searches.

Rules:

- Every narration segment becomes a scene.
- Create visual searches that look good on
  Pexels.
- Never repeat identical searches.
- Use cinematic visual language.
- Focus on what viewers should SEE.
- Use short search phrases.
- Return only JSON.

Output format:

[
  {{
    "query":"startup founder laptop",
    "style":"business",
    "emotion":"authority",
    "duration":4.2
  }}
]
"""

    response = client.models.generate_content(
        model=DEFAULT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=1200
        )
    )

    data = extract_json(
        response.text
    )

    scenes = []

    for i, item in enumerate(data):

        duration = float(
            timeline[i].get(
                "duration",
                3
            )
        )

        scenes.append(
            {
                "query": item.get(
                    "query",
                    "professional office"
                ),
                "style": item.get(
                    "style",
                    "general"
                ),
                "emotion": item.get(
                    "emotion",
                    "neutral"
                ),
                "duration": duration
            }
        )

    return scenes