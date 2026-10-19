"""
Reminder emails for paid plans.

    from reminders_service import register_reminder_routes
    register_reminder_routes(app, current_user, limiter, send_email)

Call it once, at the very bottom of main.py, after register_settings_routes.

What it does
------------
* Starts a background check (every 6 hours, and once shortly after startup).
* Plan ending soon: a paid plan with 3 days or less left gets one email.
* Credits running low: a paid plan with 20% or less of its credits left
  gets one email per billing period.
* Each reminder is sent once. A renewal changes the plan's end date, which
  starts a fresh period, so the next period can send again.
* Users can switch reminders off in Settings -> Preferences.

Routes added:

    GET  /settings/reminders   {"enabled": true}
    POST /settings/reminders   {"enabled": false}

Nothing here may break the app: every failure is logged and swallowed.
"""

import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from pydantic import BaseModel

from db import PLAN_CONFIG, _conn, now_iso

CHECK_EVERY_SECONDS = 6 * 60 * 60
FIRST_CHECK_DELAY_SECONDS = 90
EXPIRY_WINDOW_DAYS = 3
LOW_CREDITS_FRACTION = 0.20
APP_URL = os.getenv("APP_URL", "https://clippirate.onrender.com").rstrip("/")

_schema_ready = False


class RemindersBody(BaseModel):
    enabled: bool = True


# ------------------------------------------------------------
# SCHEMA
# ------------------------------------------------------------

def _ensure_schema():

    global _schema_ready

    if _schema_ready:
        return

    with _conn() as c:

        columns = {
            row["name"]
            for row in c.execute("PRAGMA table_info(users)").fetchall()
        }

        if "email_reminders" not in columns:
            c.execute(
                "ALTER TABLE users ADD COLUMN "
                "email_reminders INTEGER NOT NULL DEFAULT 1"
            )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS reminder_log (
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                period TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (user_id, kind, period)
            )
            """
        )

    _schema_ready = True


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

def _parse(value):

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def _first_name(name):
    name = re.sub(r"[<>&]", "", str(name or "")).strip()
    return name.split(" ")[0] if name else "there"


def _already_sent(c, user_id, kind, period):
    return c.execute(
        "SELECT 1 FROM reminder_log WHERE user_id = ? AND kind = ? AND period = ?",
        (user_id, kind, period),
    ).fetchone() is not None


def _mark_sent(user_id, kind, period):
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO reminder_log (user_id, kind, period, sent_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, kind, period, now_iso()),
        )


def _wrap(heading, paragraphs, button_text):

    body = "".join(f"<p style='margin:0 0 14px'>{p}</p>" for p in paragraphs)

    return (
        "<html><body style='font-family:Arial,sans-serif;color:#1b1d24;line-height:1.5'>"
        f"<h2 style='margin:0 0 14px'>{heading}</h2>{body}"
        f"<p><a href='{APP_URL}/app/' style='background:#7c5cff;color:#fff;"
        "padding:11px 18px;border-radius:8px;text-decoration:none;font-weight:bold'>"
        f"{button_text}</a></p>"
        "<p style='color:#6b7280;font-size:12px;margin-top:26px'>"
        "You can turn these reminders off any time in Settings &rarr; Preferences."
        "</p></body></html>"
    )


def _expiry_message(user, days_left, expires_at):

    plan_name = PLAN_CONFIG.get(user["plan"], {}).get("name", str(user["plan"]).title())
    name = _first_name(user["name"])
    date_text = expires_at.strftime("%B %d, %Y").replace(" 0", " ")

    if days_left < 1:
        when = "within 24 hours"
    elif days_left < 2:
        when = "tomorrow"
    else:
        when = f"in {int(days_left)} days"

    subject = f"Your Clip Pirate {plan_name} plan ends {when}"

    html = _wrap(
        f"Your {plan_name} plan ends {when}",
        [
            f"Hi {name},",
            f"Your {plan_name} plan ends on <b>{date_text}</b>. After that your account "
            "returns to the Free plan, and any unused credits on your plan are replaced "
            "by the Free daily credits.",
            "Renew before then to keep your credits, your daily limit and Dialogue narration.",
        ],
        "Renew my plan",
    )

    text = (
        f"Hi {name},\n\n"
        f"Your {plan_name} plan ends on {date_text}. After that your account returns "
        "to the Free plan.\n"
        f"Renew to keep your credits and limits: {APP_URL}/app/\n\n"
        "You can turn these reminders off in Settings > Preferences.\n"
    )

    return subject, html, text


def _low_credits_message(user, total):

    plan_name = PLAN_CONFIG.get(user["plan"], {}).get("name", str(user["plan"]).title())
    name = _first_name(user["name"])
    left = int(user["credits"] or 0)

    subject = (
        f"You have {left} Clip Pirate credits left"
        if left > 0 else "You are out of Clip Pirate credits"
    )

    html = _wrap(
        "Your credits are running low",
        [
            f"Hi {name},",
            f"You have <b>{left}</b> of {total} credits left on your {plan_name} plan. "
            "A 30 second video uses 2 credits, 45 seconds uses 4 and 60 seconds uses 6.",
            "Upgrade your plan or renew early to keep creating without interruption.",
        ],
        "See plans",
    )

    text = (
        f"Hi {name},\n\n"
        f"You have {left} of {total} credits left on your {plan_name} plan.\n"
        f"See plans: {APP_URL}/app/\n\n"
        "You can turn these reminders off in Settings > Preferences.\n"
    )

    return subject, html, text


# ------------------------------------------------------------
# THE CHECK
# ------------------------------------------------------------

async def run_reminders_once(send_email):
    """Find users who are due a reminder and email them. Returns the count sent."""

    if send_email is None:
        return 0

    _ensure_schema()

    now = datetime.now(timezone.utc)

    with _conn() as c:
        users = c.execute(
            """
            SELECT id, name, email, plan, credits, subscription_expires_at
            FROM users
            WHERE plan != 'free'
              AND email_reminders = 1
              AND subscription_expires_at IS NOT NULL
            """
        ).fetchall()

    sent = 0

    for user in users:

        expires_at = _parse(user["subscription_expires_at"])

        if expires_at is None or expires_at <= now:
            continue

        period = user["subscription_expires_at"]
        days_left = (expires_at - now).total_seconds() / 86400

        due = []

        if days_left <= EXPIRY_WINDOW_DAYS:
            due.append(("expiring", _expiry_message(user, days_left, expires_at)))

        plan_credits = PLAN_CONFIG.get(user["plan"], {}).get("credits") or 0

        if plan_credits and int(user["credits"] or 0) <= plan_credits * LOW_CREDITS_FRACTION:
            due.append(("low_credits", _low_credits_message(user, plan_credits)))

        for kind, (subject, html, text) in due:

            with _conn() as c:
                if _already_sent(c, user["id"], kind, period):
                    continue

            try:
                await send_email(
                    recipient_email=str(user["email"]).strip().lower(),
                    subject=subject,
                    html_content=html,
                    text_content=text,
                )
            except Exception as error:
                print(f"[REMINDERS] {kind} email failed for {user['id']}: "
                      f"{type(error).__name__}: {error}", flush=True)
                continue

            _mark_sent(user["id"], kind, period)
            sent += 1

    return sent


async def _loop(send_email):

    await asyncio.sleep(FIRST_CHECK_DELAY_SECONDS)

    while True:

        try:
            sent = await run_reminders_once(send_email)
            if sent:
                print(f"[REMINDERS] Sent {sent} reminder email(s).", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"[REMINDERS] Check failed: {type(error).__name__}: {error}", flush=True)

        await asyncio.sleep(CHECK_EVERY_SECONDS)


# ------------------------------------------------------------
# WIRING
# ------------------------------------------------------------

def register_reminder_routes(app, current_user, limiter, send_email=None):

    try:
        _ensure_schema()
    except Exception as error:
        print(f"[REMINDERS] Schema setup failed: {error}", flush=True)

    # Run the background check for as long as the app lives. Wrapping the
    # lifespan works whether main.py uses on_event handlers or its own lifespan.
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):

        task = asyncio.create_task(_loop(send_email))

        try:
            async with original(application) as state:
                yield state
        finally:
            task.cancel()

    app.router.lifespan_context = lifespan

    @app.get("/settings/reminders")
    def get_reminders(request: Request):

        user = current_user(request)

        with _conn() as c:
            row = c.execute(
                "SELECT email_reminders FROM users WHERE id = ?", (user["id"],)
            ).fetchone()

        return {"enabled": bool(row["email_reminders"]) if row else True}

    @app.post("/settings/reminders")
    @limiter.limit("30/hour")
    def set_reminders(request: Request, body: RemindersBody):

        user = current_user(request)

        with _conn() as c:
            c.execute(
                "UPDATE users SET email_reminders = ? WHERE id = ?",
                (1 if body.enabled else 0, user["id"]),
            )

        return {"ok": True, "enabled": bool(body.enabled)}
