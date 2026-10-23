import datetime
import json
import pytest
from unittest.mock import MagicMock, patch
from tgtg_bot.monitor import format_item, check_favorites, get_client

class TestFormatItem:
    def test_available_item_formatting(self, sample_available_item):
        info = format_item(sample_available_item)
        assert info["store_name"] == "Artisan Bakery"
        assert info["display_name"] == "Pastry Surprise Bag"
        assert info["items_available"] == 3
        assert info["price"] == "4.99 USD"
        assert "AVAILABLE: 3" in info["status"]
        assert info["is_available"] is True

    def test_sold_out_item_formatting(self, sample_sold_out_item):
        info = format_item(sample_sold_out_item)
        assert info["store_name"] == "Green Grocer"
        assert info["display_name"] == "Vegetable Box"
        assert info["items_available"] == 0
        assert info["price"] == "3.50 EUR"
        assert "SOLD OUT" in info["status"]
        assert info["is_available"] is False

    def test_minimal_item_formatting_does_not_crash(self, sample_minimal_item):
        info = format_item(sample_minimal_item)
        assert info["store_name"] == "Unknown Store"
        assert info["display_name"] == "Unknown Item"
        assert info["items_available"] == 1
        assert info["is_available"] is True
        assert "AVAILABLE: 1" in info["status"]
        assert info["price"] == "0.00"

    def test_price_formatting_with_non_standard_decimals(self):
        item = {
            "items_available": 1,
            "item": {
                "price_including_taxes": {
                    "code": "JPY",
                    "minor_units": 500,
                    "decimals": 0,
                }
            }
        }
        info = format_item(item)
        assert info["price"] == "500.00 JPY"

    def test_rating_formatting_with_full_dict(self):
        item = {
            "items_available": 1,
            "item": {
                "average_overall_rating": {
                    "average_overall_rating": 4.689655172413793,
                    "rating_count": 29,
                    "month_count": 6,
                }
            }
        }
        info = format_item(item)
        assert info["rating"] == "⭐ 4.7 (29)"
        assert pytest.approx(info["rating_score"], 0.01) == 4.69
        assert info["rating_count"] == 29

    def test_rating_formatting_without_count(self):
        item = {
            "items_available": 1,
            "average_overall_rating": 4.5,
        }
        info = format_item(item)
        assert info["rating"] == "⭐ 4.5"
        assert info["rating_score"] == 4.5
        assert info["rating_count"] is None

    def test_rating_formatting_empty_or_zero(self):
        item = {
            "items_available": 1,
            "item": {
                "average_overall_rating": {
                    "average_overall_rating": 0,
                    "rating_count": 0,
                }
            }
        }
        info = format_item(item)
        assert info["rating"] == ""
        assert info["rating_score"] is None
        assert info["rating_count"] is None


class TestCheckFavorites:
    def test_check_favorites_filters_available_only(self, sample_available_item, sample_sold_out_item):
        mock_client = MagicMock()
        mock_client.get_items.return_value = [sample_available_item, sample_sold_out_item]

        available = check_favorites(mock_client)
        assert len(available) == 1
        assert available[0]["display_name"] == "Pastry Surprise Bag"
        mock_client.get_items.assert_called_once()

    def test_check_favorites_empty_list(self):
        mock_client = MagicMock()
        mock_client.get_items.return_value = []

        available = check_favorites(mock_client)
        assert available == []
        mock_client.get_items.assert_called_once()

    def test_check_favorites_all_sold_out(self, sample_sold_out_item):
        mock_client = MagicMock()
        mock_client.get_items.return_value = [sample_sold_out_item]

        available = check_favorites(mock_client)
        assert available == []


class TestGetClient:
    def test_get_client_from_tokens_file(self, tmp_path, sample_credentials):
        tokens_file = tmp_path / "tokens.json"
        tokens_file.write_text(json.dumps(sample_credentials))

        with patch("tgtg_bot.monitor.PersistentTgtgClient") as mock_client_cls:
            get_client(tokens_path=tokens_file)
            mock_client_cls.assert_called_once_with(
                access_token="mock-access-token-123",
                refresh_token="mock-refresh-token-456",
                cookie="mock-cookie-abc",
                user_agent="TGTG/26.9.11 Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Pro Build/UP1A.231005.007)",
                tokens_path=tokens_file,
            )

    def test_get_client_from_env_vars(self, monkeypatch, tmp_path):
        non_existent_file = tmp_path / "does_not_exist.json"
        monkeypatch.setenv("TGTG_ACCESS_TOKEN", "env-access-token")
        monkeypatch.setenv("TGTG_REFRESH_TOKEN", "env-refresh-token")
        monkeypatch.setenv("TGTG_COOKIE", "env-cookie")

        with patch("tgtg_bot.monitor.PersistentTgtgClient") as mock_client_cls:
            get_client(tokens_path=non_existent_file)
            mock_client_cls.assert_called_once_with(
                access_token="env-access-token",
                refresh_token="env-refresh-token",
                cookie="env-cookie",
                user_agent="TGTG/26.9.11 Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Pro Build/UP1A.231005.007)",
                tokens_path=non_existent_file,
            )

    def test_get_client_unauthenticated_exits(self, monkeypatch, tmp_path):
        non_existent_file = tmp_path / "does_not_exist.json"
        monkeypatch.delenv("TGTG_ACCESS_TOKEN", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            get_client(tokens_path=non_existent_file)
        assert exc_info.value.code == 1


class TestPersistentTgtgClient:
    def test_save_tokens_persists_to_file(self, tmp_path):
        from tgtg_bot.monitor import PersistentTgtgClient
        tokens_file = tmp_path / "tokens.json"

        client = PersistentTgtgClient(
            access_token="new-acc-tok",
            refresh_token="new-ref-tok",
            cookie="new-cookie",
            tokens_path=tokens_file,
        )
        client.save_tokens()

        assert tokens_file.exists()
        saved = json.loads(tokens_file.read_text())
        assert saved["access_token"] == "new-acc-tok"
        assert saved["refresh_token"] == "new-ref-tok"
        assert saved["cookie"] == "new-cookie"

    def test_refresh_token_triggers_save_tokens(self, tmp_path):
        from tgtg_bot.monitor import PersistentTgtgClient
        tokens_file = tmp_path / "tokens.json"

        client = PersistentTgtgClient(
            access_token="old-acc",
            refresh_token="old-ref",
            tokens_path=tokens_file,
        )

        with patch.object(client, "save_tokens") as mock_save:
            with patch("tgtg.TgtgClient._refresh_token"):
                client._refresh_token()
                mock_save.assert_called_once()

    def test_save_tokens_skips_when_empty(self, tmp_path):
        from tgtg_bot.monitor import PersistentTgtgClient
        tokens_file = tmp_path / "tokens.json"

        client = PersistentTgtgClient(tokens_path=tokens_file)
        client.save_tokens()
        assert not tokens_file.exists()


class TestCheckNearby:
    def test_check_nearby_calls_client_with_params(self, sample_available_item):
        from tgtg_bot.monitor import check_nearby
        mock_client = MagicMock()
        mock_client.get_items.return_value = [sample_available_item]

        items = check_nearby(mock_client, latitude=40.7128, longitude=-74.0060, radius=15)
        assert len(items) == 1
        mock_client.get_items.assert_called_once_with(
            latitude=40.7128,
            longitude=-74.0060,
            radius=15,
            favorites_only=False,
            with_stock_only=True,
            page_size=100,
        )


class TestGetCoordinatesFromFavorites:
    def test_derives_coordinates_successfully(self):
        from tgtg_bot.monitor import get_coordinates_from_favorites
        mock_client = MagicMock()
        mock_client.get_items.return_value = [
            {"pickup_location": {"location": {"latitude": 40.7128, "longitude": -74.0060}}}
        ]
        coords = get_coordinates_from_favorites(mock_client)
        assert coords == (40.7128, -74.0060)

    def test_returns_none_when_no_location(self):
        from tgtg_bot.monitor import get_coordinates_from_favorites
        mock_client = MagicMock()
        mock_client.get_items.return_value = [{"store": {}}]
        coords = get_coordinates_from_favorites(mock_client)
        assert coords is None


class TestFormatPickupInterval:
    def test_same_day_interval(self):
        from tgtg_bot.items import format_pickup_interval
        now = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc).astimezone()
        start_dt = now.replace(hour=18, minute=0, second=0)
        end_dt = now.replace(hour=19, minute=30, second=0)
        interval = {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }
        formatted = format_pickup_interval(interval, now=now)
        assert formatted == "Today 6:00 PM - 7:30 PM"

    def test_tomorrow_interval(self):
        from tgtg_bot.items import format_pickup_interval
        now = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc).astimezone()
        tomorrow = now + datetime.timedelta(days=1)
        start_dt = tomorrow.replace(hour=9, minute=0, second=0)
        end_dt = tomorrow.replace(hour=10, minute=0, second=0)
        interval = {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }
        formatted = format_pickup_interval(interval, now=now)
        assert formatted == "Tomorrow 9:00 AM - 10:00 AM"

    def test_future_date_interval(self):
        from tgtg_bot.items import format_pickup_interval
        now = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc).astimezone()
        future = now + datetime.timedelta(days=3)
        start_dt = future.replace(hour=14, minute=0, second=0)
        end_dt = future.replace(hour=15, minute=0, second=0)
        interval = {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }
        formatted = format_pickup_interval(interval, now=now)
        assert "Oct 15 2:00 PM - 3:00 PM" in formatted

    def test_only_start_or_only_end(self):
        from tgtg_bot.items import format_pickup_interval
        now = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc).astimezone()
        start_dt = now.replace(hour=20, minute=0, second=0)
        end_dt = now.replace(hour=21, minute=0, second=0)

        assert format_pickup_interval({"start": start_dt.isoformat()}, now=now) == "Today from 8:00 PM"
        assert format_pickup_interval({"end": end_dt.isoformat()}, now=now) == "Today until 9:00 PM"

    def test_empty_and_none_interval(self):
        from tgtg_bot.items import format_pickup_interval
        assert format_pickup_interval(None) == ""
        assert format_pickup_interval({}) == ""
        assert format_pickup_interval({"start": None, "end": None}) == ""
        assert format_pickup_interval({"start": "invalid_date"}) == ""

    def test_format_item_includes_pickup_time(self):
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        start_dt = now.replace(hour=17, minute=0, second=0)
        end_dt = now.replace(hour=18, minute=0, second=0)
        item = {
            "display_name": "Bakery Bag",
            "items_available": 1,
            "pickup_interval": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
        }
        info = format_item(item)
        assert "pickup_time" in info
        assert "Today 5:00 PM - 6:00 PM" in info["pickup_time"]
