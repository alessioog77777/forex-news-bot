import json
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

# GitHub Actions stellt diesen Ordner über den Cache wieder her.
STATE_DIRECTORY = Path(".warning-state")
STATE_FILE = STATE_DIRECTORY / "sent_warnings.json"

# GitHub prüft alle 5 Minuten.
# Es wird gewarnt, sobald ein Ereignis höchstens 10 Minuten entfernt ist.
WARNING_WINDOW_MINUTES = 10


def clean_value(value: Any) -> str:
    """Convert empty values to a readable dash."""

    if value is None:
        return "–"

    text = str(value).strip()
    return text if text else "–"


def download_forex_calendar() -> list[dict[str, Any]]:
    """Download the weekly Forex Factory calendar."""

    response = requests.get(
        FOREX_FACTORY_URL,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "USD-News-Warning-Webhook/1.0"
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
    """Convert an event datetime to Swiss local time."""

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

    return event_datetime.astimezone(SWISS_TIMEZONE)


def create_event_id(
    event_datetime: datetime,
    title: str,
) -> str:
    """Create a stable identifier for one economic event."""

    normalized_title = " ".join(
        title.lower().split()
    )

    return (
        f"{event_datetime.isoformat()}|"
        f"{normalized_title}"
    )


def load_sent_warnings() -> dict[str, str]:
    """Load warning IDs already sent during previous runs."""

    if not STATE_FILE.exists():
        return {}

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return {}

        return {
            str(event_id): str(sent_at)
            for event_id, sent_at in data.items()
        }

    except (
        OSError,
        json.JSONDecodeError,
        AttributeError,
    ):
        return {}


def remove_old_warning_ids(
    sent_warnings: dict[str, str],
) -> dict[str, str]:
    """Remove state entries older than three days."""

    cutoff = datetime.now(SWISS_TIMEZONE) - timedelta(
        days=3
    )

    cleaned: dict[str, str] = {}

    for event_id, sent_at_text in sent_warnings.items():
        try:
            sent_at = datetime.fromisoformat(
                sent_at_text
            )

            if sent_at.tzinfo is None:
                continue

            if sent_at >= cutoff:
                cleaned[event_id] = sent_at_text

        except ValueError:
            continue

    return cleaned


def save_sent_warnings(
    sent_warnings: dict[str, str],
) -> None:
    """Save the IDs of events that have already been announced."""

    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            sent_warnings,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def get_upcoming_events() -> list[dict[str, Any]]:
    """
    Return high-impact USD events beginning within ten minutes.
    """

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

        minutes_until_event = seconds_until_event / 60

        # Vergangene Ereignisse ausschließen.
        if minutes_until_event <= 0:
            continue

        # Nur Ereignisse innerhalb der nächsten 10 Minuten.
        if minutes_until_event > WARNING_WINDOW_MINUTES:
            continue

        title = clean_value(event.get("title"))

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
                    round(minutes_until_event),
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
    """Build one Discord warning message."""

    event_datetime: datetime = event["datetime"]

    description_lines = [
        (
            f"🔴 **{event_datetime.strftime('%H:%M')} "
            f"Swiss time** — {event['title']}"
        )
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
        description_lines.append(
            " · ".join(details)
        )

    return {
        "username": "Economic Calendar",
        "content": (
            "@everyone ⚠️ **Attention: High-impact "
            "USD news in approximately 10 minutes!**"
        ),
        "allowed_mentions": {
            "parse": ["everyone"]
        },
        "embeds": [
            {
                "title": "🚨 USD News Warning",
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
                        "High-impact USD event · "
                        "Swiss time · Forex Factory"
                    )
                },
            }
        ],
    }


def build_test_payload() -> dict[str, Any]:
    """Build a harmless manual test warning."""

    now = datetime.now(SWISS_TIMEZONE)
    example_time = now + timedelta(minutes=10)

    return {
        "username": "Economic Calendar",
        "content": (
            "@everyone ⚠️ **Test: High-impact "
            "USD news warning!**"
        ),
        "allowed_mentions": {
            "parse": ["everyone"]
        },
        "embeds": [
            {
                "title": "🧪 USD Warning Test",
                "description": (
                    f"🔴 **{example_time.strftime('%H:%M')} "
                    "Swiss time** — Example economic event\n\n"
                    "This is only a manual test."
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
) -> None:
    """Send a webhook message to Discord."""

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise requests.HTTPError(
            (
                f"Discord returned HTTP "
                f"{response.status_code}: "
                f"{response.text}"
            ),
            response=response,
        )


def run_test() -> None:
    """Send a manual test message."""

    payload = build_test_payload()
    send_discord_message(payload)

    print(
        "The manual warning test was sent successfully."
    )


def run_warning_check() -> None:
    """Check and announce upcoming USD events."""

    sent_warnings = remove_old_warning_ids(
        load_sent_warnings()
    )

    upcoming_events = get_upcoming_events()
    newly_sent_count = 0

    for event in upcoming_events:
        event_id = event["id"]

        if event_id in sent_warnings:
            print(
                "Warning already sent for: "
                f"{event['title']}"
            )
            continue

        payload = build_warning_payload(event)
        send_discord_message(payload)

        sent_warnings[event_id] = (
            datetime.now(SWISS_TIMEZONE).isoformat()
        )

        # Direkt nach jeder Nachricht speichern.
        save_sent_warnings(sent_warnings)

        newly_sent_count += 1

        print(
            "Warning sent for "
            f"{event['title']} at "
            f"{event['datetime'].strftime('%H:%M')}."
        )

    # Auch speichern, wenn alte Einträge entfernt wurden.
    save_sent_warnings(sent_warnings)

    if not upcoming_events:
        print(
            "No high-impact USD event begins "
            "within the next 10 minutes."
        )

    print(
        f"Finished. Sent {newly_sent_count} "
        "new warning(s)."
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
