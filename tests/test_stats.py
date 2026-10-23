"""Unit tests for DropStatsTracker in tgtg_bot.stats."""

import datetime
import json
import pytest
from pathlib import Path
from tgtg_bot.stats import DropStatsTracker, get_drop_tracker


class TestDropStatsTracker:
    def test_empty_init_and_profile(self, tmp_path):
        stats_file = tmp_path / "drop_stats.json"
        tracker = DropStatsTracker(stats_file=stats_file)
        assert tracker.get_all_profiles() == {}
        assert tracker.get_store_profile("12345") is None

    def test_record_primary_drop_computes_delays(self, tmp_path):
        stats_file = tmp_path / "drop_stats.json"
        tracker = DropStatsTracker(stats_file=stats_file)

        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        scheduled_drop = now - datetime.timedelta(seconds=45)
        stock_time = now.timestamp()

        entry = tracker.record_drop(
            item_id="101",
            store_name="Tartine Bakery",
            actual_stock_time=stock_time,
            scheduled_drop_time=scheduled_drop,
            event_type="primary",
            secured=True,
            quantity_available=2,
        )

        assert entry["store_name"] == "Tartine Bakery"
        assert entry["secured"] is True
        assert entry["delay_seconds"] == pytest.approx(45.0, abs=1.0)
        assert entry["event_type"] == "primary"

        # Check persistence
        assert stats_file.exists()
        with open(stats_file, "r") as f:
            data = json.load(f)
        assert "101" in data["stores"]
        profile = data["stores"]["101"]
        assert profile["primary_drops_observed"] == 1
        assert profile["median_primary_delay_seconds"] == pytest.approx(45.0, abs=1.0)

    def test_record_redrop_computes_delta(self, tmp_path):
        stats_file = tmp_path / "drop_stats.json"
        tracker = DropStatsTracker(stats_file=stats_file)

        sold_out_time = 1000.0
        stock_time = 1302.5  # 302.5 seconds later (~5 min)

        entry = tracker.record_drop(
            item_id="202",
            store_name="Eataly",
            actual_stock_time=stock_time,
            event_type="redrop",
            sold_out_time=sold_out_time,
            secured=True,
        )

        assert entry["redrop_delta_seconds"] == pytest.approx(302.5, abs=0.1)
        profile = tracker.get_store_profile("202")
        assert profile["redrops_observed"] == 1
        assert profile["median_redrop_delta_seconds"] == pytest.approx(302.5, abs=0.1)

    def test_adaptive_lead_seconds(self, tmp_path):
        stats_file = tmp_path / "drop_stats.json"
        tracker = DropStatsTracker(stats_file=stats_file)

        # No stats -> default 23s
        assert tracker.get_adaptive_lead_seconds("999", default=23.0) == 23.0

        # Store with high delay (>30s)
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        tracker.record_drop(
            item_id="store_slow",
            store_name="Slow Bakery",
            actual_stock_time=now.timestamp(),
            scheduled_drop_time=now - datetime.timedelta(seconds=50),
        )
        # Should return adjusted lead
        lead = tracker.get_adaptive_lead_seconds("store_slow", default=23.0)
        assert lead == 18.0

    def test_display_stats_summary(self, tmp_path, capsys):
        stats_file = tmp_path / "drop_stats.json"
        tracker = DropStatsTracker(stats_file=stats_file)

        # Empty output
        tracker.display_stats_summary()
        captured = capsys.readouterr()
        assert "No historical drop statistics recorded yet" in captured.out

        # Populated output
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        tracker.record_drop(
            item_id="item_1",
            store_name="Arsicault Bakery",
            actual_stock_time=now.timestamp(),
            scheduled_drop_time=now - datetime.timedelta(seconds=28),
        )
        tracker.display_stats_summary()
        captured2 = capsys.readouterr()
        assert "Arsicault Bakery" in captured2.out
        assert "+28.0s" in captured2.out

    def test_record_sold_out_computes_drop_timing_without_securing(self, tmp_path):
        stats_file = tmp_path / "drop_stats.json"
        tracker = DropStatsTracker(stats_file=stats_file)

        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        scheduled_drop = now - datetime.timedelta(seconds=33)

        entry = tracker.record_sold_out(
            item_id="store_unsecured",
            store_name="Contested Bakery",
            sold_out_time=now.timestamp(),
            scheduled_drop_time=scheduled_drop,
            had_seen_stock=False,
        )

        assert entry["event_type"] == "sold_out"
        assert entry["secured"] is False
        assert entry["had_seen_stock"] is False
        assert entry["delay_seconds"] == pytest.approx(33.0, abs=1.0)

        profile = tracker.get_store_profile("store_unsecured")
        assert profile["sold_outs_observed"] == 1
        assert profile["median_sold_out_delay_seconds"] == pytest.approx(33.0, abs=1.0)
        # Adaptive lead leverages sold-out delay if primary stock check was missed
        lead = tracker.get_adaptive_lead_seconds("store_unsecured", default=23.0)
        assert lead == 18.0

