import os
import re
import tempfile
import textwrap
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from math import ceil

import requests
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
)
from moviepy.video.fx import Crop, Loop
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
    "top", "most", "will", "would", "could", "should", "can", "just", "like",
    "get", "got", "one", "more", "than", "then", "they", "them", "there",
    "here", "because", "been", "being", "also", "even", "every", "never",
    "ever", "much", "many", "some", "any", "other", "still", "make", "made",
    "say", "said", "going", "thing", "things", "really", "very", "our", "out",
    "off", "but", "not", "all", "his", "she", "him", "own", "only", "while",
    "which", "these", "those", "does", "did", "doing", "after", "before",
    "again", "back", "come", "came", "know", "known", "take", "took", "want",
    "wants", "need", "needs", "seen", "see", "sees", "look", "looks", "let",
    "lets", "yet", "its", "were", "ago", "away", "around", "through", "each",
    "either", "neither", "whether", "though", "although", "without", "within",
    "might", "must", "shall", "ever", "once", "twice", "today", "now",
}

# Used only after the scene's own words (and the topic) found nothing.
FALLBACK_QUERIES = [
    q.strip()
    for q in os.getenv("PIXABAY_FALLBACK_QUERY", "cinematic background,abstract motion").split(",")
    if q.strip()
]

VIDEO_SIZE_ORDER = ("large", "medium", "small", "tiny")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# A scene is a run of sentences until it is at least this long (seconds).
SCENE_TARGET_SECONDS = float(os.getenv("SCENE_TARGET_SECONDS", "3.0"))
CAPTION_MAX_WORDS = int(os.getenv("CAPTION_MAX_WORDS", "4"))


# --------------------------------------------------------------------------
# Timeline -> timed segments
# --------------------------------------------------------------------------
TEXT_KEYS = ("text", "caption", "line", "sentence", "content")
START_KEYS = ("start", "start_time", "begin", "from", "t0", "offset")
END_KEYS = ("end", "end_time", "stop", "to", "t1")


def _num(v):
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_num(d, keys):
    for k in keys:
        n = _num(d.get(k))
        if n is not None:
            return n
    return None


def _parse_entry(entry):
    """Return (text, start, end, duration) from a dict or list/tuple entry."""
    text = start = end = dur = None
    if isinstance(entry, dict):
        for k in TEXT_KEYS:
            v = entry.get(k)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break
        start = _first_num(entry, START_KEYS)
        end = _first_num(entry, END_KEYS)
        dur = _num(entry.get("duration"))
    elif isinstance(entry, (list, tuple)):
        strs = [x for x in entry if isinstance(x, str) and x.strip()]
        nums = [n for n in (_num(x) for x in entry if not isinstance(x, str)) if n is not None]
        if strs:
            text = max(strs, key=len).strip()
        if len(nums) >= 2:
            start, end = nums[0], nums[1]
        elif len(nums) == 1:
            dur = nums[0]
    return text, start, end, dur


def normalize_timeline(timeline, duration):
    """Turn whatever the TTS step returned into [(text, start, end), ...] in seconds."""
    raw = []
    cursor = 0.0
    for entry in timeline or []:
        text, start, end, dur = _parse_entry(entry)
        if not text:
            continue
        if start is None:
            start = cursor
        if end is None and dur:
            end = start + dur
        raw.append([text, start, end])
        if end is not None:
            cursor = end

    if not raw:
        return []

    # Fill missing ends from the next start (or the audio length).
    for i, item in enumerate(raw):
        if item[2] is None:
            item[2] = raw[i + 1][1] if i + 1 < len(raw) else duration

    # Milliseconds instead of seconds?
    if max(item[2] for item in raw) > duration * 5:
        for item in raw:
            item[1] /= 1000.0
            item[2] /= 1000.0

    raw.sort(key=lambda x: x[1])
    segments = []
    prev_end = 0.0
    for text, start, end in raw:
        start = max(start, prev_end, 0.0)
        end = min(end, duration)
        if end - start < 0.15:
            continue
        segments.append((text, start, end))
        prev_end = end
    return segments


def segments_from_script(script_text, duration):
    """Fallback: split the script into sentences and spread them over the audio by length."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (script_text or "").strip()) if s.strip()]
    if not sentences:
        return []
    total = sum(len(s) for s in sentences) or 1
    segments, t = [], 0.0
    for s in sentences:
        d = duration * len(s) / total
        segments.append((s, t, t + d))
        t += d
    return segments


# --------------------------------------------------------------------------
# Scenes and search queries
# --------------------------------------------------------------------------
def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def hints_for_segments(segments, script_text, visual_hints):
    """One footage search phrase (or None) per timed sentence.

    `visual_hints` are the per-sentence phrases written together with the script.
    Sentences are matched by text; if that fails, by position.
    """
    hints = [h for h in (visual_hints or [])]
    if not hints or not segments:
        return [None] * len(segments)

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", (script_text or "").strip()) if s.strip()]
    by_text = {}
    if len(sentences) == len(hints):
        by_text = {_norm(s): h for s, h in zip(sentences, hints)}

    out = []
    for i, (text, _start, _end) in enumerate(segments):
        h = by_text.get(_norm(text))
        if not h:
            h = hints[min(int(i * len(hints) / len(segments)), len(hints) - 1)]
        out.append(h or None)
    return out


def build_scenes(segments, duration, target=SCENE_TARGET_SECONDS, seg_hints=None):
    """Group consecutive sentences into scenes of roughly `target` seconds.

    Returns [(text, start, end, hints)], contiguous and covering 0..duration.
    """
    if not seg_hints or len(seg_hints) != len(segments):
        seg_hints = [None] * len(segments)

    scenes = []
    cur_text, cur_hints, cur_start, cur_end = [], [], None, None
    for (text, s, e), hint in zip(segments, seg_hints):
        if cur_start is None:
            cur_start = s
        cur_text.append(text)
        if hint:
            cur_hints.append(hint)
        cur_end = e
        if cur_end - cur_start >= target:
            scenes.append([" ".join(cur_text), cur_start, cur_end, cur_hints])
            cur_text, cur_hints, cur_start = [], [], None
    if cur_text:
        if scenes and cur_end - cur_start < 2.0:
            scenes[-1][0] += " " + " ".join(cur_text)
            scenes[-1][2] = cur_end
            scenes[-1][3].extend(cur_hints)
        else:
            scenes.append([" ".join(cur_text), cur_start, cur_end, cur_hints])

    if not scenes:
        return [("", 0.0, duration, [])]

    scenes[0][1] = 0.0
    for i in range(len(scenes) - 1):
        scenes[i][2] = scenes[i + 1][1]
    scenes[-1][2] = duration
    return [(t, s, e, h) for t, s, e, h in scenes if e - s > 0.05]


def keywords(text, n=3):
    words = re.findall(r"[A-Za-z]{4,}", (text or "").lower())
    freq, order = {}, {}
    for i, w in enumerate(words):
        if w in STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
        order.setdefault(w, i)
    ranked = sorted(freq, key=lambda w: (-freq[w], -len(w), order[w]))
    return ranked[:n]


def topic_keywords(topic):
    return [
        w for w in re.findall(r"[A-Za-z0-9]+", topic or "")
        if len(w) > 2 and w.lower() not in STOPWORDS
    ]


def scene_queries(scene_text, topic_words, hints=None):
    qs = []
    # Phrases written for this scene together with the script come first,
    # each shortened step by step: "soccer stadium crowd" -> "stadium crowd" -> "crowd".
    for hint in hints or []:
        parts = hint.split()
        qs.append(hint)
        if len(parts) > 2:
            qs.append(" ".join(parts[-2:]))
        if len(parts) > 1:
            qs.append(parts[-1])

    kws = keywords(scene_text, 3)
    tw = " ".join(topic_words[:2])
    if len(kws) >= 2:
        qs.append(f"{kws[0]} {kws[1]}")
    if kws and tw:
        qs.append(f"{tw} {kws[0]}")
    qs.extend(kws[:2])
    if tw:
        qs.append(tw)
    qs.extend(FALLBACK_QUERIES)

    seen, result = set(), []
    for q in qs:
        q = q.strip()[:100]
        if q and q.lower() not in seen:
            seen.add(q.lower())
            result.append(q)
    return result


# --------------------------------------------------------------------------
# Captions
# --------------------------------------------------------------------------
def caption_chunks(segments, max_words=CAPTION_MAX_WORDS):
    """Split sentences into short on-screen chunks timed by character count."""
    out = []
    for text, start, end in segments:
        words = text.split()
        if not words:
            continue
        groups = [words[i:i + max_words] for i in range(0, len(words), max_words)]
        if len(groups) > 1 and len(groups[-1]) == 1:
            groups[-2].extend(groups.pop())
        total = sum(len(" ".join(g)) for g in groups) or 1
        t = start
        for g in groups:
            s = " ".join(g)
            d = (end - start) * len(s) / total
            out.append((s, t, t + d))
            t += d
    return out


def make_text_image(text, W, H):
    """Render a caption to a transparent PNG with PIL (no ImageMagick needed)."""
    try:
        fontsize = max(36, int(min(W, H) * 0.06))
        try:
            font = ImageFont.truetype(FONT_PATH, fontsize)
        except Exception:
            font = ImageFont.load_default()

        stroke = max(3, fontsize // 16)
        if not isinstance(font, ImageFont.FreeTypeFont):
            stroke = 0  # bitmap fallback font cannot draw strokes

        wrap_width = max(12, int(W * 0.8 / (fontsize * 0.62)))
        wrapped = textwrap.fill(text, width=wrap_width)

        scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bbox = scratch.multiline_textbbox(
            (0, 0), wrapped, font=font, stroke_width=stroke, spacing=4, align="center"
        )
        # Newer Pillow returns floats from textbbox; image sizes and positions must be ints.
        tw, th = int(ceil(bbox[2] - bbox[0])), int(ceil(bbox[3] - bbox[1]))

        img = Image.new("RGBA", (tw + 20, th + 20), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.multiline_text(
            (int(round(10 - bbox[0])), int(round(10 - bbox[1]))),
            wrapped,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke,
            stroke_fill=(0, 0, 0, 255),
            align="center",
            spacing=4,
        )

        tmp = os.path.join(tempfile.gettempdir(), f"cap-{uuid.uuid4().hex}.png")
        img.save(tmp)
        return tmp
    except Exception as e:
        print(f"text img err {e}")
        return None


# --------------------------------------------------------------------------
# Stock footage
# --------------------------------------------------------------------------
def pick_video_url(hit):
    videos = hit.get("videos") or {}

    best_url = None
    best_pixels = 0

    for data in videos.values():
        try:
            width = int(data.get("width", 0))
            height = int(data.get("height", 0))
            pixels = width * height

            if data.get("url") and pixels > best_pixels:
                best_pixels = pixels
                best_url = data["url"]
        except Exception:
            pass

    return best_url


def download_to_temp(url, suffix, timeout=30):
    r = requests.get(
        url,
        timeout=timeout,
        stream=True,
    )
    r.raise_for_status()
    path = os.path.join(tempfile.gettempdir(), f"pb-{uuid.uuid4().hex}{suffix}")
    with open(path, "wb") as f:
        f.write(r.content)
    return path


class PixabayClient:
    """Thread-safe Pixabay search/download that avoids reusing the same clip across scenes."""

    def __init__(self, key, temp_files):
        self.key = key
        self.temp_files = temp_files
        self._lock = threading.Lock()
        self._used = set()
        self._cache = {}

    def _search(self, kind, q, portrait=False):
        cache_key = (kind, q, portrait)
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        hits = []
        try:
            if kind == "video":
                r = requests.get(
                    "https://pixabay.com/api/videos/",
                    params={
                        "key": self.key,
                        "q": q,
                        "per_page": 50,
                        "safesearch": "true",
                        "video_type": "film",  # real footage, no vector animations
                        "min_height": 720,
                    },
                    timeout=15,
                )
            else:
                r = requests.get(
                    "https://pixabay.com/api/",
                    params={
                        "key": self.key,
                        "q": q,
                        "image_type": "photo",
                        "per_page": 50,
                        "orientation": "vertical" if portrait else "horizontal",
                        "safesearch": "true",
                    },
                    timeout=15,
                )
            if r.status_code == 200:
                hits = r.json().get("hits", [])
            else:
                print(f"Pixabay {kind} '{q}' -> HTTP {r.status_code}")
        except Exception as e:
            print(f"Pixabay {kind} '{q}' err {e}")
        with self._lock:
            self._cache[cache_key] = hits
        return hits

    def _claim(self, uid):
        with self._lock:
            if uid in self._used:
                return False
            self._used.add(uid)
            return True

    def fetch(self, queries, scene_dur, portrait):
        """Return ('video'|'image', path) or None."""
        for q in queries:
            hits = self._search("video", q, portrait)

            hits = sorted(hits, key=lambda h: 0 if (h.get("duration") or 0) >= scene_dur else 1)
            for h in hits:
                if not self._claim(("v", h.get("id"))):
                    continue
                url = pick_video_url(h)
                if not url:
                    continue
                try:
                    path = download_to_temp(url, ".mp4", timeout=60)
                    with self._lock:
                        self.temp_files.append(path)
                    print(f"  scene footage: video '{q}' id={h.get('id')}")
                    return ("video", path)
                except Exception as e:
                    print(f"  download err {e}")

        for q in queries:
            for h in self._search("image", q, portrait):
                if not self._claim(("i", h.get("id"))):
                    continue
                url = h.get("largeImageURL") or h.get("webformatURL")
                if not url:
                    continue
                try:
                    path = download_to_temp(url, ".jpg", timeout=30)
                    with self._lock:
                        self.temp_files.append(path)
                    print(f"  scene footage: image '{q}' id={h.get('id')}")
                    return ("image", path)
                except Exception as e:
                    print(f"  download err {e}")
        return None


def fit_cover(clip, W, H):
    """Scale to fully cover W x H (keeping aspect ratio), then center-crop."""
    scale = max(W / clip.w, H / clip.h)
    new_w = max(W, ceil(clip.w * scale))
    new_h = max(H, ceil(clip.h * scale))
    clip = clip.resized(new_size=(new_w, new_h))
    return clip.with_effects(
        [Crop(x_center=new_w / 2, y_center=new_h / 2, width=W, height=H)]
    )


def fit_duration(clip, duration):
    """Loop short clips, trim long ones."""
    if clip.duration is None:
        return clip.with_duration(duration)
    if clip.duration < duration:
        return clip.with_effects([Loop(duration=duration)])
    return clip.subclipped(0, duration)


def make_scene_clip(media, scene_dur, W, H, offset=0.0):
    kind, path = media
    if kind == "video":
        clip = VideoFileClip(path).without_audio()
        if offset and clip.duration and clip.duration > scene_dur + offset:
            clip = clip.subclipped(offset)
        clip = fit_duration(clip, scene_dur)
    else:
        clip = ImageClip(path).with_duration(scene_dur)
    return fit_cover(clip, W, H)


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
    visual_hints=None,
    **kwargs,
):
    audio_src = audio_file or audio_path
    out = output_file or output_path or os.path.join(tempfile.gettempdir(), "output.mp4")
    key = (kwargs.get("pixabay_key") or kwargs.get("pexels_key") or os.getenv("PIXABAY_API_KEY", "")).strip()
    print(f"Build: key={'YES' if key else 'NO'} topic={topic!r} captions={captions} ratio={aspect_ratio}")

    W, H = ASPECT_SIZES.get(aspect_ratio, ASPECT_SIZES["9:16"])

    temp_files = []
    clips = []
    audio = AudioFileClip(audio_src)
    final = None
    try:
        duration = audio.duration

        # 1) Timed sentences: from the TTS timeline, or from the script text if unusable
        print(f"Timeline sample: {(timeline or [])[:2]}")
        segments = normalize_timeline(timeline, duration)
        if not segments or segments[-1][2] < duration * 0.5:
            print("Timeline missing/unusable -> timing from script text")
            segments = segments_from_script(script_text, duration)
        print(f"{len(segments)} timed sentences")

        # 2) Scenes + one stock clip per scene
        print(f"Visual hints: {visual_hints}")
        seg_hints = hints_for_segments(segments, script_text, visual_hints)
        scenes = build_scenes(segments, duration, seg_hints=seg_hints)
        topic_words = topic_keywords(topic)
        jobs = [
            (scene_queries(text, topic_words, hints), end - start)
            for text, start, end, hints in scenes
        ]
        for i, (qs, _) in enumerate(jobs):
            print(f"Scene {i + 1}: {scenes[i][1]:.1f}-{scenes[i][2]:.1f}s queries={qs[:4]}")

        client = PixabayClient(key, temp_files)

        def safe_fetch(job):
            try:
                return client.fetch(job[0], job[1], H > W)
            except Exception as e:
                print(f"fetch err {e}")
                return None

        if key:
            with ThreadPoolExecutor(max_workers=6) as ex:
                results = list(ex.map(safe_fetch, jobs))
        else:
            print("No Pixabay key -> plain colour background")
            results = [None] * len(jobs)

        fallback = next((r for r in results if r), None)
        for i, ((text, start, end, _hints), media) in enumerate(zip(scenes, results)):
            scene_dur = end - start
            offset = 0.0
            if media is None and fallback is not None:
                media, offset = fallback, 2.0 * (i + 1)
            try:
                if media:
                    clip = make_scene_clip(media, scene_dur, W, H, offset)
                else:
                    clip = ColorClip(size=(W, H), color=(25, 25, 80), duration=scene_dur)
            except Exception as e:
                print(f"scene {i + 1} clip err {e}")
                clip = ColorClip(size=(W, H), color=(25, 25, 80), duration=scene_dur)
            clips.append(clip.with_start(start))

        clips.append(ColorClip(size=(W, H), color=(0, 0, 0), duration=duration).with_opacity(0.35))

        # 3) Captions
        n_caps = 0
        if captions:
            for text, start, end in caption_chunks(segments):
                if end - start < 0.1:
                    continue
                img_path = make_text_image(text, W, H)
                if not img_path:
                    continue
                temp_files.append(img_path)
                ic = ImageClip(img_path).with_duration(end - start).with_start(start)
                y = min(int(H * 0.68), H - ic.h - 60)
                clips.append(ic.with_position(("center", max(0, y))))
                n_caps += 1
        print(f"Captions added: {n_caps}")

        final = CompositeVideoClip(clips, size=(W, H)).with_audio(audio).with_duration(duration)
        temp_audio = os.path.join(tempfile.gettempdir(), f"tmp-audio-{uuid.uuid4().hex}.m4a")
        final.write_videofile(
            out,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            bitrate="5000k",
            preset="medium",
            threads=2,
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