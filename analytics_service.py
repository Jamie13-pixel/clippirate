"""
Per-user analytics for Clip Pirate.

Everything here is computed from the `projects` table, so no new tables are
needed. The only schema change is one optional column, projects.template_id,
which is added automatically the first time it is needed.

Used by main.py:

    from analytics_service import build_analytics, set_project_template

    GET /analytics  ->  build_analytics(user)
    after create_project(...)  ->  set_project_template(project_id, body.template_id)

Nothing in this module may break video generation, so the write path never
raises.
"""

from datetime import datetime, timedelta, timezone

from db import _conn, today_iso

WINDOW_DAYS = 30

_FAILED = "failed"
_DONE = ("completed", "expired")       # expired = finished, file since deleted
_RUNNING = ("processing", "pending")

_schema_ready = False


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
            for row in c.execute("PRAGMA table_info(projects)").fetchall()
        }

        if "template_id" not in columns:
            c.execute("ALTER TABLE projects ADD COLUMN template_id TEXT")

    _schema_ready = True


def set_project_template(project_id, template_id):
    """Remember which template a video used. Never raises."""

    template_id = str(template_id or "").strip()[:60]

    if not template_id:
        return

    try:
        _ensure_schema()

        with _conn() as c:
            c.execute(
                "UPDATE projects SET template_id = ? WHERE id = ?",
                (template_id, project_id),
            )

    except Exception as error:
        print(f"[analytics] Could not save template for {project_id}: {error}")


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

def _pretty(key):
    return str(key or "").replace("-", " ").replace("_", " ").strip().title()


def _template_label(template_id):

    if not template_id:
        return "No template"

    try:
        from templates_service import get_template

        template = get_template(template_id)

        if template and template.get("name"):
            return template["name"]

    except Exception:
        pass

    return _pretty(template_id)


def _tally(rows, field, label):
    """Count rows by a field, biggest first. label(value) -> display text."""

    counts = {}

    for row in rows:
        key = row[field]
        counts[key] = counts.get(key, 0) + 1

    ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))

    return [
        {"label": label(key), "count": count}
        for key, count in ordered[:6]
    ]


def _days_until(value):

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


# ------------------------------------------------------------
# MAIN ENTRY
# ------------------------------------------------------------

def build_analytics(user, days=WINDOW_DAYS):
    """Analytics for one user. All days are UTC, like the daily limit."""

    _ensure_schema()

    user_id = user["id"]

    today = datetime.fromisoformat(today_iso()).date()
    first_day = today - timedelta(days=days - 1)

    with _conn() as c:

        rows = c.execute(
            """
            SELECT
                substr(created_at, 1, 10) AS day,
                status,
                duration,
                aspect_ratio,
                voice,
                narration_mode,
                template_id,
                credits_cost

            FROM projects

            WHERE user_id = ? AND created_at >= ?
            """,
            (user_id, first_day.isoformat()),
        ).fetchall()

        modes = c.execute(
            """
            SELECT narration_mode, COUNT(*) AS n

            FROM projects

            WHERE user_id = ? AND status != 'failed'

            GROUP BY narration_mode
            """,
            (user_id,),
        ).fetchall()

        recent = c.execute(
            """
            SELECT topic, status, duration, credits_cost, narration_mode,
                   created_at

            FROM projects

            WHERE user_id = ?

            ORDER BY created_at DESC

            LIMIT 10
            """,
            (user_id,),
        ).fetchall()

    # Videos that actually used credits (failed ones are refunded).
    made = [r for r in rows if r["status"] != _FAILED]

    per_day = {}
    for r in made:
        per_day[r["day"]] = per_day.get(r["day"], 0) + 1

    series = []
    for offset in range(days):
        day = (first_day + timedelta(days=offset)).isoformat()
        series.append({"date": day, "count": per_day.get(day, 0)})

    completed = sum(1 for r in rows if r["status"] in _DONE)
    failed = sum(1 for r in rows if r["status"] == _FAILED)
    running = sum(1 for r in rows if r["status"] in _RUNNING)

    finished = completed + failed
    success_rate = round(completed * 100 / finished) if finished else None

    spent_30 = sum(int(r["credits_cost"] or 0) for r in made)

    week_start = (today - timedelta(days=6)).isoformat()
    spent_7 = sum(
        int(r["credits_cost"] or 0) for r in made if r["day"] >= week_start
    )

    plan = str(user["plan"] or "free").lower()
    balance = int(user["credits"] or 0)

    runway = None
    if plan != "free" and spent_7 > 0:
        runway = max(0, int(balance / (spent_7 / 7)))

    mode_counts = {"solo": 0, "dialogue": 0}
    for row in modes:
        key = str(row["narration_mode"] or "solo").lower()
        mode_counts[key if key in mode_counts else "solo"] += int(row["n"])

    return {
        "days": days,
        "series": series,
        "total": len(made),

        "credits": {
            "balance": balance,
            "spent_30d": spent_30,
            "spent_7d": spent_7,
            "runway_days": runway,
            "refills_daily": plan == "free",
            "plan": plan,
            "plan_days_left": _days_until(user["subscription_expires_at"])
            if plan != "free" else None,
        },

        "outcomes": {
            "completed": completed,
            "failed": failed,
            "in_progress": running,
            "success_rate": success_rate,
        },

        "breakdown": {
            "length": _tally(made, "duration", lambda v: f"{v} seconds"),
            "format": _tally(made, "aspect_ratio", lambda v: str(v)),
            "voice": _tally(made, "voice", _pretty),
            "template": _tally(made, "template_id", _template_label),
            "narration": _tally(made, "narration_mode", _pretty),
        },

        "narration_counts": mode_counts,

        "recent": [
            {
                "topic": str(r["topic"] or "")[:90],
                "status": str(r["status"] or ""),
                "duration": r["duration"],
                "credits": r["credits_cost"],
                "mode": r["narration_mode"],
                "created_at": r["created_at"],
            }
            for r in recent
        ],
    }
