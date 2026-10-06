import json
import uuid
from enum import Enum
from typing import Optional

from db import _conn, now_iso


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def create_job(
    user_id: str,
    project_id: str
) -> str:

    job_id = uuid.uuid4().hex

    with _conn() as c:
        c.execute(
            """
            INSERT INTO jobs (
                id,
                user_id,
                project_id,
                status,
                result,
                error,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                project_id,
                JobStatus.PENDING.value,
                None,
                None,
                now_iso(),
            )
        )

    return job_id


def set_status(
    job_id: str,
    status: JobStatus
):
    with _conn() as c:
        c.execute(
            """
            UPDATE jobs
            SET status=?
            WHERE id=?
            """,
            (
                status.value,
                job_id,
            )
        )


def set_result(
    job_id: str,
    result: dict
):
    with _conn() as c:
        c.execute(
            """
            UPDATE jobs
            SET status=?,
                result=?,
                error=NULL
            WHERE id=?
            """,
            (
                JobStatus.COMPLETED.value,
                json.dumps(result),
                job_id,
            )
        )


def set_error(
    job_id: str,
    error: str
):
    with _conn() as c:
        c.execute(
            """
            UPDATE jobs
            SET status=?,
                error=?,
                result=NULL
            WHERE id=?
            """,
            (
                JobStatus.FAILED.value,
                error,
                job_id,
            )
        )


def get_job(
    job_id: str
) -> Optional[dict]:

    with _conn() as c:

        row = c.execute(
            """
            SELECT
                id,
                user_id,
                project_id,
                status,
                result,
                error,
                created_at
            FROM jobs
            WHERE id=?
            """,
            (job_id,)
        ).fetchone()

    if row is None:
        return None

    result = None

    if row["result"]:
        try:
            result = json.loads(row["result"])
        except (TypeError, json.JSONDecodeError):
            result = None

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "project_id": row["project_id"],
        "status": JobStatus(row["status"]),
        "result": result,
        "error": row["error"],
        "created_at": row["created_at"],
    }