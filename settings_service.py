"""
Account and billing endpoints for the Settings page.

    from settings_service import register_settings_routes
    register_settings_routes(app, current_user, limiter, SESSION_COOKIE, send_email)

Call it once, at the very bottom of main.py (after current_user and limiter
exist). Routes added:

    GET  /settings/billing      plan, renewal date and payment history
    POST /settings/profile      change display name
    POST /settings/password     change password, sign out other devices
    POST /settings/signout-all  sign out everywhere, including this device

Every error uses a plain-text `detail` so the app can show it as a message.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

from db import PLAN_CONFIG, _conn, _hash_password, verify_password

MAX_PAYMENTS = 20


class ProfileBody(BaseModel):
    name: str = ""


class PasswordBody(BaseModel):
    current_password: str = ""
    new_password: str = ""


def _clean_name(value):
    value = re.sub(r"[<>]", "", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _days_left(value):

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    seconds = (parsed - datetime.now(timezone.utc)).total_seconds()

    return max(0, int(seconds // 86400))


def register_settings_routes(app, current_user, limiter, session_cookie, send_email=None):

    # --------------------------------------------------------
    # BILLING
    # --------------------------------------------------------

    @app.get("/settings/billing")
    def settings_billing(request: Request):

        user = current_user(request)

        plan = str(user["plan"] or "free").lower()

        # Abandoned checkouts stay "pending" forever; hide them after a day.
        stale_before = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        with _conn() as c:

            rows = c.execute(
                """
                SELECT plan, amount, currency, provider, provider_reference,
                       status, created_at

                FROM payments

                WHERE user_id = ?
                  AND NOT (status = 'pending' AND created_at < ?)

                ORDER BY created_at DESC

                LIMIT ?
                """,
                (user["id"], stale_before, MAX_PAYMENTS),
            ).fetchall()

        expires_at = user["subscription_expires_at"] if plan != "free" else None

        return {
            "plan": plan,
            "plan_name": PLAN_CONFIG.get(plan, PLAN_CONFIG["free"])["name"],
            "status": user["subscription_status"],
            "expires_at": expires_at,
            "days_left": _days_left(expires_at),
            "credits": user["credits"],
            "payments": [
                {
                    "plan": str(r["plan"] or "").title(),
                    "amount": r["amount"],
                    "currency": r["currency"],
                    "provider": str(r["provider"] or "").title(),
                    "reference": r["provider_reference"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }

    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    @app.post("/settings/profile")
    @limiter.limit("20/hour")
    def settings_profile(request: Request, body: ProfileBody):

        user = current_user(request)

        name = _clean_name(body.name)

        if len(name) < 2:
            raise HTTPException(status_code=400, detail="Please enter at least 2 characters.")

        if len(name) > 60:
            raise HTTPException(status_code=400, detail="Your name can be at most 60 characters.")

        with _conn() as c:
            c.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))

        return {"ok": True, "name": name}

    # --------------------------------------------------------
    # PASSWORD
    # --------------------------------------------------------

    @app.post("/settings/password")
    @limiter.limit("5/hour")
    async def settings_password(request: Request, body: PasswordBody):

        user = current_user(request)

        if not verify_password(body.current_password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="Your current password is not correct.")

        new_password = body.new_password

        if len(new_password) < 8:
            raise HTTPException(status_code=400, detail="Your new password must be at least 8 characters.")

        if len(new_password) > 128:
            raise HTTPException(status_code=400, detail="Your new password is too long.")

        if new_password == body.current_password:
            raise HTTPException(status_code=400, detail="Your new password must be different from the current one.")

        token = request.cookies.get(session_cookie) or ""
        keep = hashlib.sha256(token.encode()).hexdigest()

        with _conn() as c:

            c.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (_hash_password(new_password), user["id"]),
            )

            # Every other device has to log in again with the new password.
            signed_out = c.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user["id"], keep),
            ).rowcount

        if send_email is not None:

            try:
                await send_email(
                    recipient_email=str(user["email"]).strip().lower(),
                    subject="Your Clip Pirate password was changed",
                    html_content=(
                        "<html><body style='font-family:Arial,sans-serif'>"
                        "<p>Hi " + str(user["name"]).replace("<", "") + ",</p>"
                        "<p>The password on your Clip Pirate account was just changed.</p>"
                        "<p>If this was you, there is nothing more to do. If it was not, "
                        "use <b>Forgot password</b> on the login page right away to "
                        "regain access.</p></body></html>"
                    ),
                    text_content=(
                        "Hi " + str(user["name"]) + ",\n\n"
                        "The password on your Clip Pirate account was just changed.\n"
                        "If this was not you, use 'Forgot password' on the login page "
                        "right away to regain access.\n"
                    ),
                )
            except Exception as error:
                print(f"[SETTINGS] Password-change email failed: {type(error).__name__}: {error}", flush=True)

        return {"ok": True, "signed_out_other_devices": signed_out}

    # --------------------------------------------------------
    # SIGN OUT EVERYWHERE
    # --------------------------------------------------------

    @app.post("/settings/signout-all")
    @limiter.limit("10/hour")
    def settings_signout_all(request: Request, response: Response):

        user = current_user(request)

        with _conn() as c:
            removed = c.execute(
                "DELETE FROM sessions WHERE user_id = ?",
                (user["id"],),
            ).rowcount

        response.delete_cookie(session_cookie, path="/")

        return {"ok": True, "signed_out": removed}
