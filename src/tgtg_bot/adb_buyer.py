"""Android Emulator ADB Auto-Buyer module for Too Good To Go.

=============================================================================
Architecture & Rationale:
=============================================================================
Why Android App + ADB instead of Reverse-Engineered Web API?
  1. The Too Good To Go private API blocks automated web reservations via DataDome
     anti-bot CAPTCHA challenges.
  2. The official Android app contains saved payment methods (Google Pay, saved cards)
     and trusted device tokens.
  3. By automating user taps on an Android emulator via Android Debug Bridge (ADB),
     we complete purchases with 100% native app authenticity in under 2 seconds.

Checkout Lifecycle:
  1. Launch Intent: toogoodtogoapp://item/{id} opens item page directly in the app.
  2. Screen 1 (Details): Tap 'Reserve' pill button (bottom-right).
  3. Screen 2 (Disclaimer): Tap 'Got it!' disclaimer modal button.
  4. Screen 3 (Review): Optional: Tap '+' stepper for requested quantity -> Tap 'Confirm & Pay'.
  5. Screen 4 (Payment/CVV): Focus CVV field, enter security code, submit payment.
  6. Verification: Poll TGTG API (client.get_active()) to verify order is ACTIVE.
=============================================================================
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from typing import Any

from tgtg import TgtgClient

logger = logging.getLogger(__name__)

_ANDROID_SDK_PLATFORM_TOOLS = os.path.expanduser("~/Library/Android/sdk/platform-tools")
if os.path.isdir(_ANDROID_SDK_PLATFORM_TOOLS) and _ANDROID_SDK_PLATFORM_TOOLS not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{os.environ.get('PATH', '')}:{_ANDROID_SDK_PLATFORM_TOOLS}"

# Standard Android screen dimensions (Pixel 7 / 1080x2400)
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 2400

# Default card CVV / security code (customizable via TGTG_SECURITY_CODE)
DEFAULT_SECURITY_CODE = "876"

# Button tap coordinates as relative (x, y) ratios of screen dimensions,
# along with corresponding environment variable override names.
BUTTON_COORDINATES: dict[str, tuple[float, float, str, str]] = {
    "reserve": (0.720, 0.898, "TGTG_ADB_RESERVE_X", "TGTG_ADB_RESERVE_Y"),
    "got_it": (0.500, 0.864, "TGTG_ADB_GOT_IT_X", "TGTG_ADB_GOT_IT_Y"),
    "payment_method": (0.865, 0.419, "TGTG_ADB_PAYMENT_METHOD_X", "TGTG_ADB_PAYMENT_METHOD_Y"),
    "confirm": (0.665, 0.713, "TGTG_ADB_CONFIRM_X", "TGTG_ADB_CONFIRM_Y"),
    "confirm_expanded": (0.665, 0.944, "TGTG_ADB_CONFIRM_EXPANDED_X", "TGTG_ADB_CONFIRM_EXPANDED_Y"),
    "cvv": (0.740, 0.806, "TGTG_ADB_CVV_X", "TGTG_ADB_CVV_Y"),
    "second_pay": (0.500, 0.899, "TGTG_ADB_SECOND_PAY_X", "TGTG_ADB_SECOND_PAY_Y"),
    "plus": (0.293, 0.713, "TGTG_ADB_PLUS_X", "TGTG_ADB_PLUS_Y"),
}

# Dimension cache per device to avoid redundant wm size queries during drops
_cached_screen_dims: dict[str, tuple[int, int]] = {}


def run_adb_command(
    args: list[str],
    device_id: str | None = None,
    timeout: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    """Execute an ADB command with optional device targeting."""
    cmd = ["adb", *(["-s", device_id] if device_id else []), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def is_emulator_connected(device_id: str | None = None) -> tuple[bool, str]:
    """Check if an Android device or emulator is connected and online via ADB.

    Returns:
        (True, selected_device_id) if an online device is found,
        (False, error_reason) otherwise.
    """
    try:
        proc = run_adb_command(["devices"])
        if proc.returncode != 0:
            return False, f"adb error: {proc.stderr.strip()}"

        lines = [line.strip().split() for line in proc.stdout.splitlines()[1:] if line.strip()]
        devices = [(parts[0], parts[1]) for parts in lines if len(parts) >= 2]
        if not devices:
            return False, "No Android devices or emulators attached"

        online_devices = [d[0] for d in devices if d[1] == "device"]
        if not online_devices:
            return False, f"Device(s) found but none online/authorized: {devices}"

        if device_id and device_id not in online_devices:
            return False, f"Specified device '{device_id}' not online (available: {online_devices})"

        return True, device_id or online_devices[0]
    except Exception as e:
        return False, f"Failed to check adb devices: {e}"


def get_screen_dimensions(device_id: str | None = None) -> tuple[int, int]:
    """Fetch physical screen dimensions (width, height) using 'adb shell wm size'."""
    cache_key = device_id or "default"
    if cache_key in _cached_screen_dims:
        return _cached_screen_dims[cache_key]

    try:
        proc = run_adb_command(["shell", "wm", "size"], device_id=device_id)
        if proc.returncode == 0:
            match = re.search(r"(\d+)x(\d+)", proc.stdout)
            if match:
                dims = (int(match.group(1)), int(match.group(2)))
                _cached_screen_dims[cache_key] = dims
                return dims
    except Exception:
        pass
    return DEFAULT_WIDTH, DEFAULT_HEIGHT


def wake_and_unlock(device_id: str | None = None) -> bool:
    """Ensure the emulator screen is awake and the lockscreen is dismissed."""
    try:
        run_adb_command(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], device_id=device_id)
        run_adb_command(["shell", "wm", "dismiss-keyguard"], device_id=device_id)
        return True
    except Exception as e:
        logger.warning(f"Error waking emulator: {e}")
        return False


def is_item_view_focused(device_id: str | None = None) -> bool:
    """Check if ItemViewActivity is currently focused on screen.

    Inspects active focus lines (mCurrentFocus, mFocusedApp, topResumedActivity)
    rather than matching anywhere in the full dumpsys window history.
    """
    try:
        proc = run_adb_command(["shell", "dumpsys", "window"], device_id=device_id, timeout=1.5)
        if proc.returncode != 0 or not proc.stdout:
            return False
        focus_lines = [
            line for line in proc.stdout.splitlines()
            if any(k in line for k in ("mCurrentFocus", "mFocusedApp", "topResumedActivity"))
        ]
        if focus_lines:
            return any("ItemViewActivity" in line for line in focus_lines)
        return "ItemViewActivity" in proc.stdout
    except Exception:
        return False


def wait_for_item_view(
    device_id: str | None = None,
    max_wait: float = 2.0,
    poll_interval: float = 0.05,
) -> bool:
    """Wait until ItemViewActivity is focused on screen."""
    start = time.time()
    while time.time() - start < max_wait:
        if is_item_view_focused(device_id=device_id):
            return True
        time.sleep(poll_interval)
    return False


def open_item_in_app(
    item_id: str,
    device_id: str | None = None,
    load_delay: float = 1.0,
    wait_for_focus: bool = True,
    flags: str = "0x14000000",
) -> bool:
    """Launch the Too Good To Go app directly to an item's detail page.

    Uses the native internal URI scheme (toogoodtogoapp://item/{item_id}) targeting
    the app package 'com.app.tgtg'.
    """
    deep_link = f"toogoodtogoapp://item/{item_id}"
    try:
        proc = run_adb_command(
            [
                "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", deep_link,
                "-p", "com.app.tgtg",
                "-f", flags,
            ],
            device_id=device_id,
        )
        if proc.returncode != 0:
            logger.error(f"Failed to start intent: {proc.stderr}")
            return False

        if wait_for_focus:
            focused = wait_for_item_view(device_id=device_id, max_wait=max(load_delay + 2.0, 12.0))
            if not focused:
                logger.warning(f"ItemViewActivity did not gain focus after intent launch for item {item_id}")
                return False
            time.sleep(load_delay)
        else:
            time.sleep(load_delay)

        return True
    except Exception as e:
        logger.error(f"Exception launching item intent: {e}")
        return False


def get_tap_coordinates(action: str, width: int, height: int) -> tuple[int, int]:
    """Calculate button tap coordinates, allowing environment variable overrides."""
    rel_x, rel_y, env_x, env_y = BUTTON_COORDINATES.get(action, BUTTON_COORDINATES["reserve"])
    val_x, val_y = os.environ.get(env_x), os.environ.get(env_y)
    return (int(val_x), int(val_y)) if val_x and val_y else (int(width * rel_x), int(height * rel_y))


def input_security_code_and_confirm(
    security_code: str = DEFAULT_SECURITY_CODE,
    device_id: str | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    focus_delay: float = 0.20,
    submit_delay: float = 0.25,
    tap_cvv_field: bool = False,
) -> bool:
    """Enter card security code (CVV) on Adyen Payment card bottom sheet and tap Pay.

    Note: com.app.tgtg:id/editText_securityCode starts with focused="true" and the soft
    keyboard hidden when the Adyen bottom sheet opens. Typing directly via 'input text'
    populates the CVV without popping up the soft keyboard (keeping payButton stationary
    at (540, 2158)) and avoids sending KEYCODE_ESCAPE/BACK which would dismiss the sheet.
    """
    cvv_x, cvv_y = get_tap_coordinates("cvv", width, height)
    sec_x, sec_y = get_tap_coordinates("second_pay", width, height)

    if tap_cvv_field:
        logger.info(f"Focusing CVV input field at ({cvv_x}, {cvv_y})")
        tap_screen(cvv_x, cvv_y, device_id=device_id)
        if focus_delay > 0:
            time.sleep(focus_delay)

    logger.info("Typing security code into CVV field...")
    run_adb_command(["shell", "input", "text", str(security_code)], device_id=device_id)
    if submit_delay > 0:
        time.sleep(submit_delay)

    logger.info(f"Tapping second pay button at ({sec_x}, {sec_y})")
    tap_screen(sec_x, sec_y, device_id=device_id)
    return True


def tap_screen(x: int, y: int, device_id: str | None = None) -> bool:
    """Execute a single touch tap at coordinate (x, y)."""
    try:
        proc = run_adb_command(["shell", "input", "tap", str(x), str(y)], device_id=device_id)
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"Failed to tap ({x}, {y}): {e}")
        return False


def take_screenshot(output_path: str, device_id: str | None = None) -> bool:
    """Capture emulator screen to output_path for diagnostics."""
    cmd = ["adb"] + (["-s", device_id] if device_id else []) + ["exec-out", "screencap", "-p"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode == 0 and proc.stdout:
        with open(output_path, "wb") as f:
            f.write(proc.stdout)
        return True
    return False


def get_order_quantity(order: dict[str, Any] | None) -> int:
    """Extract purchased bag quantity from an order payload."""
    if not isinstance(order, dict):
        return 1
    raw = (
        order.get("quantity")
        or (order.get("order_items") or [{}])[0].get("quantity")
        or (order.get("order_lines") or [{}])[0].get("quantity")
        or order.get("item", {}).get("quantity")
    )
    try:
        return int(raw) if raw is not None else 1
    except (ValueError, TypeError):
        return 1


def verify_order_confirmed(
    client: TgtgClient,
    item_id: str,
    timeout: float = 4.0,
    poll_interval: float = 0.5,
    exclude_order_ids: set[str] | list[str] | None = None,
) -> dict[str, Any] | None:
    """Poll TGTG API client.get_active() to verify the order transitioned to ACTIVE."""
    start = time.time()
    excluded = set(exclude_order_ids or [])
    while time.time() - start < timeout:
        try:
            active = client.get_active()
            orders = active.get("orders", []) if isinstance(active, dict) else []
            for order in orders:
                order_id = str(order.get("order_id", ""))
                if order_id in excluded:
                    continue
                order_item_id = (
                    order.get("item_id")
                    or order.get("item", {}).get("item_id")
                    or (order.get("order_items") or [{}])[0].get("item_id")
                    or (order.get("order_lines") or [{}])[0].get("item", {}).get("item_id")
                )
                if str(order_item_id) == str(item_id):
                    return order
        except Exception as e:
            logger.debug(f"Verification poll check failed: {e}")

        time.sleep(poll_interval)
    return None


def check_item_sold_out(client: TgtgClient | None, item_id: str) -> bool:
    """Check if item is sold out according to TGTG API."""
    if not client:
        return False
    try:
        data = client.get_item(item_id)
        if isinstance(data, dict):
            if data.get("items_available", 0) <= 0:
                return True
            return any(isinstance(t, dict) and t.get("id") == "SOLD_OUT" for t in data.get("item_tags", []))
        return False
    except Exception as e:
        logger.debug(f"Error checking sold out state for {item_id}: {e}")
        return False


def disable_animations(device_id: str | None = None) -> bool:
    """Disable Android window, transition, and animator scales for instant modal/sheet rendering."""
    try:
        proc = run_adb_command(
            [
                "shell",
                "settings put global window_animation_scale 0.0;"
                " settings put global transition_animation_scale 0.0;"
                " settings put global animator_duration_scale 0.0",
            ],
            device_id=device_id,
        )
        return proc.returncode == 0
    except Exception as e:
        logger.debug(f"Could not disable emulator animations: {e}")
        return False


def prewarm_target(item_id: str, device_id: str | None = None) -> bool:
    """Pre-warm Android emulator before drop time: wake screen, cache dimensions,
    and disable transition animations for instant modal/sheet rendering.

    Does NOT open the item page before drop time because pre-loading a sold-out item
    leaves ItemViewActivity in a static 'Sold out' state (ComposeView has no pull-to-refresh).
    """
    online, dev = is_emulator_connected(device_id)
    if not online:
        return False
    device_id = dev

    wake_and_unlock(device_id)
    get_screen_dimensions(device_id)
    disable_animations(device_id)
    return True


def _abort_sold_out(item_id: str, device_id: str | None) -> tuple[bool, str, None]:
    """Dismiss screens and save debug screenshot when an item is confirmed sold out."""
    screenshot_path = "adb_checkout_debug.png"
    take_screenshot(screenshot_path, device_id=device_id)
    run_adb_command(["shell", "input", "keyevent", "KEYCODE_BACK"], device_id=device_id)
    logger.warning(f"Item {item_id} is sold out (taken by another user). Aborting checkout.")
    print(f"\n❌ Item {item_id} is SOLD OUT (secured by someone else). Aborting checkout immediately.")
    return False, "Item sold out before order was confirmed. Aborted checkout.", None


def _execute_checkout_taps(
    width: int,
    height: int,
    device_id: str | None,
    quantity: int = 1,
    modal_wait: float = 0.50,
    review_wait: float = 1.50,
    payment_select_wait: float = 0.40,
    reserve_burst: int = 1,
    burst_interval: float = 0.05,
    select_payment_method: bool = False,
) -> None:
    """Execute touch taps across checkout screens:
    1. Tap 'Reserve' pill button (bottom right of item page).
    2. Tap 'Got it!' disclaimer modal button.
    3. (Optional) Tap '+' stepper for requested quantity.
    4. (Optional) Tap payment method radio button if default card is not pre-selected.
    5. Tap collapsed 'Reserve' confirmation button to open Adyen CVV sheet.
    """
    res_x, res_y = get_tap_coordinates("reserve", width, height)
    got_it_x, got_it_y = get_tap_coordinates("got_it", width, height)
    plus_x, plus_y = get_tap_coordinates("plus", width, height)
    pm_x, pm_y = get_tap_coordinates("payment_method", width, height)
    pay_x, pay_y = get_tap_coordinates("confirm", width, height)

    burst_count = max(1, reserve_burst)
    logger.info(f"Tapping 'Reserve' at ({res_x}, {res_y}) (burst: {burst_count}x)")
    for i in range(burst_count):
        tap_screen(res_x, res_y, device_id=device_id)
        if i < burst_count - 1 and burst_interval > 0:
            time.sleep(burst_interval)
    time.sleep(modal_wait)

    logger.info(f"Tapping 'Got it!' at ({got_it_x}, {got_it_y})")
    tap_screen(got_it_x, got_it_y, device_id=device_id)
    time.sleep(review_wait)

    if select_payment_method:
        logger.info(f"Selecting payment method at ({pm_x}, {pm_y})")
        tap_screen(pm_x, pm_y, device_id=device_id)
        if payment_select_wait > 0:
            time.sleep(payment_select_wait)

    if quantity > 1:
        times_to_tap = quantity - 1
        logger.info(f"Increasing quantity to {quantity} (tapping '+' {times_to_tap} times)")
        for _ in range(times_to_tap):
            tap_screen(plus_x, plus_y, device_id=device_id)
            time.sleep(0.12)

    logger.info(f"Tapping 'Reserve / Pay' at ({pay_x}, {pay_y})")
    tap_screen(pay_x, pay_y, device_id=device_id)


def auto_checkout_item(
    item_id: str,
    client: TgtgClient | None = None,
    device_id: str | None = None,
    quantity: int = 1,
    item_load_wait: float = 1.80,
    modal_wait: float = 0.50,
    review_wait: float = 1.50,
    adyen_sheet_wait: float = 1.40,
    exclude_order_ids: set[str] | list[str] | None = None,
    reserve_burst: int = 1,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Execute complete automated native Android checkout flow for an item.

    Workflow:
    1. Verify emulator is connected and awake.
    2. Launch deep-link intent to load fresh live item state from the TGTG server.
    3. Verify target item view is focused.
    4. Wave 1: Tap 'Reserve' -> 'Got it!' -> Increment '+' if needed -> Select payment card -> 'Reserve'.
    5. Verify order via TGTG API.
    6. If pending, wait for Adyen CVV bottom sheet, enter security code, and tap 'Pay'.
    7. Abort immediately if the item is sold out to prevent stuck screens.

    Returns:
        (success: bool, status_message: str, order_details: dict | None)
    """
    # 1. Device check & wake
    online, dev = is_emulator_connected(device_id)
    if not online:
        return False, f"Android emulator not available: {dev}", None
    device_id = dev

    wake_and_unlock(device_id)
    width, height = get_screen_dimensions(device_id)
    logger.info(f"Target device '{device_id}' detected. Screen size: {width}x{height}")

    # 2. Always launch deep link fresh at drop time so ItemViewActivity fetches live stock
    logger.info(f"Launching item deep link: {item_id}")
    if not open_item_in_app(item_id, device_id=device_id, load_delay=item_load_wait):
        return False, "Failed to launch item deep link in TGTG app", None

    # Safety check: ensure ItemViewActivity is currently focused
    if not is_item_view_focused(device_id):
        screenshot_path = "adb_checkout_debug.png"
        take_screenshot(screenshot_path, device_id=device_id)
        return False, f"Target item view not focused on screen. Saved screenshot to {screenshot_path}", None

    # 3. Wave 1 Checkout Taps (Reserve -> Got it! -> (optional +) -> Select Payment -> Reserve)
    _execute_checkout_taps(
        width=width,
        height=height,
        device_id=device_id,
        quantity=quantity,
        modal_wait=modal_wait,
        review_wait=review_wait,
        reserve_burst=reserve_burst,
    )

    sec_code = os.environ.get("TGTG_SECURITY_CODE") or DEFAULT_SECURITY_CODE

    # Check if order was already confirmed immediately (while waiting for Adyen CVV sheet)
    order = None
    if client:
        order = verify_order_confirmed(
            client,
            item_id,
            timeout=0.5,
            exclude_order_ids=exclude_order_ids,
        )

    # 4. Security Code & Second Pay (if order not yet confirmed)
    if not order:
        if adyen_sheet_wait > 0:
            time.sleep(adyen_sheet_wait)
        print(f"💳 Entering security code ('{sec_code}') and tapping second pay button...")
        input_security_code_and_confirm(
            security_code=sec_code,
            device_id=device_id,
            width=width,
            height=height,
        )

    # 5. Verification & Fast Sold-Out Detection
    if client:
        if not order:
            logger.info("Polling TGTG API to verify order confirmation...")
            order = verify_order_confirmed(
                client,
                item_id,
                timeout=4.0,
                exclude_order_ids=exclude_order_ids,
            )

        # Retry loop if payment transition took an extra beat
        attempt = 1
        max_attempts = 3
        while not order and attempt <= max_attempts:
            if check_item_sold_out(client, item_id):
                order = verify_order_confirmed(client, item_id, timeout=0.6, exclude_order_ids=exclude_order_ids)
                if not order:
                    return _abort_sold_out(item_id, device_id)
                break

            if not is_item_view_focused(device_id):
                screenshot_path = "adb_checkout_debug.png"
                take_screenshot(screenshot_path, device_id=device_id)
                return False, f"Checkout screen lost focus during retry attempt {attempt}. Saved screenshot to {screenshot_path}", None

            print(f"🔄 Order verification pending... [Attempt {attempt}/{max_attempts}]: Re-tapping purchase sequence...")
            _execute_checkout_taps(
                width=width,
                height=height,
                device_id=device_id,
                quantity=quantity,
                modal_wait=0.50,
                review_wait=1.50,
                reserve_burst=1,
            )
            if adyen_sheet_wait > 0:
                time.sleep(adyen_sheet_wait)
            input_security_code_and_confirm(
                security_code=sec_code,
                device_id=device_id,
                width=width,
                height=height,
            )

            order = verify_order_confirmed(client, item_id, timeout=3.0, exclude_order_ids=exclude_order_ids)
            attempt += 1
            if not order and attempt <= max_attempts:
                time.sleep(0.30)

        if order:
            order_id = order.get("order_id", "Unknown")
            acquired_qty = get_order_quantity(order)
            logger.info(f"Order confirmed! Order ID: {order_id}, Quantity: {acquired_qty}")
            return True, f"Successfully purchased {acquired_qty} bag(s)! (Order ID: {order_id})", order
        else:
            screenshot_path = "adb_checkout_debug.png"
            take_screenshot(screenshot_path, device_id=device_id)
            run_adb_command(["shell", "input", "keyevent", "KEYCODE_BACK"], device_id=device_id)
            return False, f"Checkout taps sent ({max_attempts} attempts), but order did not appear in active orders. Saved screenshot to {screenshot_path}", None

    return True, "Checkout taps executed on emulator (unverified mode)", None
