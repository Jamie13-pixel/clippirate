import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

# All persistent data (database, videos) lives under DATA_DIR.
# On Render, set DATA_DIR to the mount path of a persistent disk,
# otherwise everything is erased on each deploy / restart.
DATA_DIR = os.getenv(
    "DATA_DIR",
    "data"
)

DB_PATH = os.getenv(
    "CLIP_PIRATE_DB",
    os.path.join(DATA_DIR, "clip_pirate.db")
)


# ============================================================
# LEGACY CREDIT CONFIGURATION
# ============================================================
#
# Kept temporarily for compatibility with the existing system.
# Daily video generation limits are now controlled by plans.
#

FREE_CREDITS = int(
    os.getenv("FREE_MONTHLY_CREDITS", "6")
)

PRO_CREDITS = int(
    os.getenv("PRO_MONTHLY_CREDITS", "500")
)


# ============================================================
# SUBSCRIPTION PERIOD
# ============================================================

# Length of one paid period. Each successful payment buys this many days.
SUBSCRIPTION_DAYS = int(
    os.getenv("SUBSCRIPTION_DAYS", "30")
)

# Paid users who existed before expiry was introduced get at least
# this many days before they are moved back to the free plan.
LEGACY_GRACE_DAYS = int(
    os.getenv("LEGACY_GRACE_DAYS", "7")
)


# ============================================================
# SUBSCRIPTION PLANS
# ============================================================

PLAN_CONFIG = {

    "free": {
        "name": "Free",
        "credits": 6,
        "daily_limit": 2,
        "price": 0,
        "billing": "free",
    },

    "starter": {
        "name": "Starter",
        "credits": 150,
        "daily_limit": 10,
        "price": None,
        "billing": "monthly",
    },

    "pro": {
        "name": "Pro",
        "credits": 300,
        "daily_limit": 20,
        "price": None,
        "billing": "monthly",
    },

    "mega": {
        "name": "Mega",
        "credits": 600,
        "daily_limit": 40,
        "price": None,
        "billing": "monthly",
    },
}


# ============================================================
# DATABASE CONNECTION
# ============================================================

@contextmanager
def _conn():

    Path(DB_PATH).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

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


def _parse_dt(value):

    """Parse a stored timestamp (naive values are treated as UTC)."""

    if not value:

        return None

    try:

        parsed = datetime.fromisoformat(
            str(value)
        )

    except ValueError:

        return None

    if parsed.tzinfo is None:

        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


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

                email_verified INTEGER NOT NULL DEFAULT 1,

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


            CREATE TABLE IF NOT EXISTS email_verification_tokens (

                token_hash TEXT PRIMARY KEY,

                user_id TEXT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                expires_at TEXT NOT NULL,

                used_at TEXT,

                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_email_verification_user
                ON email_verification_tokens(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS password_reset_tokens (

                token_hash TEXT PRIMARY KEY,

                user_id TEXT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                expires_at TEXT NOT NULL,

                used_at TEXT,

                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_password_reset_user
                ON password_reset_tokens(user_id);


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

                narration_mode TEXT NOT NULL DEFAULT 'solo',

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


            CREATE TABLE IF NOT EXISTS payments (

                id TEXT PRIMARY KEY,

                user_id TEXT NOT NULL
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                plan TEXT NOT NULL,

                amount INTEGER NOT NULL,

                currency TEXT NOT NULL DEFAULT 'KES',

                provider TEXT NOT NULL,

                provider_reference TEXT,

                status TEXT NOT NULL DEFAULT 'pending',

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL
            );


            CREATE INDEX IF NOT EXISTS idx_payments_user
                ON payments(user_id, created_at DESC);


            CREATE INDEX IF NOT EXISTS idx_payments_reference
                ON payments(provider, provider_reference);


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

            "email_verified":
                "ALTER TABLE users ADD COLUMN "
                "email_verified INTEGER NOT NULL DEFAULT 1",

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

            # UTC date (YYYY-MM-DD) of the last daily free-credit reset.
            "credits_reset_on":
                "ALTER TABLE users ADD COLUMN "
                "credits_reset_on TEXT",
        }
        project_columns = {
            row["name"]
            for row in c.execute(
                "PRAGMA table_info(projects)"
            ).fetchall()
        }
        

        for column, sql in migrations.items():
           
            if "narration_mode" not in project_columns:

                c.execute(
                    "ALTER TABLE projects ADD COLUMN "
                    "narration_mode TEXT NOT NULL DEFAULT 'solo'"
                )
            if column not in columns:

                c.execute(sql)

        # ----------------------------------------------------
        # GIVE EXISTING PAID USERS AN EXPIRY DATE
        # ----------------------------------------------------
        #
        # Paid users created before expiry existed have no
        # subscription_expires_at. Base it on their last payment, but
        # never earlier than LEGACY_GRACE_DAYS from now so nobody is
        # downgraded by surprise the moment this is deployed.
        #

        now_utc = datetime.now(
            timezone.utc
        )

        paid_without_expiry = c.execute(
            """
            SELECT id, subscription_started_at
            FROM users
            WHERE
                plan != 'free'
                AND subscription_expires_at IS NULL
            """
        ).fetchall()

        for row in paid_without_expiry:

            last_payment = c.execute(
                """
                SELECT updated_at
                FROM payments
                WHERE
                    user_id = ?
                    AND status = 'paid'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (
                    row["id"],
                )
            ).fetchone()

            base = (
                _parse_dt(
                    last_payment["updated_at"]
                    if last_payment
                    else None
                )
                or _parse_dt(
                    row["subscription_started_at"]
                )
                or now_utc
            )

            expires = max(
                base + timedelta(days=SUBSCRIPTION_DAYS),
                now_utc + timedelta(days=LEGACY_GRACE_DAYS),
            )

            c.execute(
                """
                UPDATE users

                SET
                    subscription_expires_at = ?,
                    reset_date = ?

                WHERE id = ?
                """,
                (
                    expires.isoformat(),
                    expires.date().isoformat(),
                    row["id"],
                )
            )

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
                    email_verified,
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
                    ?,
                    ?
                )
                """,
                (
                    user_id,
                    name.strip(),
                    normalized_email,
                    _hash_password(password),
                    0,
                    plan,
                    PLAN_CONFIG[plan]["credits"],
                    PLAN_CONFIG[plan]["credits"],
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


def store_email_verification_token(user_id, raw_token, expires_at):
    """Store only a hash of a one-time email-verification token."""
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with _conn() as c:
        c.execute(
            "DELETE FROM email_verification_tokens WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        )
        c.execute(
            """
            INSERT INTO email_verification_tokens(
                token_hash, user_id, expires_at, used_at, created_at
            ) VALUES (?, ?, ?, NULL, ?)
            """,
            (token_hash, user_id, expires_at, now_iso()),
        )


def verify_email_token(raw_token):
    """Atomically consume a valid, unexpired token and verify its account."""
    if not raw_token:
        return False
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = now_iso()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            """
            SELECT user_id FROM email_verification_tokens
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if not row:
            return False
        updated = c.execute(
            "UPDATE email_verification_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now, token_hash),
        )
        if updated.rowcount != 1:
            return False
        c.execute(
            "UPDATE users SET email_verified = 1 WHERE id = ?",
            (row["user_id"],),
        )
        c.execute(
            "DELETE FROM email_verification_tokens WHERE user_id = ? AND token_hash != ?",
            (row["user_id"], token_hash),
        )
        return True

def store_password_reset_token(user_id, raw_token, expires_at):
    """Store only a hash of a one-time reset token (one live token per user)."""
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with _conn() as c:
        c.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ?",
            (user_id,),
        )
        c.execute(
            """
            INSERT INTO password_reset_tokens(
                token_hash, user_id, expires_at, used_at, created_at
            ) VALUES (?, ?, ?, NULL, ?)
            """,
            (token_hash, user_id, expires_at, now_iso()),
        )


def reset_password_with_token(raw_token, new_password):
    """Consume a valid token, set the new password, sign out every device."""
    if not raw_token:
        return False
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = now_iso()
    with _conn() as c:
        row = c.execute(
            """
            SELECT user_id FROM password_reset_tokens
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if not row:
            return False
        claimed = c.execute(
            """
            UPDATE password_reset_tokens SET used_at = ?
            WHERE token_hash = ? AND used_at IS NULL
            """,
            (now, token_hash),
        )
        if claimed.rowcount != 1:
            return False
        c.execute(
            "UPDATE users SET password_hash = ?, email_verified = 1 WHERE id = ?",
            (_hash_password(new_password), row["user_id"]),
        )
        c.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        c.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (row["user_id"],))
        return True

def is_email_verified(user):
    """Handle SQLite rows and dictionaries consistently."""
    if not user:
        return False
    try:
        return bool(user["email_verified"])
    except (KeyError, IndexError, TypeError):
        return False


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

        return _refresh_user(
            c,
            user
        )


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

        user = c.execute(
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

        return _refresh_user(
            c,
            user
        )


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

def delete_expired_auth_rows():

    """
    Remove expired sessions and expired email-verification / password-reset
    tokens. Returns (sessions, verification_tokens, reset_tokens) removed.
    """

    now = now_iso()

    with _conn() as c:

        sessions = c.execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (now,)
        ).rowcount

        verification = c.execute(
            "DELETE FROM email_verification_tokens WHERE expires_at < ?",
            (now,)
        ).rowcount

        resets = c.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at < ?",
            (now,)
        ).rowcount

    return sessions, verification, resets


# ============================================================
# SUBSCRIPTION EXPIRY
# ============================================================

def _expire_subscription(
    c,
    user
):

    """
    Move a paid user back to the free plan once their paid period
    has ended.

    Runs lazily whenever the user is loaded. The WHERE clause only
    matches if the expiry date is unchanged, so a payment that
    renews the subscription at the same moment is never overwritten.
    """

    if user is None:

        return user

    plan = str(
        user["plan"] or "free"
    ).lower()

    if plan == "free":

        return user

    expires_at = _parse_dt(
        user["subscription_expires_at"]
    )

    if (
        expires_at is None
        or expires_at > datetime.now(timezone.utc)
    ):

        return user

    free = PLAN_CONFIG["free"]

    tomorrow = (
        datetime.now(timezone.utc).date()
        + timedelta(days=1)
    ).isoformat()

    c.execute(
        """
        UPDATE users

        SET
            plan = 'free',
            daily_generation_limit = ?,
            monthly_limit = ?,
            credits = ?,
            subscription_status = 'expired',
            credits_reset_on = ?,
            reset_date = ?

        WHERE
            id = ?
            AND plan = ?
            AND subscription_expires_at = ?
        """,
        (
            free["daily_limit"],
            free["credits"],
            free["credits"],
            today_iso(),
            tomorrow,
            user["id"],
            user["plan"],
            user["subscription_expires_at"],
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


def _refresh_user(
    c,
    user
):

    """Apply subscription expiry, then the free daily credit reset."""

    user = _expire_subscription(
        c,
        user
    )

    return _free_daily_reset(
        c,
        user
    )


# ============================================================
# FREE PLAN DAILY CREDIT RESET
# ============================================================

def _free_daily_reset(
    c,
    user
):

    """
    Free-plan users get their credits set back to the plan amount
    once per UTC day.

    The reset happens lazily whenever the user is loaded, so it
    needs no scheduler and survives server restarts and sleeping
    instances. The WHERE clause makes it atomic: concurrent
    requests can never reset the same user twice in one day.
    """

    if user is None:

        return user

    plan = str(
        user["plan"] or "free"
    ).lower()

    if plan != "free":

        return user

    today = today_iso()

    if user["credits_reset_on"] == today:

        return user

    amount = PLAN_CONFIG["free"]["credits"]

    tomorrow = (
        datetime.now(timezone.utc).date()
        + timedelta(days=1)
    ).isoformat()

    c.execute(
        """
        UPDATE users

        SET
            credits = ?,
            monthly_limit = ?,
            reset_date = ?,
            credits_reset_on = ?

        WHERE
            id = ?
            AND plan = 'free'
            AND (
                credits_reset_on IS NULL
                OR credits_reset_on < ?
            )
        """,
        (
            amount,
            amount,
            tomorrow,
            today,
            user["id"],
            today,
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


# ============================================================
# LEGACY CREDIT RESET
# ============================================================

def _reset_if_needed(
    c,
    user
):

    # Free plan resets daily in _free_daily_reset(). Paid plans get
    # their credits when a payment is made and keep them for the
    # paid period, so only legacy rows without an expiry date use
    # this monthly reset.
    if (
        str(user["plan"] or "free").lower() == "free"
        or user["subscription_expires_at"]
    ):

        return user

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

        user = _refresh_user(
            c,
            user
        )

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

def plan_daily_limit(user):

    """
    Daily generation limit for a user, derived from their
    current plan (-1 means unlimited).
    """

    plan = str(
        user["plan"] or "free"
    ).lower()

    return int(
        PLAN_CONFIG.get(
            plan,
            PLAN_CONFIG["free"]
        )["daily_limit"]
    )


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

        # --------------------------------------------------------
    # PLAN CREDIT ALLOCATION
    # --------------------------------------------------------

    monthly_credits = config["credits"]

    started_at = now_iso()

    if plan == "free":

        subscription_expires_at = None

        next_reset = (
            datetime.now(timezone.utc).date()
            + timedelta(days=1)
        ).isoformat()

    else:

        if not subscription_expires_at:

            subscription_expires_at = (
                datetime.now(timezone.utc)
                + timedelta(days=SUBSCRIPTION_DAYS)
            ).isoformat()

        next_reset = subscription_expires_at[:10]

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

                subscription_expires_at = ?,

                reset_date = ?

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
                next_reset,
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
# PAYMENT FUNCTIONS
# ============================================================

def create_payment(
    user_id,
    plan,
    amount,
    provider,
    currency="KES"
):

    payment_id = secrets.token_urlsafe(24)
    now = now_iso()

    with _conn() as c:
        c.execute(
            """
            INSERT INTO payments (
                id,
                user_id,
                plan,
                amount,
                currency,
                provider,
                provider_reference,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payment_id,
                user_id,
                plan.lower(),
                float(amount),
                currency,
                provider,
                None,
                "pending",
                now,
                now,
            )
        )

    return payment_id


def get_payment(payment_id):

    with _conn() as c:
        return c.execute(
            """
            SELECT *
            FROM payments
            WHERE id = ?
            """,
            (payment_id,)
        ).fetchone()


def get_payment_by_reference(provider, provider_reference):

    with _conn() as c:
        return c.execute(
            """
            SELECT *
            FROM payments
            WHERE provider = ?
              AND provider_reference = ?
            """,
            (
                provider,
                provider_reference,
            )
        ).fetchone()


def _extended_expiry(
    user,
    plan
):

    """
    Expiry date after a successful payment.

    Renewing the same plan while it is still active adds a new
    period on top of the remaining time. Anything else (first
    purchase, expired plan, switching plans) starts a fresh period
    from today.
    """

    now = datetime.now(
        timezone.utc
    )

    base = now

    if (
        user is not None
        and str(user["plan"] or "").lower() == plan.lower()
    ):

        current = _parse_dt(
            user["subscription_expires_at"]
        )

        if current is not None and current > now:

            base = current

    return (
        base + timedelta(days=SUBSCRIPTION_DAYS)
    ).isoformat()


def complete_payment(
    payment_id,
    provider_reference=None
):

    now = now_iso()

    with _conn() as c:

        payment = c.execute(
            """
            SELECT *
            FROM payments
            WHERE id = ?
            """,
            (payment_id,)
        ).fetchone()

        if payment is None:
            raise ValueError("Payment not found")

        # Idempotency: never activate a payment twice.
        if payment["status"] == "paid":
            return payment

        if payment["status"] != "pending":
            raise ValueError(
                f"Payment cannot be completed from status: {payment['status']}"
            )

        c.execute(
            """
            UPDATE payments
            SET
                status = 'paid',
                provider_reference = COALESCE(?, provider_reference),
                updated_at = ?
            WHERE id = ?
              AND status = 'pending'
            """,
            (
                provider_reference,
                now,
                payment_id,
            )
        )

        updated = c.execute(
            """
            SELECT *
            FROM payments
            WHERE id = ?
            """,
            (payment_id,)
        ).fetchone()

    # Activate the subscription only after payment is marked paid.
    set_plan(
        updated["user_id"],
        updated["plan"],
        subscription_status="active",
        subscription_expires_at=_extended_expiry(
            get_user(updated["user_id"]),
            updated["plan"],
        ),
    )

    return get_payment(payment_id)


def fail_payment(payment_id):

    now = now_iso()

    with _conn() as c:
        c.execute(
            """
            UPDATE payments
            SET
                status = 'failed',
                updated_at = ?
            WHERE id = ?
              AND status = 'pending'
            """,
            (
                now,
                payment_id,
            )
        )

    return get_payment(payment_id)


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

    return plan_daily_limit(
        user
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

        user = _refresh_user(
            c,
            user
        )

        limit = plan_daily_limit(
            user
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

    limit = plan_daily_limit(
        user
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
    credits_cost,
    narration_mode="solo"
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
                narration_mode,
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
                narration_mode,
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


def expire_project_video(
    video_url
):

    """
    Called after a stored video file is deleted to free disk space.

    Clears the link so the app stops offering Open / Download
    buttons for a file that no longer exists.
    """

    with _conn() as c:

        c.execute(
            """
            UPDATE projects

            SET
                video_url = NULL,
                status = 'expired'

            WHERE
                video_url = ?
            """,
            (
                video_url,
            )
        )

def list_expired_videos(free_days, paid_days):

    """Projects whose stored video is older than their plan's retention."""

    now = datetime.now(timezone.utc)

    free_cutoff = (now - timedelta(days=free_days)).isoformat()
    paid_cutoff = (now - timedelta(days=paid_days)).isoformat()

    with _conn() as c:

        return c.execute(
            """
            SELECT p.id, p.video_url

            FROM projects p

            JOIN users u
                ON u.id = p.user_id

            WHERE
                p.video_url IS NOT NULL
                AND p.video_url != ''
                AND (
                    (u.plan = 'free' AND p.created_at < ?)
                    OR (u.plan != 'free' AND p.created_at < ?)
                )
            """,
            (free_cutoff, paid_cutoff)
        ).fetchall()


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

def user_owns_video(user_id, video_url):

    """True only if this user has a project whose stored video is video_url."""

    with _conn() as c:

        row = c.execute(
            """
            SELECT 1

            FROM projects

            WHERE
                user_id = ?
                AND video_url = ?

            LIMIT 1
            """,
            (
                user_id,
                video_url,
            )
        ).fetchone()

    return row is not None

# ============================================================
# RECOVERY OF INTERRUPTED JOBS
# ============================================================

def recover_stale_jobs(max_age_minutes=5):

    """
    Fail jobs left 'pending' or 'processing' by a restart, and give the
    user their credits and daily slot back. Each job is claimed with a
    conditional UPDATE, so it can never be refunded twice.
    """

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(minutes=max_age_minutes)
    ).isoformat()

    today = today_iso()

    message = (
        "Generation was interrupted by a server restart. "
        "Your credits were refunded. Please try again."
    )

    recovered = 0

    with _conn() as c:

        stale = c.execute(
            """
            SELECT
                j.id AS job_id,
                j.user_id,
                j.project_id,
                j.created_at,
                p.credits_cost

            FROM jobs j

            JOIN projects p
                ON p.id = j.project_id

            WHERE
                j.status IN ('pending', 'processing')
                AND j.created_at < ?
            """,
            (cutoff,)
        ).fetchall()

        for row in stale:

            claimed = c.execute(
                """
                UPDATE jobs

                SET
                    status = 'failed',
                    error = ?,
                    result = NULL

                WHERE
                    id = ?
                    AND status IN ('pending', 'processing')
                """,
                (message, row["job_id"])
            )

            if claimed.rowcount != 1:
                continue

            c.execute(
                "UPDATE projects SET status = 'failed', error = ? WHERE id = ?",
                (message, row["project_id"])
            )

            c.execute(
                """
                UPDATE users
                SET credits = MIN(monthly_limit, credits + ?)
                WHERE id = ?
                """,
                (row["credits_cost"], row["user_id"])
            )

            # Only today's counter can be refunded; an older job's day is over.
            if str(row["created_at"])[:10] == today:

                c.execute(
                    """
                    UPDATE daily_generation_usage

                    SET
                        generation_count = MAX(generation_count - 1, 0),
                        updated_at = ?

                    WHERE
                        user_id = ?
                        AND usage_date = ?
                    """,
                    (now_iso(), row["user_id"], today)
                )

            recovered += 1

    return recovered


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

        "email_verified":
            bool(user["email_verified"]),

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
            plan_daily_limit(user),

        "subscription_status":
            user["subscription_status"],

        "subscription_started_at":
            user["subscription_started_at"],

        "subscription_expires_at":
            user["subscription_expires_at"],

        "reset_date":
            user["reset_date"],
    }