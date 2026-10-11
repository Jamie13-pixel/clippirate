import modal, os, tempfile, sys
sys.path.append(".")
app = modal.App("clippirate")
image = (
    modal.Image.debian_slim()
    .apt_install("imagemagick")
    .env({"IMAGEMAGICK_BINARY": "/usr/bin/convert"})
    .pip_install("moviepy==1.0.3", "requests", "Pillow", "numpy")
    .add_local_dir(".", remote_path="/root")
)
@app.function(image=image, timeout=600, memory=2048)
def build_video_modal(audio_bytes: bytes, topic: str, script_text: str, timeline: list, aspect_ratio: str = "9:16", pexels_key: str = "", pixabay_key: str = ""):
    final_key = (pixabay_key or pexels_key or os.getenv("PIXABAY_API_KEY", "")).strip()
    os.environ["PIXABAY_API_KEY"] = final_key
    print(f"MODAL key_len={len(final_key)}")
    from video_builder import build_video
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as af:
        af.write(audio_bytes)
        audio_path = af.name
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
        output_path = vf.name
    result = build_video(audio_path=audio_path, output_path=output_path, topic=topic, script_text=script_text, timeline=timeline, aspect_ratio=aspect_ratio, pixabay_key=final_key)
    with open(result, "rb") as f:
        data = f.read()
    os.unlink(audio_path)
    return data