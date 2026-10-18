"""ElevenLabs quota monitor.

Checks the ElevenLabs subscription every few minutes, logs the remaining
credits, and emails the admin when the quota runs low. Each alert level
(warning, critical, exhausted) is sent once per quota period, and the state
is saved to DATA_DIR so a redeploy does not repeat the same email.

Environment variables (all optional except ADMIN_ALERT_EMAIL for emails):
  ADMIN_ALERT_EMAIL            where alerts are sent (if unset, logs only)
  ELEVENLABS_WARN_PERCENT      warn at or below this % remaining (default 20)
  ELEVENLABS_CRITICAL_PERCENT  critical at or below this % (default 5)
  ELEVENLABS_CHECK_MINUTES     minutes between checks (default 30)
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from email_service import send_email

DATA_DIR = Path(os.getenv("DATA_DIR") or (Path(__file__).resolve().parent / "data")).resolve()
STATE_FILE = DATA_DIR / "quota_alert_state.json"

API_URL = "https://api.elevenlabs.io/v1/user/subscription"

WARN_PERCENT = float(os.getenv("ELEVENLABS_WARN_PERCENT", "20"))
CRITICAL_PERCENT = float(os.getenv("ELEVENLABS_CRITICAL_PERCENT", "5"))
CHECK_MINUTES = float(os.getenv("ELEVENLABS_CHECK_MINUTES", "30"))

LEVEL_NAMES = {1: "WARNING", 2: "CRITICAL", 3: "EXHAUSTED"}


def _admin_email() -> str:
    return os.getenv("ADMIN_ALERT_EMAIL", "").strip()


def _level_for(percent_left: float) -> int:
    if percent_left <= 0:
        return 3
    if percent_left <= CRITICAL_PERCENT:
        return 2
    if percent_left <= WARN_PERCENT:
        return 1
    return 0


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"level": 0, "reset_unix": 0}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except OSError as exc:
        print(f"[QUOTA] Could not save alert state: {exc}", flush=True)


def _fetch_subscription(api_key: str) -> dict:
    response = requests.get(
        API_URL,
        headers={"xi-api-key": api_key},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


async def _send_alert(level: int, used: int, limit: int, left: int, percent_left: float, reset_unix: int) -> None:
    name = LEVEL_NAMES[level]
    reset_text = (
        datetime.fromtimestamp(reset_unix, timezone.utc).strftime("%d %b %Y %H:%M UTC")
        if reset_unix
        else "unknown"
    )

    if level == 3:
        headline = "ElevenLabs credits are used up. Paid-plan videos are now falling back to the backup voices."
    elif level == 2:
        headline = "ElevenLabs credits are almost gone."
    else:
        headline = "ElevenLabs credits are running low."

    text_content = (
        f"[Clip Pirate] ElevenLabs quota {name}\n\n"
        f"{headline}\n\n"
        f"Used: {used:,} of {limit:,}\n"
        f"Remaining: {left:,} ({percent_left:.1f}%)\n"
        f"Quota resets: {reset_text}\n\n"
        "Top up or upgrade your ElevenLabs plan to keep premium voices running."
    )

    html_content = f"""
    <!doctype html>
    <html>
      <body style="font-family:Arial,sans-serif;color:#172033">
        <h2>ElevenLabs quota: {name}</h2>
        <p>{headline}</p>
        <table cellpadding="6" style="border-collapse:collapse">
          <tr><td>Used</td><td><strong>{used:,}</strong> of {limit:,}</td></tr>
          <tr><td>Remaining</td><td><strong>{left:,}</strong> ({percent_left:.1f}%)</td></tr>
          <tr><td>Quota resets</td><td>{reset_text}</td></tr>
        </table>
        <p>Top up or upgrade your ElevenLabs plan to keep premium voices running.</p>
      </body>
    </html>
    """

    await send_email(
        recipient_email=_admin_email(),
        subject=f"[Clip Pirate] ElevenLabs quota {name}: {percent_left:.0f}% left",
        html_content=html_content,
        text_content=text_content,
    )


async def check_once() -> None:
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return

    try:
        sub = await asyncio.to_thread(_fetch_subscription, api_key)
    except Exception as exc:
        # A restricted API key needs read access to "User" for this endpoint.
        print(f"[QUOTA] Check failed: {type(exc).__name__}: {exc}", flush=True)
        return

    used = int(sub.get("character_count") or 0)
    limit = int(sub.get("character_limit") or 0)
    if limit <= 0:
        return

    left = max(limit - used, 0)
    percent_left = left / limit * 100
    reset_unix = int(sub.get("next_character_count_reset_unix") or 0)
    level = _level_for(percent_left)

    print(
        f"[QUOTA] elevenlabs used={used:,}/{limit:,} left={left:,} ({percent_left:.1f}%)",
        flush=True,
    )

    state = _load_state()

    if reset_unix != state.get("reset_unix"):
        # New quota period: alerts start over.
        state = {"level": 0, "reset_unix": reset_unix}
    elif level < state.get("level", 0):
        # Credits went back up (top-up or plan upgrade): allow future alerts.
        state["level"] = level
    _save_state(state)

    if level <= state.get("level", 0):
        return

    print(f"[QUOTA] ELEVENLABS {LEVEL_NAMES[level]}: {percent_left:.1f}% remaining.", flush=True)

    if not _admin_email():
        print("[QUOTA] ADMIN_ALERT_EMAIL is not set, so no email was sent.", flush=True)
        state["level"] = level
        _save_state(state)
        return

    try:
        await _send_alert(level, used, limit, left, percent_left, reset_unix)
    except Exception as exc:
        print(f"[QUOTA] Alert email failed: {type(exc).__name__}: {exc}", flush=True)
        return  # not saved, so the next check tries again

    state["level"] = level
    _save_state(state)


async def monitor_loop() -> None:
    # Short delay so startup finishes first.
    await asyncio.sleep(20)
    while True:
        try:
            await check_once()
        except Exception as exc:
            print(f"[QUOTA] Monitor error: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(max(CHECK_MINUTES, 1) * 60)