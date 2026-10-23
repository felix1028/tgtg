"""One-off diagnostic tool to import historical purchase times into drop_stats.json.

This script fetches your past order history from Too Good To Go, calculates
latency offsets against store scheduled drop times, and populates drop_stats.json
without altering any core application code.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from tgtg_bot.items import parse_iso_datetime
from tgtg_bot.monitor import get_client
from tgtg_bot.stats import DropStatsTracker


def import_order_history(stats_file: Path | str = "drop_stats.json") -> None:
    print("Connecting to Too Good To Go API...")
    client = get_client()
    tracker = DropStatsTracker(stats_file)

    print("Fetching complete order history...")
    response = client._post(client._get_url("order/v8"), json={"paging": {"size": 100}})
    if response.status_code != 200:
        print(f"❌ Failed to fetch order history: {response.status_code} - {response.text}")
        return

    data = response.json()
    total_magic_bags = data.get("magic_bag_count", 0)
    print(f"Found {total_magic_bags} total bags in account history.\n")

    all_orders: list[dict[str, Any]] = []
    for m in data.get("orders_per_month", []):
        all_orders.extend(m.get("orders", []))

    print(f"Retrieved {len(all_orders)} individual order records.")
    print("Cross-referencing scheduled drop times for stores...\n")

    # Cache store item schedules to minimize API calls
    item_cache: dict[str, dict[str, Any]] = {}
    imported_count = 0

    existing_events = {
        (h.get("item_id"), h.get("timestamp"))
        for h in tracker._data.get("history", [])
    }

    for idx, order in enumerate(all_orders, 1):
        item_id = str(order.get("item_id", ""))
        store_name = order.get("store_name", "Unknown Store")
        store_branch = order.get("store_branch", "")
        display_store = f"{store_name} ({store_branch})" if store_branch else store_name
        bag_name = order.get("item_name") or order.get("display_name") or "Surprise Bag"
        purchase_time_str = order.get("time_of_purchase")

        if not purchase_time_str or not item_id:
            continue

        purchase_dt = parse_iso_datetime(purchase_time_str)
        if not purchase_dt:
            continue

        # Look up current item schedule
        if item_id not in item_cache:
            try:
                item_cache[item_id] = client.get_item(item_id) or {}
            except Exception:
                item_cache[item_id] = {}

        item_detail = item_cache[item_id]
        if bag_name == "Surprise Bag" and item_detail:
            bag_name = item_detail.get("item", {}).get("name") or item_detail.get("display_name") or bag_name

        if (item_id, purchase_dt.isoformat()) in existing_events:
            # Enrich existing history and store entries with bag_name if missing
            for h in tracker._data.get("history", []):
                if h.get("item_id") == item_id and h.get("timestamp") == purchase_dt.isoformat():
                    h["bag_name"] = bag_name
            if item_id in tracker._data.get("stores", {}):
                tracker._data["stores"][item_id]["bag_name"] = bag_name
            continue

        sched_time_str = item_detail.get("next_sales_window_purchase_start")
        sched_dt: datetime.datetime | None = None

        if sched_time_str:
            raw_sched = parse_iso_datetime(sched_time_str)
            if raw_sched:
                sched_dt = purchase_dt.replace(
                    hour=raw_sched.hour,
                    minute=raw_sched.minute,
                    second=raw_sched.second,
                    microsecond=raw_sched.microsecond,
                )

        delay_str = ""
        # Only consider drop delay if purchase was within 15 minutes of scheduled time
        if sched_dt:
            delay = (purchase_dt - sched_dt).total_seconds()
            if -60.0 <= delay <= 900.0:
                delay_str = f" | Latency: +{delay:.1f}s"
            else:
                sched_dt = None  # Schedule changed over years/months

        local_dt_str = purchase_dt.strftime("%Y-%m-%d %I:%M:%S %p")
        print(f"[{idx:2d}/{len(all_orders)}] {display_store} - {bag_name}")
        print(f"     Purchased: {local_dt_str}{delay_str}")

        tracker.record_drop(
            item_id=item_id,
            store_name=display_store,
            bag_name=bag_name,
            actual_stock_time=purchase_dt.timestamp(),
            scheduled_drop_time=sched_dt,
            event_type="primary",
            secured=True,
            quantity_available=order.get("quantity", 1),
        )
        existing_events.add((item_id, purchase_dt.isoformat()))
        imported_count += 1

    tracker._save()
    print(f"\n✅ Successfully updated drop statistics with bag types in {stats_file}!")
    tracker.display_stats_summary()


if __name__ == "__main__":
    import_order_history()
