import glob
import textwrap
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_PATHS = [
    "arial.ttf",
]

SPEAKER_COLORS = {
    "HOST": "white",
    "GUEST": "#FFD54A",
}
DEFAULT_COLOR = "white"


def _load_font(size):
    for path in FONT_PATHS:
        try:
            font = ImageFont.truetype(path, size)
            print(f"[captions] Loaded font from FONT_PATHS: {path}")
            return font
        except Exception as e:
            print(f"[captions] Failed FONT_PATHS entry '{path}': {e}")
            continue

    search_dirs = ["/usr/share/fonts/", "/usr/local/share/fonts/"]
    all_matches = []
    for base in search_dirs:
        matches = glob.glob(f"{base}**/*.ttf", recursive=True)
        print(f"[captions] Searched {base} -> found {len(matches)} ttf files: {matches[:5]}")
        all_matches.extend(matches)

    if all_matches:
        try:
            font = ImageFont.truetype(all_matches[0], size)
            print(f"[captions] Loaded discovered font: {all_matches[0]}")
            return font
        except Exception as e:
            print(f"[captions] Failed to load discovered font {all_matches[0]}: {e}")

    print("[captions] WARNING: no TTF font found anywhere, falling back to default bitmap font")
    return ImageFont.load_default()


def render_caption_image(text, video_width=720, font_size=48, max_chars_per_line=22, color="white"):
    font = _load_font(font_size)
    wrapped_lines = textwrap.wrap(text, width=max_chars_per_line) or [text]

    dummy_img = Image.new("RGBA", (video_width, 10))
    draw = ImageDraw.Draw(dummy_img)

    line_heights = []
    line_widths = []
    for line in wrapped_lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=4)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    line_spacing = 12
    total_height = sum(line_heights) + line_spacing * (len(wrapped_lines) - 1) + 30

    img = Image.new("RGBA", (video_width, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = 15
    for line, lh, lw in zip(wrapped_lines, line_heights, line_widths):
        x = (video_width - lw) / 2
        draw.text((x, y), line, font=font, fill=color, stroke_width=4, stroke_fill="black")
        y += lh + line_spacing

    return np.array(img)


def split_line_into_chunks(text, words_per_chunk=8):
    words = text.split()
    if not words:
        return []
    return [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]


def build_caption_clips(timeline, video_width=720, video_height=1280):
    from moviepy import ImageClip

    clips = []
    for entry in timeline:
        speaker = entry["speaker"]
        text = entry["text"]
        line_start = entry["start"]
        line_duration = entry["duration"]
        color = SPEAKER_COLORS.get(speaker, DEFAULT_COLOR)

        chunks = split_line_into_chunks(text)
        if not chunks:
            continue

        word_counts = [len(c.split()) for c in chunks]
        total_words = sum(word_counts) or 1

        t = line_start
        for chunk, wc in zip(chunks, word_counts):
            chunk_duration = line_duration * (wc / total_words)
            img_array = render_caption_image(chunk, video_width=video_width, color=color)
            clip = (
                ImageClip(img_array)
                .with_duration(chunk_duration)
                .with_start(t)
                .with_position(("center", int(video_height * 0.72)))
            )
            clips.append(clip)
            t += chunk_duration

    return clips