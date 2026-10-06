import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.getenv('CLIP_PIRATE_DB', 'data/clip_pirate.db')
FREE_CREDITS = int(os.getenv('FREE_MONTHLY_CREDITS', '50'))
PRO_CREDITS = int(os.getenv('PRO_MONTHLY_CREDITS', '500'))


def _conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            credits INTEGER NOT NULL DEFAULT 50,
            monthly_limit INTEGER NOT NULL DEFAULT 50,
            reset_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
                CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_projects_user
            ON projects(user_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_jobs_user
            ON jobs(user_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_jobs_project
            ON jobs(project_id);

        CREATE INDEX IF NOT EXISTS idx_sessions_user
            ON sessions(user_id);
        ''')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 210_000)
    return salt.hex() + ':' + digest.hex()


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split(':', 1)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), 210_000).hex()
        return hmac.compare_digest(actual, digest_hex)
    except Exception:
        return False


def create_user(name, email, password):
    user_id = secrets.token_hex(16)
    normalized_email = email.strip().lower()
    reset_date = _next_month_start().isoformat()
    with _conn() as c:
        try:
            c.execute(
                'INSERT INTO users(id,name,email,password_hash,plan,credits,monthly_limit,reset_date,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (user_id, name.strip(), normalized_email, _hash_password(password), 'free', FREE_CREDITS, FREE_CREDITS, reset_date, now_iso())
            )
        except sqlite3.IntegrityError as exc:
            if 'users.email' in str(exc).lower() or 'unique constraint failed: users.email' in str(exc).lower():
                raise ValueError('An account with that email already exists.') from exc
            raise
    return user_id


def get_user_by_email(email):
    with _conn() as c:
        return c.execute('SELECT * FROM users WHERE email=?', (email.strip().lower(),)).fetchone()


def get_user(user_id):
    with _conn() as c:
        return c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


def create_session(user_id, days=30):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = datetime.now(timezone.utc).timestamp() + days * 86400
    expires_iso = datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()
    with _conn() as c:
        c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)', (token_hash, user_id, expires_iso))
    return token


def get_user_by_session(token):
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _conn() as c:
        row = c.execute('''SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
                           WHERE s.token_hash=? AND s.expires_at>?''', (token_hash, now_iso())).fetchone()
        return row


def delete_session(token):
    if not token:
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _conn() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash,))


def _next_month_start():
    today = datetime.now(timezone.utc).date()
    if today.month == 12:
        return today.replace(year=today.year + 1, month=1, day=1)
    return today.replace(month=today.month + 1, day=1)

def _reset_if_needed(c, user):
    today = datetime.now(timezone.utc).date()
    reset = datetime.fromisoformat(user['reset_date']).date()
    if today >= reset:
        next_reset = _next_month_start()
        c.execute('UPDATE users SET credits=monthly_limit, reset_date=? WHERE id=?', (next_reset.isoformat(), user['id']))
        return c.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    return user


def reserve_credits(user_id, cost):
    with _conn() as c:
        user = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            return False, None
        user = _reset_if_needed(c, user)
        if user['credits'] < cost:
            return False, user
        updated = c.execute('UPDATE users SET credits=credits-? WHERE id=? AND credits>=?', (cost, user_id, cost))
        if updated.rowcount != 1:
            return False, c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        return True, c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


def refund_credits(user_id, cost):
    with _conn() as c:
        c.execute('UPDATE users SET credits=MIN(monthly_limit, credits+?) WHERE id=?', (cost, user_id))


def set_plan(user_id, plan):
    plan = plan.lower()
    limit = PRO_CREDITS if plan == 'pro' else FREE_CREDITS
    with _conn() as c:
        c.execute('UPDATE users SET plan=?, monthly_limit=?, credits=? WHERE id=?', (plan, limit, limit, user_id))
        return c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


def create_project(project_id, user_id, topic, duration, aspect_ratio, voice, captions, credits_cost):
    with _conn() as c:
        c.execute('''INSERT INTO projects(id,user_id,topic,duration,aspect_ratio,voice,captions,status,credits_cost,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)''',
                  (project_id,user_id,topic,duration,aspect_ratio,voice,int(captions), 'processing', credits_cost, now_iso()))


def update_project(project_id, user_id, **fields):
    allowed = {'status','script','video_url','error'}
    parts=[]; values=[]
    for key,value in fields.items():
        if key in allowed:
            parts.append(f'{key}=?'); values.append(value)
    if not parts:
        return
    values += [project_id, user_id]
    with _conn() as c:
        c.execute(f'UPDATE projects SET {", ".join(parts)} WHERE id=? AND user_id=?', values)


def list_projects(user_id, limit=20):
    with _conn() as c:
        return c.execute('SELECT * FROM projects WHERE user_id=? ORDER BY created_at DESC LIMIT ?', (user_id, limit)).fetchall()


def public_user(user):
    if not user:
        return None
    return {
        'id': user['id'], 'name': user['name'], 'email': user['email'],
        'plan': user['plan'], 'credits': user['credits'], 'credit_limit': user['monthly_limit'],
        'reset_date': user['reset_date']
    }
