import datetime
import pytest
from unittest.mock import MagicMock, patch
from tgtg.exceptions import TgtgAPIError
from tgtg_bot.sniper import (
    parse_drop_time,
    prompt_user_ranking,
    secure_bag,
    check_item_stock,
    snipe_item,
    run_daily_sniper,
)

class TestParseDropTime:
    def test_standard_iso(self):
        dt = parse_drop_time("2026-10-02T04:16:53Z")
        assert dt is not None
        assert dt.year == 2026

    def test_nanosecond_iso(self):
        dt = parse_drop_time("2026-10-01T04:16:57.497301272Z")
        assert dt is not None
        assert dt.year == 2026

    def test_none_or_empty(self):
        assert parse_drop_time(None) is None
        assert parse_drop_time("") is None
        assert parse_drop_time("invalid-date") is None


class TestPromptUserRanking:
    @pytest.fixture
    def mock_favorites(self):
        return [
            {"info": {"store_name": "Store A", "display_name": "Bag A", "price": "5.00 USD"}},
            {"info": {"store_name": "Store B", "display_name": "Bag B", "price": "6.00 USD"}},
            {"info": {"store_name": "Store C", "display_name": "Bag C", "price": "7.00 USD"}},
            {"info": {"store_name": "Store D", "display_name": "Bag D", "price": "8.00 USD"}},
        ]

    def test_parses_comma_separated_choices(self, mock_favorites):
        ranked = prompt_user_ranking(mock_favorites, user_input="2, 1, 4")
        assert len(ranked) == 3
        assert ranked[0]["info"]["store_name"] == "Store B"
        assert ranked[1]["info"]["store_name"] == "Store A"
        assert ranked[2]["info"]["store_name"] == "Store D"

    def test_parses_space_separated_choices(self, mock_favorites):
        ranked = prompt_user_ranking(mock_favorites, user_input="3 1")
        assert len(ranked) == 2
        assert ranked[0]["info"]["store_name"] == "Store C"
        assert ranked[1]["info"]["store_name"] == "Store A"

    def test_deduplicates_choices(self, mock_favorites):
        ranked = prompt_user_ranking(mock_favorites, user_input="2, 2, 1")
        assert len(ranked) == 2
        assert ranked[0]["info"]["store_name"] == "Store B"
        assert ranked[1]["info"]["store_name"] == "Store A"

    def test_exits_on_empty(self, mock_favorites):
        with pytest.raises(SystemExit):
            prompt_user_ranking(mock_favorites, user_input="   ")


class TestSortTargetsChronologically:
    def test_sorts_earliest_first(self):
        from tgtg_bot.sniper import sort_targets_chronologically
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        t1 = {"drop_time": now + datetime.timedelta(hours=2), "name": "Later"}
        t2 = {"drop_time": now + datetime.timedelta(minutes=30), "name": "Earlier"}
        t3 = {"drop_time": None, "name": "No drop time"}

        sorted_list = sort_targets_chronologically([t1, t2, t3])
        assert sorted_list[0]["name"] == "Earlier"
        assert sorted_list[1]["name"] == "Later"
        assert sorted_list[2]["name"] == "No drop time"


class TestSecureBag:
    @patch("tgtg_bot.sniper.send_whatsapp_message")
    def test_reserve_success(self, mock_send, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE", "+1234567890")
        monkeypatch.setenv("WHATSAPP_APIKEY", "mock_key")
        
        mock_client = MagicMock()
        mock_client.create_order.return_value = {"id": "order-12345"}
        
        target = {
            "item_id": "item-999",
            "info": {
                "store_name": "Whole Foods",
                "display_name": "Produce Bag",
                "price": "6.99 USD",
            },
        }
        
        success, action = secure_bag(mock_client, target)
        assert success is True
        assert action == "reserved"
        mock_client.create_order.assert_called_once_with("item-999", 1)
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert "BAG SECURED & RESERVED" in kwargs["message"]

    @patch("tgtg_bot.sniper.send_whatsapp_message")
    def test_reserve_fails_but_alerts(self, mock_send, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE", "+1234567890")
        monkeypatch.setenv("WHATSAPP_APIKEY", "mock_key")
        
        mock_client = MagicMock()
        mock_client.create_order.side_effect = TgtgAPIError(400, "App confirmation needed")
        
        target = {
            "item_id": "item-999",
            "info": {
                "store_name": "Whole Foods",
                "display_name": "Produce Bag",
                "price": "6.99 USD",
            },
        }
        
        success, action = secure_bag(mock_client, target)
        assert success is True
        assert action == "alerted"
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert "BAG AVAILABLE NOW" in kwargs["message"]


class TestSnipeAndRunDaily:
    def test_check_item_stock(self):
        mock_client = MagicMock()
        mock_client.get_item.return_value = {"items_available": 3}
        stock = check_item_stock(mock_client, "123")
        assert stock == 3

    @patch("tgtg_bot.sniper.secure_bag", return_value=(True, "reserved"))
    @patch("tgtg_bot.sniper.check_item_stock", side_effect=[0, 2])
    def test_snipe_item_detects_stock(self, mock_stock, mock_secure):
        mock_client = MagicMock()
        target = {
            "item_id": "item-1",
            "info": {"store_name": "Store", "display_name": "Bag", "items_available": 0},
        }
        result = snipe_item(mock_client, target, poll_interval=0.01, max_check_seconds=1)
        assert result is True
        mock_secure.assert_called_once()

    @patch("tgtg_bot.sniper.secure_bag", return_value=(True, "reserved"))
    @patch("tgtg_bot.sniper.check_item_stock", return_value=1)
    def test_run_daily_secures_immediate_stock(self, mock_stock, mock_secure):
        mock_client = MagicMock()
        ranked = [
            {
                "item_id": "item-1",
                "info": {"store_name": "Store 1", "display_name": "Bag 1"},
                "drop_time": None,
            }
        ]
        secured = run_daily_sniper(mock_client, ranked)
        assert secured is True
        mock_secure.assert_called_once()
