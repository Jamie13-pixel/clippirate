from dotenv import load_dotenv
load_dotenv()

import os
import re
import uuid
import asyncio
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from tts import create_dialogue_voice
from video_builder import build_video
from script_generator import generate_script
from jobs import create_job, set_status, set_result, set_error, get_job, JobStatus
from rate_limiter import limiter, check_daily_limit, get_daily_usage, DAILY_GENERATION_LIMIT

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("data/videos", exist_ok=True)
os.makedirs("data/audio", exist_ok=True)
os.makedirs("data/temp_clips", exist_ok=True)
app.mount("/videos", StaticFiles(directory="data/videos"), name="videos")
app.mount("/app", StaticFiles(directory="static", html=True), name="static")


def clear_temp_clips():
    temp_dir = "data/temp_clips"
    if os.path.exists(temp_dir):
        for f in os.listdir(temp_dir):
            try:
                os.remove(os.path.join(temp_dir, f))
            except Exception:
                pass


clear_temp_clips()


class VideoRequest(BaseModel):
    topic: str


def safe_filename(topic: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", topic.strip()).strip("-").lower()
    return f"{slug or 'video'}-{uuid.uuid4().hex[:8]}"


@app.get("/")
def home():
    return {"message": "AI Video Generator Running"}


async def process_video_job(job_id: str, topic: str):
    set_status(job_id, JobStatus.PROCESSING)

    file_stem = safe_filename(topic)
    audio_file = f"data/audio/{file_stem}.mp3"
    video_file = f"data/videos/{file_stem}.mp4"

    try:
        script_text, dialogue = await asyncio.to_thread(generate_script, topic)

        timeline = await create_dialogue_voice(dialogue, audio_file)

        await asyncio.to_thread(build_video, audio_file, video_file, topic, timeline)

        set_result(job_id, {
            "success": True,
            "topic": topic,
            "script": script_text,
            "audio": audio_file,
            "video": video_file,
            "download_url": f"/videos/{file_stem}.mp4",
        })
    except Exception as e:
        traceback.print_exc()
        set_error(job_id, str(e))


@app.post("/generate")
@limiter.limit("5/minute")
async def generate_video(request: Request, body: VideoRequest):
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic cannot be empty.")

    if not check_daily_limit():
        raise HTTPException(
            status_code=429,
            detail=f"Daily generation limit of {DAILY_GENERATION_LIMIT} reached. Try again tomorrow.",
        )

    job_id = create_job()
    asyncio.create_task(process_video_job(job_id, topic))

    return {
        "job_id": job_id,
        "status": "pending",
        "check_status_url": f"/status/{job_id}",
    }

@app.get("/debug-fonts")
def debug_fonts():
    import glob
    matches = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    matches += glob.glob("/usr/local/share/fonts/**/*.ttf", recursive=True)
    return {"found_fonts": matches, "count": len(matches)}

@app.get("/status/{job_id}")
def check_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job["status"] == JobStatus.FAILED:
        return {"job_id": job_id, "status": job["status"], "error": job["error"]}

    if job["status"] == JobStatus.COMPLETED:
        return {"job_id": job_id, "status": job["status"], **job["result"]}

    return {"job_id": job_id, "status": job["status"]}


@app.get("/usage")
def usage():
    return {"used_today": get_daily_usage(), "daily_limit": DAILY_GENERATION_LIMIT}