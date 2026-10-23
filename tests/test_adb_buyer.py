import subprocess
from unittest.mock import MagicMock, patch
import pytest

from tgtg_bot.adb_buyer import (
    auto_checkout_item,
    get_order_quantity,
    get_screen_dimensions,
    get_tap_coordinates,
    is_emulator_connected,
    open_item_in_app,
    run_adb_command,
    tap_screen,
    verify_order_confirmed,
    wake_and_unlock,
)


class TestAdbBuyer:
    @patch("subprocess.run")
    def test_is_emulator_connected_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\nemulator-5554\tdevice\n",
            stderr="",
        )
        connected, dev = is_emulator_connected()
        assert connected is True
        assert dev == "emulator-5554"

    @patch("subprocess.run")
    def test_is_emulator_connected_offline(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\nemulator-5554\toffline\n",
            stderr="",
        )
        connected, reason = is_emulator_connected()
        assert connected is False
        assert "none online" in reason

    @patch("subprocess.run")
    def test_is_emulator_connected_none(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\n\n",
            stderr="",
        )
        connected, reason = is_emulator_connected()
        assert connected is False
        assert "No Android devices" in reason

    @patch("subprocess.run")
    def test_get_screen_dimensions(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "shell", "wm", "size"],
            returncode=0,
            stdout="Physical size: 1080x2400\n",
            stderr="",
        )
        w, h = get_screen_dimensions()
        assert w == 1080
        assert h == 2400

    def test_get_tap_coordinates_defaults(self):
        res_x, res_y = get_tap_coordinates("reserve", 1080, 2400)
        assert res_x == int(1080 * 0.720)
        assert res_y == int(2400 * 0.898)

        got_it_x, got_it_y = get_tap_coordinates("got_it", 1080, 2400)
        assert got_it_x == int(1080 * 0.500)
        assert got_it_y == int(2400 * 0.864)

        pm_x, pm_y = get_tap_coordinates("payment_method", 1080, 2400)
        assert pm_x == int(1080 * 0.865)
        assert pm_y == int(2400 * 0.419)

        pay_x, pay_y = get_tap_coordinates("confirm", 1080, 2400)
        assert pay_x == int(1080 * 0.665)
        assert pay_y == int(2400 * 0.713)

        pay_exp_x, pay_exp_y = get_tap_coordinates("confirm_expanded", 1080, 2400)
        assert pay_exp_x == int(1080 * 0.665)
        assert pay_exp_y == int(2400 * 0.944)

        cvv_x, cvv_y = get_tap_coordinates("cvv", 1080, 2400)
        assert cvv_x == int(1080 * 0.740)
        assert cvv_y == int(2400 * 0.806)

        sec_x, sec_y = get_tap_coordinates("second_pay", 1080, 2400)
        assert sec_x == int(1080 * 0.500)
        assert sec_y == int(2400 * 0.899)

    def test_get_tap_coordinates_env_override(self, monkeypatch):
        monkeypatch.setenv("TGTG_ADB_RESERVE_X", "500")
        monkeypatch.setenv("TGTG_ADB_RESERVE_Y", "2100")
        monkeypatch.setenv("TGTG_ADB_PAYMENT_METHOD_X", "520")
        monkeypatch.setenv("TGTG_ADB_PAYMENT_METHOD_Y", "1040")
        monkeypatch.setenv("TGTG_ADB_CVV_X", "820")
        monkeypatch.setenv("TGTG_ADB_CVV_Y", "1500")
        monkeypatch.setenv("TGTG_ADB_SECOND_PAY_X", "550")
        monkeypatch.setenv("TGTG_ADB_SECOND_PAY_Y", "2120")
        
        assert get_tap_coordinates("reserve", 1080, 2400) == (500, 2100)
        assert get_tap_coordinates("payment_method", 1080, 2400) == (520, 1040)
        assert get_tap_coordinates("cvv", 1080, 2400) == (820, 1500)
        assert get_tap_coordinates("second_pay", 1080, 2400) == (550, 2120)

    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    @patch("tgtg_bot.adb_buyer.run_adb_command")
    def test_input_security_code_and_confirm(self, mock_cmd, mock_tap):
        from tgtg_bot.adb_buyer import input_security_code_and_confirm
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        
        success = input_security_code_and_confirm(
            security_code="876",
            device_id="emulator-5554",
            width=1080,
            height=2400,
            submit_delay=0.0,
        )
        assert success is True
        # Check that adb input text 876 was called directly on auto-focused field
        cmd_calls = [call[0][0] for call in mock_cmd.call_args_list]
        text_inputs = [c for c in cmd_calls if "input" in c and "text" in c]
        assert len(text_inputs) == 1
        assert "876" in text_inputs[0]

        # Ensure KEYCODE_ESCAPE (111) is NOT sent (would dismiss Adyen bottom sheet)
        key_events = [c for c in cmd_calls if "input" in c and "keyevent" in c]
        assert not any("111" in c for c in key_events)

        # Single tap on second_pay (since editText_securityCode is auto-focused)
        assert mock_tap.call_count == 1

    @patch("tgtg_bot.adb_buyer.run_adb_command")
    def test_wake_and_unlock(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        assert wake_and_unlock("emulator-5554") is True
        assert mock_cmd.call_count == 2

    @patch("tgtg_bot.adb_buyer.run_adb_command")
    def test_open_item_in_app(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="mCurrentFocus=ItemViewActivity"
        )
        assert open_item_in_app("item_123", load_delay=0.01, wait_for_focus=False) is True
        # Verify the am start call contains native scheme
        all_calls = [call[0][0] for call in mock_cmd.call_args_list]
        am_start_call = next(c for c in all_calls if "start" in c)
        assert "toogoodtogoapp://item/item_123" in am_start_call
        assert "0x14000000" in am_start_call

    @patch("tgtg_bot.adb_buyer.run_adb_command")
    def test_wait_for_item_view(self, mock_cmd):
        from tgtg_bot.adb_buyer import wait_for_item_view
        mock_cmd.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="mCurrentFocus=MainActivity"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="mCurrentFocus=ItemViewActivity"),
        ]
        assert wait_for_item_view("emulator-5554", max_wait=0.5, poll_interval=0.01) is True


    def test_verify_order_confirmed_success(self):
        mock_client = MagicMock()
        mock_client.get_active.return_value = {
            "orders": [
                {
                    "order_id": "ord_999",
                    "state": "ACTIVE",
                    "item": {"item_id": "item_123"},
                }
            ]
        }
        order = verify_order_confirmed(mock_client, "item_123", timeout=1.0, poll_interval=0.01)
        assert order is not None
        assert order["order_id"] == "ord_999"

    def test_verify_order_confirmed_top_level_and_order_items(self):
        mock_client = MagicMock()
        mock_client.get_active.return_value = {
            "orders": [
                {
                    "order_id": "h217ykvc01dc1",
                    "state": "ACTIVE",
                    "item_id": "230523622664989121",
                    "order_items": [{"item_id": "230523622664989121", "quantity": 1}],
                }
            ]
        }
        order = verify_order_confirmed(mock_client, "230523622664989121", timeout=1.0, poll_interval=0.01)
        assert order is not None
        assert order["order_id"] == "h217ykvc01dc1"

    @patch("tgtg_bot.adb_buyer.is_item_view_focused", return_value=True)
    @patch("tgtg_bot.adb_buyer.verify_order_confirmed")
    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    @patch("tgtg_bot.adb_buyer.open_item_in_app", return_value=True)
    @patch("tgtg_bot.adb_buyer.wake_and_unlock", return_value=True)
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    def test_auto_checkout_item_success(
        self,
        mock_conn,
        mock_wake,
        mock_open,
        mock_tap,
        mock_verify,
        mock_focused,
    ):
        mock_client = MagicMock()
        mock_verify.return_value = {"order_id": "ord_12345"}
        
        success, msg, order = auto_checkout_item(
            item_id="item_123",
            client=mock_client,
            item_load_wait=0.01,
            modal_wait=0.01,
            review_wait=0.01,
            adyen_sheet_wait=0.0,
        )
        assert success is True
        assert "ord_12345" in msg
        assert order["order_id"] == "ord_12345"
        # 3 taps: 1x Reserve + Got it! + Confirm Reserve (payment card pre-selected)
        assert mock_tap.call_count == 3

    @patch("tgtg_bot.adb_buyer.is_item_view_focused", return_value=True)
    @patch("tgtg_bot.adb_buyer.verify_order_confirmed")
    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    @patch("tgtg_bot.adb_buyer.open_item_in_app", return_value=True)
    @patch("tgtg_bot.adb_buyer.wake_and_unlock", return_value=True)
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    def test_auto_checkout_item_retry_success(
        self,
        mock_conn,
        mock_wake,
        mock_open,
        mock_tap,
        mock_verify,
        mock_focused,
    ):
        mock_client = MagicMock()
        mock_verify.side_effect = [None, {"order_id": "ord_retry_999"}]
        
        success, msg, order = auto_checkout_item(
            item_id="item_123",
            client=mock_client,
            item_load_wait=0.01,
            modal_wait=0.01,
            review_wait=0.01,
            adyen_sheet_wait=0.0,
        )
        assert success is True
        assert "ord_retry_999" in msg
        assert order["order_id"] == "ord_retry_999"
        # 3 initial checkout taps + 1 second_pay tap = 4 taps
        assert mock_tap.call_count == 4

    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(False, "No device"))
    def test_auto_checkout_item_no_device(self, mock_conn):
        success, msg, order = auto_checkout_item("item_123")
        assert success is False
        assert "not available" in msg
        assert order is None

    def test_get_tap_coordinates_plus(self, monkeypatch):
        # Default ratios
        plus_x, plus_y = get_tap_coordinates("plus", 1080, 2400)
        assert plus_x == int(1080 * 0.293)
        assert plus_y == int(2400 * 0.713)

        # Environment overrides
        monkeypatch.setenv("TGTG_ADB_PLUS_X", "310")
        monkeypatch.setenv("TGTG_ADB_PLUS_Y", "1760")
        assert get_tap_coordinates("plus", 1080, 2400) == (310, 1760)

    def test_get_order_quantity_variants(self):
        # Top-level quantity
        assert get_order_quantity({"quantity": 3}) == 3
        # String quantity
        assert get_order_quantity({"quantity": "2"}) == 2
        # order_items quantity
        assert get_order_quantity({"order_items": [{"quantity": 5}]}) == 5
        # order_lines quantity
        assert get_order_quantity({"order_lines": [{"quantity": 4}]}) == 4
        # item quantity
        assert get_order_quantity({"item": {"quantity": 2}}) == 2
        # None or missing fallback
        assert get_order_quantity({}) == 1
        assert get_order_quantity(None) == 1

    def test_verify_order_confirmed_exclude_order_ids(self):
        mock_client = MagicMock()
        mock_client.get_active.return_value = {
            "orders": [
                {
                    "order_id": "ord_old_1",
                    "state": "ACTIVE",
                    "item": {"item_id": "item_123"},
                },
                {
                    "order_id": "ord_new_2",
                    "state": "ACTIVE",
                    "item": {"item_id": "item_123"},
                },
            ]
        }
        # Excluding the old order should return the new order
        order = verify_order_confirmed(
            mock_client,
            "item_123",
            timeout=0.5,
            poll_interval=0.01,
            exclude_order_ids={"ord_old_1"},
        )
        assert order is not None
        assert order["order_id"] == "ord_new_2"

        # If all matching orders are excluded, return None
        none_order = verify_order_confirmed(
            mock_client,
            "item_123",
            timeout=0.1,
            poll_interval=0.01,
            exclude_order_ids={"ord_old_1", "ord_new_2"},
        )
        assert none_order is None

    @patch("tgtg_bot.adb_buyer.is_item_view_focused", return_value=True)
    @patch("tgtg_bot.adb_buyer.verify_order_confirmed")
    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    @patch("tgtg_bot.adb_buyer.open_item_in_app", return_value=True)
    @patch("tgtg_bot.adb_buyer.wake_and_unlock", return_value=True)
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    def test_auto_checkout_item_multi_quantity(
        self,
        mock_conn,
        mock_wake,
        mock_open,
        mock_tap,
        mock_verify,
        mock_focused,
    ):
        mock_client = MagicMock()
        mock_verify.return_value = {"order_id": "ord_multi", "quantity": 3}

        success, msg, order = auto_checkout_item(
            item_id="item_123",
            client=mock_client,
            quantity=3,
            item_load_wait=0.01,
            modal_wait=0.01,
            review_wait=0.01,
            adyen_sheet_wait=0.0,
        )
        assert success is True
        assert "3 bag(s)" in msg
        # Taps: 1x Reserve + Got it! + (3 - 1 = 2 '+' taps) + Confirm Reserve = 5 taps total
        assert mock_tap.call_count == 5

    def test_check_item_sold_out(self):
        from tgtg_bot.adb_buyer import check_item_sold_out
        mock_client = MagicMock()
        mock_client.get_item.return_value = {"items_available": 0}
        assert check_item_sold_out(mock_client, "item_1") is True

        mock_client.get_item.return_value = {"items_available": 2}
        assert check_item_sold_out(mock_client, "item_1") is False

        mock_client.get_item.return_value = {"items_available": 1, "item_tags": [{"id": "SOLD_OUT"}]}
        assert check_item_sold_out(mock_client, "item_1") is True

        assert check_item_sold_out(None, "item_1") is False

    @patch("tgtg_bot.adb_buyer.is_item_view_focused", return_value=True)
    @patch("tgtg_bot.adb_buyer.verify_order_confirmed", return_value=None)
    @patch("tgtg_bot.adb_buyer.check_item_sold_out", return_value=True)
    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    @patch("tgtg_bot.adb_buyer.open_item_in_app", return_value=True)
    @patch("tgtg_bot.adb_buyer.wake_and_unlock", return_value=True)
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    def test_auto_checkout_item_sold_out_aborts(
        self,
        mock_conn,
        mock_wake,
        mock_open,
        mock_tap,
        mock_sold_out,
        mock_verify,
        mock_focused,
    ):
        mock_client = MagicMock()
        success, msg, order = auto_checkout_item(
            item_id="item_sold_out",
            client=mock_client,
            item_load_wait=0.01,
            modal_wait=0.01,
            review_wait=0.01,
            adyen_sheet_wait=0.0,
        )
        assert success is False
        assert "sold out" in msg.lower()
        assert order is None

    @patch("tgtg_bot.adb_buyer.is_item_view_focused", return_value=False)
    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    @patch("tgtg_bot.adb_buyer.open_item_in_app", return_value=True)
    @patch("tgtg_bot.adb_buyer.wake_and_unlock", return_value=True)
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    def test_auto_checkout_item_not_focused_aborts(
        self,
        mock_conn,
        mock_wake,
        mock_open,
        mock_tap,
        mock_focused,
    ):
        mock_client = MagicMock()
        success, msg, order = auto_checkout_item(
            item_id="item_unfocused",
            client=mock_client,
            item_load_wait=0.01,
            modal_wait=0.01,
            review_wait=0.01,
            adyen_sheet_wait=0.0,
        )
        assert success is False
        assert "not focused" in msg.lower()
        assert order is None
        assert mock_tap.call_count == 0

    @patch("tgtg_bot.adb_buyer.open_item_in_app")
    @patch("tgtg_bot.adb_buyer.disable_animations", return_value=True)
    @patch("tgtg_bot.adb_buyer.get_screen_dimensions", return_value=(1080, 2400))
    @patch("tgtg_bot.adb_buyer.wake_and_unlock", return_value=True)
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    def test_prewarm_target_wakes_and_caches_dims_without_opening_sold_out_item(
        self, mock_conn, mock_wake, mock_dims, mock_anim, mock_open
    ):
        from tgtg_bot.adb_buyer import prewarm_target
        assert prewarm_target("item_123", device_id="emulator-5554") is True
        mock_open.assert_not_called()
        mock_wake.assert_called_once_with("emulator-5554")
        mock_dims.assert_called_once_with("emulator-5554")
        mock_anim.assert_called_once_with("emulator-5554")

    @patch("tgtg_bot.adb_buyer.is_item_view_focused", return_value=True)
    @patch("tgtg_bot.adb_buyer.verify_order_confirmed", return_value={"order_id": "ord_open_1"})
    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    @patch("tgtg_bot.adb_buyer.open_item_in_app", return_value=True)
    @patch("tgtg_bot.adb_buyer.wake_and_unlock", return_value=True)
    @patch("tgtg_bot.adb_buyer.is_emulator_connected", return_value=(True, "emulator-5554"))
    def test_auto_checkout_item_always_opens_fresh_deep_link(
        self, mock_conn, mock_wake, mock_open, mock_tap, mock_verify, mock_focused
    ):
        mock_client = MagicMock()
        success, _msg, _order = auto_checkout_item(
            item_id="item_123",
            client=mock_client,
            item_load_wait=0.01,
            modal_wait=0.01,
            review_wait=0.01,
            adyen_sheet_wait=0.0,
        )
        assert success is True
        mock_open.assert_called_once_with("item_123", device_id="emulator-5554", load_delay=0.01)

    @patch("tgtg_bot.adb_buyer.tap_screen", return_value=True)
    def test_execute_checkout_taps_burst(self, mock_tap):
        from tgtg_bot.adb_buyer import _execute_checkout_taps, get_tap_coordinates
        res_x, res_y = get_tap_coordinates("reserve", 1080, 2400)
        _execute_checkout_taps(
            width=1080,
            height=2400,
            device_id="emulator-5554",
            quantity=1,
            modal_wait=0.0,
            review_wait=0.0,
            reserve_burst=4,
            burst_interval=0.0,
        )
        # 4x reserve burst + 1 got it + 1 confirm = 6 taps
        assert mock_tap.call_count == 6
        first_four = mock_tap.call_args_list[:4]
        for c in first_four:
            assert c[0] == (res_x, res_y)

    @patch("tgtg_bot.adb_buyer.run_adb_command")
    @patch("tgtg_bot.adb_buyer.take_screenshot")
    def test_abort_sold_out_screenshot_before_back(self, mock_screenshot, mock_cmd):
        from tgtg_bot.adb_buyer import _abort_sold_out
        manager = MagicMock()
        manager.attach_mock(mock_screenshot, "take_screenshot")
        manager.attach_mock(mock_cmd, "run_adb_command")

        success, msg, order = _abort_sold_out("item_123", "emulator-5554")
        assert success is False
        assert order is None

        # Verify take_screenshot was called before run_adb_command (KEYCODE_BACK)
        calls = [name for name, _args, _kwargs in manager.mock_calls]
        assert calls == ["take_screenshot", "run_adb_command"]
        mock_cmd.assert_called_once_with(
            ["shell", "input", "keyevent", "KEYCODE_BACK"],
            device_id="emulator-5554",
        )

    @patch("tgtg_bot.adb_buyer.run_adb_command")
    def test_is_item_view_focused_ignores_stale_window_history(self, mock_cmd):
        from tgtg_bot.adb_buyer import is_item_view_focused
        mock_cmd.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "  Window #4 Window{123 u0 com.app.tgtg/.ItemViewActivity}:\n"
                "  mCurrentFocus=Window{456 u0 com.app.tgtg/.MainActivity}\n"
                "  mFocusedApp=ActivityRecord{789 u0 com.app.tgtg/.MainActivity t12}\n"
            ),
        )
        assert is_item_view_focused("emulator-5554") is False



