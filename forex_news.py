import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "",
).strip()

FOREX_FACTORY_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

SWISS_TIMEZONE = ZoneInfo("Europe/Zurich")
REQUEST_TIMEOUT = 30

DAILY_STATE_DIRECTORY = Path(".bot-state")
DAILY_STATE_FILE = (
    DAILY_STATE_DIRECTORY / "messages.json"
)

WARNING_STATE_DIRECTORY = Path(".warning-state")
WARNING_STATE_FILE = (
    WARNING_STATE_DIRECTORY / "warnings.json"
)


def clean_value(value: Any) -> str:
    if value is None:
        return "–"

    text = str(value).strip()
    return text if text else "–"


def download_forex_calendar() -> list[dict[str, Any]]:
    response = requests.get(
        FOREX_FACTORY_URL,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "Forex-News-Discord-Webhook/2.0"
            )
        },
    )

    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise ValueError(
            "Forex Factory returned unexpected data."
        )

    return data


def parse_event_datetime(
    date_value: str,
) -> datetime | None:
    if not date_value or date_value == "–":
        return None

    try:
        event_datetime = datetime.fromisoformat(
            date_value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if event_datetime.tzinfo is None:
        return None

    return event_datetime.astimezone(
        SWISS_TIMEZONE
    )


def get_today_events() -> list[dict[str, str]]:
    today = datetime.now(SWISS_TIMEZONE).date()
    calendar_events = download_forex_calendar()

    filtered_events: list[dict[str, str]] = []

    for event in calendar_events:
        currency = clean_value(
            event.get("country")
            or event.get("currency")
        ).upper()

        impact = clean_value(
            event.get("impact")
        ).lower()

        if currency != "USD":
            continue

        if impact != "high":
            continue

        event_datetime = parse_event_datetime(
            clean_value(event.get("date"))
        )

        if event_datetime is None:
            continue

        if event_datetime.date() != today:
            continue

        filtered_events.append(
            {
                "time": event_datetime.strftime("%H:%M"),
                "title": clean_value(event.get("title")),
                "forecast": clean_value(
                    event.get("forecast")
                ),
                "previous": clean_value(
                    event.get("previous")
                ),
                "sort_key": event_datetime.isoformat(),
            }
        )

    filtered_events.sort(
        key=lambda item: item["sort_key"]
    )

    return filtered_events


def build_event_line(
    event: dict[str, str],
) -> str:
    details: list[str] = []

    if event["forecast"] != "–":
        details.append(
            f"Forecast: {event['forecast']}"
        )

    if event["previous"] != "–":
        details.append(
            f"Previous: {event['previous']}"
        )

    detail_text = ""

    if details:
        detail_text = "\n" + " · ".join(details)

    return (
        f"🔴 **{event['time']} Swiss time** — "
        f"**{event['title']}**"
        f"{detail_text}"
    )


def build_payload(
    events: list[dict[str, str]],
) -> dict[str, Any]:
    now = datetime.now(SWISS_TIMEZONE)

    date_text = now.strftime(
        "%A, %B %d"
    ).replace(" 0", " ")

    if events:
        description = "\n\n".join(
            build_event_line(event)
            for event in events
        )

        color = 0xED4245
        footer = (
            "Today's USD high-impact events · "
            "Swiss time · Forex Factory"
        )

    else:
        description = (
            "No high-impact USD news today. "
            "Have a great day!"
        )

        color = 0x5865F2
        footer = (
            "USD high-impact events only · "
            "Swiss time · Forex Factory"
        )

    return {
        "username": "Economic Calendar",
        "content": "@everyone",
        "allowed_mentions": {
            "parse": ["everyone"]
        },
        "embeds": [
            {
                "title": (
                    f"📅 Economic Calendar — {date_text}"
                ),
                "url": (
                    "https://www.forexfactory.com/"
                    "calendar"
                ),
                "description": description,
                "color": color,
                "footer": {
                    "text": footer
                },
            }
        ],
    }


def load_daily_message_ids() -> list[str]:
    if not DAILY_STATE_FILE.exists():
        return []

    try:
        data = json.loads(
            DAILY_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        message_ids = data.get(
            "message_ids",
            [],
        )

        if not isinstance(message_ids, list):
            return []

        return [
            str(message_id).strip()
            for message_id in message_ids
            if str(message_id).strip()
        ]

    except (
        OSError,
        json.JSONDecodeError,
        AttributeError,
    ):
        return []


def load_warning_message_ids() -> list[str]:
    if not WARNING_STATE_FILE.exists():
        return []

    try:
        data = json.loads(
            WARNING_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        message_ids = data.get(
            "message_ids",
            [],
        )

        if not isinstance(message_ids, list):
            return []

        return [
            str(message_id).strip()
            for message_id in message_ids
            if str(message_id).strip()
        ]

    except (
        OSError,
        json.JSONDecodeError,
        AttributeError,
    ):
        return []


def save_daily_message_id(
    message_id: str,
) -> None:
    DAILY_STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    DAILY_STATE_FILE.write_text(
        json.dumps(
            {
                "message_ids": [message_id]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def clear_warning_state() -> None:
    WARNING_STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    WARNING_STATE_FILE.write_text(
        json.dumps(
            {
                "sent_events": {},
                "message_ids": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def delete_discord_message(
    message_id: str,
) -> None:
    response = requests.delete(
        (
            f"{DISCORD_WEBHOOK_URL}"
            f"/messages/{message_id}"
        ),
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code in {
        200,
        204,
        404,
    }:
        return

    response.raise_for_status()


def delete_previous_messages() -> None:
    message_ids = (
        load_daily_message_ids()
        + load_warning_message_ids()
    )

    unique_message_ids = list(
        dict.fromkeys(message_ids)
    )

    for message_id in unique_message_ids:
        try:
            delete_discord_message(message_id)

            print(
                "Deleted previous message: "
                f"{message_id}"
            )

        except requests.RequestException as error:
            print(
                "Could not delete message "
                f"{message_id}: {error}",
                file=sys.stderr,
            )

    clear_warning_state()


def send_new_daily_message(
    payload: dict[str, Any],
) -> str:
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        params={"wait": "true"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()
    response_data = response.json()

    message_id = str(
        response_data.get("id", "")
    ).strip()

    if not message_id:
        raise ValueError(
            "Discord did not return a message ID."
        )

    return message_id


def main() -> None:
    if not DISCORD_WEBHOOK_URL:
        print(
            "Error: DISCORD_WEBHOOK_URL is missing.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        events = get_today_events()

        delete_previous_messages()

        message_id = send_new_daily_message(
            build_payload(events)
        )

        save_daily_message_id(message_id)

        print(
            f"Posted {len(events)} high-impact "
            "USD event(s)."
        )

    except requests.RequestException as error:
        print(
            f"Network error: {error}",
            file=sys.stderr,
        )
        sys.exit(1)

    except ValueError as error:
        print(
            f"Data error: {error}",
            file=sys.stderr,
        )
        sys.exit(1)

    except Exception as error:
        print(
            f"Unexpected error: {error}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
