from dotenv import load_dotenv

load_dotenv()

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import traceback
import uuid
import shutil
import time
from decimal import Decimal, ROUND_HALF_UP
from email_service import send_email
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote


import modal
import requests
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from tts import create_dialogue_voice
from templates_service import get_templates, get_template
from script_generator import generate_script
from script_quality_control import quality_check_script

from jobs import (
    create_job,
    set_status,
    set_result,
    set_error,
    get_job,
    JobStatus,
)

from rate_limiter import (
    limiter,
    check_daily_limit,
    get_daily_usage,
    DAILY_GENERATION_LIMIT,
)

from db import (
    expire_project_video,
    init_db,
    create_user,
    get_user_by_email,
    get_user,
    list_expired_videos,
    store_email_verification_token,
    _conn,
    verify_email_token,
    is_email_verified,
    create_session,
    get_user_by_session,
    delete_session,
    verify_password,
    reserve_credits,
    refund_credits,
    get_plan_config,
    reserve_generation,
    refund_generation,
    get_daily_generation_usage,
    get_generation_limit,
    get_remaining_generations,
    get_generation_summary,
    set_plan,
    create_payment,
    get_payment,
    get_payment_by_reference,
    complete_payment,
    fail_payment,
    create_project,
    update_project,
    list_projects,
    recover_stale_jobs,
    public_user,
    DB_PATH,
)

APP_NAME = "Clip Pirate.ai"
SESSION_COOKIE = "clip_pirate_session"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
# Persistent data (videos, audio, database). On Render, point DATA_DIR at the
# mount path of a persistent disk, e.g. /var/data. The database file itself
# is DB_PATH from db.py, which also lives under DATA_DIR.
DATA_DIR = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data")).resolve()

# Must match modal.App("...") in modal_app.py, and the app must be deployed:
#   modal deploy modal_app.py
MODAL_APP_NAME = os.getenv("MODAL_APP_NAME", "clippirate")
MODAL_FUNCTION_NAME = "build_video_modal"

app = FastAPI(title=APP_NAME)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY")
PAYSTACK_CALLBACK_URL = os.getenv(
    "PAYSTACK_CALLBACK_URL",
    "https://clippirate.onrender.com/payments/paystack/callback",
)
PAYSTACK_API = "https://api.paystack.co"
PAYSTACK_IS_LIVE = bool(PAYSTACK_SECRET_KEY and PAYSTACK_SECRET_KEY.startswith("sk_live_"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://clippirate.onrender.com").rstrip("/")
EMAIL_VERIFICATION_MINUTES = int(os.getenv("EMAIL_VERIFICATION_MINUTES", "30"))
VIDEO_RETENTION_DAYS_FREE = int(os.getenv("VIDEO_RETENTION_DAYS_FREE", "12"))
VIDEO_RETENTION_DAYS_PAID = int(os.getenv("VIDEO_RETENTION_DAYS_PAID", "30"))
MIN_FREE_DISK_MB = int(os.getenv("MIN_FREE_DISK_MB", "150"))


def report_payment_configuration():
    if not PAYSTACK_SECRET_KEY:
        print("[PAYMENTS] WARNING: PAYSTACK_SECRET_KEY is not set. Payments are disabled.", flush=True)
        return
    print(f"[PAYMENTS] Paystack mode: {'LIVE (real money)' if PAYSTACK_IS_LIVE else 'TEST'}", flush=True)
    if not PAYSTACK_IS_LIVE:
        return
    warnings = []
    if (
        "127.0.0.1" in PAYSTACK_CALLBACK_URL
        or "localhost" in PAYSTACK_CALLBACK_URL
        or not PAYSTACK_CALLBACK_URL.startswith("https://")
    ):
        warnings.append("PAYSTACK_CALLBACK_URL should be your public https:// address.")
    if not COOKIE_SECURE:
        warnings.append("COOKIE_SECURE is not 'true', so login cookies are not Secure.")
    if "*" in CORS_ORIGINS:
        warnings.append("CORS_ORIGINS contains '*'. List your own domain instead.")
    if os.getenv("ALLOW_DEV_UPGRADE", "false").lower() == "true":
        warnings.append("ALLOW_DEV_UPGRADE is 'true'. It is ignored in live mode, but remove it from your .env.")
    for warning in warnings:
        print(f"[PAYMENTS] WARNING: {warning}", flush=True)
    print("[EMAIL] WARNING: SMTP_HOST and SMTP_FROM_EMAIL must be configured for email verification.", flush=True)

def report_storage():
    """Log where data lives and how much is in it, so a wipe is obvious in the logs."""
    try:
        with _conn() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            paid = conn.execute("SELECT COUNT(*) FROM users WHERE plan != 'free'").fetchone()[0]
    except Exception as exc:
        users = paid = f"unknown ({exc})"
    print(f"[STORAGE] database={DB_PATH} data_dir={DATA_DIR} users={users} paid_users={paid}", flush=True)
    if os.getenv("RENDER", "").lower() == "true" and not os.getenv("DATA_DIR"):
        print(
            "[STORAGE] WARNING: DATA_DIR is not set. On Render the default filesystem is erased on "
            "every deploy, restart and spin-down, so users, credits, payments and videos will be "
            "lost. Attach a persistent disk and set DATA_DIR to its mount path.",
            flush=True,
        )

os.makedirs(DATA_DIR / "videos", exist_ok=True)
os.makedirs(DATA_DIR / "audio", exist_ok=True)
os.makedirs(DATA_DIR / "temp_clips", exist_ok=True)

app.mount("/videos", StaticFiles(directory=DATA_DIR / "videos"), name="videos")


@app.get("/download/{filename}")
def download_video(request: Request, filename: str):
    current_user(request)
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    if not safe_name.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Only MP4 video files can be downloaded.")
    video_path = DATA_DIR / "videos" / safe_name
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found.")
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=safe_name,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="static")

init_db()
report_storage()
print(f"[STARTUP] Recovered {recover_stale_jobs()} interrupted job(s).", flush=True)


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class VideoRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    duration: int = Field(default=30, ge=15, le=60)
    aspect_ratio: str = Field(default="4:3")
    voice: str = Field(default="professional")
    captions: bool = True
    template_id: str = Field(default="", max_length=60)
    narration_mode: str = "solo"


def current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    user = get_user_by_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    if not is_email_verified(user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "email_not_verified",
                "message": "Please verify your email before accessing Clip Pirate.",
            },
        )
    return user


def safe_filename(topic: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", topic.strip()).strip("-").lower()
    return f"{slug or 'video'}-{uuid.uuid4().hex[:8]}"


def credit_cost(duration: int) -> int:
    if duration <= 30:
        return 2
    if duration <= 45:
        return 4
    return 6


def normalize_ratio(ratio: str):
    allowed = {"4:3", "9:16", "16:9", "1:1"}
    if ratio not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported aspect ratio.")
    return ratio


def normalize_voice(voice: str):
    allowed = {"professional", "energetic", "calm"}
    if voice not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported voice.")
    return voice


@app.get("/health")
def health():
    return {"ok": True, "service": APP_NAME}


@app.get("/auth/health")
def auth_health():
    return {"ok": True, "service": "Clip Pirate.ai authentication"}


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"service": APP_NAME, "status": "running", "app": "/app"}


@app.get("/dashboard")
async def dashboard(request: Request):
    try:
        current_user(request)
    except HTTPException:
        return RedirectResponse(url="/app", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")



async def send_verification_email(email: str, raw_token: str) -> None:
    """Send a verification link through Brevo."""
    verify_url = (
        f"{APP_BASE_URL}/auth/verify?"
        f"token={quote(raw_token, safe='')}"
    )

    subject = f"Verify your {APP_NAME} email"

    text_content = (
        f"Welcome to {APP_NAME}!\n\n"
        "Verify your email by opening this link within "
        f"{EMAIL_VERIFICATION_MINUTES} minutes:\n"
        f"{verify_url}\n\n"
        "If you did not create this account, ignore this email."
    )

    html_content = f"""
    <!doctype html>
    <html>
      <body style="font-family:Arial,sans-serif;color:#172033">
        <h2>Verify your email</h2>
        <p>Welcome to {APP_NAME}.</p>
        <p>Confirm your email address to activate your account.</p>
        <p>
          <a href="{verify_url}"
             style="display:inline-block;padding:12px 18px;
                    background:#6d5dfc;color:#fff;
                    text-decoration:none;border-radius:8px">
            Verify email
          </a>
        </p>
        <p>
          This link expires in {EMAIL_VERIFICATION_MINUTES} minutes.
          If you did not sign up, ignore this email.
        </p>
      </body>
    </html>
    """

    await send_email(
        recipient_email=str(email).strip().lower(),
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )



async def create_and_send_verification(
    user_id: str,
    email: str,
) -> None:
    raw_token = secrets.token_urlsafe(32)

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=EMAIL_VERIFICATION_MINUTES)
    ).isoformat()

    store_email_verification_token(
        user_id,
        raw_token,
        expires_at,
    )

    await send_verification_email(email, raw_token)



@app.post("/auth/signup")
@limiter.limit("5/hour")
async def signup(request: Request, body: SignupRequest, response: Response):
    existing_user = get_user_by_email(body.email)
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already exists.")
    try:
        user_id = create_user(body.name, body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Email already exists.")

    try:
        await create_and_send_verification(
            user_id,
            str(body.email).strip().lower(),
        )
    except Exception as exc:
        # Account remains unverified; the user can retry via /auth/resend-verification
        # after SMTP is configured or temporarily unavailable delivery recovers.
        print(
            f"[EMAIL] Verification send failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        raise HTTPException(
            status_code=503,
            detail={
                "code": "verification_email_unavailable",
                "message": "Your account was created, but the verification email could not be sent. Please try resending the verification email shortly.",
            },
        ) from exc

    return {
        "ok": True,
        "email_verification_required": True,
        "message": "Account created. Check your email for the verification link before logging in.",
        "email": str(body.email).strip().lower(),
    }


@app.post("/auth/resend-verification")
@limiter.limit("3/hour")
async def resend_verification(request: Request, body: ResendVerificationRequest):
    # Same public response for unknown, already verified, and unverified addresses
    # to reduce account enumeration.
    generic = {
        "ok": True,
        "message": "If that address has an unverified account, a new verification email will be sent.",
    }
    user = get_user_by_email(body.email)
    if not user or is_email_verified(user):
        return generic
    try:
        await create_and_send_verification(
            user["id"],
            str(body.email).strip().lower(),
        )
    except Exception as exc:
        print(f"[EMAIL] Verification resend failed: {type(exc).__name__}", flush=True)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "verification_email_unavailable",
                "message": "Email delivery is temporarily unavailable. Please try again later.",
            },
        ) from exc
    return generic


@app.get("/auth/verify", response_class=HTMLResponse)
def verify_email_page(token: str = Query(default="", max_length=200)):
    # Do not verify on GET: email security scanners often pre-open links.
    # Require the person to click the confirmation button, which POSTs the token.
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", token or ""):
        return HTMLResponse(
            "<!doctype html><html><body style='font-family:Arial,sans-serif;padding:40px'>"
            "<h2>Verification link invalid</h2><p>Request a new verification email from Clip Pirate.</p>"
            "<a href='/app'>Return to Clip Pirate</a></body></html>",
            status_code=400,
        )
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Verify email</title></head>
    <body style='font-family:Arial,sans-serif;background:#f5f6fb;padding:40px;color:#172033'>
      <main style='max-width:480px;margin:8vh auto;background:white;padding:32px;border-radius:16px'>
        <h2>Verify your email</h2><p>Click below to confirm your email address and activate your Clip Pirate account.</p>
        <button id='verify' style='padding:12px 18px;background:#6d5dfc;color:white;border:0;border-radius:8px;cursor:pointer'>Confirm email</button>
        <p id='result' role='status'></p><a id='continue' href='/app' hidden>Continue to Clip Pirate</a>
      </main>
      <script>
        const token = {json.dumps(token)};
        document.getElementById('verify').addEventListener('click', async () => {{
          const button = document.getElementById('verify');
          const result = document.getElementById('result');
          button.disabled = true; result.textContent = 'Verifying…';
          try {{
            const response = await fetch('/auth/verify', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{token}})}});
            const data = await response.json();
            result.textContent = data.message || (response.ok ? 'Email verified.' : 'This link is invalid or expired.');
            if (response.ok) {{ document.getElementById('continue').hidden = false; button.hidden = true; }}
            else {{ button.disabled = false; }}
          }} catch (_) {{ result.textContent = 'Could not verify right now. Please try again.'; button.disabled = false; }}
        }});
      </script>
    </body></html>"""
    return HTMLResponse(page, status_code=200)


@app.post("/auth/verify")
def verify_email(payload: VerifyEmailRequest):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", payload.token or ""):
        raise HTTPException(status_code=400, detail={"code": "invalid_verification_token", "message": "This verification link is invalid or expired."})
    if not verify_email_token(payload.token):
        raise HTTPException(status_code=400, detail={"code": "invalid_verification_token", "message": "This verification link is invalid or expired."})
    return {"ok": True, "message": "Email verified successfully. You can now log in."}


@app.post("/auth/login")
def login(body: LoginRequest, response: Response):
    user = get_user_by_email(body.email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not is_email_verified(user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "email_not_verified",
                "message": "Please verify your email before logging in.",
                "email": user["email"],
            },
        )
    token = create_session(user["id"])
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=COOKIE_SECURE, max_age=30 * 86400, path="/",
    )
    return {"user": public_user(get_user(user["id"]))}

@app.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"user": public_user(user)}


@app.get("/credits")
def credits(request: Request):
    user = current_user(request)
    return {"user": public_user(user)}


@app.get("/projects")
def projects(request: Request):
    user = current_user(request)
    project_rows = list_projects(user["id"])
    return {"projects": [dict(row) for row in project_rows]}


def align_visual_hints(orig_sentences, visuals, final_sentences):
    """One footage search phrase per final sentence.

    Gemini writes a phrase for each sentence it generates. If quality control
    changed the script, the phrases are spread proportionally instead.
    """
    visuals = [v for v in (visuals or [])]
    if not visuals or not final_sentences:
        return []
    if len(visuals) == len(final_sentences) and [s.strip() for s in orig_sentences] == final_sentences:
        return visuals
    n = len(visuals)
    return [visuals[min(int(i * n / len(final_sentences)), n - 1)] for i in range(len(final_sentences))]


# Keep references to background tasks so they are not garbage-collected mid-run.
_background_tasks: set = set()

def _delete_old_files(folder: Path, max_age_seconds: int) -> int:
    cutoff = time.time() - max_age_seconds
    removed = 0
    for f in folder.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def cleanup_old_files():
    videos_removed = 0

    for row in list_expired_videos(VIDEO_RETENTION_DAYS_FREE, VIDEO_RETENTION_DAYS_PAID):
        path = DATA_DIR / "videos" / Path(str(row["video_url"])).name
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"[CLEANUP] Could not delete {path.name}: {exc}", flush=True)
            continue
        expire_project_video(row["video_url"])
        videos_removed += 1

    longest = max(VIDEO_RETENTION_DAYS_FREE, VIDEO_RETENTION_DAYS_PAID)
    stray_videos = _delete_old_files(DATA_DIR / "videos", (longest + 1) * 86400)
    audio = _delete_old_files(DATA_DIR / "audio", 86400)
    clips = _delete_old_files(DATA_DIR / "temp_clips", 86400)

    usage = shutil.disk_usage(DATA_DIR)
    print(
        f"[CLEANUP] expired_videos={videos_removed} stray_videos={stray_videos} "
        f"audio={audio} temp_clips={clips} "
        f"disk_used={usage.used // 2**20}MB free={usage.free // 2**20}MB",
        flush=True,
    )


async def cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(cleanup_old_files)
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(6 * 3600)


@app.on_event("startup")
async def start_cleanup():
    task = asyncio.create_task(cleanup_loop())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def process_video_job(job_id: str, user_id: str, topic: str, settings: dict, project_id: str):
    set_status(job_id, JobStatus.PROCESSING)
    file_stem = safe_filename(topic)
    audio_file = DATA_DIR / "audio" / f"{file_stem}.mp3"
    video_file = DATA_DIR / "videos" / f"{file_stem}.mp4"
    cost = settings["credit_cost"]
    try:
        print(f"[JOB {job_id}] Starting script generation...", flush=True)

        narration_mode = settings.get("narration_mode", "solo")
        dialogue_mode = narration_mode == "dialogue"

        script_text, orig_sentences, visuals, speakers = await asyncio.to_thread(
            generate_script,
            topic,
            settings["duration"],
            with_visuals=True,
            narration_mode=narration_mode,
            return_speakers=True,
            template=settings.get("template"),
        )
        if not script_text:
            raise RuntimeError("Script generator returned an empty script.")
        print(f"[JOB {job_id}] Script generated: {len(script_text.split())} words", flush=True)

        print(f"[JOB {job_id}] Running script quality control...", flush=True)
        qc_result = await asyncio.to_thread(quality_check_script, topic, script_text, settings["duration"])
        print(f"[JOB {job_id}] QC result: {qc_result}", flush=True)

        if not qc_result.get("approved", False):
            issues = qc_result.get("issues", ["Unknown script quality problem."])
            if not isinstance(issues, list):
                issues = [str(issues)]
            raise RuntimeError(
                "Generated script failed quality control: " + "; ".join(str(issue) for issue in issues)
            )

        script_text = qc_result.get("script", script_text)
        if not script_text.strip():
            raise RuntimeError("Quality control returned an empty script.")
        print(f"[JOB {job_id}] Approved script: {len(script_text.split())} words", flush=True)

        if dialogue_mode and script_text.strip() == " ".join(orig_sentences).strip():
            # QC left the script untouched, so Gemini's speaker labels still line up.
            dialogue = list(zip(speakers, orig_sentences))
        else:
            lines = [
                line.strip()
                for line in re.split(r"(?<=[.!?])\s+", script_text.strip())
                if line.strip()
            ]
            if dialogue_mode:
                # QC rewrote the script, so fall back to strict HOST/GUEST alternation.
                dialogue = [
                    ("HOST" if i % 2 == 0 else "GUEST", line)
                    for i, line in enumerate(lines)
                ]
            else:
                dialogue = [("HOST", line) for line in lines]

        if not dialogue:
            raise RuntimeError(
                "Quality-controlled script contains no dialogue."
            )

        print(
            f"[JOB {job_id}] Dialogue lines: {len(dialogue)}",
            flush=True
        )

        print(
            f"[JOB {job_id}] narration_mode={narration_mode}",
            flush=True
        )
        scene_hints = align_visual_hints(orig_sentences, visuals, [line for _, line in dialogue])
        print(f"[JOB {job_id}] Visual hints: {scene_hints}", flush=True)

        print(f"[JOB {job_id}] Starting TTS...", flush=True)
        timeline = await create_dialogue_voice(
            dialogue,
            str(audio_file),
            voice_profile=settings["voice"],
            target_duration=settings["duration"],
        )
        print(f"[JOB {job_id}] TTS completed: {audio_file}", flush=True)

        print(f"[JOB {job_id}] Sending to Modal.com...", flush=True)
        with open(audio_file, "rb") as f:
            audio_bytes = f.read()

        pixabay_key = os.getenv("PIXABAY_API_KEY", "").strip()
        print(f"[JOB {job_id}] Pixabay key present? {bool(pixabay_key)} (len={len(pixabay_key)})", flush=True)

        def run_modal_build() -> bytes:
            fn = modal.Function.from_name(MODAL_APP_NAME, MODAL_FUNCTION_NAME)
            return fn.remote(
                audio_bytes=audio_bytes,
                topic=topic,
                script_text=script_text,
                timeline=timeline,
                aspect_ratio=settings["aspect_ratio"],
                pixabay_key=pixabay_key,
                pexels_key=pixabay_key,
                captions=settings["captions"],
                visual_hints=scene_hints,
            )

        # .remote() blocks, so run it in a worker thread to keep the event loop free.
        video_bytes = await asyncio.to_thread(run_modal_build)

        with open(video_file, "wb") as f:
            f.write(video_bytes)

        print(f"[JOB {job_id}] Video completed: {video_file}", flush=True)

        if not video_file.exists():
            raise RuntimeError("Video builder completed but the MP4 file was not created.")
        if video_file.stat().st_size <= 0:
            raise RuntimeError("Video builder created an empty video file.")

        url = f"/videos/{file_stem}.mp4"
        set_result(
            job_id,
            {
                "success": True,
                "topic": topic,
                "script": script_text,
                "video": str(video_file),
                "download_url": url,
            },
        )
        update_project(project_id, user_id, status="completed", script=script_text, video_url=url)
        print(f"[JOB {job_id}] Job completed successfully.", flush=True)

    except Exception as exc:
        traceback.print_exc()
        error_message = str(exc)
        print(f"[JOB {job_id}] FAILED: {error_message}", flush=True)
        try:
            refund_credits(user_id, cost)
            refund_generation(user_id)
        except Exception:
            traceback.print_exc()
        try:
            set_error(job_id, error_message)
        except Exception:
            traceback.print_exc()
        try:
            update_project(project_id, user_id, status="failed", error=error_message)
        except Exception:
            traceback.print_exc()

    finally:
        try:
            audio_file.unlink(missing_ok=True)
        except OSError:
            pass


@app.post("/generate")
@limiter.limit("5/minute")
async def generate_video(request: Request, body: VideoRequest):
    user = current_user(request)
    if shutil.disk_usage(DATA_DIR).free < MIN_FREE_DISK_MB * 1024 * 1024:
        raise HTTPException(
            status_code=503,
            detail="Video storage is temporarily full. Please try again shortly.",
        )

    narration_mode = (body.narration_mode or "solo").strip().lower()

    if narration_mode not in ("solo", "dialogue"):
        raise HTTPException(
            status_code=422,
            detail="Unsupported narration mode."
        )

    if (
        narration_mode == "dialogue"
        and str(user["plan"]).lower() == "free"
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "upgrade_required",
                "message":
                    "Dialogue narration is available only on Starter, Pro and Mega plans.",
                "required_plan": "starter",
                "upgrade_required": True,
            },
        )
    
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic cannot be empty.")
    normalize_ratio(body.aspect_ratio)
    normalize_voice(body.voice)
    cost = credit_cost(body.duration)

    allowed, _ = reserve_generation(user["id"])
    if not allowed:
        summary = get_generation_summary(user["id"]) or {}
        raise HTTPException(
            status_code=429,
            detail={
                "code": "daily_limit_reached",
                "message": (
                    f"You have reached your daily limit of {summary.get('daily_limit', 0)} videos "
                    f"on the {summary.get('plan_name', 'current')} plan. "
                    "Upgrade your plan or try again tomorrow."
                ),
                "upgrade_required": True,
                "daily_limit": summary.get("daily_limit"),
                "used_today": summary.get("generated_today"),
            },
        )

    ok, refreshed_user = reserve_credits(user["id"], cost)
    if not ok:
        refund_generation(user["id"])
        raise HTTPException(
            status_code=402,
            detail={
                "code": "credits_exhausted",
                "message": "You do not have enough credits to generate this video. Upgrade your plan to continue.",
                "upgrade_required": True,
                "required_credits": cost,
                "available_credits": user["credits"],
            },
        )

    project_id = uuid.uuid4().hex
    try:
        create_project(
            project_id, user["id"], topic, body.duration,
            body.aspect_ratio, body.voice, body.captions, cost,
            narration_mode=narration_mode
        )
    except Exception:
        refund_credits(user["id"], cost)
        refund_generation(user["id"])
        raise

    job_id = create_job(user["id"], project_id)
    template = get_template(body.template_id) if body.template_id else None
    settings = {
        "duration": body.duration,
        "aspect_ratio": body.aspect_ratio,
        "voice": body.voice,
        "captions": body.captions,
        "credit_cost": cost,
        "template": template,
        "narration_mode": narration_mode,   # not body.narration_mode
    }
    task = asyncio.create_task(process_video_job(job_id, user["id"], topic, settings, project_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {
        "job_id": job_id,
        "project_id": project_id,
        "status": "pending",
        "credits": public_user(refreshed_user),
    }


@app.get("/status/{job_id}")
def check_status(request: Request, job_id: str):
    user = current_user(request)
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job["status"] == JobStatus.FAILED:
        return {"job_id": job_id, "status": job["status"], "error": job["error"]}
    if job["status"] == JobStatus.COMPLETED:
        return {"job_id": job_id, "status": job["status"], **job["result"]}
    return {"job_id": job_id, "status": job["status"]}


@app.get("/usage")
def usage(request: Request):
    user = current_user(request)
    summary = get_generation_summary(user["id"]) or {}
    narration_counts = {"solo": 0, "dialogue": 0}
    for row in list_projects(user["id"]):
        row = dict(row)
        if str(row.get("status", "")).lower() == "failed":
            continue
        mode = str(row.get("narration_mode") or "solo").lower()
        narration_counts[mode if mode in narration_counts else "solo"] += 1
    return {
        "user": public_user(user),
        "narration_counts": narration_counts,
        "plan": summary.get("plan"),
        "plan_name": summary.get("plan_name"),
        "daily_limit": summary.get("daily_limit"),
        "used_today": summary.get("generated_today"),
        "remaining_today": summary.get("remaining_today"),
        "platform_used_today": get_daily_usage(),
        "platform_daily_limit": DAILY_GENERATION_LIMIT,
    }


PLAN_CATALOG = [
    {"id": "free", "name": "Free", "credits": 6, "price_usd": 0, "billing": "free"},
    {"id": "starter", "name": "Starter", "credits": 150, "price_usd": 5, "billing": "monthly"},
    {"id": "pro", "name": "Pro", "credits": 300, "price_usd": 10, "billing": "monthly"},
    {"id": "mega", "name": "Mega", "credits": 600, "price_usd": 20, "billing": "monthly"},
]

FX_RATES = {
    "USD": 1.0,
    "KES": float(os.getenv("USD_TO_KES", "129.0")),
    "GHS": float(os.getenv("USD_TO_GHS", "12.0")),
    "ZAR": float(os.getenv("USD_TO_ZAR", "17.0")),
    "NGN": float(os.getenv("USD_TO_NGN", "1600.0")),
}

CURRENCY_META = {
    "USD": {"name": "US Dollar", "symbol": "$", "decimals": 2},
    "KES": {"name": "Kenyan Shilling", "symbol": "KSh", "decimals": 2},
    "GHS": {"name": "Ghanaian Cedi", "symbol": "GH₵", "decimals": 2},
    "ZAR": {"name": "South African Rand", "symbol": "R", "decimals": 2},
    "NGN": {"name": "Nigerian Naira", "symbol": "₦", "decimals": 2},
}

PAYMENT_DEFAULT_CURRENCY = os.getenv("PAYMENT_DEFAULT_CURRENCY", "USD").strip().upper()
PAYSTACK_ENABLED_CURRENCIES = {
    currency.strip().upper()
    for currency in os.getenv("PAYSTACK_ENABLED_CURRENCIES", "USD").split(",")
    if currency.strip()
}
PAYSTACK_SUPPORTED_CURRENCIES = set(CURRENCY_META)


def normalize_payment_currency(currency: str | None) -> str:
    currency = (currency or PAYMENT_DEFAULT_CURRENCY).strip().upper()
    if currency not in PAYSTACK_SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_currency",
                "message": f"Currency {currency} is not supported by Clip Pirate.",
                "supported_currencies": sorted(PAYSTACK_SUPPORTED_CURRENCIES),
            },
        )
    if currency not in PAYSTACK_ENABLED_CURRENCIES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "currency_not_enabled",
                "message": (
                    f"{currency} is not enabled for this Paystack merchant account. "
                    "Enable it in PAYSTACK_ENABLED_CURRENCIES only after Paystack "
                    "activates the currency for your account."
                ),
                "enabled_currencies": sorted(PAYSTACK_ENABLED_CURRENCIES),
            },
        )
    return currency


def convert_usd_to_currency(amount_usd: float, currency: str) -> float:
    currency = normalize_payment_currency(currency)
    rate = FX_RATES.get(currency)
    if rate is None or rate <= 0:
        raise HTTPException(status_code=500, detail=f"No valid exchange rate is configured for {currency}.")
    amount = Decimal(str(amount_usd)) * Decimal(str(rate))
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def paystack_amount_subunit(amount: float, currency: str) -> int:
    normalize_payment_currency(currency)
    return int((Decimal(str(amount)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def build_plan_prices(currency: str) -> list[dict]:
    currency = normalize_payment_currency(currency)
    meta = CURRENCY_META[currency]
    result = []
    for plan in PLAN_CATALOG:
        price = convert_usd_to_currency(plan["price_usd"], currency)
        result.append(
            {
                **plan,
                "price": price,
                "currency": currency,
                "currency_symbol": meta["symbol"],
                "currency_name": meta["name"],
            }
        )
    return result


@app.get("/plans")
def plans(currency: str = Query(default=PAYMENT_DEFAULT_CURRENCY, min_length=3, max_length=3)):
    currency = normalize_payment_currency(currency)
    return {
        "plans": build_plan_prices(currency),
        "currency": currency,
        "currency_name": CURRENCY_META[currency]["name"],
        "currency_symbol": CURRENCY_META[currency]["symbol"],
        "enabled_currencies": sorted(PAYSTACK_ENABLED_CURRENCIES),
        "default_currency": PAYMENT_DEFAULT_CURRENCY,
    }


@app.get("/payments/currencies")
def payment_currencies():
    return {
        "default_currency": PAYMENT_DEFAULT_CURRENCY,
        "enabled_currencies": [
            {"code": currency, **CURRENCY_META[currency]}
            for currency in sorted(PAYSTACK_ENABLED_CURRENCIES)
            if currency in CURRENCY_META
        ],
    }


@app.get("/templates")
def list_templates():
    return get_templates()


@app.post("/subscription/dev-upgrade/{plan}")
def dev_upgrade(plan: str, request: Request):
    if PAYSTACK_IS_LIVE or os.getenv("ALLOW_DEV_UPGRADE", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found.")
    plan = plan.lower()
    if plan not in ("free", "starter", "pro", "mega"):
        raise HTTPException(status_code=400, detail="Invalid subscription plan.")
    user = current_user(request)
    updated = set_plan(user["id"], plan)
    return {"user": public_user(updated)}


PLAN_PRICES_USD = {plan["id"]: plan["price_usd"] for plan in PLAN_CATALOG if plan["price_usd"] > 0}


@app.post("/payments/paystack/initialize")
@limiter.limit("10/minute")
def initialize_paystack_payment(request: Request, payload: dict):
    user = current_user(request)
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments are not configured.")

    plan = str(payload.get("plan", "")).lower().strip()
    if plan not in PLAN_PRICES_USD:
        raise HTTPException(status_code=400, detail="Invalid subscription plan.")

    currency = normalize_payment_currency(str(payload.get("currency") or PAYMENT_DEFAULT_CURRENCY))
    amount_usd = PLAN_PRICES_USD[plan]
    amount = convert_usd_to_currency(amount_usd, currency)
    amount_subunit = paystack_amount_subunit(amount, currency)

    user = dict(user)
    email = user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="A valid account email is required for payment.")

    payment_id = create_payment(
        user_id=user["id"], plan=plan, amount=amount, provider="paystack", currency=currency
    )

    try:
        response = requests.post(
            f"{PAYSTACK_API}/transaction/initialize",
            headers={
                "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "email": email,
                "amount": amount_subunit,
                "currency": currency,
                "callback_url": PAYSTACK_CALLBACK_URL,
                "metadata": {
                    "payment_id": payment_id,
                    "user_id": user["id"],
                    "plan": plan,
                    "currency": currency,
                    "amount": amount,
                    "amount_usd": amount_usd,
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        fail_payment(payment_id)
        raise HTTPException(status_code=502, detail="Unable to connect to Paystack.")

    if not data.get("status") or not data.get("data"):
        fail_payment(payment_id)
        raise HTTPException(status_code=502, detail="Paystack could not initialize the transaction.")

    paystack_data = data["data"]
    reference = paystack_data.get("reference")
    authorization_url = paystack_data.get("authorization_url")
    access_code = paystack_data.get("access_code")
    if not reference or not access_code:
        fail_payment(payment_id)
        raise HTTPException(status_code=502, detail="Paystack returned an incomplete transaction.")

    with _conn() as conn:
        conn.execute(
            "UPDATE payments SET provider_reference = ?, updated_at = datetime('now') WHERE id = ?",
            (reference, payment_id),
        )

    return {
        "payment_id": payment_id,
        "reference": reference,
        "access_code": access_code,
        "authorization_url": authorization_url,
        "amount": amount,
        "amount_usd": amount_usd,
        "amount_subunit": amount_subunit,
        "currency": currency,
        "currency_symbol": CURRENCY_META[currency]["symbol"],
        "plan": plan,
    }


class PaymentMismatch(Exception):
    pass


_payment_lock = threading.Lock()


def fetch_paystack_transaction(reference: str) -> dict:
    response = requests.get(
        f"{PAYSTACK_API}/transaction/verify/{quote(reference, safe='')}",
        headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("status") or not data.get("data"):
        raise ValueError("Paystack returned an invalid verification response.")
    return data["data"]


def finalize_paystack_payment(payment, transaction: dict, reference: str):
    expected_amount = paystack_amount_subunit(float(payment["amount"]), str(payment["currency"]))
    actual_amount = int(transaction.get("amount") or 0)
    actual_currency = str(transaction.get("currency") or "").upper()

    if actual_amount != expected_amount:
        fail_payment(payment["id"])
        raise PaymentMismatch("Payment amount does not match the selected plan.")

    expected_currency = str(payment["currency"]).upper()
    if actual_currency != expected_currency:
        fail_payment(payment["id"])
        raise PaymentMismatch(f"Expected {expected_currency}, got {actual_currency}")

    with _payment_lock:
        latest = get_payment_by_reference("paystack", reference)
        if latest is not None and latest["status"] == "paid":
            return latest
        return complete_payment(payment["id"], provider_reference=reference)


@app.get("/payments/paystack/verify/{reference}")
def verify_paystack_payment(reference: str, request: Request):
    user = current_user(request)
    reference = reference.strip()
    if not reference:
        raise HTTPException(status_code=400, detail="Payment reference is required.")

    payment = get_payment_by_reference("paystack", reference)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment record not found.")
    if payment["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="This payment does not belong to this account.")

    if payment["status"] == "paid":
        return {"status": "paid", "payment": dict(payment), "user": public_user(get_user(user["id"]))}

    try:
        transaction = fetch_paystack_transaction(reference)
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=502, detail="Unable to verify the payment with Paystack.")

    transaction_status = str(transaction.get("status", "")).lower()
    if transaction_status != "success":
        return {"status": transaction_status or "pending", "message": "Payment has not been completed."}

    try:
        completed = finalize_paystack_payment(payment, transaction, reference)
    except PaymentMismatch as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "status": "paid",
        "payment": dict(completed),
        "user": public_user(get_user(payment["user_id"])),
    }


def process_paystack_webhook(event: dict):
    if event.get("event") != "charge.success":
        return {"ok": True, "ignored": True}

    data = event.get("data") or {}
    reference = str(data.get("reference") or "").strip()
    if not reference:
        return {"ok": True, "ignored": True}

    payment = get_payment_by_reference("paystack", reference)
    if payment is None:
        return {"ok": True, "ignored": True}
    if payment["status"] == "paid":
        return {"ok": True, "already_paid": True}

    try:
        transaction = fetch_paystack_transaction(reference)
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=502, detail="Could not verify the payment with Paystack.")

    if str(transaction.get("status", "")).lower() != "success":
        return {"ok": True, "ignored": True}

    try:
        finalize_paystack_payment(payment, transaction, reference)
    except PaymentMismatch as exc:
        print(f"[PAYSTACK WEBHOOK] {reference}: {exc}", flush=True)
        return {"ok": True, "mismatch": True}

    return {"ok": True}


@app.post("/payments/paystack/webhook")
async def paystack_webhook(request: Request):
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments are not configured.")

    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    expected = hmac.new(PAYSTACK_SECRET_KEY.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected.encode("utf-8"), signature.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid signature.")

    try:
        event = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid event.")

    return await asyncio.to_thread(process_paystack_webhook, event)


@app.get("/payments/paystack/callback")
def paystack_callback(reference: str | None = None):
    if not reference:
        raise HTTPException(status_code=400, detail="Missing Paystack payment reference.")
    return RedirectResponse(url=f"/app/?payment=paystack&reference={quote(reference, safe='')}")