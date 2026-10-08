import math
import os
import random
import subprocess
import uuid

import imageio_ffmpeg
import requests
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)

    RATIO_SIZES = {
        "9:16": (1080, 1920),
          "16:9": (1920, 1080),
        "4:3": (1440, 1080),
        "3:4": (1080, 1440),
        "1:1": (1080, 1080),
    }
else:
    RATIO_SIZES = {
        "9:16": (1080, 1920),
        "16:9": (1920, 1080),
        "4:3": (1440, 1080),
        "3:4": (1080, 1440),
        "1:1": (1080, 1080),
    }

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_CLIPS_DIR = os.path.join(BASE_DIR, "data", "temp_clips")

PEXELS_API_KEY_ENV = "PEXELS_API_KEY"

CAPTION_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


# ============================================================
# STOCK FOOTAGE (Pexels)
# If you already have your own search/download functions,
# delete these two and import yours instead.
# ============================================================

def search_video_clips(query, count=5, aspect_ratio="9:16"):
    """Return a list of direct mp4 URLs for the query."""

    api_key = os.environ.get(PEXELS_API_KEY_ENV, "").strip()

    if not api_key:
        raise RuntimeError(
            f"{PEXELS_API_KEY_ENV} is not set, so stock footage "
            "cannot be searched."
        )

    target_w, target_h = RATIO_SIZES.get(aspect_ratio, (1080, 1920))

    if target_h > target_w:
        orientation = "portrait"
    elif target_w > target_h:
        orientation = "landscape"
    else:
        orientation = "square"

    response = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": api_key},
        params={
            "query": query,
            "per_page": max(count, 1),
            "orientation": orientation,
        },
        timeout=20,
    )
    response.raise_for_status()

    urls = []

    for video in response.json().get("videos", []):

        files = [
            f for f in video.get("video_files", [])
            if f.get("file_type") == "video/mp4"
            and f.get("link")
            and f.get("width")
            and f.get("height")
        ]

        if not files:
            continue

        best = min(
            files,
            key=lambda f: abs(
                f["width"] * f["height"] - target_w * target_h
            ),
        )

        urls.append(best["link"])

    return urls


def download_clip(url, path):
    """Download a clip to `path`."""

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()

        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise RuntimeError("Downloaded clip is empty.")


# ============================================================
# HELPERS
# ============================================================

def _prepare_audio(audio_file, target_duration):
    """
    Make the narration file exactly `target_duration` seconds long.
    Shorter audio is padded with silence. Longer audio is trimmed
    with a short fade-out so it doesn't cut off with a click.
    The file is rewritten in place.
    """

    clip = VideoFileClip(path, audio=False)

    try:
        current = float(clip.duration)
    finally:
        clip.close()

    if abs(current - target_duration) < 0.05:
        return

    root, ext = os.path.splitext(audio_file)
    temp_file = f"{root}.fit{ext}"

    if current < target_duration:
        audio_filter = "apad"
    else:
        fade = min(0.4, target_duration / 4)
        audio_filter = (
            f"afade=t=out:st={max(target_duration - fade, 0):.3f}:d={fade:.3f}"
        )

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-i", audio_file,
        "-af", audio_filter,
        "-t", f"{target_duration:.3f}",
        temp_file,
    ]

    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0 or not os.path.exists(temp_file):
        raise RuntimeError(
            "ffmpeg could not adjust the audio length: "
            + (result.stderr or "")[-400:]
        )

    os.replace(temp_file, audio_file)


def _cover_resize_crop(clip, target_w, target_h):
    """Scale to cover the frame, then center-crop to the exact size."""

    scale = max(target_w / clip.w, target_h / clip.h)

    new_w = max(math.ceil(clip.w * scale), target_w)
    new_h = max(math.ceil(clip.h * scale), target_h)

    resized = clip.resized((new_w, new_h))

    return resized.cropped(
        x_center=new_w / 2,
        y_center=new_h / 2,
        width=target_w,
        height=target_h,
    )


def _fit_clip_to_duration(clip, duration):
    """Trim a clip that is too long, loop one that is too short."""

    if clip.duration > duration + 0.01:
        return clip.subclipped(0, duration)

    if clip.duration < duration - 0.01:
        return clip.with_effects([vfx.Loop(duration=duration)])

    return clip


def _find_caption_font():

    for path in CAPTION_FONT_CANDIDATES:
        if os.path.exists(path):
            return path

    return None


def _caption_segments(timeline, script_text, target_duration, words_per_chunk=4):
    """Return a list of (text, start, duration) caption chunks."""

    lines = []

    if timeline:
        for item in timeline:
            text = str(item.get("text", "")).strip()
            duration = float(item.get("duration", 0) or 0)

            if text and duration > 0:
                lines.append((text, duration))

    if not lines:
        text = (script_text or "").strip()

        if not text:
            return []

        lines = [(text, target_duration)]

    total = sum(duration for _, duration in lines)

    if total <= 0:
        return []

    scale = target_duration / total

    segments = []
    cursor = 0.0

    for text, duration in lines:

        duration *= scale
        words = text.split()

        if not words:
            cursor += duration
            continue

        chunks = [
            words[i:i + words_per_chunk]
            for i in range(0, len(words), words_per_chunk)
        ]

        for chunk in chunks:
            chunk_duration = duration * len(chunk) / len(words)

            segments.append((" ".join(chunk), cursor, chunk_duration))

            cursor += chunk_duration

    return segments


def _add_captions(
    video,
    target_duration,
    aspect_ratio,
    scene_plan=None,
    script_text="",
    timeline=None,
):

    width, height = RATIO_SIZES.get(aspect_ratio, (1080, 1920))

    segments = _caption_segments(timeline, script_text, target_duration)

    if not segments:
        return video

    font = _find_caption_font()
    font_size = max(int(min(width, height) * 0.07), 28)

    overlays = []

    for text, start, duration in segments:

        if duration <= 0.05:
            continue

        caption = TextClip(
            font=font,
            text=text,
            font_size=font_size,
            color="white",
            stroke_color="black",
            stroke_width=max(font_size // 14, 2),
            method="caption",
            size=(int(width * 0.86), None),
            margin=(0, 14),
            text_align="center",
        )

        caption = (
            caption
            .with_start(start)
            .with_duration(duration)
            .with_position(("center", int(height * 0.66)))
        )

        overlays.append(caption)

    if not overlays:
        return video

    return CompositeVideoClip(
        [video, *overlays],
        size=(width, height),
    ).with_duration(video.duration)


# ============================================================
# MAIN
# ============================================================

def build_video(
    audio_file,
    output_file,
    topic="",
    script_text="",
    timeline=None,
    video_style="viral",
    aspect_ratio="9:16",
    captions=True,
    target_duration=None
):

    if aspect_ratio not in RATIO_SIZES:
        raise ValueError(
            f"Unsupported aspect ratio: {aspect_ratio}"
        )

    target_w, target_h = RATIO_SIZES[aspect_ratio]

    # ---- narration length -------------------------------------------

    probe = AudioFileClip(audio_file)

    try:
        audio_duration = float(probe.duration)
    finally:
        probe.close()

    target_duration = float(target_duration or audio_duration)

    if target_duration <= 0:
        raise ValueError(
            "Video duration must be greater than zero."
        )

    _prepare_audio(audio_file, target_duration)

    os.makedirs(TEMP_CLIPS_DIR, exist_ok=True)

    out_dir = os.path.dirname(output_file)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    audio = None
    video = None
    final_video = None

    downloaded_paths = []
    raw_clips = []
    scene_clips = []

    try:

        # ========================================
        # BUILD SCENE PLAN
        # ========================================

        scene_plan = []

        if timeline:

            try:

                from scene_director import build_scene_plan

                scene_plan = build_scene_plan(
                    topic=topic,
                    script=script_text,
                    timeline=timeline,
                    style=video_style
                )

            except Exception as e:

                print("[video_builder] scene planner fallback:", e)

                for item in timeline:

                    scene_plan.append(
                        {
                            "query": item.get("text", topic),
                            "duration": item.get("duration", 3)
                        }
                    )

        if not scene_plan:

            scene_plan = [
                {
                    "query": topic or "abstract background",
                    "duration": target_duration
                }
            ]

        # ========================================
        # EXPAND LONG SCENES
        # ========================================

        expanded_plan = []

        for scene in scene_plan:

            duration = float(scene.get("duration", 3))

            if duration > 4:

                half = duration / 2

                expanded_plan.append({**scene, "duration": half})
                expanded_plan.append({**scene, "duration": half})

            else:

                expanded_plan.append({**scene, "duration": duration})

        scene_plan = expanded_plan

        # Stretch or shrink the plan so it covers the whole narration.
        plan_total = sum(float(s["duration"]) for s in scene_plan)

        if plan_total > 0:

            factor = target_duration / plan_total

            scene_plan = [
                {**s, "duration": float(s["duration"]) * factor}
                for s in scene_plan
            ]

        # ========================================
        # CREATE SCENE CLIPS
        # ========================================

        url_cache = {}
        used_urls = set()

        for scene in scene_plan:

            query = scene.get("query", topic)
            duration = float(scene["duration"])

            try:

                if query not in url_cache:
                    url_cache[query] = search_video_clips(
                        query,
                        count=5,
                        aspect_ratio=aspect_ratio
                    )

                clip_urls = url_cache[query]

                if not clip_urls:
                    continue

                # Prefer footage we haven't used yet, so scenes that
                # share a query don't show the same clip twice.
                fresh = [u for u in clip_urls if u not in used_urls]

                selected_url = random.choice(fresh or clip_urls)

                used_urls.add(selected_url)

                path = os.path.join(
                    TEMP_CLIPS_DIR,
                    f"{uuid.uuid4().hex}.mp4"
                )

                download_clip(selected_url, path)

                downloaded_paths.append(path)

                clip = VideoFileClip(path, audio=False)

                raw_clips.append(clip)

                clip = _cover_resize_crop(clip, target_w, target_h)

                clip = _fit_clip_to_duration(clip, duration)

                scene_clips.append(clip)

            except Exception as e:

                print("[video_builder] clip generation failed:", e)

                continue

        # ========================================
        # ASSEMBLE VIDEO
        # ========================================

        if not scene_clips:
            raise RuntimeError(
                "No valid video clips were generated. "
                "Check the stock footage API key and search results."
            )

        video = concatenate_videoclips(scene_clips, method="compose")

        # If some scenes failed, loop what we have to cover the narration.
        video = _fit_clip_to_duration(video, target_duration)

        if captions:

            try:

                video = _add_captions(
                    video,
                    target_duration=target_duration,
                    aspect_ratio=aspect_ratio,
                    scene_plan=scene_plan,
                    script_text=script_text,
                    timeline=timeline,
                )

            except Exception as e:

                print(
                    "[video_builder] captions skipped:",
                    e
                )

        # ========================================
        # ATTACH NARRATION AND WRITE
        # ========================================

        audio = AudioFileClip(audio_file)

        narration = audio.subclipped(
            0,
            min(float(audio.duration), float(video.duration))
        )

        final_video = video.with_audio(narration)

        final_video.write_videofile(
            output_file,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="veryfast",
            threads=4,
            logger=None,
        )

        print(f"[video_builder] saved final video: {output_file}")

    except Exception as e:

        print("[video_builder] build failed:", e)

        raise

    finally:

        for clip in (final_video, video, audio):
            try:
                if clip is not None:
                    clip.close()
            except Exception:
                pass

        for clip in scene_clips:
            try:
                clip.close()
            except Exception:
                pass

        for clip in raw_clips:
            try:
                clip.close()
            except Exception:
                pass

        for path in downloaded_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
