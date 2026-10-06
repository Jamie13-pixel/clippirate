import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_PATH = os.getenv(
    "CLIP_PIRATE_DB",
    "data/clip_pirate.db"
)


# ============================================================
# LEGACY CREDIT CONFIGURATION
# ============================================================
#
# Kept temporarily for compatibility with the existing system.
# Daily video generation limits are now controlled by plans.
#

FREE_CREDITS = int(
    os.getenv("FREE_MONTHLY_CREDITS", "50")
)

PRO_CREDITS = int(
    os.getenv("PRO_MONTHLY_CREDITS", "500")
)


# ============================================================
# SUBSCRIPTION PLANS
# ============================================================

PLAN_CONFIG = {

    "free": {
        "name": "Free",
        "daily_limit": 5,
        "price": 0,
        "billing": "free",
    },

    "creator": {
        "name": "Creator",
        "daily_limit": 15,
        "price": None,
        "billing": "monthly",
    },

    "pro": {
        "name": "Pro",
        "daily_limit": 30,
        "price": None,
        "billing": "monthly",
    },

    "mega": {
        "name": "Mega",
        "daily_limit": -1,
        "price": None,
        "billing": "monthly",
    },
}


# ============================================================
# DATABASE CONNECTION
# ============================================================

def _conn():

    Path(DB_PATH).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    conn = sqlite3.connect(
        DB_PATH
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conn


# ============================================================
# TIME
# ============================================================

def now_iso():

    return datetime.now(
        timezone.utc
    ).isoformat()


def today_iso():

    return datetime.now(
        timezone.utc
    ).date().isoformat()


def _next_month_start():

    today = datetime.now(
        timezone.utc
    ).date()

    if today.month == 12:

        return today.replace(
            year=today.year + 1,
            month=1,
            day=1
        )

    return today.replace(
        month=today.month + 1,
        day=1
    )


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():

    with _conn() as c:

        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (

                id TEXT PRIMARY KEY,

                name TEXT NOT NULL,

                email TEXT NOT NULL UNIQUE,

                password_hash TEXT NOT NULL,

                plan TEXT NOT NULL DEFAULT 'free',

                credits INTEGER NOT NULL DEFAULT 50,

                monthly_limit INTEGER NOT NULL DEFAULT 50,

                reset_date TEXT NOT NULL,

                created_at TEXT NOT NULL,

                subscription_status TEXT
                    NOT NULL DEFAULT 'active',

                subscription_started_at TEXT,

                subscription_expires_at TEXT,

                daily_generation_limit INTEGER
                    NOT NULL DEFAULT 5
            );


            CREATE TABLE IF NOT EXISTS sessions (

                token_hash TEXT PRIMARY KEY,

                user_id TEXT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                expires_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS projects (

                id TEXT PRIMARY KEY,

                user_id TEXT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                topic TEXT NOT NULL,

                duration INTEGER NOT NULL,

                aspect_ratio TEXT NOT NULL,

                voice TEXT NOT NULL,

                captions INTEGER NOT NULL,

                status TEXT NOT NULL,

                script TEXT,

                video_url TEXT,

                error TEXT,

                credits_cost INTEGER NOT NULL DEFAULT 1,

                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS jobs (

                id TEXT PRIMARY KEY,

                user_id TEXT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                project_id TEXT NOT NULL
                    REFERENCES projects(id)
                    ON DELETE CASCADE,

                status TEXT NOT NULL,

                result TEXT,

                error TEXT,

                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS daily_generation_usage (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id TEXT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                usage_date TEXT NOT NULL,

                generation_count INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                UNIQUE(user_id, usage_date)
            );


            CREATE INDEX IF NOT EXISTS idx_projects_user

                ON projects(
                    user_id,
                    created_at DESC
                );


            CREATE INDEX IF NOT EXISTS idx_jobs_user

                ON jobs(
                    user_id,
                    created_at DESC
                );


            CREATE INDEX IF NOT EXISTS idx_jobs_project

                ON jobs(project_id);


            CREATE INDEX IF NOT EXISTS idx_sessions_user

                ON sessions(user_id);


            CREATE INDEX IF NOT EXISTS idx_generation_usage_user

                ON daily_generation_usage(
                    user_id,
                    usage_date
                );
            """
        )

        # ----------------------------------------------------
        # SAFE MIGRATION FOR EXISTING DATABASES
        # ----------------------------------------------------

        columns = {
            row["name"]
            for row in c.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        }

        migrations = {

            "subscription_status":
                "ALTER TABLE users ADD COLUMN "
                "subscription_status TEXT NOT NULL "
                "DEFAULT 'active'",

            "subscription_started_at":
                "ALTER TABLE users ADD COLUMN "
                "subscription_started_at TEXT",

            "subscription_expires_at":
                "ALTER TABLE users ADD COLUMN "
                "subscription_expires_at TEXT",

            "daily_generation_limit":
                "ALTER TABLE users ADD COLUMN "
                "daily_generation_limit INTEGER "
                "NOT NULL DEFAULT 5",
        }

        for column, sql in migrations.items():

            if column not in columns:

                c.execute(sql)

        # ----------------------------------------------------
        # SYNCHRONIZE EXISTING USERS WITH THEIR PLAN
        # ----------------------------------------------------

        users = c.execute(
            "SELECT id, plan, daily_generation_limit "
            "FROM users"
        ).fetchall()

        for user in users:

            plan = (
                user["plan"]
                or "free"
            ).lower()

            config = PLAN_CONFIG.get(
                plan,
                PLAN_CONFIG["free"]
            )

            c.execute(
                """
                UPDATE users

                SET daily_generation_limit = ?

                WHERE id = ?
                """,
                (
                    config["daily_limit"],
                    user["id"],
                )
            )


# ============================================================
# PASSWORD SECURITY
# ============================================================

def _hash_password(
    password,
    salt=None
):

    salt = (
        salt
        or secrets.token_bytes(16)
    )

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        210_000
    )

    return (
        salt.hex()
        + ":"
        + digest.hex()
    )


def verify_password(
    password,
    stored
):

    try:

        salt_hex, digest_hex = (
            stored.split(":", 1)
        )

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt_hex),
            210_000
        ).hex()

        return hmac.compare_digest(
            actual,
            digest_hex
        )

    except Exception:

        return False


# ============================================================
# USERS
# ============================================================

def create_user(
    name,
    email,
    password
):

    user_id = secrets.token_hex(16)

    normalized_email = (
        email.strip().lower()
    )

    reset_date = (
        _next_month_start().isoformat()
    )

    plan = "free"

    daily_limit = PLAN_CONFIG[
        plan
    ]["daily_limit"]

    with _conn() as c:

        try:

            c.execute(
                """
                INSERT INTO users(

                    id,
                    name,
                    email,
                    password_hash,
                    plan,
                    credits,
                    monthly_limit,
                    reset_date,
                    created_at,
                    subscription_status,
                    subscription_started_at,
                    subscription_expires_at,
                    daily_generation_limit

                )

                VALUES(
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    user_id,
                    name.strip(),
                    normalized_email,
                    _hash_password(password),
                    plan,
                    FREE_CREDITS,
                    FREE_CREDITS,
                    reset_date,
                    now_iso(),
                    "active",
                    now_iso(),
                    None,
                    daily_limit,
                )
            )

        except sqlite3.IntegrityError as exc:

            if (
                "users.email"
                in str(exc).lower()
                or
                "unique constraint failed: users.email"
                in str(exc).lower()
            ):

                raise ValueError(
                    "An account with that email already exists."
                ) from exc

            raise

    return user_id


def get_user_by_email(email):

    with _conn() as c:

        return c.execute(
            """
            SELECT *
            FROM users
            WHERE email = ?
            """,
            (
                email.strip().lower(),
            )
        ).fetchone()


def get_user(user_id):

    with _conn() as c:

        return c.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (
                user_id,
            )
        ).fetchone()


# ============================================================
# SESSIONS
# ============================================================

def create_session(
    user_id,
    days=30
):

    token = secrets.token_urlsafe(32)

    token_hash = hashlib.sha256(
        token.encode()
    ).hexdigest()

    expires = (
        datetime.now(
            timezone.utc
        ).timestamp()
        + days * 86400
    )

    expires_iso = datetime.fromtimestamp(
        expires,
        tz=timezone.utc
    ).isoformat()

    with _conn() as c:

        c.execute(
            """
            INSERT INTO sessions(
                token_hash,
                user_id,
                expires_at
            )

            VALUES(
                ?,
                ?,
                ?
            )
            """,
            (
                token_hash,
                user_id,
                expires_iso,
            )
        )

    return token


def get_user_by_session(token):

    if not token:

        return None

    token_hash = hashlib.sha256(
        token.encode()
    ).hexdigest()

    with _conn() as c:

        return c.execute(
            """
            SELECT u.*

            FROM users u

            JOIN sessions s
                ON s.user_id = u.id

            WHERE
                s.token_hash = ?
                AND s.expires_at > ?
            """,
            (
                token_hash,
                now_iso(),
            )
        ).fetchone()


def delete_session(token):

    if not token:

        return

    token_hash = hashlib.sha256(
        token.encode()
    ).hexdigest()

    with _conn() as c:

        c.execute(
            """
            DELETE FROM sessions
            WHERE token_hash = ?
            """,
            (
                token_hash,
            )
        )


# ============================================================
# LEGACY CREDIT RESET
# ============================================================

def _reset_if_needed(
    c,
    user
):

    today = datetime.now(
        timezone.utc
    ).date()

    reset = datetime.fromisoformat(
        user["reset_date"]
    ).date()

    if today >= reset:

        next_reset = (
            _next_month_start()
        )

        c.execute(
            """
            UPDATE users

            SET
                credits = monthly_limit,
                reset_date = ?

            WHERE id = ?
            """,
            (
                next_reset.isoformat(),
                user["id"],
            )
        )

        return c.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (
                user["id"],
            )
        ).fetchone()

    return user


# ============================================================
# LEGACY CREDIT FUNCTIONS
# ============================================================

def reserve_credits(
    user_id,
    cost
):

    with _conn() as c:

        user = c.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (
                user_id,
            )
        ).fetchone()

        if not user:

            return False, None

        user = _reset_if_needed(
            c,
            user
        )

        if user["credits"] < cost:

            return False, user

        updated = c.execute(
            """
            UPDATE users

            SET credits = credits - ?

            WHERE
                id = ?
                AND credits >= ?
            """,
            (
                cost,
                user_id,
                cost,
            )
        )

        if updated.rowcount != 1:

            return (
                False,
                c.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE id = ?
                    """,
                    (
                        user_id,
                    )
                ).fetchone()
            )

        return (
            True,
            c.execute(
                """
                SELECT *
                FROM users
                WHERE id = ?
                """,
                (
                    user_id,
                )
            ).fetchone()
        )


def refund_credits(
    user_id,
    cost
):

    with _conn() as c:

        c.execute(
            """
            UPDATE users

            SET credits =
                MIN(
                    monthly_limit,
                    credits + ?
                )

            WHERE id = ?
            """,
            (
                cost,
                user_id,
            )
        )


# ============================================================
# SUBSCRIPTION PLAN FUNCTIONS
# ============================================================

def get_plan_config(plan):

    return PLAN_CONFIG.get(
        plan.lower(),
        PLAN_CONFIG["free"]
    )


def set_plan(
    user_id,
    plan,
    subscription_status="active",
    subscription_expires_at=None
):

    plan = plan.lower()

    if plan not in PLAN_CONFIG:

        raise ValueError(
            f"Unknown subscription plan: {plan}"
        )

    config = PLAN_CONFIG[
        plan
    ]

    # Keep the old credit system populated
    # while we transition to generation-based plans.

    if plan == "free":

        monthly_credits = FREE_CREDITS

    else:

        monthly_credits = PRO_CREDITS

    started_at = now_iso()

    with _conn() as c:

        c.execute(
            """
            UPDATE users

            SET

                plan = ?,

                daily_generation_limit = ?,

                monthly_limit = ?,

                credits = ?,

                subscription_status = ?,

                subscription_started_at = ?,

                subscription_expires_at = ?

            WHERE id = ?
            """,
            (
                plan,
                config["daily_limit"],
                monthly_credits,
                monthly_credits,
                subscription_status,
                started_at,
                subscription_expires_at,
                user_id,
            )
        )

        return c.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (
                user_id,
            )
        ).fetchone()


# ============================================================
# DAILY GENERATION USAGE
# ============================================================

def get_daily_generation_usage(
    user_id
):

    today = today_iso()

    with _conn() as c:

        row = c.execute(
            """
            SELECT generation_count

            FROM daily_generation_usage

            WHERE
                user_id = ?
                AND usage_date = ?
            """,
            (
                user_id,
                today,
            )
        ).fetchone()

        if not row:

            return 0

        return int(
            row["generation_count"]
        )


def get_generation_limit(
    user_id
):

    user = get_user(
        user_id
    )

    if not user:

        return 0

    return int(
        user["daily_generation_limit"]
    )


def get_remaining_generations(
    user_id
):

    limit = get_generation_limit(
        user_id
    )

    used = get_daily_generation_usage(
        user_id
    )

    # -1 means unlimited.

    if limit < 0:

        return -1

    return max(
        0,
        limit - used
    )


def reserve_generation(
    user_id
):

    """
    Reserve one daily video generation.

    Returns:

        True, user
            if generation is allowed.

        False, user
            if the daily plan allowance is exhausted.

        False, None
            if the user does not exist.
    """

    today = today_iso()

    with _conn() as c:

        user = c.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (
                user_id,
            )
        ).fetchone()

        if not user:

            return False, None

        limit = int(
            user["daily_generation_limit"]
        )

        current = c.execute(
            """
            SELECT generation_count

            FROM daily_generation_usage

            WHERE
                user_id = ?
                AND usage_date = ?
            """,
            (
                user_id,
                today,
            )
        ).fetchone()

        used = (
            int(current["generation_count"])
            if current
            else 0
        )

        # ----------------------------------------------------
        # CHECK DAILY PLAN LIMIT
        # ----------------------------------------------------

        if limit >= 0 and used >= limit:

            return False, user

        # ----------------------------------------------------
        # RECORD GENERATION
        # ----------------------------------------------------

        if current:

            c.execute(
                """
                UPDATE daily_generation_usage

                SET
                    generation_count =
                        generation_count + 1,

                    updated_at = ?

                WHERE
                    user_id = ?
                    AND usage_date = ?
                """,
                (
                    now_iso(),
                    user_id,
                    today,
                )
            )

        else:

            c.execute(
                """
                INSERT INTO daily_generation_usage(
                    user_id,
                    usage_date,
                    generation_count,
                    created_at,
                    updated_at
                )

                VALUES(
                    ?,
                    ?,
                    1,
                    ?,
                    ?
                )
                """,
                (
                    user_id,
                    today,
                    now_iso(),
                    now_iso(),
                )
            )

        return (
            True,
            c.execute(
                """
                SELECT *
                FROM users
                WHERE id = ?
                """,
                (
                    user_id,
                )
            ).fetchone()
        )


def refund_generation(
    user_id
):

    """
    Refund one daily generation when
    video generation fails.
    """

    today = today_iso()

    with _conn() as c:

        row = c.execute(
            """
            SELECT generation_count

            FROM daily_generation_usage

            WHERE
                user_id = ?
                AND usage_date = ?
            """,
            (
                user_id,
                today,
            )
        ).fetchone()

        if not row:

            return

        current = int(
            row["generation_count"]
        )

        if current <= 0:

            return

        c.execute(
            """
            UPDATE daily_generation_usage

            SET
                generation_count =
                    generation_count - 1,

                updated_at = ?

            WHERE
                user_id = ?
                AND usage_date = ?
            """,
            (
                now_iso(),
                user_id,
                today,
            )
        )


# ============================================================
# USER GENERATION SUMMARY
# ============================================================

def get_generation_summary(
    user_id
):

    user = get_user(
        user_id
    )

    if not user:

        return None

    limit = int(
        user["daily_generation_limit"]
    )

    used = get_daily_generation_usage(
        user_id
    )

    if limit < 0:

        remaining = -1

    else:

        remaining = max(
            0,
            limit - used
        )

    return {
        "plan": user["plan"],
        "plan_name": PLAN_CONFIG[
            user["plan"]
        ]["name"]
        if user["plan"] in PLAN_CONFIG
        else user["plan"],

        "subscription_status":
            user["subscription_status"],

        "daily_limit": limit,

        "generated_today": used,

        "remaining_today": remaining,

        "usage_date": today_iso(),

        "subscription_expires_at":
            user["subscription_expires_at"],
    }


# ============================================================
# PROJECTS
# ============================================================

def create_project(
    project_id,
    user_id,
    topic,
    duration,
    aspect_ratio,
    voice,
    captions,
    credits_cost
):

    with _conn() as c:

        c.execute(
            """
            INSERT INTO projects(

                id,
                user_id,
                topic,
                duration,
                aspect_ratio,
                voice,
                captions,
                status,
                credits_cost,
                created_at

            )

            VALUES(
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                project_id,
                user_id,
                topic,
                duration,
                aspect_ratio,
                voice,
                int(captions),
                "processing",
                credits_cost,
                now_iso(),
            )
        )


def update_project(
    project_id,
    user_id,
    **fields
):

    allowed = {
        "status",
        "script",
        "video_url",
        "error",
    }

    parts = []

    values = []

    for key, value in fields.items():

        if key in allowed:

            parts.append(
                f"{key}=?"
            )

            values.append(
                value
            )

    if not parts:

        return

    values += [
        project_id,
        user_id,
    ]

    with _conn() as c:

        c.execute(
            f"""
            UPDATE projects

            SET {", ".join(parts)}

            WHERE
                id = ?
                AND user_id = ?
            """,
            values
        )


def list_projects(
    user_id,
    limit=20
):

    with _conn() as c:

        return c.execute(
            """
            SELECT *

            FROM projects

            WHERE user_id = ?

            ORDER BY created_at DESC

            LIMIT ?
            """,
            (
                user_id,
                limit,
            )
        ).fetchall()


# ============================================================
# PUBLIC USER
# ============================================================

def public_user(user):

    if not user:

        return None

    plan = user["plan"]

    config = PLAN_CONFIG.get(
        plan,
        PLAN_CONFIG["free"]
    )

    return {

        "id":
            user["id"],

        "name":
            user["name"],

        "email":
            user["email"],

        "plan":
            plan,

        "plan_name":
            config["name"],

        # Legacy credit information.
        "credits":
            user["credits"],

        "credit_limit":
            user["monthly_limit"],

        # New subscription information.
        "daily_generation_limit":
            user["daily_generation_limit"],

        "subscription_status":
            user["subscription_status"],

        "subscription_started_at":
            user["subscription_started_at"],

        "subscription_expires_at":
            user["subscription_expires_at"],

        "reset_date":
            user["reset_date"],
    }