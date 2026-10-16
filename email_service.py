
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


async def send_email(
    recipient_email: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
):
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("BREVO_SENDER_EMAIL")
    sender_name = os.getenv(
        "BREVO_SENDER_NAME",
        "Clip Pirate .ai",
    )

    if not api_key or not sender_email:
        raise RuntimeError(
            "Brevo API key or sender email is not configured."
        )

    payload = {
        "sender": {
            "name": sender_name,
            "email": sender_email,
        },
        "to": [{"email": recipient_email}],
        "subject": subject,
        "htmlContent": html_content,
    }

    if text_content:
        payload["textContent"] = text_content

    headers = {
        "api-key": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            BREVO_API_URL,
            headers=headers,
            json=payload,
        )

    if response.status_code not in (200, 201, 202):
        # Do not log the API key or email body.
        raise RuntimeError(
            f"Brevo email request failed "
            f"({response.status_code})."
        )

    return response.json()
