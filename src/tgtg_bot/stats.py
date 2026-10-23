"""Persistent drop statistics tracker and adaptive timer analytics for Too Good To Go.

Tracks scheduled vs. actual drop release times, queue delays, and 5-minute
cart abandonment re-drop intervals per store to adapt polling schedules.
"""

from __future__ import annotations

import datetime
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_STATS_FILE = Path("drop_stats.json")


class DropStatsTracker:
    """Manages persistent logging and statistical profiling of store inventory releases."""

    def __init__(self, stats_file: Path | str = DEFAULT_STATS_FILE):
        self.stats_file = Path(stats_file)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """Load stats from disk or return empty template."""
        if not self.stats_file.exists():
            return {"stores": {}, "history": []}
        try:
            with open(self.stats_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("stores", {})
                    data.setdefault("history", [])
                    return data
        except Exception as exc:
            print(f"⚠️ Failed to load drop stats from {self.stats_file}: {exc}")
        return {"stores": {}, "history": []}

    def _save(self) -> None:
        """Atomically persist stats to disk via temporary file."""
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=self.stats_file.parent, delete=False, encoding="utf-8") as tf:
                json.dump(self._data, tf, indent=2, default=str)
                temp_name = tf.name
            os.replace(temp_name, self.stats_file)
        except Exception as exc:
            print(f"⚠️ Failed to save drop stats to {self.stats_file}: {exc}")

    def record_drop(
        self,
        item_id: str,
        store_name: str,
        actual_stock_time: float,
        scheduled_drop_time: datetime.datetime | None = None,
        event_type: str = "primary",  # "primary" or "redrop"
        sold_out_time: float | None = None,
        secured: bool = False,
        quantity_available: int = 1,
    ) -> dict[str, Any]:
        """Record an observed drop event and recalculate store metrics."""
        actual_dt = datetime.datetime.fromtimestamp(actual_stock_time, tz=datetime.timezone.utc).astimezone()

        delay_seconds: float | None = None
        if scheduled_drop_time is not None:
            delay_seconds = round((actual_dt - scheduled_drop_time).total_seconds(), 2)

        redrop_delta_seconds: float | None = None
        if event_type == "redrop" and sold_out_time is not None:
            redrop_delta_seconds = round(actual_stock_time - sold_out_time, 2)

        entry = {
            "item_id": str(item_id),
            "store_name": store_name,
            "event_type": event_type,
            "timestamp": actual_dt.isoformat(),
            "scheduled_drop_time": scheduled_drop_time.isoformat() if scheduled_drop_time else None,
            "delay_seconds": delay_seconds,
            "redrop_delta_seconds": redrop_delta_seconds,
            "secured": secured,
            "quantity_available": quantity_available,
        }

        self._data["history"].append(entry)

        # Update store aggregate profile
        store_key = str(item_id)
        store_entry = self._data["stores"].setdefault(
            store_key,
            {
                "store_name": store_name,
                "primary_drops_observed": 0,
                "redrops_observed": 0,
                "primary_delays": [],
                "redrop_deltas": [],
                "median_primary_delay_seconds": None,
                "mean_primary_delay_seconds": None,
                "median_redrop_delta_seconds": None,
                "last_seen": None,
            },
        )

        store_entry["store_name"] = store_name
        store_entry["last_seen"] = actual_dt.isoformat()

        if event_type == "primary":
            store_entry["primary_drops_observed"] += 1
            if delay_seconds is not None:
                store_entry["primary_delays"].append(delay_seconds)
                store_entry["median_primary_delay_seconds"] = round(statistics.median(store_entry["primary_delays"]), 2)
                store_entry["mean_primary_delay_seconds"] = round(statistics.mean(store_entry["primary_delays"]), 2)
        elif event_type == "redrop":
            store_entry["redrops_observed"] += 1
            if redrop_delta_seconds is not None:
                store_entry["redrop_deltas"].append(redrop_delta_seconds)
                store_entry["median_redrop_delta_seconds"] = round(statistics.median(store_entry["redrop_deltas"]), 2)

        self._save()
        return entry

    def record_sold_out(
        self,
        item_id: str,
        store_name: str,
        sold_out_time: float,
        scheduled_drop_time: datetime.datetime | None = None,
        had_seen_stock: bool = False,
    ) -> dict[str, Any]:
        """Record an observed sold-out event at or after drop time.

        Provides vital timing bounds on when the drop occurred and how fast it was claimed,
        even if the user/bot did not secure the bag.
        """
        sold_dt = datetime.datetime.fromtimestamp(sold_out_time, tz=datetime.timezone.utc).astimezone()

        delay_seconds: float | None = None
        if scheduled_drop_time is not None:
            delay_seconds = round((sold_dt - scheduled_drop_time).total_seconds(), 2)

        entry = {
            "item_id": str(item_id),
            "store_name": store_name,
            "event_type": "sold_out",
            "timestamp": sold_dt.isoformat(),
            "scheduled_drop_time": scheduled_drop_time.isoformat() if scheduled_drop_time else None,
            "delay_seconds": delay_seconds,
            "had_seen_stock": had_seen_stock,
            "secured": False,
        }

        self._data["history"].append(entry)

        store_key = str(item_id)
        store_entry = self._data["stores"].setdefault(
            store_key,
            {
                "store_name": store_name,
                "primary_drops_observed": 0,
                "sold_outs_observed": 0,
                "redrops_observed": 0,
                "primary_delays": [],
                "sold_out_delays": [],
                "redrop_deltas": [],
                "median_primary_delay_seconds": None,
                "mean_primary_delay_seconds": None,
                "median_sold_out_delay_seconds": None,
                "median_redrop_delta_seconds": None,
                "last_seen": None,
            },
        )

        store_entry["store_name"] = store_name
        store_entry["last_seen"] = sold_dt.isoformat()
        store_entry["sold_outs_observed"] = store_entry.get("sold_outs_observed", 0) + 1
        store_entry.setdefault("sold_out_delays", [])

        if delay_seconds is not None:
            store_entry["sold_out_delays"].append(delay_seconds)
            store_entry["median_sold_out_delay_seconds"] = round(statistics.median(store_entry["sold_out_delays"]), 2)

        self._save()
        return entry

    def get_store_profile(self, item_id: str) -> dict[str, Any] | None:
        """Retrieve aggregated drop metrics for a store/item."""
        return self._data["stores"].get(str(item_id))

    def get_all_profiles(self) -> dict[str, Any]:
        """Retrieve all store profiles."""
        return self._data.get("stores", {})

    def get_adaptive_lead_seconds(self, item_id: str, default: float = 23.0) -> float:
        """Calculate adaptive lead seconds before an event based on past observations.

        If sufficient history exists, adjusts lead timing around the store's median drop delay
        or median sold-out delay. Otherwise falls back to the default (e.g. 23s).
        """
        profile = self.get_store_profile(item_id)
        if not profile:
            return default

        median_delay = profile.get("median_primary_delay_seconds")
        if median_delay is None:
            median_delay = profile.get("median_sold_out_delay_seconds")

        if median_delay is None:
            return default

        # If store consistently drops 45s late, don't rush early; adjust lead accordingly
        if median_delay > 30.0:
            return round(min(default, max(15.0, default - 5.0)), 2)
        return default

    def display_stats_summary(self) -> None:
        """Print a summary table of tracked store drop latencies."""
        stores = self._data.get("stores", {})
        if not stores:
            print("\n📊 No historical drop statistics recorded yet.")
            return

        print("\n" + "=" * 80)
        print("📊 HISTORICAL DROP LATENCY & RE-DROP STATISTICS")
        print("=" * 80)
        print(f"{'Store Name':<26} | {'Drops':<5} | {'Drop Delay':<11} | {'Sold-Out (Est)':<14} | {'Re-drops':<8} | {'Median Re-drop'}")
        print("-" * 80)
        for _, s in stores.items():
            name = (s.get("store_name") or "Unknown")[:24]
            p_count = s.get("primary_drops_observed", 0)
            med_delay = f"+{s['median_primary_delay_seconds']}s" if s.get("median_primary_delay_seconds") is not None else "N/A"
            sold_out_delay = f"+{s['median_sold_out_delay_seconds']}s" if s.get("median_sold_out_delay_seconds") is not None else "N/A"
            r_count = s.get("redrops_observed", 0)
            med_redrop = f"{s['median_redrop_delta_seconds']}s" if s.get("median_redrop_delta_seconds") is not None else "N/A"
            print(f"{name:<26} | {p_count:<5} | {med_delay:<11} | {sold_out_delay:<14} | {r_count:<8} | {med_redrop}")
        print("=" * 80)


# Global singleton instance
_tracker_instance: DropStatsTracker | None = None


def get_drop_tracker(stats_file: Path | str = DEFAULT_STATS_FILE) -> DropStatsTracker:
    """Get or initialize the shared DropStatsTracker singleton."""
    global _tracker_instance
    if _tracker_instance is None or _tracker_instance.stats_file != Path(stats_file):
        _tracker_instance = DropStatsTracker(stats_file)
    return _tracker_instance
