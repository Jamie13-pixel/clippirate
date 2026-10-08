from dotenv import load_dotenv
load_dotenv()
import modal
import requests
import asyncio
import hashlib
import hmac
import json
import os
import re
import threading
import traceback
import uuid
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from tts import create_dialogue_voice
from templates_service import get_templates, get_template
from video_builder import build_video
from script_generator import generate_script
from script_quality_control import quality_check_script

from modal_app import build_video_modal

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
    init_db,
    create_user,
    get_user_by_email,
    get_user,
    create_session,
    get_user_by_session,
    delete_session,
    verify_password,

    # Legacy credit system
    reserve_credits,
    refund_credits,

    # Subscription system
    get_plan_config,
    reserve_generation,
    refund_generation,
    get_daily_generation_usage,
    get_generation_limit,
    get_remaining_generations,
    get_generation_summary,

    set_plan,

    # Payments
    create_payment,
    get_payment,
    get_payment_by_reference,
    complete_payment,
    fail_payment,

    # Projects
    create_project,
    update_project,
    list_projects,

    # Public user
    public_user,
)


# ============================================================
# APP CONFIGURATION
# ============================================================

APP_NAME = "Clip Pirate .ai"
SESSION_COOKIE = "clip_pirate_session"

# IMPORTANT:
# Use __file__ exactly as written.
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title=APP_NAME)

app.state.limiter = limiter

app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler
)

# The app and the API are served from the same origin (/app), so normal
# use does not need CORS at all. Allowing every origin together with
# cookies would let any website make logged-in requests on a user's
# behalf, so only the origins listed here are allowed. Add more in
# CORS_ORIGINS (comma-separated) only if the frontend is hosted elsewhere.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000"
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
    "http://clippirate.onrender.com./payments/paystack/callback"
)

PAYSTACK_API = "https://api.paystack.co"

# True only when a live secret key (sk_live_...) is configured.
PAYSTACK_IS_LIVE = bool(
    PAYSTACK_SECRET_KEY and PAYSTACK_SECRET_KEY.startswith("sk_live_")
)

# Session cookies are only marked Secure when COOKIE_SECURE=true.
# Turn it on in production, where the site is served over HTTPS.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


def report_payment_configuration():

    """Print the payment mode at startup and warn about unsafe live setups."""

    if not PAYSTACK_SECRET_KEY:

        print(
            "[PAYMENTS] WARNING: PAYSTACK_SECRET_KEY is not set. "
            "Payments are disabled.",
            flush=True
        )

        return

    print(
        f"[PAYMENTS] Paystack mode: "
        f"{'LIVE (real money)' if PAYSTACK_IS_LIVE else 'TEST'}",
        flush=True
    )

    if not PAYSTACK_IS_LIVE:
        return

    warnings = []

    if (
        "127.0.0.1" in PAYSTACK_CALLBACK_URL
        or "localhost" in PAYSTACK_CALLBACK_URL
        or not PAYSTACK_CALLBACK_URL.startswith("https://")
    ):
        warnings.append(
            "PAYSTACK_CALLBACK_URL should be your public https:// address."
        )

    if not COOKIE_SECURE:
        warnings.append(
            "COOKIE_SECURE is not 'true', so login cookies are not Secure."
        )

    if "*" in CORS_ORIGINS:
        warnings.append(
            "CORS_ORIGINS contains '*'. List your own domain instead."
        )

    if os.getenv("ALLOW_DEV_UPGRADE", "false").lower() == "true":
        warnings.append(
            "ALLOW_DEV_UPGRADE is 'true'. It is ignored in live mode, "
            "but remove it from your .env."
        )

    for warning in warnings:

        print(
            f"[PAYMENTS] WARNING: {warning}",
            flush=True
        )


report_payment_configuration()


# ============================================================
# DIRECTORIES
# ============================================================

os.makedirs(
    BASE_DIR / "data" / "videos",
    exist_ok=True
)

os.makedirs(
    BASE_DIR / "data" / "audio",
    exist_ok=True
)

os.makedirs(
    BASE_DIR / "data" / "temp_clips",
    exist_ok=True
)


# ============================================================
# STATIC VIDEO FILES
# ============================================================

app.mount(
    "/videos",
    StaticFiles(
        directory=BASE_DIR / "data" / "videos"
    ),
    name="videos",
)


# ============================================================
# VIDEO DOWNLOAD
# ============================================================

@app.get("/download/{filename}")
def download_video(
    request: Request,
    filename: str
):
    """
    Download a generated video without navigating
    the browser to the video URL.

    Only authenticated users can download videos.
    """

    current_user(request)

    # Prevent path traversal.
    safe_name = Path(filename).name

    if safe_name != filename:
        raise HTTPException(
            status_code=400,
            detail="Invalid filename."
        )

    if not safe_name.lower().endswith(".mp4"):
        raise HTTPException(
            status_code=400,
            detail="Only MP4 video files can be downloaded."
        )

    video_path = (
        BASE_DIR
        / "data"
        / "videos"
        / safe_name
    )

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Video not found."
        )

    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=safe_name,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_name}"'
            )
        }
    )


# ============================================================
# FRONTEND
# ============================================================

# /app is the public ClipPirate entry point.

app.mount(
    "/app",
    StaticFiles(
        directory=STATIC_DIR,
        html=True
    ),
    name="static",
)


# ============================================================
# DATABASE
# ============================================================

init_db()


# ============================================================
# REQUEST MODELS
# ============================================================

class SignupRequest(BaseModel):

    name: str = Field(
        min_length=2,
        max_length=80
    )

    email: EmailStr

    password: str = Field(
        min_length=8,
        max_length=128
    )


class LoginRequest(BaseModel):

    email: EmailStr
    password: str


class VideoRequest(BaseModel):

    topic: str = Field(
        min_length=1,
        max_length=300
    )

    duration: int = Field(
        default=30,
        ge=15,
        le=60
    )

    aspect_ratio: str = Field(
        default="4:3"
    )

    voice: str = Field(
        default="professional"
    )

    captions: bool = True

    # Optional: id of a template from GET /templates. The server looks up
    # the template itself (its script/footage/voice instructions are private).
    template_id: str = Field(
        default="",
        max_length=60
    )


# ============================================================
# AUTHENTICATION HELPERS
# ============================================================

def current_user(request: Request):

    """
    Return the currently logged-in user.

    Raises 401 when there is no valid session.
    """

    token = request.cookies.get(
        SESSION_COOKIE
    )

    if not token:

        raise HTTPException(
            status_code=401,
            detail="Please log in to continue."
        )

    user = get_user_by_session(
        token
    )

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Please log in to continue."
        )

    return user


# ============================================================
# FILE / VIDEO HELPERS
# ============================================================

def safe_filename(topic: str) -> str:

    slug = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "-",
        topic.strip()
    ).strip("-").lower()

    return (
        f"{slug or 'video'}-"
        f"{uuid.uuid4().hex[:8]}"
    )


def credit_cost(duration: int) -> int:

    if duration <= 30:
        return 2

    if duration <= 45:
        return 4

    return 6

def normalize_ratio(ratio: str):

    allowed = {
        "4:3",
        "9:16",
        "16:9",
        "1:1",
    }

    if ratio not in allowed:

        raise HTTPException(
            status_code=422,
            detail="Unsupported aspect ratio."
        )

    return ratio


def normalize_voice(voice: str):

    allowed = {
        "professional",
        "energetic",
        "calm",
    }

    if voice not in allowed:

        raise HTTPException(
            status_code=422,
            detail="Unsupported voice."
        )

    return voice


# ============================================================
# BASIC API HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "ok": True,
        "service": APP_NAME
    }


@app.get("/auth/health")
def auth_health():

    return {
        "ok": True,
        "service": "Clip Pirate .ai authentication"
    }


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

@app.api_route("/", methods=["GET", "HEAD"])async def root():
    return {
        "service": APP_NAME,
        "status": "running",
        "app": "/app"
    }

# ============================================================
# DASHBOARD
# ============================================================

@app.get("/dashboard")
async def dashboard(
    request: Request
):

    """
    Dashboard is protected.

    Logged-in users:
        /dashboard -> dashboard

    Logged-out users:
        /dashboard -> /app
    """

    try:

        current_user(request)

    except HTTPException:

        return RedirectResponse(
            url="/app",
            status_code=303
        )

    return FileResponse(
        STATIC_DIR / "index.html"
    )


# ============================================================
# AUTHENTICATION
# ============================================================

@app.post("/auth/signup")
def signup(
    body: SignupRequest,
    response: Response
):

    """
    Create a new account.
    """

    existing_user = get_user_by_email(
        body.email
    )

    if existing_user:

        raise HTTPException(
            status_code=409,
            detail="Email already exists."
        )

    try:

        user_id = create_user(
            body.name,
            body.email,
            body.password
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=409,
            detail=str(exc)
        ) from exc

    except sqlite3.IntegrityError:

        raise HTTPException(
            status_code=409,
            detail="Email already exists."
        )

    token = create_session(
        user_id
    )

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=30 * 86400,
        path="/",
    )

    user = get_user(
        user_id
    )

    return {
        "user": public_user(user)
    }


@app.post("/auth/login")
def login(
    body: LoginRequest,
    response: Response
):

    user = get_user_by_email(
        body.email
    )

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    if not verify_password(
        body.password,
        user["password_hash"]
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    token = create_session(
        user["id"]
    )

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=30 * 86400,
        path="/",
    )

    return {
        "user": public_user(
            get_user(user["id"])
        )
    }


# ============================================================
# AUTH COMPATIBILITY ALIASES
# ============================================================

@app.post("/signup")
def signup_alias(
    body: SignupRequest,
    response: Response
):

    return signup(
        body,
        response
    )


@app.post("/login")
def login_alias(
    body: LoginRequest,
    response: Response
):

    return login(
        body,
        response
    )


# ============================================================
# LOGOUT
# ============================================================

@app.post("/auth/logout")
def logout(
    request: Request,
    response: Response
):

    token = request.cookies.get(
        SESSION_COOKIE
    )

    if token:

        delete_session(
            token
        )

    response.delete_cookie(
        SESSION_COOKIE,
        path="/"
    )

    return {
        "ok": True
    }


# ============================================================
# CURRENT USER
# ============================================================

@app.get("/auth/me")
def me(
    request: Request
):

    user = current_user(
        request
    )

    return {
        "user": public_user(user)
    }


# ============================================================
# CREDITS
# ============================================================

@app.get("/credits")
def credits(
    request: Request
):

    user = current_user(
        request
    )

    return {
        "user": public_user(user)
    }


# ============================================================
# PROJECTS API
# ============================================================

@app.get("/projects")
def projects(
    request: Request
):

    """
    Retrieve projects belonging to the
    currently logged-in user.
    """

    user = current_user(
        request
    )

    project_rows = list_projects(
        user["id"]
    )

    return {
        "projects": [
            dict(row)
            for row in project_rows
        ]
    }


# ============================================================
# VIDEO GENERATION BACKGROUND JOB
# ============================================================

async def process_video_job(
    job_id: str,
    user_id: str,
    topic: str,
    settings: dict,
    project_id: str
):

    # --------------------------------------------------------
    # MARK JOB AS PROCESSING
    # --------------------------------------------------------

    set_status(
        job_id,
        JobStatus.PROCESSING
    )

    file_stem = safe_filename(
        topic
    )

    audio_file = (
        BASE_DIR
        / "data"
        / "audio"
        / f"{file_stem}.mp3"
    )

    video_file = (
        BASE_DIR
        / "data"
        / "videos"
        / f"{file_stem}.mp4"
    )

    cost = settings[
        "credit_cost"
    ]

    try:

        # ====================================================
        # SCRIPT GENERATION
        # ====================================================

        print(
            f"[JOB {job_id}] Starting script generation...",
            flush=True
        )

        script_text, dialogue = await asyncio.to_thread(
            generate_script,
            topic,
            settings["duration"]
        )

        if not script_text:

            raise RuntimeError(
                "Script generator returned an empty script."
            )

        print(
            f"[JOB {job_id}] Script generated: "
            f"{len(script_text.split())} words",
            flush=True
        )

        # ====================================================
        # SCRIPT QUALITY CONTROL
        # ====================================================

        print(
            f"[JOB {job_id}] Running script quality control...",
            flush=True
        )

        qc_result = await asyncio.to_thread(
            quality_check_script,
            topic,
            script_text,
            settings["duration"]
        )

        print(
            f"[JOB {job_id}] QC result: {qc_result}",
            flush=True
        )

        # ----------------------------------------------------
        # CHECK QC APPROVAL
        # ----------------------------------------------------

        if not qc_result.get(
            "approved",
            False
        ):

            issues = qc_result.get(
                "issues",
                ["Unknown script quality problem."]
            )

            if not isinstance(
                issues,
                list
            ):

                issues = [
                    str(issues)
                ]

            raise RuntimeError(
                "Generated script failed quality control: "
                + "; ".join(
                    str(issue)
                    for issue in issues
                )
            )

        # ----------------------------------------------------
        # USE CLEANED / APPROVED SCRIPT
        # ----------------------------------------------------

        script_text = qc_result.get(
            "script",
            script_text
        )

        if not script_text.strip():

            raise RuntimeError(
                "Quality control returned an empty script."
            )

        print(
            f"[JOB {job_id}] Approved script: "
            f"{len(script_text.split())} words",
            flush=True
        )

        # ====================================================
        # REBUILD TTS DIALOGUE
        # ====================================================

        dialogue = [
            (
                "HOST",
                line.strip()
            )
            for line in re.split(
                r"(?<=[.!?])\s+",
                script_text.strip()
            )
            if line.strip()
        ]

        if not dialogue:

            raise RuntimeError(
                "Quality-controlled script contains no dialogue."
            )

        print(
            f"[JOB {job_id}] Dialogue lines: "
            f"{len(dialogue)}",
            flush=True
        )

        # ====================================================
        # TEXT TO SPEECH
        # ====================================================

        print(
            f"[JOB {job_id}] Starting TTS...",
            flush=True
        )

        timeline = await create_dialogue_voice(
            dialogue,
            str(audio_file),
            voice_profile=settings["voice"],
            target_duration=settings["duration"]
        )

        print(
            f"[JOB {job_id}] TTS completed: "
            f"{audio_file}",
            flush=True
        )

        # ====================================================
        # VIDEO CREATION
        # ====================================================

        print(
            f"[JOB {job_id}] Starting video builder...",
            flush=True
        )

        # NEW - Call Modal


        print(f"[JOB {job_id}] Sending to Modal...")
        # Read audio file
        with open(audio_file, "rb") as f:
            audio_bytes = f.read()

        video_bytes = await asyncio.to_thread(
            build_video_modal.remote,
            audio_file_bytes=audio_bytes,
            output_filename=f"{file_stem}.mp4",
            topic=topic,
            script_text=script_text,
            timeline=timeline,
            video_style=...,
            aspect_ratio=settings["aspect_ratio"],
            captions=settings["captions"],
            target_duration=settings["duration"],
            pexels_key=os.getenv("PEXELS_API_KEY"),
        )

        # Save returned video
        with open(video_file, "wb") as f:
            f.write(video_bytes)

        print(
            f"[JOB {job_id}] Video completed: "
            f"{video_file}",
            flush=True
        )

        # ====================================================
        # VERIFY VIDEO OUTPUT
        # ====================================================

        if not video_file.exists():

            raise RuntimeError(
                "Video builder completed but the MP4 file "
                "was not created."
            )

        if video_file.stat().st_size <= 0:

            raise RuntimeError(
                "Video builder created an empty video file."
            )

        # ====================================================
        # RESULT
        # ====================================================

        url = (
            f"/videos/{file_stem}.mp4"
        )

        set_result(
            job_id,
            {
                "success": True,
                "topic": topic,
                "script": script_text,
                "video": str(video_file),
                "download_url": url,
            }
        )

        update_project(
            project_id,
            user_id,
            status="completed",
            script=script_text,
            video_url=url
        )

        print(
            f"[JOB {job_id}] Job completed successfully.",
            flush=True
        )

    except Exception as exc:

        # ====================================================
        # JOB FAILURE
        # ====================================================

        traceback.print_exc()

        error_message = str(exc)

        print(
            f"[JOB {job_id}] FAILED: "
            f"{error_message}",
            flush=True
        )

        # ----------------------------------------------------
        # REFUND RESERVED CREDITS
        # ----------------------------------------------------

        try:
            # Refund the reserved credit cost.
            refund_credits(
                user_id,
                cost
            )

            # Refund the reserved daily generation.
            refund_generation(
                user_id
            )

        except Exception:

            traceback.print_exc()

        # ----------------------------------------------------
        # SAVE ERROR
        # ----------------------------------------------------

        try:

            set_error(
                job_id,
                error_message
            )

        except Exception:

            traceback.print_exc()

        # ----------------------------------------------------
        # UPDATE PROJECT
        # ----------------------------------------------------

        try:

            update_project(
                project_id,
                user_id,
                status="failed",
                error=error_message
            )

        except Exception:

            traceback.print_exc()


# ============================================================
# GENERATE VIDEO
# ============================================================

@app.post("/generate")
@limiter.limit("5/minute")
async def generate_video(
    request: Request,
    body: VideoRequest
):

    user = current_user(
        request
    )

    topic = body.topic.strip()

    if not topic:

        raise HTTPException(
            status_code=400,
            detail="Topic cannot be empty."
        )

    # --------------------------------------------------------
    # VALIDATE SETTINGS
    # --------------------------------------------------------

    normalize_ratio(
        body.aspect_ratio
    )

    normalize_voice(
        body.voice
    )

    cost = credit_cost(
        body.duration
    )

    # --------------------------------------------------------
    # RESERVE DAILY GENERATION (based on the user's plan)
    # --------------------------------------------------------

    allowed, _ = reserve_generation(
        user["id"]
    )

    if not allowed:

        summary = get_generation_summary(
            user["id"]
        ) or {}

        raise HTTPException(
            status_code=429,
            detail={
                "code": "daily_limit_reached",
                "message": (
                    f"You have reached your daily limit of "
                    f"{summary.get('daily_limit', 0)} videos "
                    f"on the {summary.get('plan_name', 'current')} plan. "
                    "Upgrade your plan or try again tomorrow."
                ),
                "upgrade_required": True,
                "daily_limit": summary.get("daily_limit"),
                "used_today": summary.get("generated_today"),
            }
        )

    # --------------------------------------------------------
    # RESERVE USER CREDITS
    # --------------------------------------------------------

    ok, refreshed_user = reserve_credits(
        user["id"],
        cost
    )

    if not ok:

        # Credits failed, so give the daily generation back.
        refund_generation(
            user["id"]
        )

        raise HTTPException(
            status_code=402,
            detail={
                "code": "credits_exhausted",
                "message": (
                    "You do not have enough credits "
                    "to generate this video. "
                    "Upgrade your plan to continue."
                ),
                "upgrade_required": True,
                "required_credits": cost,
                "available_credits": user["credits"],
            }
        )

    # --------------------------------------------------------
    # CREATE PROJECT
    # --------------------------------------------------------

    project_id = uuid.uuid4().hex

    try:

        create_project(
            project_id,
            user["id"],
            topic,
            body.duration,
            body.aspect_ratio,
            body.voice,
            body.captions,
            cost
        )

    except Exception:

        # Project creation failed after reserving
        # both credits and a daily generation.

        refund_credits(
            user["id"],
            cost
        )

        refund_generation(
            user["id"]
        )

        raise

    # --------------------------------------------------------
    # CREATE JOB
    # --------------------------------------------------------

    job_id = create_job(
        user["id"],
        project_id
    )

    # --------------------------------------------------------
    # GENERATION SETTINGS
    # --------------------------------------------------------

    template = (
        get_template(body.template_id)
        if body.template_id
        else None
    )

    settings = {
        "duration": body.duration,
        "aspect_ratio": body.aspect_ratio,
        "voice": body.voice,
        "captions": body.captions,
        "credit_cost": cost,

        # Full template (or None). Used by the script, footage and
        # voice steps once they are wired to read it.
        "template": template,
    }

    # --------------------------------------------------------
    # START BACKGROUND JOB
    # --------------------------------------------------------

    asyncio.create_task(
        process_video_job(
            job_id,
            user["id"],
            topic,
            settings,
            project_id
        )
    )

    # --------------------------------------------------------
    # RETURN IMMEDIATELY
    # --------------------------------------------------------

    return {
        "job_id": job_id,
        "project_id": project_id,
        "status": "pending",
        "credits": public_user(
            refreshed_user
        )
    }


# ============================================================
# JOB STATUS
# ============================================================

@app.get("/status/{job_id}")
def check_status(
    request: Request,
    job_id: str
):

    user = current_user(
        request
    )

    job = get_job(
        job_id
    )

    # --------------------------------------------------------
    # JOB DOES NOT EXIST
    # --------------------------------------------------------

    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    # --------------------------------------------------------
    # JOB OWNERSHIP CHECK
    # --------------------------------------------------------

    if job["user_id"] != user["id"]:

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    # --------------------------------------------------------
    # FAILED
    # --------------------------------------------------------

    if job["status"] == JobStatus.FAILED:

        return {
            "job_id": job_id,
            "status": job["status"],
            "error": job["error"],
        }

    # --------------------------------------------------------
    # COMPLETED
    # --------------------------------------------------------

    if job["status"] == JobStatus.COMPLETED:

        return {
            "job_id": job_id,
            "status": job["status"],
            **job["result"],
        }

    # --------------------------------------------------------
    # PENDING / PROCESSING
    # --------------------------------------------------------

    return {
        "job_id": job_id,
        "status": job["status"],
    }


# ============================================================
# USAGE
# ============================================================

@app.get("/usage")
def usage(
    request: Request
):

    user = current_user(
        request
    )

    summary = get_generation_summary(
        user["id"]
    ) or {}

    return {
        "user": public_user(user),

        # Plan-based, per-user values (-1 = unlimited).
        "plan": summary.get("plan"),
        "plan_name": summary.get("plan_name"),
        "daily_limit": summary.get("daily_limit"),
        "used_today": summary.get("generated_today"),
        "remaining_today": summary.get("remaining_today"),

        # Kept for backward compatibility.
        "platform_used_today": get_daily_usage(),
        "platform_daily_limit": DAILY_GENERATION_LIMIT,
    }


# ============================================================
# PLANS & MULTI-CURRENCY PAYMENTS
# ============================================================

# Subscription prices are maintained internally in USD. At checkout,
# the selected currency is converted to a fixed, configurable retail
# amount so a customer's price does not unexpectedly change because
# of a live FX fluctuation.
PLAN_CATALOG = [
    {"id": "free", "name": "Free", "credits": 6, "price_usd": 0, "billing": "free"},
    {"id": "starter", "name": "Starter", "credits": 150, "price_usd": 5, "billing": "monthly"},
    {"id": "pro", "name": "Pro", "credits": 300, "price_usd": 10, "billing": "monthly"},
    {"id": "mega", "name": "Mega", "credits": 600, "price_usd": 20, "billing": "monthly"},
]

# Configure these in .env when you want to change your retail FX
# conversion. Only used for currencies listed in
# PAYSTACK_ENABLED_CURRENCIES (USD only by default).
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

PAYMENT_DEFAULT_CURRENCY = os.getenv(
    "PAYMENT_DEFAULT_CURRENCY", "USD"
).strip().upper()

# USD is the only enabled currency by default. Add others only after
# Paystack has enabled them for your merchant account:
# PAYSTACK_ENABLED_CURRENCIES=USD,KES
# PAYSTACK_ENABLED_CURRENCIES=USD,KES,GHS,ZAR,NGN
# If you set PAYMENT_DEFAULT_CURRENCY in .env, make sure it is also
# listed in PAYSTACK_ENABLED_CURRENCIES.
PAYSTACK_ENABLED_CURRENCIES = {
    currency.strip().upper()
    for currency in os.getenv(
        "PAYSTACK_ENABLED_CURRENCIES", "USD"
    ).split(",")
    if currency.strip()
}

PAYSTACK_SUPPORTED_CURRENCIES = set(CURRENCY_META)


def normalize_payment_currency(currency: str | None) -> str:
    currency = (
        currency or PAYMENT_DEFAULT_CURRENCY
    ).strip().upper()

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
        raise HTTPException(
            status_code=500,
            detail=f"No valid exchange rate is configured for {currency}.",
        )

    amount = Decimal(str(amount_usd)) * Decimal(str(rate))

    return float(
        amount.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def paystack_amount_subunit(amount: float, currency: str) -> int:
    # Paystack requires the amount in the smallest unit of the selected
    # currency (for example KES cents or USD cents).
    normalize_payment_currency(currency)

    return int(
        (Decimal(str(amount)) * Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def build_plan_prices(currency: str) -> list[dict]:
    currency = normalize_payment_currency(currency)
    meta = CURRENCY_META[currency]
    result = []

    for plan in PLAN_CATALOG:
        price = convert_usd_to_currency(
            plan["price_usd"], currency
        )
        result.append({
            **plan,
            "price": price,
            "currency": currency,
            "currency_symbol": meta["symbol"],
            "currency_name": meta["name"],
        })

    return result


@app.get("/plans")
def plans(
    currency: str = Query(
        default=PAYMENT_DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
    )
):
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
            {
                "code": currency,
                **CURRENCY_META[currency],
            }
            for currency in sorted(PAYSTACK_ENABLED_CURRENCIES)
            if currency in CURRENCY_META
        ],
    }


# ============================================================
# TEMPLATES (live source with built-in fallback)
# ============================================================

@app.get("/templates")
def list_templates():

    return get_templates()


# ============================================================
# DEVELOPMENT PLAN UPGRADE
# ============================================================

@app.post("/subscription/dev-upgrade/{plan}")
def dev_upgrade(
    plan: str,
    request: Request
):

    """
    Development-only plan switch.

    This remains disabled unless:

        ALLOW_DEV_UPGRADE=true

    is explicitly configured.
    """

    if PAYSTACK_IS_LIVE or os.getenv(
        "ALLOW_DEV_UPGRADE",
        "false"
    ).lower() != "true":

        raise HTTPException(
            status_code=404,
            detail="Not found."
        )

    plan = plan.lower()

    if plan not in (
        "free",
        "starter",
        "pro",
        "mega"
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid subscription plan."
        )

    user = current_user(
        request
    )

    updated = set_plan(
        user["id"],
        plan
    )

    return {
        "user": public_user(
            updated
        )
    }
# ============================================================
# PAYSTACK PAYMENTS
# ============================================================

# Prices charged through Paystack, taken from PLAN_CATALOG above.
PLAN_PRICES_USD = {
    plan["id"]: plan["price_usd"]
    for plan in PLAN_CATALOG
    if plan["price_usd"] > 0
}


@app.post("/payments/paystack/initialize")
@limiter.limit("10/minute")
def initialize_paystack_payment(
    request: Request,
    payload: dict,
):
    user = current_user(request)

    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="Payments are not configured.",
        )

    plan = str(
        payload.get("plan", "")
    ).lower().strip()

    if plan not in PLAN_PRICES_USD:
        raise HTTPException(
            status_code=400,
            detail="Invalid subscription plan.",
        )

    currency = normalize_payment_currency(
        str(
            payload.get("currency")
            or PAYMENT_DEFAULT_CURRENCY
        )
    )

    amount_usd = PLAN_PRICES_USD[plan]
    amount = convert_usd_to_currency(
        amount_usd,
        currency,
    )
    amount_subunit = paystack_amount_subunit(
        amount,
        currency,
    )

    user = dict(user)
    email = user.get("email")

    if not email:
        raise HTTPException(
            status_code=400,
            detail="A valid account email is required for payment.",
        )

    payment_id = create_payment(
        user_id=user["id"],
        plan=plan,
        amount=amount,
        provider="paystack",
        currency=currency,
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

        raise HTTPException(
            status_code=502,
            detail="Unable to connect to Paystack.",
        )

    if not data.get("status") or not data.get("data"):
        fail_payment(payment_id)

        raise HTTPException(
            status_code=502,
            detail="Paystack could not initialize the transaction.",
        )

    paystack_data = data["data"]

    reference = paystack_data.get("reference")
    authorization_url = paystack_data.get("authorization_url")
    access_code = paystack_data.get("access_code")

    if not reference or not access_code:
        fail_payment(payment_id)

        raise HTTPException(
            status_code=502,
            detail="Paystack returned an incomplete transaction.",
        )

    with sqlite3.connect(BASE_DIR / "data" / "clip_pirate.db") as conn:
        conn.execute(
            """
            UPDATE payments
            SET provider_reference = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (reference, payment_id),
        )
        conn.commit()

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


# ============================================================
# PAYSTACK SHARED HELPERS
# ============================================================

class PaymentMismatch(Exception):

    """Paystack's amount or currency does not match our own record."""


# The browser's verify call and Paystack's webhook can arrive at almost
# the same moment. This lock makes sure only one of them activates the
# plan. It is enough for one server process; with several workers,
# complete_payment itself must be made atomic in the database.
_payment_lock = threading.Lock()


def fetch_paystack_transaction(reference: str) -> dict:

    """Ask Paystack directly for the real state of a transaction."""

    response = requests.get(
        f"{PAYSTACK_API}/transaction/verify/{quote(reference, safe='')}",
        headers={
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("status") or not data.get("data"):
        raise ValueError(
            "Paystack returned an invalid verification response."
        )

    return data["data"]


def finalize_paystack_payment(payment, transaction: dict, reference: str):

    """
    Check the amount and currency against our own record, then activate
    the plan exactly once. Returns the completed payment row.
    """

    # Paystack amounts are in the smallest currency unit.
    expected_amount = paystack_amount_subunit(float(payment["amount"]), str(payment["currency"]))

    actual_amount = int(
        transaction.get("amount") or 0
    )

    actual_currency = str(
        transaction.get("currency") or ""
    ).upper()

    if actual_amount != expected_amount:
        fail_payment(payment["id"])

        raise PaymentMismatch(
            "Payment amount does not match the selected plan."
        )

    expected_currency = str(
        payment["currency"]
    ).upper()

    if actual_currency != expected_currency:
        fail_payment(payment["id"])
        raise PaymentMismatch(
            f"Expected {expected_currency}, got {actual_currency}"
        )

    with _payment_lock:

        latest = get_payment_by_reference(
            "paystack",
            reference
        )

        if latest is not None and latest["status"] == "paid":
            return latest

        return complete_payment(
            payment["id"],
            provider_reference=reference,
        )


# ============================================================
# PAYSTACK PAYMENT VERIFICATION
# ============================================================

@app.get("/payments/paystack/verify/{reference}")
def verify_paystack_payment(
    reference: str,
    request: Request
):
    user = current_user(request)

    reference = reference.strip()

    if not reference:
        raise HTTPException(
            status_code=400,
            detail="Payment reference is required."
        )

    payment = get_payment_by_reference(
        "paystack",
        reference
    )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment record not found."
        )

    # Make sure the payment belongs to the logged-in user.
    if payment["user_id"] != user["id"]:
        raise HTTPException(
            status_code=403,
            detail="This payment does not belong to this account."
        )

    # Already completed? Return the existing state without
    # activating the subscription a second time.
    if payment["status"] == "paid":
        return {
            "status": "paid",
            "payment": dict(payment),
            "user": public_user(
                get_user(user["id"])
            ),
        }

    try:
        transaction = fetch_paystack_transaction(reference)

    except (requests.RequestException, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Unable to verify the payment with Paystack."
        )

    transaction_status = str(
        transaction.get("status", "")
    ).lower()

    if transaction_status != "success":
        return {
            "status": transaction_status or "pending",
            "message": "Payment has not been completed.",
        }

    try:
        completed = finalize_paystack_payment(
            payment,
            transaction,
            reference
        )

    except PaymentMismatch as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    return {
        "status": "paid",
        "payment": dict(completed),
        "user": public_user(
            get_user(payment["user_id"])
        ),
    }


# ============================================================
# PAYSTACK WEBHOOK
# ============================================================
#
# Paystack calls this URL itself (server to server) after a payment,
# even when the customer closed the browser before the page could
# verify. Set it in the Paystack dashboard under
# Settings -> API Keys & Webhooks, in the LIVE webhook URL box:
#
#     https://YOUR-DOMAIN/payments/paystack/webhook
#
# The URL must be public https. localhost cannot receive webhooks.

def process_paystack_webhook(event: dict):

    if event.get("event") != "charge.success":
        return {"ok": True, "ignored": True}

    data = event.get("data") or {}

    reference = str(
        data.get("reference") or ""
    ).strip()

    if not reference:
        return {"ok": True, "ignored": True}

    payment = get_payment_by_reference(
        "paystack",
        reference
    )

    if payment is None:
        # Not one of our payments. Acknowledge it so Paystack
        # stops retrying.
        return {"ok": True, "ignored": True}

    if payment["status"] == "paid":
        return {"ok": True, "already_paid": True}

    # Never trust the webhook body for money. Ask Paystack directly.
    try:
        transaction = fetch_paystack_transaction(reference)

    except (requests.RequestException, ValueError):
        # An error response makes Paystack retry later.
        raise HTTPException(
            status_code=502,
            detail="Could not verify the payment with Paystack."
        )

    if str(transaction.get("status", "")).lower() != "success":
        return {"ok": True, "ignored": True}

    try:
        finalize_paystack_payment(
            payment,
            transaction,
            reference
        )

    except PaymentMismatch as exc:
        print(
            f"[PAYSTACK WEBHOOK] {reference}: {exc}",
            flush=True
        )

        return {"ok": True, "mismatch": True}

    return {"ok": True}


@app.post("/payments/paystack/webhook")
async def paystack_webhook(
    request: Request
):

    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="Payments are not configured."
        )

    # The signature is calculated over the exact raw bytes Paystack sent,
    # so read the body before parsing it.
    raw_body = await request.body()

    signature = request.headers.get(
        "x-paystack-signature",
        ""
    )

    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(
        expected.encode("utf-8"),
        signature.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid signature."
        )

    try:
        event = json.loads(raw_body)

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON."
        )

    if not isinstance(event, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid event."
        )

    return await asyncio.to_thread(
        process_paystack_webhook,
        event
    )


# ============================================================
# PAYSTACK CALLBACK
# ============================================================

@app.get("/payments/paystack/callback")
def paystack_callback(
    reference: str | None = None
):
    if not reference:
        raise HTTPException(
            status_code=400,
            detail="Missing Paystack payment reference."
        )

    # Redirect the browser to the application with the
    # reference. The frontend will then call our authenticated
    # verification endpoint.
    return RedirectResponse(
        url=(
            "/app/?payment=paystack&reference="
            f"{quote(reference, safe='')}"
        )
    )