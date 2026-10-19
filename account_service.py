"""
Download my data and Delete account.

    from account_service import register_account_routes
    register_account_routes(app, current_user, limiter, SESSION_COOKIE, send_email, delete_video_file)

Call it once, at the very bottom of main.py, after the other register_* lines.
`delete_video_file` is optional: a function that takes a project's stored
video_url and removes the file (use the same function main.py already uses to
delete expired videos). Without it the account is still deleted, but the video
files stay on disk, so pass it.

Routes added:

    GET  /settings/export          everything we hold about the user, as JSON
    POST /settings/delete-account  {"password": "...", "confirm": "DELETE"}

Rules
-----
* Paid plans that are still running cannot be deleted (no lost paid time).
* A video that is still being made blocks deletion until it finishes.
* Payment records are kept in `deleted_payments_archive` with no name or
  email, only an anonymous reference, so refunds and disputes stay answerable.
* Everything else (projects, jobs, sessions, usage, tokens) is removed.

Every error uses a plain-text `detail` so the app can show it as a message.
"""

import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

from db import _conn, now_iso, verify_password

_schema_ready = False


class DeleteBody(BaseModel):
    password: str = ""
    confirm: str = ""


def _ensure_schema():

    global _schema_ready

    if _schema_ready:
        return

    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS deleted_payments_archive (
                id TEXT PRIMARY KEY,
                user_ref TEXT NOT NULL,
                plan TEXT,
                amount INTEGER,
                currency TEXT,
                provider TEXT,
                provider_reference TEXT,
                status TEXT,
                created_at TEXT,
                deleted_at TEXT NOT NULL
            )
            """
        )

    _schema_ready = True


def _user_ref(user_id):
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]


def _delete_files(urls, delete_video_file):

    removed = 0

    if delete_video_file is None:
        return removed

    for url in urls:

        try:
            delete_video_file(url)
            removed += 1
        except Exception as error:
            print(f"[ACCOUNT] Could not remove video file {url}: "
                  f"{type(error).__name__}: {error}", flush=True)

    return removed


def register_account_routes(app, current_user, limiter, session_cookie, send_email=None, delete_video_file=None):

    try:
        _ensure_schema()
    except Exception as error:
        print(f"[ACCOUNT] Schema setup failed: {error}", flush=True)

    # --------------------------------------------------------
    # DOWNLOAD MY DATA
    # --------------------------------------------------------

    @app.get("/settings/export")
    @limiter.limit("6/hour")
    def settings_export(request: Request):

        user = current_user(request)

        with _conn() as c:

            projects = c.execute(
                "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()

            payments = c.execute(
                """
                SELECT plan, amount, currency, provider, provider_reference,
                       status, created_at
                FROM payments WHERE user_id = ? ORDER BY created_at DESC
                """,
                (user["id"],),
            ).fetchall()

            try:
                reminders = c.execute(
                    "SELECT email_reminders FROM users WHERE id = ?", (user["id"],)
                ).fetchone()
                reminders_on = bool(reminders["email_reminders"])
            except Exception:
                reminders_on = True

        skip = {"user_id", "video_url", "error"}

        return {
            "exported_at": now_iso(),
            "note": "Video files are not included. Download them from your Projects page while they are still available.",
            "account": {
                "name": user["name"],
                "email": user["email"],
                "created_at": user["created_at"],
                "plan": user["plan"],
                "credits": user["credits"],
                "subscription_status": user["subscription_status"],
                "subscription_expires_at": user["subscription_expires_at"],
                "email_reminders": reminders_on,
            },
            "payments": [dict(r) for r in payments],
            "projects": [
                {k: row[k] for k in row.keys() if k not in skip}
                for row in projects
            ],
        }

    # --------------------------------------------------------
    # DELETE ACCOUNT
    # --------------------------------------------------------

    @app.post("/settings/delete-account")
    @limiter.limit("5/hour")
    async def settings_delete_account(request: Request, response: Response, body: DeleteBody):

        user = current_user(request)

        if str(body.confirm or "").strip() != "DELETE":
            raise HTTPException(status_code=400, detail="Type DELETE in capital letters to confirm.")

        if not verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="Your password is not correct.")

        plan = str(user["plan"] or "free").lower()

        if plan != "free":

            expires = str(user["subscription_expires_at"] or "")[:10]
            until = f" until {expires}" if expires else ""

            raise HTTPException(
                status_code=400,
                detail=f"Your {plan.title()} plan is still active{until}. "
                       "You can delete your account once it has ended, so you keep what you paid for.",
            )

        _ensure_schema()

        with _conn() as c:

            running = c.execute(
                "SELECT COUNT(*) AS n FROM projects WHERE user_id = ? "
                "AND status IN ('processing', 'pending')",
                (user["id"],),
            ).fetchone()["n"]

            if running:
                raise HTTPException(
                    status_code=400,
                    detail="A video is still being made. Please try again when it has finished.",
                )

            urls = [
                r["video_url"]
                for r in c.execute(
                    "SELECT video_url FROM projects WHERE user_id = ? "
                    "AND video_url IS NOT NULL AND video_url != ''",
                    (user["id"],),
                ).fetchall()
            ]

            # Keep payment records, without any name or email.
            c.execute(
                """
                INSERT OR IGNORE INTO deleted_payments_archive
                    (id, user_ref, plan, amount, currency, provider,
                     provider_reference, status, created_at, deleted_at)
                SELECT id, ?, plan, amount, currency, provider,
                       provider_reference, status, created_at, ?
                FROM payments WHERE user_id = ?
                """,
                (_user_ref(user["id"]), now_iso(), user["id"]),
            )

            # Child rows (projects, jobs, sessions, tokens, usage, payments)
            # are removed by the foreign keys' ON DELETE CASCADE.
            c.execute("DELETE FROM users WHERE id = ?", (user["id"],))

        _delete_files(urls, delete_video_file)

        response.delete_cookie(session_cookie, path="/")

        if send_email is not None:

            name = str(user["name"] or "there").replace("<", "").replace(">", "")

            try:
                await send_email(
                    recipient_email=str(user["email"]).strip().lower(),
                    subject="Your Clip Pirate account was deleted",
                    html_content=(
                        "<html><body style='font-family:Arial,sans-serif'>"
                        f"<p>Hi {name},</p>"
                        "<p>Your Clip Pirate account and your videos have been deleted, "
                        "as you asked. We only keep an anonymous record of past payments "
                        "for accounting.</p>"
                        "<p>You are welcome to come back any time and start fresh.</p>"
                        "</body></html>"
                    ),
                    text_content=(
                        f"Hi {name},\n\nYour Clip Pirate account and your videos have been "
                        "deleted, as you asked. We only keep an anonymous record of past "
                        "payments for accounting.\n"
                    ),
                )
            except Exception as error:
                print(f"[ACCOUNT] Deletion email failed: {type(error).__name__}: {error}", flush=True)

        return {"ok": True, "videos_removed": len(urls)}
