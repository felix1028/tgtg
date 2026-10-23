import datetime
import pytest
from unittest.mock import MagicMock, patch
from tgtg.exceptions import TgtgAPIError
from tgtg_bot.sniper import (
    check_item_stock,
    fetch_favorites_schedule,
    parse_drop_time,
    prompt_user_ranking,
    run_daily_sniper,
    secure_bag,
    snipe_item,
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

    def test_ranking_prints_rating_if_present(self, capsys):
        favorites = [
            {
                "info": {
                    "store_name": "Bakery",
                    "display_name": "Pastry Bag",
                    "price": "5.00 USD",
                    "rating": "⭐ 4.8 (50)",
                },
                "items_available": 1,
            }
        ]
        prompt_user_ranking(favorites, user_input="1")
        out = capsys.readouterr().out
        assert "⭐ 4.8 (50)" in out

    def test_ranking_prints_pickup_time_if_present(self, capsys):
        favorites = [
            {
                "info": {
                    "store_name": "Bakery",
                    "display_name": "Pastry Bag",
                    "price": "5.00 USD",
                    "pickup_time": "Today 8:00 PM - 9:00 PM",
                },
                "items_available": 1,
            }
        ]
        prompt_user_ranking(favorites, user_input="1")
        out = capsys.readouterr().out
        assert "🧺 Today 8:00 PM - 9:00 PM" in out

    def test_exits_on_empty(self, mock_favorites):
        with pytest.raises(SystemExit):
            prompt_user_ranking(mock_favorites, user_input="   ")

    def test_prompt_user_ranking_allows_more_than_three_stores(self, mock_favorites):
        # User can select any number of stores (e.g. 4 stores), not limited to 3
        ranked = prompt_user_ranking(mock_favorites, user_input="1, 2, 3, 4")
        assert len(ranked) == 4
        assert [r["info"]["store_name"] for r in ranked] == ["Store A", "Store B", "Store C", "Store D"]

    def test_prompt_user_ranking_all_keyword(self, mock_favorites):
        # User can type 'all' to select all stores in favorites
        ranked = prompt_user_ranking(mock_favorites, user_input="all")
        assert len(ranked) == 4
        assert [r["info"]["store_name"] for r in ranked] == ["Store A", "Store B", "Store C", "Store D"]


class TestDisplayMenu:
    def test_display_menu_includes_ratings(self, capsys):
        from tgtg_bot.sniper import display_menu
        favorites = [
            {
                "info": {
                    "store_name": "Whole Foods",
                    "display_name": "Produce Bag",
                    "price": "6.99 USD",
                    "rating": "⭐ 4.7 (29)",
                },
                "items_available": 0,
                "is_future_drop": False,
                "drop_time_str": "No drop time",
            }
        ]
        display_menu(favorites)
        out = capsys.readouterr().out
        assert "Whole Foods" in out
        assert "Bag: Produce Bag (6.99 USD) | ⭐ 4.7 (29)" in out

    def test_display_menu_includes_pickup_time(self, capsys):
        from tgtg_bot.sniper import display_menu
        favorites = [
            {
                "info": {
                    "store_name": "Artisan Bakery",
                    "display_name": "Pastry Bag",
                    "price": "4.99 USD",
                    "pickup_time": "Today 8:00 PM - 9:00 PM",
                },
                "items_available": 2,
                "is_future_drop": False,
                "drop_time_str": "No drop time",
            }
        ]
        display_menu(favorites)
        out = capsys.readouterr().out
        assert "Artisan Bakery" in out
        assert "Pickup: 🧺 Today 8:00 PM - 9:00 PM" in out
        assert "IN STOCK (2)" in out


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

    def test_sorts_in_stock_before_future_drops(self):
        from tgtg_bot.sniper import sort_targets_chronologically
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        future_drop = {"drop_time": now + datetime.timedelta(hours=2), "items_available": 0, "name": "Future"}
        in_stock_now = {"drop_time": None, "items_available": 2, "name": "InStock"}

        sorted_list = sort_targets_chronologically([future_drop, in_stock_now])
        assert sorted_list[0]["name"] == "InStock"
        assert sorted_list[1]["name"] == "Future"


class TestSecureBag:
    @patch("tgtg_bot.sniper.send_whatsapp_message")
    def test_reserve_success(self, mock_send, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE", "+1234567890")
        monkeypatch.setenv("WHATSAPP_APIKEY", "mock_key")
        
        mock_client = MagicMock()
        
        target = {
            "item_id": "item-999",
            "info": {
                "store_name": "Whole Foods",
                "display_name": "Produce Bag",
                "price": "6.99 USD",
                "items_available": 1,
            },
        }
        
        success, action = secure_bag(mock_client, target)
        assert success is True
        assert action == "alerted"
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert "BAG AVAILABLE NOW" in kwargs["message"]
        assert "https://share.toogoodtogo.com/item/item-999" in kwargs["message"]

    @patch("tgtg_bot.sniper.send_whatsapp_message")
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    @patch("tgtg_bot.adb_buyer.auto_checkout_item", return_value=(True, "Success", {"order_id": "ord_100"}))
    def test_auto_buy_success(self, mock_checkout, mock_conn, mock_send, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE", "+1234567890")
        monkeypatch.setenv("WHATSAPP_APIKEY", "mock_key")
        mock_client = MagicMock()
        target = {
            "item_id": "item-999",
            "info": {
                "store_name": "Whole Foods",
                "display_name": "Produce Bag",
                "price": "6.99 USD",
                "items_available": 1,
            },
        }
        success, action = secure_bag(mock_client, target, auto_buy=True)
        assert success is True
        assert action == "purchased"
        mock_checkout.assert_called_once()
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert "BAG PURCHASED & CONFIRMED" in kwargs["message"]
        assert "ord_100" in kwargs["message"]

    @patch("tgtg_bot.sniper.send_whatsapp_message")
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(False, "No device attached"))
    def test_auto_buy_fails_when_offline_without_fallback(self, mock_conn, mock_send, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE", "+1234567890")
        monkeypatch.setenv("WHATSAPP_APIKEY", "mock_key")
        mock_client = MagicMock()
        target = {
            "item_id": "item-999",
            "info": {
                "store_name": "Whole Foods",
                "display_name": "Produce Bag",
                "price": "6.99 USD",
                "items_available": 1,
            },
        }
        success, action = secure_bag(mock_client, target, auto_buy=True)
        assert success is False
        assert action == "failed"
        mock_send.assert_not_called()

    @patch("tgtg_bot.sniper.send_whatsapp_message")
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    @patch("tgtg_bot.adb_buyer.auto_checkout_item", return_value=(False, "Failed to confirm order", None))
    def test_auto_buy_fails_when_checkout_fails_without_fallback(self, mock_checkout, mock_conn, mock_send, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE", "+1234567890")
        monkeypatch.setenv("WHATSAPP_APIKEY", "mock_key")
        mock_client = MagicMock()
        target = {
            "item_id": "item-999",
            "info": {
                "store_name": "Whole Foods",
                "display_name": "Produce Bag",
                "price": "6.99 USD",
                "items_available": 1,
            },
        }
        success, action = secure_bag(mock_client, target, auto_buy=True)
        assert success is False
        assert action == "failed"
        mock_send.assert_not_called()


class TestFavoritesSchedule:
    def test_fetch_favorites_schedule_queries_detail_when_schedule_missing(self):
        mock_client = MagicMock()
        mock_client.get_items.return_value = [
            {
                "item": {"item_id": "item_123", "name": "Surprise Bag"},
                "store": {"store_name": "Test Bakery"},
                "display_name": "Pastry Bag",
                "items_available": 0,
                "item_tags": [],
            }
        ]
        mock_client.get_item.return_value = {
            "item_id": "item_123",
            "items_available": 0,
            "next_sales_window_purchase_start": "2026-10-12T15:00:00Z",
        }

        favorites = fetch_favorites_schedule(mock_client)
        assert len(favorites) == 1
        assert favorites[0]["item_id"] == "item_123"
        assert favorites[0]["drop_time"] is not None
        mock_client.get_items.assert_called_once_with(page_size=100)
        mock_client.get_item.assert_called_once_with("item_123")

    def test_fetch_favorites_schedule_skips_nothing_to_save_today(self):
        mock_client = MagicMock()
        mock_client.get_items.return_value = [
            {
                "item": {"item_id": "item_empty", "name": "Surprise Bag"},
                "store": {"store_name": "Sold Out Bakery"},
                "display_name": "Bread Bag",
                "items_available": 0,
                "item_tags": [{"id": "NOTHING_TO_SAVE_TODAY"}],
            }
        ]

        favorites = fetch_favorites_schedule(mock_client)
        assert len(favorites) == 1
        assert favorites[0]["drop_time"] is None
        mock_client.get_item.assert_not_called()


class TestSnipeAndRunDaily:
    def test_check_item_stock(self):
        mock_client = MagicMock()
        mock_client.get_item.return_value = {"items_available": 3}
        stock = check_item_stock(mock_client, "123")
        assert stock == 3

    def test_check_item_stock_error_returns_none(self):
        mock_client = MagicMock()
        mock_client.get_item.side_effect = TgtgAPIError(500, "Server error")
        stock = check_item_stock(mock_client, "123")
        assert stock is None

    @patch("tgtg_bot.sniper.check_item_stock", return_value=None)
    def test_snipe_item_aborts_on_repeated_errors(self, mock_stock):
        mock_client = MagicMock()
        target = {
            "item_id": "item-err",
            "info": {"store_name": "Store", "display_name": "Bag", "items_available": 0},
        }
        with patch("time.sleep"):  # Speed up loop
            result = snipe_item(mock_client, target, poll_interval=0.01, max_check_seconds=30)
        assert result is False
        assert mock_stock.call_count >= 5

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

    @patch("tgtg_bot.sniper.send_whatsapp_message")
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    @patch("tgtg_bot.adb_buyer.auto_checkout_item")
    def test_secure_bag_multi_quantity_autobuy(self, mock_checkout, mock_conn, mock_send, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE", "+1234567890")
        monkeypatch.setenv("WHATSAPP_APIKEY", "mock_key")
        mock_checkout.return_value = (True, "Purchased 2 bags!", {"order_id": "ord_multi", "quantity": 2})

        mock_client = MagicMock()
        target = {
            "item_id": "item-1",
            "info": {
                "store_name": "Bakery",
                "display_name": "Pastry Bag",
                "price": "4.99 USD",
                "items_available": 3,
            },
        }
        success, action = secure_bag(mock_client, target, auto_buy=True, quantity=2)
        assert success is True
        assert action == "purchased"
        assert target["last_acquired_quantity"] == 2
        mock_checkout.assert_called_once_with(
            item_id="item-1",
            client=mock_client,
            device_id="emulator-5554",
            quantity=2,
            exclude_order_ids=None,
        )
        _, kwargs = mock_send.call_args
        assert "2 BAGS PURCHASED & CONFIRMED" in kwargs["message"]
        assert "Quantity: 2" in kwargs["message"]

    @patch("tgtg_bot.sniper.secure_bag")
    @patch("tgtg_bot.sniper.check_item_stock")
    def test_run_daily_sniper_multi_targets_quota(self, mock_stock, mock_secure):
        # Target 1 has 1 bag, Target 2 has 1 bag. With goal of 2 bags, sniper continues to Target 2.
        mock_stock.side_effect = [1, 1]
        
        def fake_secure(client, target, **kwargs):
            target["last_order"] = {"order_id": f"ord_{target['item_id']}"}
            target["last_acquired_quantity"] = 1
            return True, "purchased"

        mock_secure.side_effect = fake_secure

        mock_client = MagicMock()
        ranked = [
            {"item_id": "t1", "info": {"store_name": "Store 1", "display_name": "Bag 1"}, "drop_time": None},
            {"item_id": "t2", "info": {"store_name": "Store 2", "display_name": "Bag 2"}, "drop_time": None},
        ]

        success = run_daily_sniper(
            mock_client,
            ranked,
            total_bags_target=2,
        )
        assert success is True
        assert mock_secure.call_count == 2

    @patch("tgtg_bot.sniper.secure_bag")
    @patch("tgtg_bot.sniper.check_item_stock")
    def test_run_daily_sniper_one_bag_per_target(self, mock_stock, mock_secure):
        # 3 different store targets, each strictly targets 1 bag
        mock_stock.side_effect = [2, 1, 3]

        def fake_secure(client, target, quantity=1, **kwargs):
            target["last_order"] = {"order_id": f"ord_{target['item_id']}"}
            target["last_acquired_quantity"] = quantity
            return True, "purchased"

        mock_secure.side_effect = fake_secure
        mock_client = MagicMock()

        ranked = [
            {"item_id": "t1", "info": {"store_name": "Store 1", "display_name": "Bag 1"}, "drop_time": None},
            {"item_id": "t2", "info": {"store_name": "Store 2", "display_name": "Bag 2"}, "drop_time": None},
            {"item_id": "t3", "info": {"store_name": "Store 3", "display_name": "Bag 3"}, "drop_time": None},
        ]

        # Targets 3 stores, 1 bag each = goal of 3 bags
        success = run_daily_sniper(
            mock_client,
            ranked,
            total_bags_target=3,
        )
        assert success is True
        assert mock_secure.call_count == 3

    @patch("tgtg_bot.sniper.secure_bag")
    @patch("tgtg_bot.sniper.check_item_stock")
    def test_run_daily_sniper_single_bag_per_store_limit(self, mock_stock, mock_secure):
        # Store has multiple bags in stock, but bot procures strictly 1
        mock_stock.return_value = 5

        def fake_secure(client, target, quantity=1, **kwargs):
            target["last_order"] = {"order_id": f"ord_{target['item_id']}"}
            target["last_acquired_quantity"] = quantity
            return True, "purchased"

        mock_secure.side_effect = fake_secure
        mock_client = MagicMock()

        ranked = [
            {"item_id": "t1", "info": {"store_name": "Store 1", "display_name": "Bag 1"}, "drop_time": None},
        ]

        success = run_daily_sniper(
            mock_client,
            ranked,
            total_bags_target=1,
        )
        assert success is True
        mock_secure.assert_called_once()

    @patch("tgtg_bot.sniper.secure_bag", return_value=(True, "purchased"))
    @patch("tgtg_bot.sniper.check_item_stock")
    def test_snipe_item_three_phase_redrop_capture(self, mock_stock, mock_secure):
        # Phase 1: Item is sold out (stock = 0), establishing first_sold_out_time
        # Phase 2: Cart-lock rest (heartbeat polling)
        # Phase 3: At 5m timeout, cart expires and stock returns to 1!
        mock_stock.side_effect = [0, 0, 1]
        mock_client = MagicMock()
        target = {
            "item_id": "item-redrop",
            "info": {"store_name": "Eataly", "display_name": "Prime Bag", "items_available": 0},
            "drop_time": None,
        }

        with patch("time.sleep"):
            # Set short test durations for fast unit test execution
            secured = snipe_item(
                mock_client,
                target,
                poll_interval=0.01,
                max_check_seconds=0.05,
                watch_redrop=True,
                heartbeat_poll_interval=0.01,
                redrop_timeout_seconds=0.10,
                redrop_burst_seconds=0.10,
            )
        assert secured is True
        mock_secure.assert_called_once()

    @patch("tgtg_bot.sniper.secure_bag", return_value=(True, "purchased"))
    @patch("tgtg_bot.sniper.check_item_stock", return_value=1)
    def test_run_daily_sniper_seeds_initial_active_order_ids(self, mock_stock, mock_secure):
        mock_client = MagicMock()
        mock_client.get_active.return_value = {
            "orders": [{"order_id": "existing_ord_42"}]
        }
        ranked = [
            {"item_id": "t1", "info": {"store_name": "Store 1", "display_name": "Bag 1"}, "drop_time": None},
        ]
        run_daily_sniper(mock_client, ranked)
        assert "existing_ord_42" in mock_secure.call_args.kwargs["exclude_order_ids"]

    @patch("tgtg_bot.sniper.secure_bag")
    @patch("tgtg_bot.sniper.check_item_stock", return_value=1)
    def test_snipe_item_retries_and_sends_fallback_alert_on_autobuy_failure(self, mock_stock, mock_secure):
        # First 2 auto_buy calls fail, then 3rd call is fallback manual alert (auto_buy=False)
        mock_secure.side_effect = [
            (False, "failed"),
            (False, "failed"),
            (True, "alerted"),
        ]
        mock_client = MagicMock()
        target = {
            "item_id": "item-1",
            "info": {"store_name": "Store", "display_name": "Bag", "items_available": 0},
        }
        with patch("time.sleep"):
            result = snipe_item(
                mock_client,
                target,
                poll_interval=0.01,
                max_check_seconds=5,
                auto_buy=True,
                max_buy_attempts=2,
            )
        assert result is False
        assert mock_secure.call_count == 3
        assert mock_secure.call_args_list[2].kwargs["auto_buy"] is False

    @patch("tgtg_bot.sniper.secure_bag", return_value=(True, "purchased"))
    @patch("tgtg_bot.sniper.check_item_stock", side_effect=[0, 1])
    def test_snipe_item_burst_polling_near_drop_time(self, mock_stock, mock_secure):
        mock_client = MagicMock()
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        target = {
            "item_id": "item-burst",
            "info": {"store_name": "Store", "display_name": "Bag", "items_available": 0},
            "drop_time": now,
        }
        with patch("time.sleep") as mock_sleep:
            result = snipe_item(
                mock_client,
                target,
                poll_interval=0.80,
                burst_poll_interval=0.25,
                max_check_seconds=5,
            )
        assert result is True
        assert mock_sleep.call_count == 1
        slept = mock_sleep.call_args[0][0]
        assert 0.15 <= slept <= 0.35

    @patch("tgtg_bot.sniper.sys.platform", "darwin")
    @patch("tgtg_bot.sniper.subprocess.Popen")
    def test_prevent_system_sleep_starts_and_terminates_caffeinate(self, mock_popen):
        import tgtg_bot.sniper as sniper_mod
        from tgtg_bot.sniper import prevent_system_sleep
        sniper_mod._active_caffeinate_proc = None
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        with prevent_system_sleep() as proc:
            assert proc is mock_proc
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[:3] == ["caffeinate", "-dims", "-w"]
            # Nested call reuses the same process without spawning another
            with prevent_system_sleep() as nested_proc:
                assert nested_proc is mock_proc
                assert mock_popen.call_count == 1
            mock_proc.terminate.assert_not_called()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=2)


