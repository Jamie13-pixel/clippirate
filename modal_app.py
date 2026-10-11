import os
import tempfile

import modal

# NOTE: main.py must look this app up with the SAME name:
#   modal.Function.from_name("clippirate", "build_video_modal")
app = modal.App("clippirate")

image = (
    # Pin Python 3.11: Modal's default is now 3.14, which has no prebuilt Pillow 9.5.0 / numpy<2 wheels
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("imagemagick", "fonts-dejavu-core")  # fonts needed by the PIL captions
    .env({"IMAGEMAGICK_BINARY": "/usr/bin/convert"})
    # moviepy 1.0.3 needs Pillow < 10 (Image.ANTIALIAS was removed) and numpy < 2
    .pip_install("moviepy==1.0.3", "requests", "Pillow==9.5.0", "numpy<2")
    # Ship only video_builder.py, not the whole project (.env, database, etc.)
    .add_local_python_source("video_builder")
)


@app.function(image=image, timeout=600, memory=2048)
def build_video_modal(
    audio_bytes: bytes,
    topic: str,
    script_text: str,
    timeline: list,
    aspect_ratio: str = "9:16",
    pexels_key: str = "",
    pixabay_key: str = "",
    captions: bool = True,
):
    final_key = (pixabay_key or pexels_key or os.getenv("PIXABAY_API_KEY", "")).strip()
    os.environ["PIXABAY_API_KEY"] = final_key
    print(f"MODAL key_len={len(final_key)}")

    from video_builder import build_video

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as af:
        af.write(audio_bytes)
        audio_path = af.name

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
        output_path = vf.name

    try:
        result = build_video(
            audio_path=audio_path,
            output_path=output_path,
            topic=topic,
            script_text=script_text,
            timeline=timeline,
            aspect_ratio=aspect_ratio,
            captions=captions,
            pixabay_key=final_key,
        )
        with open(result, "rb") as f:
            return f.read()
    finally:
        for p in (audio_path, output_path):
            try:
                os.unlink(p)
            except OSError:
                pass
