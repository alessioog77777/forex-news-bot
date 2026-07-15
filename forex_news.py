import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


load_dotenv()

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

FOREX_FACTORY_FEED = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
)

FOREX_FACTORY_TIMEZONE = ZoneInfo("America/New_York")
SWISS_TIMEZONE = ZoneInfo("Europe/Zurich")


def get_text(event, tag: str, default: str = "–") -> str:
    element = event.find(tag)

    if element is None or element.text is None:
        return default

    value = element.text.strip()
    return value or default


def parse_event_datetime(date_text: str, time_text: str):
    date_formats = (
        "%m-%d-%Y",
        "%m/%d/%Y",
        "%Y-%m-%d",
    )

    time_formats = (
        "%I:%M%p",
        "%I%p",
        "%H:%M",
    )

    event_date = None

    for date_format in date_formats:
        try:
            event_date = datetime.strptime(
                date_text.strip(),
                date_format,
            ).date()
            break
        except ValueError:
            continue

    if event_date is None:
        return None

    normalized_time = (
        time_text.strip()
        .replace(" ", "")
        .upper()
    )

    if normalized_time in {
        "",
        "ALLDAY",
        "TENTATIVE",
        "TENTATIVE.",
    }:
        return None

    for time_format in time_formats:
        try:
            event_time = datetime.strptime(
                normalized_time,
                time_format,
            ).time()

            new_york_datetime = datetime.combine(
                event_date,
                event_time,
                tzinfo=FOREX_FACTORY_TIMEZONE,
            )

            return new_york_datetime.astimezone(
                SWISS_TIMEZONE
            )

        except ValueError:
            continue

    return None


def load_high_impact_usd_events():
    response = requests.get(
        FOREX_FACTORY_FEED,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "USD-News-Discord-Webhook/1.0"
            )
        },
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    today = datetime.now(SWISS_TIMEZONE).date()

    events = []

    for event in root.findall(".//event"):
        country = get_text(event, "country", "")
        currency = get_text(event, "currency", "")
        impact = get_text(event, "impact", "")

        is_usd = (
            country.upper() == "USD"
            or currency.upper() == "USD"
        )

        is_high_impact = impact.lower() == "high"

        if not is_usd or not is_high_impact:
            continue

        event_datetime = parse_event_datetime(
            get_text(event, "date", ""),
            get_text(event, "time", ""),
        )

        if event_datetime is None:
            continue

        if event_datetime.date() != today:
            continue

        events.append(
            {
                "datetime": event_datetime,
                "title": get_text(event, "title"),
                "forecast": get_text(event, "forecast"),
                "previous": get_text(event, "previous"),
            }
        )

    events.sort(key=lambda item: item["datetime"])

    return events


def create_description(events):
    lines = []

    for event in events:
        time_text = event["datetime"].strftime("%H:%M")

        line = (
            f"🔴 **{time_text} Swiss time** — "
            f"{event['title']} · "
            f"F: {event['forecast']} · "
            f"P: {event['previous']}"
        )

        lines.append(line)

    return "\n".join(lines)


def send_discord_message(events):
    now = datetime.now(SWISS_TIMEZONE)

    date_title = now.strftime("%A, %B %d")
    date_title = date_title.replace(" 0", " ")

    if not events:
        payload = {
            "username": "Economic Calendar",
            "content": "@everyone",
            "embeds": [
                {
                    "title": (
                        f"📅 Economic Calendar — {date_title}"
                    ),
                    "description": (
                        "No high-impact USD news today. "
                        "Have a great day!"
                    ),
                    "color": 0x5865F2,
                    "footer": {
                        "text": (
                            "USD high-impact events only · "
                            "Swiss time · Forex Factory"
                        )
                    },
                }
            ],
            "allowed_mentions": {
                "parse": ["everyone"]
            },
        }

    else:
        payload = {
            "username": "Economic Calendar",
            "content": "@everyone",
            "embeds": [
                {
                    "title": (
                        f"📅 Economic Calendar — {date_title}"
                    ),
                    "description": create_description(events),
                    "color": 0xED4245,
                    "footer": {
                        "text": (
                            "Today's USD high-impact events · "
                            "Swiss time · Forex Factory"
                        )
                    },
                }
            ],
            "allowed_mentions": {
                "parse": ["everyone"]
            },
        }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()


def main():
    if not WEBHOOK_URL:
        print(
            "DISCORD_WEBHOOK_URL is missing.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        events = load_high_impact_usd_events()
        send_discord_message(events)

        print(
            f"Posted {len(events)} high-impact USD events."
        )

    except requests.RequestException as error:
        print(
            f"Network error: {error}",
            file=sys.stderr,
        )
        sys.exit(1)

    except ET.ParseError as error:
        print(
            f"XML error: {error}",
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