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
STATE_FILE = STATE_DIRECTORY / "messages.json"


def clean_value(value: Any) -> str:
    """Convert empty calendar values into a dash."""
    if value is None:
        return "–"

    text = str(value).strip()
    return text if text else "–"


def download_forex_calendar() -> list[dict[str, Any]]:
    """Download the weekly economic calendar."""

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
    """Convert an event time to Swiss local time."""

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
    """Get today's high-impact USD events."""

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
    """Build one Discord calendar line."""

    return (
        f"🔴 **{event['time']} Swiss time** — "
        f"{event['title']} · "
        f"F: {event['forecast']} · "
        f"P: {event['previous']}"
    )


def build_payload(
    events: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the Discord webhook message."""

    now = datetime.now(SWISS_TIMEZONE)

    date_text = now.strftime(
        "%A, %B %d"
    ).replace(" 0", " ")

    if events:
        description = "\n".join(
            build_event_line(event)
            for event in events
        )

        footer_text = (
            "Today's USD high-impact events · "
            "Swiss time · Forex Factory"
        )

        embed_color = 0xED4245

    else:
        description = (
            "No high-impact USD news today. "
            "Have a great day!"
        )

        footer_text = (
            "USD high-impact events only · "
            "Swiss time · Forex Factory"
        )

        embed_color = 0x5865F2

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
                "color": embed_color,
                "footer": {
                    "text": footer_text
                },
            }
        ],
    }


def load_old_message_ids() -> list[str]:
    """Load all previously saved Discord message IDs."""

    if not STATE_FILE.exists():
        return []

    try:
        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        message_ids = data.get("message_ids", [])

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


def save_message_id(
    message_id: str,
) -> None:
    """Save the current Discord message ID."""

    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            {
                "message_ids": [message_id]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def delete_discord_message(
    message_id: str,
) -> bool:
    """Delete one previously posted webhook message."""

    delete_url = (
        f"{DISCORD_WEBHOOK_URL}"
        f"/messages/{message_id}"
    )

    response = requests.delete(
        delete_url,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code in {200, 204}:
        return True

    if response.status_code == 404:
        return False

    response.raise_for_status()
    return False


def delete_old_messages() -> None:
    """Delete all webhook messages known to the bot."""

    message_ids = load_old_message_ids()

    if not message_ids:
        print("No saved old messages were found.")
        return

    for message_id in message_ids:
        try:
            deleted = delete_discord_message(
                message_id
            )

            if deleted:
                print(
                    "Deleted previous Discord message: "
                    f"{message_id}"
                )
            else:
                print(
                    "Previous message was already deleted: "
                    f"{message_id}"
                )

        except requests.RequestException as error:
            print(
                "Could not delete previous message "
                f"{message_id}: {error}",
                file=sys.stderr,
            )


def send_new_message(
    payload: dict[str, Any],
) -> str:
    """Send the current calendar and return its message ID."""

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
        payload = build_payload(events)

        # First remove the previously stored message.
        delete_old_messages()

        # Then send the new daily message.
        new_message_id = send_new_message(payload)

        # Save its ID for the next workflow run.
        save_message_id(new_message_id)

        print(
            f"Successfully posted {len(events)} "
            "high-impact USD event(s)."
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
