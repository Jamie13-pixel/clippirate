import os
import uuid
import subprocess
import asyncio
import time
import edge_tts

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

    voices = VOICE_PROFILES.get(
        profile,
        VOICE_PROFILES[DEFAULT_VOICE]
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

    EDGE_VOICES = {
    "professional": ["en-US-GuyNeural", "en-US-JennyNeural"],
    "energetic":    ["en-US-ChristopherNeural", "en-US-AriaNeural"],
    "calm":         ["en-US-AndrewNeural", "en-US-EmmaNeural"],
    }


def build_edge_voice_map(profile):
    pair = EDGE_VOICES.get((profile or DEFAULT_VOICE).lower(), EDGE_VOICES[DEFAULT_VOICE])
    return {"HOST": pair[0], "GUEST": pair[1]}

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


class TTSUnavailable(RuntimeError):
    """Voice generation failed in a way the user should hear about plainly."""


MAX_ATTEMPTS = int(os.getenv("ELEVENLABS_MAX_ATTEMPTS", "4"))
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# Keep this at or below your ElevenLabs plan's concurrent-request limit.
_tts_slots = asyncio.Semaphore(int(os.getenv("ELEVENLABS_MAX_CONCURRENCY", "2")))


def _status_of(exc):
    return getattr(exc, "status_code", None)


def _is_quota_error(exc):
    text = str(getattr(exc, "body", "") or exc).lower()
    return "quota_exceeded" in text or "exceeds your quota" in text


def _is_retryable(exc):
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    status = _status_of(exc)
    if status is None:
        # Network-level errors from the HTTP client (timeouts, resets).
        return type(exc).__module__.startswith("httpx")
    return status == 429 or status >= 500


def _retry_delay(exc, attempt):
    headers = getattr(exc, "headers", None) or {}
    try:
        return min(float(headers.get("retry-after")), 30.0)
    except (TypeError, ValueError, AttributeError):
        pass
    return min(2 ** attempt, 15) + random.uniform(0, 1)


def _remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def _synthesize_line(text, voice, path):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with _tts_slots:
                audio = _get_client().text_to_speech.convert(
                    voice_id=voice,
                    text=text,
                    model_id=ELEVENLABS_MODEL,
                    output_format="mp3_44100_64",
                )
                with open(path, "wb") as f:
                    async for chunk in audio:
                        if chunk:
                            f.write(chunk)
            return

        except Exception as exc:
            _remove(path)

            if _is_quota_error(exc):
                _block_eleven(3600) 
                print("[TTS] ELEVENLABS QUOTA EXHAUSTED - top up or upgrade the plan.", flush=True)
                raise TTSUnavailable(
                    "Voice generation is temporarily unavailable. "
                    "Your credits were refunded. Please try again later."
                ) from exc

            if not _is_retryable(exc) or attempt == MAX_ATTEMPTS:
                raise

            delay = _retry_delay(exc, attempt)
            print(
                f"[TTS] attempt {attempt}/{MAX_ATTEMPTS} failed "
                f"({type(exc).__name__} {_status_of(exc)}); retrying in {delay:.1f}s",
                flush=True,
            )
            await asyncio.sleep(delay)

_eleven_blocked_until = 0.0


def _eleven_available():
    return time.time() >= _eleven_blocked_until


def _block_eleven(seconds):
    global _eleven_blocked_until
    _eleven_blocked_until = max(_eleven_blocked_until, time.time() + seconds)


async def _synthesize_line_edge(text, voice, path):
    for attempt in range(1, 4):
        try:
            await asyncio.wait_for(
                edge_tts.Communicate(text, voice).save(path),
                timeout=45
            )
            if os.path.getsize(path) == 0:
                raise RuntimeError("Backup voice returned empty audio.")
            return
        except Exception:
            _remove(path)
            if attempt == 3:
                raise
            await asyncio.sleep(2 * attempt)


async def _synthesize_eleven_line(text, voice, path, voice_profile):
    try:
        await _synthesize_line(text, voice, path)
    except TTSUnavailable:
        raise
    except Exception as exc:
        # Only a problem with this one voice justifies swapping the voice.
        if _status_of(exc) in (400, 404, 422):
            await _synthesize_line(text, get_fallback_voice(voice_profile), path)
        else:
            raise


def _normalize_dialogue(dialogue):
    items = []
    for item in dialogue:
        if isinstance(item, str):
            speaker, text = "HOST", item.strip()
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            speaker, text = item
            speaker = str(speaker).strip() or "HOST"
            text = str(text).strip()
        else:
            raise ValueError("Invalid dialogue format.")
        if text:
            items.append((speaker.upper(), text))
    return items


async def _render_lines(items, provider, voice_profile, temp_dir):
    voices = (
        build_voice_map(voice_profile)
        if provider == "elevenlabs"
        else build_edge_voice_map(voice_profile)
    )

    rendered = []

    try:
        for speaker, text in items:
            voice = voices.get(speaker, voices["GUEST"])
            path = os.path.join(temp_dir, f"line_{uuid.uuid4().hex}.mp3")
            rendered.append((speaker, text, path))   # registered first so cleanup sees it

            if provider == "elevenlabs":
                await _synthesize_eleven_line(text, voice, path, voice_profile)
            else:
                await _synthesize_line_edge(text, voice, path)

        return rendered

    except Exception:
        for _, _, p in rendered:
            _remove(p)
        raise


async def _render_with_backup(items, voice_profile, temp_dir):
    primary = os.getenv("TTS_PRIMARY", "elevenlabs").strip().lower()
    order = ["edge", "elevenlabs"] if primary == "edge" else ["elevenlabs", "edge"]

    last_exc = None

    for provider in order:

        if provider == "elevenlabs" and not _eleven_available():
            print("[TTS] ElevenLabs temporarily skipped.", flush=True)
            continue

        try:
            result = await _render_lines(items, provider, voice_profile, temp_dir)
            print(f"[TTS] Rendered with {provider}.", flush=True)
            return result

        except Exception as exc:
            last_exc = exc
            if provider == "elevenlabs":
                _block_eleven(60)
            print(f"[TTS] {provider} failed ({type(exc).__name__}): {exc}", flush=True)

    raise TTSUnavailable(
        "The voice service is busy right now. "
        "Your credits were refunded. Please try again in a minute."
    ) from last_exc


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
    temp_dir=None,
    target_duration=None,
):
    """
    Synthesize dialogue and return timing metadata. Uses ElevenLabs first
    and falls back to a backup voice if it fails.
    """

    temp_dir = temp_dir or os.path.join(os.getenv("DATA_DIR", "data"), "temp_clips")
    os.makedirs(temp_dir, exist_ok=True)

    items = _normalize_dialogue(dialogue)

    if not items:
        raise ValueError("No dialogue was generated.")

    line_paths = []
    combined_file = None
    adjusted_file = None

    try:

        line_paths = await _render_with_backup(items, voice_profile, temp_dir)

        # ----------------------------------------------------
        # LOAD GENERATED AUDIO
        # ----------------------------------------------------

        clips = []
        timeline = []

        current_time = 0.0

        for speaker, text, path in line_paths:

            clip = await asyncio.to_thread(AudioFileClip, path)


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

        await asyncio.to_thread(final_audio.write_audiofile, combined_file, logger=None)


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

            await asyncio.to_thread(
                _adjust_audio_duration,
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