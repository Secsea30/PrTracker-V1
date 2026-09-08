"""
Sends the "new press release" alert email.

Sending method is picked automatically based on what's configured in .env:
  1. Gmail (GMAIL_APP_PASSWORD set)   -> works today, sends to anyone.
  2. Resend (RESEND_API_KEY set)      -> the long-term plan, needs the
                                          placecomms.com domain verified first.
  3. Neither set                      -> "dry run": prints what it would send
                                          instead of actually sending.

Swapping from Gmail to Resend/Outlook later is just a matter of filling in
the other set of .env values — nothing else in the tool needs to change.
"""

from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import localtime

RECIPIENTS_FILE = Path(__file__).parent / "recipients.json"

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GMAIL_SENDER_ADDRESS = os.environ.get("GMAIL_SENDER_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_ADDRESS = os.environ.get("ALERT_FROM_ADDRESS", "PRTracker <onboarding@resend.dev>")


def load_recipients() -> list[str]:
    data = json.loads(RECIPIENTS_FILE.read_text())
    return data["alert_recipients"]


def save_recipients(recipients: list[str]) -> None:
    RECIPIENTS_FILE.write_text(json.dumps({"alert_recipients": recipients}, indent=2))


def add_recipient(email: str) -> None:
    recipients = load_recipients()
    email = email.strip().lower()
    if email and email not in recipients:
        recipients.append(email)
        save_recipients(recipients)


def remove_recipient(email: str) -> None:
    recipients = load_recipients()
    remaining = [r for r in recipients if r != email]
    if remaining:
        save_recipients(remaining)


def _fmt(dt) -> str:
    return localtime.format_local(dt, with_seconds=True)


def build_email(item: dict, sent_at, has_screenshot: bool = False) -> dict:
    subject = f"New press release: {item['title']}"

    detected_at = item.get("detected_at")
    detected_str = _fmt(detected_at) if detected_at else None
    sent_str = _fmt(sent_at)

    screenshot_html = f"""
    <img src="cid:article_screenshot" alt="Screenshot of the article"
         style="width:100%; display:block; border-radius: 8px; border: 1px solid #e8e9ec; margin: 0 0 24px;">
    """ if has_screenshot else ""

    html = f"""
    <div style="background:#f4f5f7; padding: 32px 16px; font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
      <div style="max-width: 560px; margin: 0 auto; background:#ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e8e9ec;">

        <div style="height: 4px; background: #1E3A5F;"></div>

        <div style="padding: 20px 32px; border-bottom: 1px solid #eceef1;">
          <span style="font-size: 13px; font-weight: 700; letter-spacing: 0.04em; color: #1E3A5F;">PRTRACKER</span>
          <span style="font-size: 13px; color: #9599a3; margin-left: 8px;">New press release detected</span>
        </div>

        <div style="padding: 32px;">
          <h1 style="margin: 0 0 16px; font-size: 21px; line-height: 1.4; color: #111318; font-weight: 600;">
            {item['title']}
          </h1>

          {screenshot_html}

          <div style="margin: 0 0 24px;">
            {f'''<p style="margin: 0 0 4px; font-size: 13px; color: #6b7078;">
              Change detected at: {detected_str}
            </p>''' if detected_str else ""}
            <p style="margin: 0; font-size: 13px; color: #6b7078;">
              Email sent at: {sent_str}
            </p>
          </div>

          <a href="{item['url']}"
             style="display:inline-block; padding: 12px 22px; background:#1E3A5F; color:#ffffff; text-decoration:none; border-radius: 8px; font-size: 14px; font-weight: 500;">
            View press release &rarr;
          </a>

          <p style="margin: 24px 0 0; font-size: 12px; color: #9599a3; word-break: break-all;">
            {item['url']}
          </p>
        </div>

        <div style="padding: 16px 32px; background:#fafafa; border-top: 1px solid #eceef1;">
          <span style="font-size: 11px; color: #b0b4bc;">Automated alert &middot; mohamedbinzayed.ae/en/latest-news-listing</span>
        </div>

      </div>
    </div>
    """
    return {"subject": subject, "html": html}


def _send_via_gmail(recipients: list[str], email: dict, screenshot_bytes: bytes = None) -> None:
    msg = MIMEMultipart("related")
    msg["Subject"] = email["subject"]
    msg["From"] = f"PRTracker <{GMAIL_SENDER_ADDRESS}>"
    msg["To"] = ", ".join(recipients)

    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(email["html"], "html"))
    msg.attach(alt_part)

    if screenshot_bytes:
        image = MIMEImage(screenshot_bytes, _subtype="png")
        image.add_header("Content-ID", "<article_screenshot>")
        image.add_header("Content-Disposition", "inline", filename="screenshot.png")
        msg.attach(image)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_SENDER_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_SENDER_ADDRESS, recipients, msg.as_string())

    print(f"Sent alert email (via Gmail) to {len(recipients)} recipients: {', '.join(recipients)}")


def _send_via_resend(recipients: list[str], email: dict, screenshot_bytes: bytes = None) -> None:
    import base64
    import resend

    payload = {
        "from": FROM_ADDRESS,
        "to": recipients,
        "subject": email["subject"],
        "html": email["html"],
    }
    if screenshot_bytes:
        payload["attachments"] = [{
            "filename": "screenshot.png",
            "content": base64.b64encode(screenshot_bytes).decode(),
            "content_id": "article_screenshot",
        }]

    resend.api_key = RESEND_API_KEY
    resend.Emails.send(payload)
    print(f"Sent alert email (via Resend) to {len(recipients)} recipients: {', '.join(recipients)}")


def _dispatch(recipients: list[str], email: dict, screenshot_bytes: bytes = None) -> None:
    if GMAIL_SENDER_ADDRESS and GMAIL_APP_PASSWORD:
        _send_via_gmail(recipients, email, screenshot_bytes)
    elif RESEND_API_KEY:
        _send_via_resend(recipients, email, screenshot_bytes)
    else:
        print(f"[DRY RUN — no sending method configured] Would email {len(recipients)} people:")
        print(f"  To: {', '.join(recipients)}")
        print(f"  Subject: {email['subject']}")
        print(f"  Screenshot attached: {'yes' if screenshot_bytes else 'no'}")


def send_alert(item: dict, screenshot_bytes: bytes = None) -> datetime:
    """item = {"title": ..., "url": ..., "detected_at": <datetime, optional>}. Returns when it was sent."""
    recipients = load_recipients()
    sent_at = datetime.now(timezone.utc)
    email = build_email(item, sent_at, has_screenshot=bool(screenshot_bytes))
    _dispatch(recipients, email, screenshot_bytes)
    return sent_at


def build_health_alert_email(error: str, failure_count: int) -> dict:
    subject = "PRTracker health alert — checks are failing"
    html = f"""
    <div style="background:#f4f5f7; padding: 32px 16px; font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
      <div style="max-width: 560px; margin: 0 auto; background:#ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e8e9ec;">

        <div style="height: 4px; background: #B24E1F;"></div>

        <div style="padding: 20px 32px; border-bottom: 1px solid #eceef1;">
          <span style="font-size: 13px; font-weight: 700; letter-spacing: 0.04em; color: #B24E1F;">PRTRACKER</span>
          <span style="font-size: 13px; color: #9599a3; margin-left: 8px;">Health alert</span>
        </div>

        <div style="padding: 32px;">
          <h1 style="margin: 0 0 16px; font-size: 20px; line-height: 1.4; color: #111318; font-weight: 600;">
            The checker has failed {failure_count} times in a row
          </h1>
          <p style="margin: 0 0 16px; font-size: 14px; color: #6b7078;">
            PRTracker may not be catching new press releases right now. This usually means the target site
            changed something, or there's a network/server problem. Someone should take a look.
          </p>
          <p style="margin: 0; font-size: 13px; color: #9599a3; background:#fafafa; padding: 12px 16px; border-radius: 8px; word-break: break-word;">
            Last error: {error}
          </p>
        </div>

        <div style="padding: 16px 32px; background:#fafafa; border-top: 1px solid #eceef1;">
          <span style="font-size: 11px; color: #b0b4bc;">Automated health alert &middot; PRTracker</span>
        </div>

      </div>
    </div>
    """
    return {"subject": subject, "html": html}


def send_health_alert(error: str, failure_count: int) -> None:
    recipients = load_recipients()
    email = build_health_alert_email(error, failure_count)
    _dispatch(recipients, email)
