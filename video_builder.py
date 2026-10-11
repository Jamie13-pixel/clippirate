import os
import re
import tempfile
import textwrap
import uuid
from math import ceil

import requests
from moviepy.editor import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
)
from moviepy.video.fx.all import crop, loop
from PIL import Image, ImageDraw, ImageFont

ASPECT_SIZES = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "what", "why", "how",
    "who", "when", "where", "about", "into", "over", "your", "you", "are",
    "was", "were", "has", "have", "had", "his", "her", "its", "their", "best",
    "top", "most",
}

# Used only when the topic words return nothing from Pixabay.
FALLBACK_QUERIES = [
    q.strip()
    for q in os.getenv("PIXABAY_FALLBACK_QUERY", "cinematic background,abstract motion").split(",")
    if q.strip()
]

VIDEO_SIZE_ORDER = ("large", "medium", "small", "tiny")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def make_text_image(text, W, fontsize=65):
    """Render a caption to a transparent PNG with PIL (no ImageMagick needed)."""
    try:
        try:
            font = ImageFont.truetype(FONT_PATH, fontsize)
        except Exception:
            font = ImageFont.load_default()

        wrapped = textwrap.fill(text, width=22)

        scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bbox = scratch.multiline_textbbox((0, 0), wrapped, font=font, stroke_width=4, spacing=4, align="center")
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

        img = Image.new("RGBA", (tw + 20, th + 20), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.multiline_text(
            (10 - bbox[0], 10 - bbox[1]),
            wrapped,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=4,
            stroke_fill=(0, 0, 0, 255),
            align="center",
            spacing=4,
        )

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        img.save(tmp)
        return tmp, th + 20
    except Exception as e:
        print(f"text img err {e}")
        return None, 0


def build_queries(topic):
    """Topic-based search queries first, generic fallbacks last."""
    words = [
        w for w in re.findall(r"[A-Za-z0-9]+", topic or "")
        if len(w) > 2 and w.lower() not in STOPWORDS
    ]
    queries = []
    if words:
        queries.append(" ".join(words[:3]))
        queries.append(words[0])
        if len(words) > 1:
            queries.append(words[1])
    queries.extend(FALLBACK_QUERIES)

    seen, result = set(), []
    for q in queries:
        q = q[:100]
        if q.lower() not in seen:
            seen.add(q.lower())
            result.append(q)
    return result


def pick_video_url(hit):
    videos = hit.get("videos") or {}
    for size in VIDEO_SIZE_ORDER:
        url = (videos.get(size) or {}).get("url")
        if url:
            return url
    return None


def download_to_temp(url, suffix, timeout=30):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    path = os.path.join(tempfile.gettempdir(), f"pb-{uuid.uuid4().hex}{suffix}")
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def fit_cover(clip, W, H):
    """Scale to fully cover W x H (keeping aspect ratio), then center-crop."""
    scale = max(W / clip.w, H / clip.h)
    new_w = max(W, ceil(clip.w * scale))
    new_h = max(H, ceil(clip.h * scale))
    clip = clip.resize(newsize=(new_w, new_h))
    return crop(clip, x_center=new_w / 2, y_center=new_h / 2, width=W, height=H)


def fit_duration(clip, duration):
    """Loop short clips, trim long ones."""
    if clip.duration is None:
        return clip.set_duration(duration)
    if clip.duration < duration:
        return loop(clip, duration=duration)
    return clip.subclip(0, duration)


def fetch_background(key, queries, W, H, duration, temp_files):
    """Try Pixabay video, then Pixabay photo. Returns a clip or None."""
    if not key:
        print("No Pixabay key provided -> skipping stock footage")
        return None

    # 1) Video
    for q in queries:
        try:
            print(f"Trying Pixabay video: {q}")
            r = requests.get(
                "https://pixabay.com/api/videos/",
                params={"key": key, "q": q, "per_page": 5, "safesearch": "true"},
                timeout=15,
            )
            print(f"Video status {r.status_code}")
            if r.status_code != 200:
                continue
            for hit in r.json().get("hits", []):
                vurl = pick_video_url(hit)
                if not vurl:
                    continue
                path = download_to_temp(vurl, ".mp4", timeout=60)
                temp_files.append(path)
                clip = VideoFileClip(path).without_audio()
                clip = fit_duration(clip, duration)
                clip = fit_cover(clip, W, H)
                print("VIDEO FOUND!")
                return clip
        except Exception as e:
            print(f"video err {e}")

    # 2) Photo
    orientation = "vertical" if H > W else "horizontal"
    for q in queries:
        try:
            print(f"Trying Pixabay image: {q}")
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key": key,
                    "q": q,
                    "image_type": "photo",
                    "per_page": 5,
                    "orientation": orientation,
                    "safesearch": "true",
                },
                timeout=15,
            )
            print(f"Image status {r.status_code}")
            if r.status_code != 200:
                continue
            for hit in r.json().get("hits", []):
                iurl = hit.get("largeImageURL") or hit.get("webformatURL")
                if not iurl:
                    continue
                path = download_to_temp(iurl, ".jpg", timeout=30)
                temp_files.append(path)
                clip = ImageClip(path).set_duration(duration)
                clip = fit_cover(clip, W, H)
                print("IMAGE FOUND!")
                return clip
        except Exception as e:
            print(f"img err {e}")

    return None


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def build_video(
    audio_path=None,
    audio_file=None,
    output_path=None,
    output_file=None,
    topic="",
    script_text="",
    timeline=None,
    aspect_ratio="9:16",
    captions=True,
    **kwargs,
):
    audio_src = audio_file or audio_path
    out = output_file or output_path or os.path.join(tempfile.gettempdir(), "output.mp4")
    key = (kwargs.get("pixabay_key") or kwargs.get("pexels_key") or os.getenv("PIXABAY_API_KEY", "")).strip()
    print(f"Pixabay build: key={'YES' if key else 'NO'} topic={topic} key_len={len(key)}")

    W, H = ASPECT_SIZES.get(aspect_ratio, ASPECT_SIZES["9:16"])

    temp_files = []
    clips = []
    audio = AudioFileClip(audio_src)
    final = None
    try:
        duration = audio.duration

        bg = fetch_background(key, build_queries(topic), W, H, duration, temp_files)
        if bg is None:
            print("Pixabay returned nothing -> fallback solid colour (check PIXABAY_API_KEY)")
            bg = ColorClip(size=(W, H), color=(25, 25, 80), duration=duration)

        clips = [
            bg,
            ColorClip(size=(W, H), color=(0, 0, 0), duration=duration).set_opacity(0.35),
        ]

        if captions and timeline:
            for entry in timeline:
                try:
                    txt = entry.get("text", "")
                    if not txt:
                        continue
                    start = float(entry.get("start", 0))
                    end = float(entry.get("end", start + 2))
                    if end <= start:
                        continue
                    img_path, _ = make_text_image(txt, W)
                    if img_path:
                        temp_files.append(img_path)
                        ic = (
                            ImageClip(img_path)
                            .set_duration(end - start)
                            .set_start(start)
                            .set_position(("center", int(H * 0.72)))
                        )
                        clips.append(ic)
                except Exception as e:
                    print(f"cap err {e}")

        final = CompositeVideoClip(clips, size=(W, H)).set_audio(audio).set_duration(duration)
        temp_audio = os.path.join(tempfile.gettempdir(), f"tmp-audio-{uuid.uuid4().hex}.m4a")
        final.write_videofile(
            out,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=temp_audio,
            remove_temp=True,
            logger=None,
        )
        print(f"Saved {out}")
        return out
    finally:
        for c in [final, *clips, audio]:
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        for p in temp_files:
            try:
                os.remove(p)
            except OSError:
                pass
