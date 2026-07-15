import json
import math
import os
import sys
from datetime import datetime, timedelta
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

SEND_TEST_WARNING = (
    os.getenv("SEND_TEST_WARNING", "false").lower() == "true"
)

FOREX_FACTORY_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

SWISS_TIMEZONE = ZoneInfo("Europe/Zurich")
REQUEST_TIMEOUT = 30
WARNING_WINDOW_MINUTES = 10

STATE_DIRECTORY = Path(".warning-state")
STATE_FILE = STATE_DIRECTORY / "warnings.json"


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
                "USD-News-Warning-Webhook/2.0"
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


def create_event_id(
    event_datetime: datetime,
    title: str,
) -> str:
    normalized_title = " ".join(
        title.lower().split()
    )

    return (
        f"{event_datetime.isoformat()}|"
        f"{normalized_title}"
    )


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {
            "sent_events": {},
            "message_ids": [],
        }

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            raise ValueError

        sent_events = data.get("sent_events", {})
        message_ids = data.get("message_ids", [])

        if not isinstance(sent_events, dict):
            sent_events = {}

        if not isinstance(message_ids, list):
            message_ids = []

        return {
            "sent_events": sent_events,
            "message_ids": [
                str(message_id).strip()
                for message_id in message_ids
                if str(message_id).strip()
            ],
        }

    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        AttributeError,
    ):
        return {
            "sent_events": {},
            "message_ids": [],
        }


def save_state(state: dict[str, Any]) -> None:
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def clean_old_sent_events(
    sent_events: dict[str, str],
) -> dict[str, str]:
    cutoff = datetime.now(
        SWISS_TIMEZONE
    ) - timedelta(days=3)

    cleaned: dict[str, str] = {}

    for event_id, sent_at_text in sent_events.items():
        try:
            sent_at = datetime.fromisoformat(
                str(sent_at_text)
            )

            if sent_at.tzinfo is None:
                continue

            if sent_at >= cutoff:
                cleaned[event_id] = str(sent_at_text)

        except ValueError:
            continue

    return cleaned


def get_upcoming_events() -> list[dict[str, Any]]:
    now = datetime.now(SWISS_TIMEZONE)
    calendar_events = download_forex_calendar()

    upcoming_events: list[dict[str, Any]] = []

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

        seconds_until_event = (
            event_datetime - now
        ).total_seconds()

        if seconds_until_event <= 0:
            continue

        minutes_until_event = (
            seconds_until_event / 60
        )

        if minutes_until_event > WARNING_WINDOW_MINUTES:
            continue

        title = clean_value(
            event.get("title")
        )

        upcoming_events.append(
            {
                "id": create_event_id(
                    event_datetime,
                    title,
                ),
                "datetime": event_datetime,
                "title": title,
                "forecast": clean_value(
                    event.get("forecast")
                ),
                "previous": clean_value(
                    event.get("previous")
                ),
                "minutes_until": max(
                    1,
                    math.ceil(minutes_until_event),
                ),
            }
        )

    upcoming_events.sort(
        key=lambda item: item["datetime"]
    )

    return upcoming_events


def build_warning_payload(
    event: dict[str, Any],
) -> dict[str, Any]:
    event_datetime: datetime = event["datetime"]
    minutes_until = event["minutes_until"]

    if minutes_until == 1:
        countdown_text = "1 minute"
    else:
        countdown_text = f"{minutes_until} minutes"

    description_lines = [
        f"⏳ **Countdown: {countdown_text}**",
        "",
        (
            f"🔴 **{event['title']}**"
        ),
        (
            f"🕒 **{event_datetime.strftime('%H:%M')} "
            "Swiss time**"
        ),
    ]

    details: list[str] = []

    if event["forecast"] != "–":
        details.append(
            f"Forecast: {event['forecast']}"
        )

    if event["previous"] != "–":
        details.append(
            f"Previous: {event['previous']}"
        )

    if details:
        description_lines.extend(
            [
                "",
                " · ".join(details),
            ]
        )

    return {
        "username": "Economic Calendar",
        "content": (
            "@everyone 🚨 **HIGH-IMPACT USD NEWS "
            f"IN {countdown_text.upper()}!**"
        ),
        "allowed_mentions": {
            "parse": ["everyone"]
        },
        "embeds": [
            {
                "title": "⚠️ USD News Alert",
                "url": (
                    "https://www.forexfactory.com/"
                    "calendar"
                ),
                "description": "\n".join(
                    description_lines
                ),
                "color": 0xED4245,
                "footer": {
                    "text": (
                        "Expect increased volatility · "
                        "Swiss time · Forex Factory"
                    )
                },
            }
        ],
    }


def build_test_payload() -> dict[str, Any]:
    example_time = (
        datetime.now(SWISS_TIMEZONE)
        + timedelta(minutes=10)
    )

    return {
        "username": "Economic Calendar",
        "content": (
            "@everyone 🚨 **TEST: HIGH-IMPACT "
            "USD NEWS IN 10 MINUTES!**"
        ),
        "allowed_mentions": {
            "parse": ["everyone"]
        },
        "embeds": [
            {
                "title": "⚠️ USD News Alert",
                "description": (
                    "⏳ **Countdown: 10 minutes**\n\n"
                    "🔴 **Example USD Economic News**\n"
                    f"🕒 **{example_time.strftime('%H:%M')} "
                    "Swiss time**\n\n"
                    "This is only a test message."
                ),
                "color": 0xED4245,
                "footer": {
                    "text": "Test message · No real event"
                },
            }
        ],
    }


def send_discord_message(
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


def run_test() -> None:
    message_id = send_discord_message(
        build_test_payload()
    )

    print(
        "Test warning sent successfully. "
        f"Message ID: {message_id}"
    )


def run_warning_check() -> None:
    state = load_state()

    sent_events = clean_old_sent_events(
        state["sent_events"]
    )

    message_ids = state["message_ids"]
    upcoming_events = get_upcoming_events()

    sent_count = 0

    for event in upcoming_events:
        event_id = event["id"]

        if event_id in sent_events:
            print(
                "Warning was already sent for: "
                f"{event['title']}"
            )
            continue

        message_id = send_discord_message(
            build_warning_payload(event)
        )

        sent_events[event_id] = (
            datetime.now(
                SWISS_TIMEZONE
            ).isoformat()
        )

        message_ids.append(message_id)

        save_state(
            {
                "sent_events": sent_events,
                "message_ids": message_ids,
            }
        )

        sent_count += 1

        print(
            f"Warning sent for {event['title']} "
            f"at {event['datetime'].strftime('%H:%M')}."
        )

    save_state(
        {
            "sent_events": sent_events,
            "message_ids": message_ids,
        }
    )

    if not upcoming_events:
        print(
            "No high-impact USD event begins "
            "within the next 10 minutes."
        )

    print(
        f"Finished. Sent {sent_count} warning(s)."
    )


def main() -> None:
    if not DISCORD_WEBHOOK_URL:
        print(
            "Error: DISCORD_WEBHOOK_URL is missing.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        if SEND_TEST_WARNING:
            run_test()
        else:
            run_warning_check()

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
