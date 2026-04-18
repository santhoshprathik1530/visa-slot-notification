#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import sqlite3
import sys
import time
import xml.sax.saxutils as xml_utils
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL_TEMPLATE = (
    "https://api.atlys.com/api/v2/application/slots/{country_code}"
    "?residence=IN&citizenship=IN&purpose=atlys_black"
    "&travellersCount=1&withAllSlots=true&getCitiesWiseSlots=false"
)
ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".state"
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "runs.db"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 30
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
COUNTRY_CODES = {
    "at": "AT",
    "austria": "AT",
    "it": "IT",
    "italy": "IT",
}
COUNTRY_DISPLAY_NAMES = {
    "austria": "Austria",
    "italy": "Italy",
}
COUNTRY_PAGE_URLS = {
    "austria": "https://www.atlys.com/tools/appointments/schengen/austria",
    "italy": "https://www.atlys.com/tools/appointments/schengen/italy",
}


class ChannelError(RuntimeError):
    pass


@dataclass
class SlotChange:
    city: str
    current_date: str
    previous_date: str | None
    reason: str


@dataclass
class AlertPayload:
    title: str
    message: str
    details: str
    country_slug: str
    page_url: str
    changes: list[SlotChange]
    current_slots: dict[str, str]


@dataclass
class AppConfig:
    country_slug: str
    country_code: str
    timeout_seconds: int
    interval_seconds: int
    alert_mode: str
    required_city: str | None
    run_source: str


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_db_connection() -> sqlite3.Connection:
    ensure_dirs()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                command TEXT NOT NULL,
                run_source TEXT NOT NULL DEFAULT 'manual',
                country_slug TEXT NOT NULL,
                required_city TEXT,
                alert_mode TEXT NOT NULL,
                api_status TEXT,
                alert_status TEXT,
                slots_count INTEGER NOT NULL DEFAULT 0,
                current_slots_json TEXT NOT NULL,
                changes_json TEXT NOT NULL,
                delivery_results_json TEXT NOT NULL,
                error_text TEXT,
                exit_code INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "run_source" not in columns:
            connection.execute("ALTER TABLE runs ADD COLUMN run_source TEXT NOT NULL DEFAULT 'manual'")


def log_run(
    *,
    config: AppConfig,
    command: str,
    api_status: str,
    alert_status: str,
    current_slots: dict[str, str],
    changes: list[SlotChange],
    delivery_results: list[dict[str, str]],
    error_text: str | None,
    exit_code: int,
) -> None:
    initialize_db()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO runs (
                created_at,
                command,
                run_source,
                country_slug,
                required_city,
                alert_mode,
                api_status,
                alert_status,
                slots_count,
                current_slots_json,
                changes_json,
                delivery_results_json,
                error_text,
                exit_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                command,
                config.run_source,
                config.country_slug,
                config.required_city,
                config.alert_mode,
                api_status,
                alert_status,
                len(current_slots),
                json.dumps(current_slots, sort_keys=True),
                json.dumps([change.__dict__ for change in changes]),
                json.dumps(delivery_results),
                error_text,
                exit_code,
            ),
        )


def state_file_for(country_slug: str) -> Path:
    return STATE_DIR / f"{country_slug}_slots_state.json"


def load_state(country_slug: str) -> dict[str, Any]:
    state_file = state_file_for(country_slug)
    if not state_file.exists():
        return {
            "last_snapshot": {},
            "last_checked_at": None,
            "last_api_status": None,
            "last_alert_status": None,
            "last_error": None,
        }
    return json.loads(state_file.read_text())


def save_state(country_slug: str, state: dict[str, Any]) -> None:
    ensure_dirs()
    state_file_for(country_slug).write_text(json.dumps(state, indent=2, sort_keys=True))


def fetch_payload(country_code: str, timeout: int) -> dict[str, Any]:
    request = Request(
        API_URL_TEMPLATE.format(country_code=country_code),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def extract_country_slots(payload: dict[str, Any], country_slug: str) -> dict[str, str]:
    all_slots = payload.get("allSlots") or {}
    country_slots = all_slots.get(country_slug) or {}
    cleaned: dict[str, str] = {}

    for city, slot_info in country_slots.items():
        if not isinstance(slot_info, dict):
            continue
        earliest = slot_info.get("earliest_slot")
        if earliest and earliest != "no slots":
            cleaned[city] = earliest

    return dict(sorted(cleaned.items()))


def detect_changes(previous: dict[str, str], current: dict[str, str]) -> list[SlotChange]:
    changes: list[SlotChange] = []

    for city, current_date in current.items():
        previous_date = previous.get(city)
        if previous_date is None:
            changes.append(
                SlotChange(city=city, current_date=current_date, previous_date=None, reason="new_city")
            )
            continue

        if current_date != previous_date and is_earlier(current_date, previous_date):
            changes.append(
                SlotChange(
                    city=city,
                    current_date=current_date,
                    previous_date=previous_date,
                    reason="earlier_date",
                )
            )

    return changes


def is_earlier(left: str, right: str) -> bool:
    return date.fromisoformat(left) < date.fromisoformat(right)


def pretty_city(city: str) -> str:
    return city.replace("_", " ").title()


def country_name(country_slug: str) -> str:
    return COUNTRY_DISPLAY_NAMES.get(country_slug, country_slug.title())


def build_reply_hint() -> str:
    return 'Reply "ok" or "ack" when you see this.'


def build_alert_payload(
    country_slug: str, changes: list[SlotChange], current_slots: dict[str, str]
) -> AlertPayload:
    label = country_name(country_slug)
    page_url = COUNTRY_PAGE_URLS[country_slug]
    earliest = min(current_slots.values()) if current_slots else "n/a"

    if len(changes) == 1:
        change = changes[0]
        title = f"{label} visa slot found"
        if change.reason == "new_city":
            message = f"{pretty_city(change.city)} now has a slot on {change.current_date}."
        else:
            message = (
                f"{pretty_city(change.city)} moved earlier from "
                f"{change.previous_date} to {change.current_date}."
            )
    else:
        title = f"{label} visa slots updated in {len(changes)} cities"
        preview = ", ".join(pretty_city(change.city) for change in changes[:5])
        if len(changes) > 5:
            preview = f"{preview}, ..."
        message = f"{preview}. Earliest visible date: {earliest}."

    lines = [
        title,
        message,
        f"Country: {label}",
        f"Atlys page: {page_url}",
        "",
        "Changed cities:",
    ]
    for change in changes:
        if change.reason == "new_city":
            lines.append(f"- {pretty_city(change.city)}: {change.current_date} (new)")
        else:
            lines.append(
                f"- {pretty_city(change.city)}: {change.previous_date} -> {change.current_date} (earlier)"
            )

    return AlertPayload(
        title=title,
        message=message,
        details="\n".join(lines),
        country_slug=country_slug,
        page_url=page_url,
        changes=changes,
        current_slots=current_slots,
    )


def build_presence_alert_payload(
    country_slug: str,
    current_slots: dict[str, str],
    required_city: str | None = None,
) -> AlertPayload:
    label = country_name(country_slug)
    page_url = COUNTRY_PAGE_URLS[country_slug]
    cities = list(current_slots.items())
    reply_hint = build_reply_hint()

    if country_slug == "italy" and required_city == "bangalore":
        if not cities:
            title = "Italy visa summary"
            message = "Hey, No Italy visa Slots available today :("
            details = "\n".join([message, reply_hint])
        else:
            city, slot_date = cities[0]
            title = "Bangalore has Italy visa slots"
            message = "hey hey hey , bangalore has visa slots."
            details = "\n".join(
                [
                    message,
                    f"City: {pretty_city(city)}",
                    f"Date: {slot_date}",
                    f"Atlys: {page_url}",
                    reply_hint,
                ]
            )
        return AlertPayload(
            title=title,
            message=message,
            details=details,
            country_slug=country_slug,
            page_url=page_url,
            changes=[],
            current_slots=current_slots,
        )

    if not cities:
        title = f"{label} visa slots daily summary"
        message = "No visible slots are currently available."
    elif len(cities) == 1:
        city, slot_date = cities[0]
        title = f"{label} visa slot available"
        message = f"{pretty_city(city)} has a visible slot on {slot_date}."
    else:
        earliest = min(current_slots.values())
        preview = ", ".join(pretty_city(city) for city, _ in cities[:5])
        if len(cities) > 5:
            preview = f"{preview}, ..."
        title = f"{label} visa slots currently available in {len(cities)} cities"
        message = f"{preview}. Earliest visible date: {earliest}."

    lines = [
        title,
        message,
        f"Country: {label}",
        f"Atlys page: {page_url}",
        "",
        "Current visible slots:",
    ]
    if cities:
        for city, slot_date in cities:
            lines.append(f"- {pretty_city(city)}: {slot_date}")
    else:
        lines.append("- None")
    lines.extend(["", reply_hint])

    return AlertPayload(
        title=title,
        message=message,
        details="\n".join(lines),
        country_slug=country_slug,
        page_url=page_url,
        changes=[],
        current_slots=current_slots,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return {}
        charset = response.headers.get_content_charset() or "utf-8"
        text = raw.decode(charset)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}


def http_post_form(
    url: str,
    data: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    encoded = urlencode(data).encode("utf-8")
    request = Request(url, data=encoded, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return {}
        charset = response.headers.get_content_charset() or "utf-8"
        text = raw.decode(charset)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}


def basic_auth_header(username: str, password: str) -> str:
    import base64

    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def send_email_alert(payload: AlertPayload) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("EMAIL_FROM")
    recipients = split_csv(os.getenv("EMAIL_TO"))

    if not host or not sender or not recipients:
        raise ChannelError("email enabled but SMTP_HOST, EMAIL_FROM, or EMAIL_TO is missing")

    message = EmailMessage()
    message["Subject"] = payload.title
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(payload.details)

    use_ssl = env_bool("SMTP_USE_SSL", False)
    use_starttls = env_bool("SMTP_USE_STARTTLS", not use_ssl)

    if use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=DEFAULT_TIMEOUT_SECONDS)
    else:
        server = smtplib.SMTP(host, port, timeout=DEFAULT_TIMEOUT_SECONDS)

    try:
        server.ehlo()
        if use_starttls:
            server.starttls()
            server.ehlo()
        if username:
            server.login(username, password or "")
        server.send_message(message)
    finally:
        server.quit()


def send_telegram_alert(payload: AlertPayload) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise ChannelError("telegram enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")

    http_post_json(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        {
            "chat_id": chat_id,
            "text": payload.details,
            "disable_web_page_preview": False,
        },
    )


def send_pushover_alert(payload: AlertPayload) -> None:
    token = os.getenv("PUSHOVER_APP_TOKEN")
    user = os.getenv("PUSHOVER_USER_KEY")
    if not token or not user:
        raise ChannelError("pushover enabled but PUSHOVER_APP_TOKEN or PUSHOVER_USER_KEY is missing")

    data = {
        "token": token,
        "user": user,
        "title": payload.title,
        "message": payload.details,
        "priority": os.getenv("PUSHOVER_PRIORITY", "1"),
        "url": payload.page_url,
        "url_title": "Open Atlys page",
    }
    device = os.getenv("PUSHOVER_DEVICE")
    sound = os.getenv("PUSHOVER_SOUND")
    if device:
        data["device"] = device
    if sound:
        data["sound"] = sound

    http_post_form("https://api.pushover.net/1/messages.json", data)


def send_generic_webhook_alert(payload: AlertPayload) -> None:
    url = os.getenv("WEBHOOK_URL")
    if not url:
        raise ChannelError("webhook enabled but WEBHOOK_URL is missing")

    headers: dict[str, str] = {}
    bearer = os.getenv("WEBHOOK_BEARER_TOKEN")
    secret = os.getenv("WEBHOOK_SECRET")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if secret:
        headers["X-Webhook-Secret"] = secret

    http_post_json(
        url,
        {
            "title": payload.title,
            "message": payload.message,
            "details": payload.details,
            "page_url": payload.page_url,
            "country": payload.country_slug,
            "changes": [change.__dict__ for change in payload.changes],
            "current_slots": payload.current_slots,
            "sent_at": now_iso(),
        },
        headers=headers,
    )


def send_slack_webhook_alert(payload: AlertPayload) -> None:
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        raise ChannelError("slack enabled but SLACK_WEBHOOK_URL is missing")

    http_post_json(url, {"text": payload.details})


def send_discord_webhook_alert(payload: AlertPayload) -> None:
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        raise ChannelError("discord enabled but DISCORD_WEBHOOK_URL is missing")

    http_post_json(
        url,
        {
            "content": payload.details,
            "embeds": [
                {
                    "title": payload.title,
                    "description": payload.message,
                    "url": payload.page_url,
                }
            ],
        },
    )


def twilio_headers() -> dict[str, str]:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        raise ChannelError("twilio channel enabled but TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN is missing")
    return {"Authorization": basic_auth_header(sid, token)}


def twilio_messages_url() -> str:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    if not sid:
        raise ChannelError("TWILIO_ACCOUNT_SID is missing")
    return f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def twilio_calls_url() -> str:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    if not sid:
        raise ChannelError("TWILIO_ACCOUNT_SID is missing")
    return f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"


def send_twilio_sms_alert(payload: AlertPayload) -> None:
    sender = os.getenv("TWILIO_SMS_FROM")
    recipients = split_csv(os.getenv("TWILIO_SMS_TO"))
    if not sender or not recipients:
        raise ChannelError("twilio sms enabled but TWILIO_SMS_FROM or TWILIO_SMS_TO is missing")

    for recipient in recipients:
        http_post_form(
            twilio_messages_url(),
            {"From": sender, "To": recipient, "Body": payload.details[:1600]},
            headers=twilio_headers(),
        )


def send_twilio_whatsapp_alert(payload: AlertPayload) -> None:
    sender = os.getenv("TWILIO_WHATSAPP_FROM")
    recipients = split_csv(os.getenv("TWILIO_WHATSAPP_TO"))
    if not sender or not recipients:
        raise ChannelError(
            "twilio whatsapp enabled but TWILIO_WHATSAPP_FROM or TWILIO_WHATSAPP_TO is missing"
        )

    sender_value = sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}"
    body = build_twilio_whatsapp_body(payload)
    for recipient in recipients:
        to_value = recipient if recipient.startswith("whatsapp:") else f"whatsapp:{recipient}"
        http_post_form(
            twilio_messages_url(),
            {"From": sender_value, "To": to_value, "Body": body[:1600]},
            headers=twilio_headers(),
        )


def build_twilio_whatsapp_body(payload: AlertPayload) -> str:
    template_mode = os.getenv("TWILIO_WHATSAPP_TEMPLATE_MODE", "").strip().lower()
    if template_mode in {"sandbox_order_notification", "sandbox_order"}:
        return build_twilio_sandbox_order_template(payload)
    return payload.details


def build_twilio_sandbox_order_template(payload: AlertPayload) -> str:
    country = country_name(payload.country_slug)
    item = f"{country} slot alert"
    if payload.current_slots:
        deliver_on = min(payload.current_slots.values())
        details = payload.message
    else:
        deliver_on = datetime.now(timezone.utc).date().isoformat()
        details = "No visible slots currently available."

    details = f"{details} {payload.page_url}".strip()
    return (
        f"Your visa order of {item} has shipped and should be delivered on "
        f"{deliver_on}. Details: {details}"
    )


def send_twilio_voice_alert(payload: AlertPayload) -> None:
    caller_id = os.getenv("TWILIO_VOICE_FROM")
    recipients = split_csv(os.getenv("TWILIO_VOICE_TO"))
    if not caller_id or not recipients:
        raise ChannelError("twilio voice enabled but TWILIO_VOICE_FROM or TWILIO_VOICE_TO is missing")

    spoken_text = f"{payload.title}. {payload.message}. Check Atlys for more details."
    twiml = (
        "<Response><Say voice=\"alice\">"
        f"{xml_utils.escape(spoken_text)}"
        "</Say></Response>"
    )
    for recipient in recipients:
        http_post_form(
            twilio_calls_url(),
            {"From": caller_id, "To": recipient, "Twiml": twiml},
            headers=twilio_headers(),
        )


def enabled_channels() -> list[str]:
    channels = split_csv(os.getenv("ALERT_CHANNELS"))
    return [channel.strip().lower() for channel in channels]


CHANNEL_SENDERS = {
    "discord": send_discord_webhook_alert,
    "email": send_email_alert,
    "pushover": send_pushover_alert,
    "slack": send_slack_webhook_alert,
    "sms": send_twilio_sms_alert,
    "telegram": send_telegram_alert,
    "voice": send_twilio_voice_alert,
    "webhook": send_generic_webhook_alert,
    "whatsapp": send_twilio_whatsapp_alert,
}


def dispatch_alerts(payload: AlertPayload) -> list[dict[str, str]]:
    channels = enabled_channels()
    if not channels:
        print("No alert channels enabled. Set ALERT_CHANNELS in the environment.", file=sys.stderr)
        return []

    results: list[dict[str, str]] = []
    for channel in channels:
        sender = CHANNEL_SENDERS.get(channel)
        if sender is None:
            results.append({"channel": channel, "status": "failed", "error": "unsupported channel"})
            continue
        try:
            sender(payload)
            results.append({"channel": channel, "status": "sent"})
        except Exception as exc:
            results.append({"channel": channel, "status": "failed", "error": str(exc)})
    return results


def normalize_country_slug(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in COUNTRY_CODES:
        supported = ", ".join(sorted(COUNTRY_CODES))
        raise SystemExit(f"Unsupported watch country '{value}'. Supported values: {supported}")
    if normalized == "at":
        return "austria"
    if normalized == "it":
        return "italy"
    return normalized


def config_from_args(args: argparse.Namespace) -> AppConfig:
    country_slug = normalize_country_slug(args.watch_country)
    required_city = getattr(args, "required_city", None)
    if required_city:
        required_city = required_city.strip().lower().replace(" ", "_")
    return AppConfig(
        country_slug=country_slug,
        country_code=COUNTRY_CODES[country_slug],
        timeout_seconds=args.timeout,
        interval_seconds=getattr(args, "interval_seconds", DEFAULT_INTERVAL_SECONDS),
        alert_mode=getattr(args, "alert_mode", "changes_only"),
        required_city=required_city,
        run_source=getattr(args, "run_source", "manual"),
    )


def run_check(config: AppConfig, notify_enabled: bool) -> int:
    ensure_dirs()
    initialize_db()
    command_name = "daily-summary" if config.alert_mode == "daily_summary" else "check"
    state = load_state(config.country_slug)
    previous_slots = state.get("last_snapshot") or {}

    try:
        payload = fetch_payload(country_code=config.country_code, timeout=config.timeout_seconds)
    except HTTPError as exc:
        state["last_checked_at"] = now_iso()
        state["last_api_status"] = f"http_error:{exc.code}"
        state["last_error"] = f"HTTP error: {exc.code}"
        save_state(config.country_slug, state)
        log_run(
            config=config,
            command=command_name,
            api_status=state["last_api_status"],
            alert_status="failed_before_alert",
            current_slots={},
            changes=[],
            delivery_results=[],
            error_text=state["last_error"],
            exit_code=1,
        )
        print(f"HTTP error while fetching Atlys API: {exc.code}", file=sys.stderr)
        return 1
    except URLError as exc:
        state["last_checked_at"] = now_iso()
        state["last_api_status"] = "network_error"
        state["last_error"] = f"Network error: {exc.reason}"
        save_state(config.country_slug, state)
        log_run(
            config=config,
            command=command_name,
            api_status=state["last_api_status"],
            alert_status="failed_before_alert",
            current_slots={},
            changes=[],
            delivery_results=[],
            error_text=state["last_error"],
            exit_code=1,
        )
        print(f"Network error while fetching Atlys API: {exc.reason}", file=sys.stderr)
        return 1

    current_slots = extract_country_slots(payload, country_slug=config.country_slug)
    changes = detect_changes(previous_slots, current_slots)

    if config.required_city:
        current_slots = {
            city: slot_date
            for city, slot_date in current_slots.items()
            if city == config.required_city
        }
        changes = [change for change in changes if change.city == config.required_city]

    output: dict[str, Any] = {"slots": current_slots, "changes": [change.__dict__ for change in changes]}

    state["last_checked_at"] = now_iso()
    state["last_api_status"] = "ok"
    state["last_error"] = None

    should_alert = False
    alert_payload: AlertPayload | None = None

    if config.alert_mode == "changes_only":
        if changes:
            should_alert = True
            alert_payload = build_alert_payload(config.country_slug, changes, current_slots)
    elif config.alert_mode == "always_when_present":
        if current_slots:
            should_alert = True
            alert_payload = build_presence_alert_payload(
                config.country_slug,
                current_slots,
                required_city=config.required_city,
            )
    elif config.alert_mode == "daily_summary":
        should_alert = True
        alert_payload = build_presence_alert_payload(
            config.country_slug,
            current_slots,
            required_city=config.required_city,
        )
    else:
        raise SystemExit(f"Unsupported alert mode: {config.alert_mode}")

    if not should_alert:
        state["last_snapshot"] = current_slots
        state["last_alert_status"] = "no_changes"
        save_state(config.country_slug, state)
        log_run(
            config=config,
            command=command_name,
            api_status=state["last_api_status"],
            alert_status=state["last_alert_status"],
            current_slots=current_slots,
            changes=changes,
            delivery_results=[],
            error_text=None,
            exit_code=0,
        )
        print(json.dumps(output, indent=2))
        return 0

    delivery_results: list[dict[str, str]] = []
    if notify_enabled:
        delivery_results = dispatch_alerts(alert_payload)
        output["delivery_results"] = delivery_results
    else:
        output["delivery_results"] = [{"channel": "all", "status": "skipped"}]

    failed = [result for result in output["delivery_results"] if result["status"] != "sent" and result["status"] != "skipped"]
    if failed:
        state["last_alert_status"] = "partial_failure"
        state["last_error"] = "; ".join(
            f'{result["channel"]}: {result.get("error", "unknown error")}' for result in failed
        )
        save_state(config.country_slug, state)
        log_run(
            config=config,
            command=command_name,
            api_status=state["last_api_status"],
            alert_status=state["last_alert_status"],
            current_slots=current_slots,
            changes=changes,
            delivery_results=delivery_results,
            error_text=state["last_error"],
            exit_code=2,
        )
        print(json.dumps(output, indent=2))
        return 2

    state["last_snapshot"] = current_slots
    state["last_alert_status"] = "sent" if notify_enabled else "skipped"
    save_state(config.country_slug, state)
    log_run(
        config=config,
        command=command_name,
        api_status=state["last_api_status"],
        alert_status=state["last_alert_status"],
        current_slots=current_slots,
        changes=changes,
        delivery_results=delivery_results,
        error_text=None,
        exit_code=0,
    )
    print(json.dumps(output, indent=2))
    return 0


def run_service(config: AppConfig, notify_enabled: bool) -> int:
    while True:
        exit_code = run_check(config, notify_enabled=notify_enabled)
        print(
            json.dumps(
                {
                    "service_mode": True,
                    "checked_at": now_iso(),
                    "exit_code": exit_code,
                    "sleep_seconds": config.interval_seconds,
                }
            ),
            flush=True,
        )
        time.sleep(config.interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Atlys Schengen slot availability and fan out alerts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--watch-country", default="italy")
        command_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
        command_parser.add_argument("--no-notify", action="store_true")
        command_parser.add_argument("--required-city", default=None)
        command_parser.add_argument("--run-source", choices=["manual", "cron"], default="manual")
        command_parser.add_argument(
            "--alert-mode",
            choices=["changes_only", "always_when_present", "daily_summary"],
            default="changes_only",
        )

    check_parser = subparsers.add_parser("check", help="Run one check.")
    add_common_arguments(check_parser)

    serve_parser = subparsers.add_parser("serve", help="Run checks forever on an interval.")
    add_common_arguments(serve_parser)
    serve_parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)

    daily_parser = subparsers.add_parser("daily-summary", help="Send a summary regardless of changes.")
    add_common_arguments(daily_parser)
    daily_parser.set_defaults(alert_mode="daily_summary")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = config_from_args(args)

    if args.command == "check":
        return run_check(config, notify_enabled=not args.no_notify)
    if args.command == "serve":
        return run_service(config, notify_enabled=not args.no_notify)
    if args.command == "daily-summary":
        return run_check(config, notify_enabled=not args.no_notify)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
