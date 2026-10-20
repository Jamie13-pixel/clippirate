"""
In-app support chat: users write to you from Settings -> Help, you answer from
the admin side. Built on the support_conversations / support_messages tables
that already exist in db.py.

    from support_service import register_support_routes
    register_support_routes(app, current_user, limiter, require_admin, STATIC_DIR, send_email)

Call it once, at the very bottom of main.py, after the other register_* lines.

User routes (any logged-in user, only ever their own conversations):

    GET  /support/conversations                    my conversations, newest first
    POST /support/conversations                    start one {subject, message}
    GET  /support/conversations/{id}               messages (marks replies as read)
    POST /support/conversations/{id}/messages      add a message {message}
    GET  /support/unread                           {count} of unread admin replies

Admin routes (admin only, same ADMIN_EMAILS check as the rest of /admin):

    GET  /admin/support                            the support inbox page
    GET  /admin/api/support/inbox                  conversations, needs-reply first
    GET  /admin/api/support/thread/{id}            one conversation with user info
    POST /admin/api/support/thread/{id}/reply      answer {message}
    POST /admin/api/support/thread/{id}/status     {status}: open|pending|resolved|closed

Every error uses a plain-text `detail` so the app can show it as a message.
"""

import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from db import (
    _conn,
    add_support_message,
    create_support_conversation,
    get_support_conversation,
    now_iso,
    update_conversation_status,
)

APP_URL = os.getenv("APP_URL", "https://clippirate.onrender.com").rstrip("/")

MAX_OPEN_CONVERSATIONS = 5
MAX_MESSAGES_PER_CONVERSATION = 200
MAX_MESSAGE_LENGTH = 2000
EMAIL_REPLY_COOLDOWN_MINUTES = 30

_schema_ready = False


class NewConversationBody(BaseModel):
    subject: str = ""
    message: str = ""


class MessageBody(BaseModel):
    message: str = ""


class StatusBody(BaseModel):
    status: str = ""


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

def _ensure_schema():

    global _schema_ready

    if _schema_ready:
        return

    with _conn() as c:

        columns = {
            row["name"]
            for row in c.execute("PRAGMA table_info(support_conversations)").fetchall()
        }

        if "user_seen_at" not in columns:
            c.execute("ALTER TABLE support_conversations ADD COLUMN user_seen_at TEXT")

    _schema_ready = True


def _clean(value, limit):
    value = str(value or "").replace("\r\n", "\n").replace("\x00", "").strip()
    return value[:limit] if len(value) > limit else value


def _own_conversation(user, conversation_id):

    conversation = get_support_conversation(conversation_id)

    # Same answer for "missing" and "someone else's", so ids cannot be probed.
    if conversation is None or conversation["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return conversation


_LAST = (
    "(SELECT {col} FROM support_messages m WHERE m.conversation_id = c.id "
    "ORDER BY m.created_at DESC LIMIT 1)"
)

_SUMMARY_COLUMNS = f"""
    c.id, c.subject, c.status, c.created_at, c.user_seen_at,
    {_LAST.format(col="message")} AS last_message,
    {_LAST.format(col="created_at")} AS last_at,
    {_LAST.format(col="sender_type")} AS last_sender,
    (SELECT COUNT(*) FROM support_messages m
        WHERE m.conversation_id = c.id
          AND m.sender_type = 'admin'
          AND (c.user_seen_at IS NULL OR m.created_at > c.user_seen_at)) AS unread
"""


def _preview(text):
    text = " ".join(str(text or "").split())
    return text[:110] + ("..." if len(text) > 110 else "")


def _summary(row):
    return {
        "id": row["id"],
        "subject": row["subject"],
        "status": row["status"],
        "created_at": row["created_at"],
        "last_at": row["last_at"] or row["created_at"],
        "last_sender": row["last_sender"],
        "preview": _preview(row["last_message"]),
        "unread": int(row["unread"] or 0),
    }


def _messages(c, conversation_id):
    return [
        {
            "id": m["id"],
            "from": "admin" if m["sender_type"] == "admin" else "user",
            "message": m["message"],
            "created_at": m["created_at"],
        }
        for m in c.execute(
            "SELECT id, sender_type, message, created_at FROM support_messages "
            "WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    ]


def _parse(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------
# REGISTRATION
# ------------------------------------------------------------

def register_support_routes(app, current_user, limiter, require_admin, static_dir, send_email=None):

    try:
        _ensure_schema()
    except Exception as error:
        print(f"[SUPPORT] Schema setup failed: {error}", flush=True)

    # ========================================================
    # USER SIDE
    # ========================================================

    @app.get("/support/conversations")
    def support_list(request: Request):

        user = current_user(request)
        _ensure_schema()

        with _conn() as c:
            rows = c.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM support_conversations c "
                "WHERE c.user_id = ? ORDER BY last_at DESC LIMIT 50",
                (user["id"],),
            ).fetchall()

        return {"conversations": [_summary(r) for r in rows]}

    @app.get("/support/unread")
    def support_unread(request: Request):

        user = current_user(request)
        _ensure_schema()

        with _conn() as c:
            row = c.execute(
                """
                SELECT COUNT(*) AS n
                FROM support_messages m
                JOIN support_conversations c ON c.id = m.conversation_id
                WHERE c.user_id = ?
                  AND m.sender_type = 'admin'
                  AND (c.user_seen_at IS NULL OR m.created_at > c.user_seen_at)
                """,
                (user["id"],),
            ).fetchone()

        return {"count": int(row["n"] or 0)}

    @app.post("/support/conversations")
    @limiter.limit("10/hour")
    def support_create(request: Request, body: NewConversationBody):

        user = current_user(request)
        _ensure_schema()

        subject = _clean(body.subject, 80)
        message = _clean(body.message, MAX_MESSAGE_LENGTH)

        if len(subject) < 3:
            raise HTTPException(status_code=400, detail="Please add a short subject (at least 3 characters).")

        if not message:
            raise HTTPException(status_code=400, detail="Please write your message.")

        with _conn() as c:
            open_count = c.execute(
                "SELECT COUNT(*) AS n FROM support_conversations "
                "WHERE user_id = ? AND status IN ('open', 'pending')",
                (user["id"],),
            ).fetchone()["n"]

        if open_count >= MAX_OPEN_CONVERSATIONS:
            raise HTTPException(
                status_code=400,
                detail="You already have several open conversations. Please wait for a reply or add to an existing one.",
            )

        conversation_id = create_support_conversation(user["id"], subject)
        add_support_message(conversation_id, "user", user["id"], message)

        with _conn() as c:
            c.execute(
                "UPDATE support_conversations SET user_seen_at = ? WHERE id = ?",
                (now_iso(), conversation_id),
            )

        return {"ok": True, "id": conversation_id}

    @app.get("/support/conversations/{conversation_id}")
    def support_thread(request: Request, conversation_id: str):

        user = current_user(request)
        _ensure_schema()

        conversation = _own_conversation(user, conversation_id)

        with _conn() as c:
            messages = _messages(c, conversation_id)
            c.execute(
                "UPDATE support_conversations SET user_seen_at = ? WHERE id = ?",
                (now_iso(), conversation_id),
            )

        return {
            "id": conversation["id"],
            "subject": conversation["subject"],
            "status": conversation["status"],
            "created_at": conversation["created_at"],
            "messages": messages,
        }

    @app.post("/support/conversations/{conversation_id}/messages")
    @limiter.limit("40/hour")
    def support_send(request: Request, conversation_id: str, body: MessageBody):

        user = current_user(request)
        _ensure_schema()

        conversation = _own_conversation(user, conversation_id)

        message = _clean(body.message, MAX_MESSAGE_LENGTH)

        if not message:
            raise HTTPException(status_code=400, detail="Please write your message.")

        if conversation["status"] == "closed":
            raise HTTPException(
                status_code=400,
                detail="This conversation is closed. Please start a new one.",
            )

        with _conn() as c:
            count = c.execute(
                "SELECT COUNT(*) AS n FROM support_messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()["n"]

        if count >= MAX_MESSAGES_PER_CONVERSATION:
            raise HTTPException(status_code=400, detail="This conversation is full. Please start a new one.")

        add_support_message(conversation_id, "user", user["id"], message)

        with _conn() as c:
            # A new message from the user puts the conversation back in your queue.
            c.execute(
                "UPDATE support_conversations SET status = 'open', user_seen_at = ? WHERE id = ?",
                (now_iso(), conversation_id),
            )

        return {"ok": True}

    # ========================================================
    # ADMIN SIDE
    # ========================================================

    @app.get("/admin/support")
    def admin_support_page(request: Request):

        try:
            require_admin(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                return RedirectResponse(url="/app", status_code=303)
            raise

        return RedirectResponse(url="/admin#support", status_code=303)

    @app.get("/admin/api/support/inbox")
    def admin_inbox(
        request: Request,
        status: str = Query(default="", max_length=20),
    ):

        require_admin(request)
        _ensure_schema()

        status = status.strip().lower()

        where = "WHERE c.status = ?" if status in ("open", "pending", "resolved", "closed") else ""
        params = (status,) if where else ()

        with _conn() as c:
            rows = c.execute(
                f"""
                SELECT {_SUMMARY_COLUMNS}, u.name AS user_name, u.email AS user_email, u.plan AS user_plan
                FROM support_conversations c
                JOIN users u ON u.id = c.user_id
                {where}
                ORDER BY last_at DESC
                LIMIT 200
                """,
                params,
            ).fetchall()

        items = []

        for r in rows:
            item = _summary(r)
            item["user_name"] = r["user_name"]
            item["user_email"] = r["user_email"]
            item["user_plan"] = r["user_plan"]
            item["needs_reply"] = r["last_sender"] == "user" and r["status"] in ("open", "pending")
            items.append(item)

        # Waiting on you first, then newest (the query is already newest first).
        waiting = [i for i in items if i["needs_reply"]]
        rest = [i for i in items if not i["needs_reply"]]

        return {
            "needs_reply": len(waiting),
            "conversations": waiting + rest,
        }

    @app.get("/admin/api/support/thread/{conversation_id}")
    def admin_thread(request: Request, conversation_id: str):

        require_admin(request)
        _ensure_schema()

        with _conn() as c:

            row = c.execute(
                """
                SELECT c.id, c.subject, c.status, c.created_at,
                       u.id AS user_id, u.name AS user_name, u.email AS user_email,
                       u.plan AS user_plan, u.credits AS user_credits
                FROM support_conversations c
                JOIN users u ON u.id = c.user_id
                WHERE c.id = ?
                """,
                (conversation_id,),
            ).fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")

            return {
                "id": row["id"],
                "subject": row["subject"],
                "status": row["status"],
                "created_at": row["created_at"],
                "user": {
                    "name": row["user_name"],
                    "email": row["user_email"],
                    "plan": row["user_plan"],
                    "credits": row["user_credits"],
                },
                "messages": _messages(c, conversation_id),
            }

    @app.post("/admin/api/support/thread/{conversation_id}/reply")
    async def admin_thread_reply(request: Request, conversation_id: str, body: MessageBody):

        admin = require_admin(request)
        _ensure_schema()

        message = _clean(body.message, 5000)

        if not message:
            raise HTTPException(status_code=400, detail="Please write a reply.")

        with _conn() as c:
            row = c.execute(
                """
                SELECT c.id, c.subject, u.name AS user_name, u.email AS user_email
                FROM support_conversations c
                JOIN users u ON u.id = c.user_id
                WHERE c.id = ?
                """,
                (conversation_id,),
            ).fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")

            previous = c.execute(
                "SELECT created_at FROM support_messages WHERE conversation_id = ? "
                "AND sender_type = 'admin' ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()

        add_support_message(conversation_id, "admin", admin["id"], message)

        with _conn() as c:
            # Waiting on the user now.
            c.execute(
                "UPDATE support_conversations SET status = 'pending' WHERE id = ?",
                (conversation_id,),
            )

        # Tell the user a reply is waiting, but never spam a running exchange.
        emailed = False
        last_admin = _parse(previous["created_at"]) if previous else None
        recent = (
            last_admin is not None
            and datetime.now(timezone.utc) - last_admin < timedelta(minutes=EMAIL_REPLY_COOLDOWN_MINUTES)
        )

        if send_email is not None and not recent:

            name = str(row["user_name"] or "there").replace("<", "").replace(">", "")
            subject_line = str(row["subject"] or "your message").replace("<", "").replace(">", "")

            try:
                await send_email(
                    recipient_email=str(row["user_email"]).strip().lower(),
                    subject="You have a reply from Clip Pirate support",
                    html_content=(
                        "<html><body style='font-family:Arial,sans-serif;line-height:1.5'>"
                        f"<p>Hi {name},</p>"
                        f"<p>We replied to your message <b>{subject_line}</b>.</p>"
                        f"<p><a href='{APP_URL}/app/' style='background:#7c5cff;color:#fff;"
                        "padding:11px 18px;border-radius:8px;text-decoration:none;font-weight:bold'>"
                        "Open Settings &rarr; Help</a></p></body></html>"
                    ),
                    text_content=(
                        f"Hi {name},\n\nWe replied to your message \"{subject_line}\".\n"
                        f"Open Settings > Help to read it: {APP_URL}/app/\n"
                    ),
                )
                emailed = True
            except Exception as error:
                print(f"[SUPPORT] Reply notification failed: {type(error).__name__}: {error}", flush=True)

        return {"ok": True, "emailed": emailed}

    @app.post("/admin/api/support/thread/{conversation_id}/status")
    def admin_thread_status(request: Request, conversation_id: str, body: StatusBody):

        require_admin(request)
        _ensure_schema()

        if get_support_conversation(conversation_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")

        try:
            update_conversation_status(conversation_id, body.status or "")
        except ValueError:
            raise HTTPException(status_code=400, detail="Status must be open, pending, resolved or closed.")

        return {"ok": True}
