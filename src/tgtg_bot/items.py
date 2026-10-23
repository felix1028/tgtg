"""Item data formatting and presentation for Too Good To Go payloads."""

from __future__ import annotations

import datetime
import re


def parse_iso_datetime(ts: str | None) -> datetime.datetime | None:
    """Parse an ISO-8601 timestamp string into a local timezone datetime.

    Handles nanosecond timestamps from TGTG API by truncating to microseconds.
    """
    if not ts or not isinstance(ts, str):
        return None
    try:
        clean_ts = re.sub(r"(\.\d{6})\d+", r"\1", ts).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone()
    except Exception:
        return None


def format_pickup_interval(
    interval: dict | None,
    now: datetime.datetime | None = None,
) -> str:
    """Format a pickup_interval dict into a human-friendly string (e.g. 'Today 8:00 PM - 9:00 PM').

    Args:
        interval: Dictionary containing 'start' and/or 'end' ISO-8601 strings.
        now: Reference datetime for 'Today' / 'Tomorrow' relative labeling. Defaults to current time.
    """
    if not isinstance(interval, dict):
        return ""

    start_dt = parse_iso_datetime(interval.get("start"))
    end_dt = parse_iso_datetime(interval.get("end"))

    if not start_dt and not end_dt:
        return ""

    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()

    ref_date = (start_dt or end_dt).date()
    today = now.date()
    tomorrow = today + datetime.timedelta(days=1)

    if ref_date == today:
        day_label = "Today"
    elif ref_date == tomorrow:
        day_label = "Tomorrow"
    else:
        day_label = f"{ref_date.strftime('%a, %b')} {ref_date.day}"

    def _fmt_time(dt: datetime.datetime) -> str:
        return dt.strftime("%I:%M %p").lstrip("0")

    if start_dt and end_dt:
        start_time = _fmt_time(start_dt)
        end_time = _fmt_time(end_dt)
        if start_dt.date() == end_dt.date():
            return f"{day_label} {start_time} - {end_time}"
        else:
            return f"{day_label} {start_time} - Next day {end_time}"
    elif start_dt:
        return f"{day_label} from {_fmt_time(start_dt)}"
    else:
        return f"{day_label} until {_fmt_time(end_dt)}"


def extract_rating(item: dict) -> tuple[str, float | None, int | None]:
    """Extract rating score, review count, and badge string from TGTG item payload.

    The TGTG API returns rating data in one of two formats:
    1. A dictionary: {'average_overall_rating': 4.7, 'rating_count': 29}
    2. A scalar float/int: 4.5
    """
    raw = (
        item.get("item", {}).get("average_overall_rating")
        or item.get("average_overall_rating")
    )
    score, count = None, None
    if isinstance(raw, dict):
        raw_score = raw.get("average_overall_rating")
        raw_count = raw.get("rating_count")
        if raw_score is not None and raw_score > 0:
            score = float(raw_score)
            count = int(raw_count) if raw_count is not None else None
    elif isinstance(raw, (int, float)) and raw > 0:
        score = float(raw)
        raw_count = item.get("item", {}).get("rating_count") or item.get("rating_count")
        count = int(raw_count) if raw_count is not None else None

    if score is not None:
        badge = f"⭐ {score:.1f} ({count})" if count else f"⭐ {score:.1f}"
        return badge, score, count
    return "", None, None


def get_item_tag_ids(item: dict) -> set[str]:
    """Extract item tag IDs (e.g. 'SOLD_OUT', 'NOTHING_TO_SAVE_TODAY')."""
    tags = item.get("item_tags", [])
    if not isinstance(tags, list):
        return set()
    return {t.get("id") for t in tags if isinstance(t, dict) and t.get("id")}


def format_item(item: dict) -> dict:
    """Format raw TGTG API item payload into a clean, human-readable dictionary."""
    display_name = item.get("display_name", "Unknown Item")
    store_name = item.get("store", {}).get("store_name", "Unknown Store")
    items_available = item.get("items_available", 0)

    # Convert minor units to currency (e.g. 499 minor units with 2 decimals = 4.99 USD)
    price_info = (
        item.get("item", {}).get("item_price")
        or item.get("item", {}).get("price_including_taxes")
        or {}
    )
    decimals = price_info.get("decimals", 2)
    minor_units = price_info.get("minor_units", 0)
    code = price_info.get("code", "")
    price = f"{minor_units / (10 ** decimals):.2f} {code}".strip()

    tags = get_item_tag_ids(item)
    if items_available > 0:
        status = f"✅ AVAILABLE: {items_available}"
    elif "NOTHING_TO_SAVE_TODAY" in tags:
        status = "💤 NOTHING TODAY"
    elif "CHECK_AGAIN_LATER" in tags:
        status = "⏳ CHECK LATER"
    else:
        status = "❌ SOLD OUT"

    rating_badge, rating_score, rating_count = extract_rating(item)

    pickup_interval = (
        item.get("pickup_interval")
        or item.get("item", {}).get("pickup_interval")
    )
    pickup_time = format_pickup_interval(pickup_interval)

    return {
        "store_name": store_name,
        "display_name": display_name,
        "items_available": items_available,
        "price": price,
        "status": status,
        "is_available": items_available > 0,
        "rating": rating_badge,
        "rating_score": rating_score,
        "rating_count": rating_count,
        "pickup_time": pickup_time,
        "pickup_interval": pickup_interval,
        "tags": tags,
    }

