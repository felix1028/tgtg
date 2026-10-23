"""Calibration and Diagnostic Tool for Android ADB Auto-Buyer.

Usage:
    # 1. Test emulator connection and screen resolution:
    uv run python scripts/calibrate_adb.py

    # 2. Test opening an item deep link:
    uv run python scripts/calibrate_adb.py --item-id 1572973

    # 3. Take a screenshot from the emulator:
    uv run python scripts/calibrate_adb.py --screenshot test_screen.png

    # 4. Dry-run test taps (simulates clicking Reserve and Pay):
    uv run python scripts/calibrate_adb.py --test-taps
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tgtg_bot.adb_buyer import (
    get_screen_dimensions,
    get_tap_coordinates,
    is_emulator_connected,
    open_item_in_app,
    take_screenshot,
    tap_screen,
    wake_and_unlock,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate and test ADB emulator connection for TGTG auto-buyer")
    parser.add_argument("--device", help="Specific ADB device serial/identifier", default=None)
    parser.add_argument("--item-id", help="Test opening an item deep link in the TGTG app", default=None)
    parser.add_argument("--screenshot", help="Capture screenshot to specified file path", default=None)
    parser.add_argument("--test-taps", action="store_true", help="Perform test taps for Reserve and Pay buttons")
    args = parser.parse_args()

    print("=======================================================")
    print("🤖 Too Good To Go - ADB Emulator Calibration & Test Tool")
    print("=======================================================")

    # 1. Check connection
    print("\n[1/4] Checking ADB emulator connection...")
    online, device_info = is_emulator_connected(args.device)
    if not online:
        print(f"❌ Error: {device_info}")
        print("\n💡 Troubleshooting tips:")
        print("1. Ensure your Android emulator (Android Studio AVD, Genymotion, or BlueStacks) is running.")
        print("2. Run 'adb devices' in your terminal to see if a device is listed.")
        print("3. If using a remote emulator, run 'adb connect localhost:5555'.")
        return 1

    device_id = device_info
    print(f"✅ Emulator connected: {device_id}")

    # 2. Wake device & get dimensions
    print("\n[2/4] Waking device & checking display properties...")
    wake_and_unlock(device_id)
    width, height = get_screen_dimensions(device_id)
    print(f"✅ Screen resolution: {width} x {height}")

    res_x, res_y = get_tap_coordinates("reserve", width, height)
    got_it_x, got_it_y = get_tap_coordinates("got_it", width, height)
    plus_x, plus_y = get_tap_coordinates("plus", width, height)
    pm_x, pm_y = get_tap_coordinates("payment_method", width, height)
    pay_x, pay_y = get_tap_coordinates("confirm", width, height)
    pay_exp_x, pay_exp_y = get_tap_coordinates("confirm_expanded", width, height)
    cvv_x, cvv_y = get_tap_coordinates("cvv", width, height)
    second_pay_x, second_pay_y = get_tap_coordinates("second_pay", width, height)
    print(f"📍 Calculated 'Reserve' button coordinates:         ({res_x}, {res_y})")
    print(f"📍 Calculated 'Got it!' modal coordinates:         ({got_it_x}, {got_it_y})")
    print(f"📍 Calculated 'Plus (+)' stepper coordinates:      ({plus_x}, {plus_y})")
    print(f"📍 Calculated 'Payment Method (radio)' coordinates:({pm_x}, {pm_y})")
    print(f"📍 Calculated 'Confirm & Pay (collapsed)':         ({pay_x}, {pay_y})")
    print(f"📍 Calculated 'Confirm & Pay (expanded fallback)': ({pay_exp_x}, {pay_exp_y})")
    print(f"📍 Calculated 'Security Code (CVV)' coordinates:  ({cvv_x}, {cvv_y})")
    print(f"📍 Calculated 'Second Pay' button coordinates:    ({second_pay_x}, {second_pay_y})")

    # 3. Optional: Open item
    if args.item_id:
        print(f"\n[3/4] Launching item #{args.item_id} in Too Good To Go app...")
        if open_item_in_app(args.item_id, device_id=device_id):
            print("✅ Item page opened successfully in app!")
        else:
            print("❌ Failed to open item page.")

    # 4. Optional: Test taps
    if args.test_taps:
        print("\n[4/4] Executing test taps...")
        print(f"👉 Tapping 'Reserve' at ({res_x}, {res_y})...")
        tap_screen(res_x, res_y, device_id=device_id)
        import time
        time.sleep(0.5)
        print(f"👉 Tapping 'Got it!' at ({got_it_x}, {got_it_y})...")
        tap_screen(got_it_x, got_it_y, device_id=device_id)
        time.sleep(1.1)
        print(f"👉 Tapping 'Plus (+)' stepper at ({plus_x}, {plus_y})...")
        tap_screen(plus_x, plus_y, device_id=device_id)
        time.sleep(0.3)
        print(f"👉 Tapping 'Payment Method' at ({pm_x}, {pm_y})...")
        tap_screen(pm_x, pm_y, device_id=device_id)
        time.sleep(0.3)
        print(f"👉 Tapping 'Confirm & Pay' at ({pay_x}, {pay_y})...")
        tap_screen(pay_x, pay_y, device_id=device_id)
        time.sleep(0.5)
        from tgtg_bot.adb_buyer import input_security_code_and_confirm
        print(f"👉 Entering security code ('876') and tapping second pay button...")
        input_security_code_and_confirm("876", device_id=device_id, width=width, height=height)
        print("✅ Test taps completed!")

    # 5. Optional: Screenshot
    if args.screenshot:
        output_file = Path(args.screenshot)
        print(f"\nCapturing screenshot to {output_file}...")
        if take_screenshot(str(output_file), device_id=device_id):
            print(f"✅ Screenshot saved: {output_file.resolve()}")
        else:
            print("❌ Failed to capture screenshot.")

    print("\n=======================================================")
    print("🎉 All checks finished!")
    print("=======================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
