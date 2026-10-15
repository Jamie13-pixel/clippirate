import os
import uuid
import subprocess

from elevenlabs.client import AsyncElevenLabs
from moviepy import AudioFileClip, concatenate_audioclips


# ============================================================
# ADVANCED VOICE CONFIGURATION
# ============================================================

import random

VOICE_PROFILES = {

    "professional": [
        "onwK4e9ZLuTAKqWW03F9",  # Daniel
        "XrExE9yKIg1WjnnlVkGX",  # Matilda
        "Xb7hH8MSUJpSbSDYk0k2",  # Alice
        "hpp4J3VqNfWAUOO0d1Us",  # Bella
    ],

    "energetic": [
        "TX3LPaxmHKxFdv7VOQHJ",  # Liam
        "cgSgspJ2msm6clMCkdW9",  # Jessica
        "IKne3meq5aSn9XLyUdCD",  # Charlie
        "FGY2WhTYpPnrIDTdsKH5",  # Laura
    ],

    "calm": [
        "EXAVITQu4vr4xnSDxMaL",  # Sarah
        "cjVigY5qzO86Huf0OWal",  # Eric
        "nPczCjzI2devNBz1zQrb",  # Brian
        "SAz9YHcvj6GT2YYXdXww",  # River
        "pFZP5JQG7iQjIQuC4Bku",  # Lily
    ],
}

DEFAULT_VOICE = "professional"


def get_random_voice(profile):

    profile = (profile or DEFAULT_VOICE).lower()

    voices = build_voice_map(
       voice_profile
   )

    return random.choice(voices)


def build_voice_map(profile):

    host_voice = get_random_voice(profile)
    guest_voice = get_random_voice(profile)

    attempts = 0

    while (
        host_voice == guest_voice
        and attempts < 10
    ):
        guest_voice = get_random_voice(profile)
        attempts += 1

    return {
        "HOST": host_voice,
        "GUEST": guest_voice,
    }


def get_fallback_voice(profile):

    fallbacks = {

        "professional":
            "onwK4e9ZLuTAKqWW03F9",  # Daniel

        "energetic":
            "TX3LPaxmHKxFdv7VOQHJ",  # Liam

        "calm":
            "EXAVITQu4vr4xnSDxMaL",  # Sarah
    }

    return fallbacks.get(
        profile,
        "onwK4e9ZLuTAKqWW03F9"
    )

# ============================================================
# TTS SYNTHESIS
# ============================================================

_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not configured on the server."
            )
        _client = AsyncElevenLabs(api_key=key)
    return _client


async def fetch_account_voices():
    try:
        voices = await _get_client().voices.get_all()

        results = []

        for voice in voices.voices:
            results.append({
                "id": voice.voice_id,
                "name": voice.name,
            })

        return results

    except Exception:
        return []


async def _synthesize_line(text, voice, path):
    audio = _get_client().text_to_speech.convert(
        voice_id=voice,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_64",
    )
    with open(path, "wb") as f:
        async for chunk in audio:
            if chunk:
                f.write(chunk)


# ============================================================
# AUDIO SPEED ADJUSTMENT
# ============================================================

def _adjust_audio_duration(input_file: str, output_file: str, target_duration: float):
    """Fit the narration to target_duration (atempo) and normalise loudness."""
    audio = AudioFileClip(input_file)
    actual_duration = float(audio.duration)
    audio.close()

    if actual_duration <= 0:
        raise ValueError("Generated audio has zero duration.")

    filters = []

    # Only change speed when the length is meaningfully off.
    if abs(actual_duration - target_duration) >= 0.15:
        remaining = actual_duration / target_duration
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
        while remaining > 2.0:
            filters.append("atempo=2.0")
            remaining /= 2.0
        filters.append(f"atempo={remaining:.8f}")

    # Bring the voice to a normal short-video loudness.
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    command = [
        "ffmpeg", "-y", "-i", input_file,
        "-filter:a", ",".join(filters),
        "-vn", "-c:a", "libmp3lame", "-q:a", "2",
        output_file,
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed while adjusting voiceover:\n{result.stderr}")
    return output_file


# ============================================================
# CREATE DIALOGUE VOICE
# ============================================================

async def create_dialogue_voice(
    dialogue,
    output_file,
    voice_profile=DEFAULT_VOICE,
    temp_dir="data/temp_clips",
    target_duration=None,
):
    """
    Synthesize dialogue and return timing metadata.

    If target_duration is supplied, the completed narration
    is adjusted to fit that duration.

    Example:

        target_duration=30

    makes the narration approximately 30 seconds long.
    """

    voices = VOICE_MAPS.get(
        voice_profile,
        VOICE_MAPS[DEFAULT_VOICE]
    )

    os.makedirs(
        temp_dir,
        exist_ok=True
    )

    line_paths = []

    combined_file = None
    adjusted_file = None

    try:

        # ----------------------------------------------------
        # SYNTHESIZE EACH DIALOGUE LINE
        # ----------------------------------------------------

        for item in dialogue:

            if isinstance(item, str):

                speaker = "HOST"
                text = item.strip()

            elif (
                isinstance(item, (list, tuple))
                and len(item) == 2
            ):

                speaker, text = item

                speaker = (
                    str(speaker).strip()
                    or "HOST"
                )

                text = str(
                    text
                ).strip()

            else:

                raise ValueError(
                    "Invalid dialogue format."
                )

            if not text:
                continue

            voice = voices.get(
                speaker.upper(),
                voices.get("GUEST")
            )

            path = os.path.join(
                temp_dir,
                f"line_{uuid.uuid4().hex}.mp3"
            )

            try:

                await _synthesize_line(
                    text,
                    voice,
                    path
                )

            except Exception as exc:

                print(
                    f"[TTS] Voice failed ({voice}), "
                    f"falling back: {exc}",
                    flush=True
                )

                fallback_voice = get_fallback_voice(
                    voice_profile
                )

                await _synthesize_line(
                    text,
                    fallback_voice,
                    path
                )

            line_paths.append(
                (
                    speaker,
                    text,
                    path
                )
            )

        if not line_paths:

            raise ValueError(
                "No dialogue was generated."
            )

        # ----------------------------------------------------
        # LOAD GENERATED AUDIO
        # ----------------------------------------------------

        clips = []
        timeline = []

        current_time = 0.0

        for speaker, text, path in line_paths:

            clip = AudioFileClip(
                path
            )

            duration = float(
                clip.duration
            )

            timeline.append(
                {
                    "speaker": speaker,
                    "text": text,
                    "start": current_time,
                    "duration": duration,
                    "end": current_time + duration,
                }
            )

            clips.append(
                clip
            )

            current_time += duration

        # ----------------------------------------------------
        # COMBINE AUDIO
        # ----------------------------------------------------

        final_audio = concatenate_audioclips(
            clips
        )

        natural_duration = float(
            final_audio.duration
        )

        # Temporary combined file.
        combined_file = os.path.join(
            temp_dir,
            f"combined_{uuid.uuid4().hex}.mp3"
        )

        final_audio.write_audiofile(
            combined_file,
            logger=None
        )

        final_audio.close()

        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass

        # ----------------------------------------------------
        # MATCH TARGET VIDEO DURATION
        # ----------------------------------------------------

        if target_duration is not None:

            target_duration = float(
                target_duration
            )

            if target_duration <= 0:

                raise ValueError(
                    "target_duration must be greater than zero."
                )

            adjusted_file = os.path.join(
                temp_dir,
                f"adjusted_{uuid.uuid4().hex}.mp3"
            )

            _adjust_audio_duration(
                combined_file,
                adjusted_file,
                target_duration
            )

            os.replace(
                adjusted_file,
                output_file
            )
            

            # Update timeline proportionally.
            if natural_duration > 0:

                scale = (
                    target_duration
                    / natural_duration
                )

                for entry in timeline:

                    entry["start"] *= scale
                    entry["duration"] *= scale
                    entry["end"] = entry["start"] + entry["duration"]

            return timeline

        # ----------------------------------------------------
        # NO TARGET DURATION
        # ----------------------------------------------------

        os.replace(
            combined_file,
            output_file
        )

        return timeline

    finally:

        # ----------------------------------------------------
        # CLEAN TEMPORARY FILES
        # ----------------------------------------------------

        for _, _, path in line_paths:

            try:

                if os.path.exists(path):
                    os.remove(path)

            except Exception:
                pass

        for path in [
            combined_file,
            adjusted_file,
        ]:

            try:

                if (
                    path
                    and os.path.exists(path)
                ):
                    os.remove(path)

            except Exception:
                pass