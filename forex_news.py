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

STATE_DIRECTORY = Path(".bot-state")
STATE_FILE = STATE_DIRECTORY / "last_message.json"


def clean_value(value: Any) -> str:
    if value is None:
        return "–"

    text = str(value).strip()
    return text if text else "–"


def get_forex_events() -> list[dict[str, Any]]:
    response = requests.get(
        FOREX_FACTORY_URL,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "Forex-News-Discord-Bot/1.0"
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
    calendar_events = get_forex_events()

    filtered_events: list[dict[str, str]] = []

    for event in calendar_events:
        country = clean_value(
            event.get("country")
        ).upper()

        impact = clean_value(
            event.get("impact")
        ).lower()

        if country != "USD":
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
                "time": event_datetime.strftime(
                    "%H:%M"
                ),
                "title": clean_value(
                    event.get("title")
                ),
                "forecast": clean_value(
                    event.get("forecast")
                ),
                "previous": clean_value(
                    event.get("previous")
                ),
                "sort_key": (
                    event_datetime.isoformat()
                ),
            }
        )

    filtered_events.sort(
        key=lambda item: item["sort_key"]
    )

    return filtered_events


def build_event_line(
    event: dict[str, str],
) -> str:
    return (
        f"🔴 **{event['time']} Swiss time** — "
        f"{event['title']} · "
        f"F: {event['forecast']} · "
        f"P: {event['previous']}"
    )


def build_payload(
    events: list[dict[str, str]],
) -> dict[str, Any]:
    now = datetime.now(SWISS_TIMEZONE)

    date_text = now.strftime(
        "%A, %B %d"
    ).replace(" 0", " ")

    if events:
        description = "\n".join(
            build_event_line(event)
            for event in events
        )

        footer = (
            "Today's USD high-impact events · "
            "Swiss time · Forex Factory"
        )

        color = 0xED4245

    else:
        description = (
            "No high-impact USD news today. "
            "Have a great day!"
        )

        footer = (
            "USD high-impact events only · "
            "Swiss time · Forex Factory"
        )

        color = 0x5865F2

    return {
        "username": "Economic Calendar",
        "content": "@everyone",
        "allowed_mentions": {
            "parse": ["everyone"]
        },
        "embeds": [
            {
                "title": (
                    f"📅 Economic Calendar — "
                    f"{date_text}"
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


def load_last_message_id() -> str | None:
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        message_id = str(
            data.get("message_id", "")
        ).strip()

        return message_id or None

    except (
        OSError,
        json.JSONDecodeError,
        AttributeError,
    ):
        return None


def save_last_message_id(
    message_id: str,
) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            {
                "message_id": message_id
            }
        ),
        encoding="utf-8",
    )


def delete_old_message(
    message_id: str,
) -> None:
    delete_url = (
        f"{DISCORD_WEBHOOK_URL}"
        f"/messages/{message_id}"
    )

    response = requests.delete(
        delete_url,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code in {
        200,
        204,
        404,
    }:
        return

    response.raise_for_status()


def send_new_message(
    payload: dict[str, Any],
) -> str:
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        params={
            "wait": "true"
        },
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
            "Error: DISCORD_WEBHOOK_URL "
            "is missing.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        events = get_today_events()
        payload = build_payload(events)

        old_message_id = (
            load_last_message_id()
        )

        if old_message_id:
            delete_old_message(
                old_message_id
            )

            print(
                "Previous Discord message "
                "was deleted."
            )

        new_message_id = send_new_message(
            payload
        )

        save_last_message_id(
            new_message_id
        )

        print(
            f"Successfully posted "
            f"{len(events)} high-impact "
            f"USD event(s)."
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
