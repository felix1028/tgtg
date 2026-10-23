"""Too Good To Go Daily Sniper & Automated Bag Procurement.

Workflow:
1. Fetches drop schedules for your favorite stores.
2. Presents an interactive menu sorted chronologically by drop time.
3. Lets you pick up to 3 bags to hunt today (with per-bag or session bag quotas).
4. Sleeps with 0% CPU until 30 seconds before drop time.
5. In the final 30 seconds, executes rapid polling with anti-bot jitter.
6. When stock drops:
   - Auto-Buy Mode (--auto-buy): Executes instant in-app checkout on Android emulator via ADB.
   - Alert Mode (Default): Sends high-priority WhatsApp alerts with 1-tap universal purchase links.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import os
import random
import re
import subprocess
import sys
import time
from dotenv import load_dotenv
from tgtg import TgtgClient

from tgtg_bot.items import (
    format_item,
    format_pickup_interval,
    get_item_tag_ids,
    parse_iso_datetime as parse_drop_time,
)
from tgtg_bot.monitor import get_client
from tgtg_bot.notifier import send_whatsapp_message

load_dotenv()


def fetch_favorites_schedule(client: TgtgClient) -> list[dict]:
    """Fetch all favorite stores and extract upcoming drop schedules.
    
    Note on TGTG API behavior:
    1. get_items(page_size=100) retrieves all favorites in a single query.
    2. However, the list payload never includes 'next_sales_window_purchase_start'.
    3. Stores tagged 'NOTHING_TO_SAVE_TODAY' never have drop times. We skip those,
       and only fetch get_item(item_id) details for stores that might drop today.
    """
    raw_items = client.get_items(page_size=100)
    scheduled_items = []
    now = datetime.datetime.now(datetime.timezone.utc).astimezone()

    for item in raw_items:
        info = format_item(item)
        item_id = item.get("item", {}).get("item_id")
        tag_ids = get_item_tag_ids(item)

        raw_drop_str = (
            item.get("next_sales_window_purchase_start")
            or item.get("item", {}).get("next_sales_window_purchase_start")
        )

        # If list payload has no drop time and store is not marked empty today, query detail
        if not raw_drop_str and "NOTHING_TO_SAVE_TODAY" not in tag_ids and item_id:
            try:
                detail = client.get_item(item_id)
                if isinstance(detail, dict):
                    raw_drop_str = (
                        detail.get("next_sales_window_purchase_start")
                        or detail.get("item", {}).get("next_sales_window_purchase_start")
                    )
                    if "items_available" in detail:
                        info["items_available"] = detail["items_available"]
                    if not info.get("pickup_time"):
                        detail_interval = (
                            detail.get("pickup_interval")
                            or detail.get("item", {}).get("pickup_interval")
                        )
                        if detail_interval:
                            info["pickup_time"] = format_pickup_interval(detail_interval)
                            info["pickup_interval"] = detail_interval
            except Exception:
                pass

        drop_time = parse_drop_time(raw_drop_str)
        is_future_drop = bool(drop_time and drop_time > now)

        scheduled_items.append({
            "item_id": item_id,
            "raw_item": item,
            "info": info,
            "drop_time": drop_time,
            "drop_time_str": drop_time.strftime("%I:%M:%S %p") if drop_time else "No drop time",
            "is_future_drop": is_future_drop,
            "items_available": info["items_available"],
            "tag_ids": tag_ids,
        })

    return scheduled_items


def display_menu(favorites: list[dict]) -> None:
    """Print a clean numbered menu of favorites with drop schedules."""
    print("\n" + "=" * 65)
    print("🛍️  YOUR FAVORITES & UPCOMING DROP SCHEDULE")
    print("=" * 65)

    for idx, fav in enumerate(favorites, 1):
        info = fav["info"]
        tag_ids = fav.get("tag_ids") or get_item_tag_ids(fav.get("raw_item", {}))

        if fav["items_available"] > 0:
            stock_badge = f"✅ IN STOCK ({fav['items_available']})"
            drop_badge = "Available right now!"
        elif fav["is_future_drop"]:
            stock_badge = "❌ Sold out"
            drop_badge = f"🕒 Drops at {fav['drop_time_str']}"
        elif "NOTHING_TO_SAVE_TODAY" in tag_ids:
            stock_badge = "❌ Sold out"
            drop_badge = "💤 Nothing to save today"
        else:
            stock_badge = "❌ Sold out"
            drop_badge = "⏳ Check back later (no drop time yet)"

        rating_badge = f" | {info['rating']}" if info.get("rating") else ""
        print(f"[{idx:2d}] {info['store_name']}")
        print(f"     Bag: {info['display_name']} ({info['price']}){rating_badge}")
        if info.get("pickup_time"):
            print(f"     Pickup: 🧺 {info['pickup_time']}")
        print(f"     Status: {stock_badge} | {drop_badge}\n")


def sort_targets_chronologically(targets: list[dict]) -> list[dict]:
    """Sort targets by availability first (in-stock now), then earliest drop time."""
    now = datetime.datetime.now(datetime.timezone.utc).astimezone()

    def get_sort_key(target):
        if target.get("items_available", 0) > 0:
            return (-1, now)
        dt = target.get("drop_time")
        if dt and dt > now:
            return (0, dt)
        return (1, dt or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))

    return sorted(targets, key=get_sort_key)


def prompt_user_ranking(
    favorites: list[dict],
    user_input: str | None = None,
) -> list[dict]:
    """Prompt user to choose favorites to target today (1 bag per store, no count limit)."""
    if not user_input:
        user_input = input("Enter bags/stores to target today (e.g. 1, 2, 4 or 'all'): ").strip()

    if not user_input:
        print("No choices entered. Exiting.")
        sys.exit(0)

    selected = []
    seen = set()

    if user_input.strip().lower() == "all":
        for fav in favorites:
            selected.append(dict(fav))
    else:
        parts = re.split(r"[\s,]+", user_input.strip())
        for part in parts:
            match = re.match(r"^(\d+)", part)
            if match:
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(favorites) and idx not in seen:
                    selected.append(dict(favorites[idx]))
                    seen.add(idx)

    if not selected:
        print("Invalid selection. Exiting.")
        sys.exit(1)

    # Sort chronologically by drop time so the earliest drop is hunted first
    chronological_targets = sort_targets_chronologically(selected)

    print("\n🎯 Your Target Schedule for Today (Earliest Drop First):")
    for order, target in enumerate(chronological_targets, 1):
        info = target["info"]
        drop_str = target.get("drop_time_str", "No drop time")
        rating_str = f" | {info['rating']}" if info.get("rating") else ""
        pickup_str = f" | 🧺 {info['pickup_time']}" if info.get("pickup_time") else ""
        print(f"  #{order}: [{drop_str}] {info['store_name']} - {info['display_name']} ({info['price']}){rating_str}{pickup_str}")

    # Warn if any selected items have no drop scheduled
    unscheduled = [
        t for t in chronological_targets
        if not t.get("is_future_drop") and t.get("items_available", 0) == 0
    ]
    if unscheduled:
        print("\n⚠️  Note on your selection:")
        for t in unscheduled:
            print(f"  • '{t['info']['store_name']} - {t['info']['display_name']}' has NO scheduled drop today.")
            print("    (It will be checked once upon startup, but cannot be sniped later if sold out).")

    total_goal = len(chronological_targets)
    print(f"\n💡 Total session goal: {total_goal} bag(s) across selected stores (1 per store). The bot will hunt drops until targets are secured, alert on WhatsApp, and exit.")
    return chronological_targets


def _notify_desktop(title: str, message: str, sound: str = "Hero") -> None:
    """Trigger native macOS desktop banner and audio chime."""
    if sys.platform != "darwin":
        return
    try:
        sound_file = f"/System/Library/Sounds/{sound}.aiff"
        subprocess.run(["afplay", sound_file], check=False)
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}" sound name "{sound}"'
        ], check=False)
    except Exception:
        pass


def secure_bag(
    client: TgtgClient,
    item_data: dict,
    auto_buy: bool = False,
    device_id: str | None = None,
    quantity: int = 1,
    exclude_order_ids: set[str] | list[str] | None = None,
) -> tuple[bool, str]:
    """Trigger bag purchase via Android emulator ADB auto-buyer, or alert with direct link.

    - If auto_buy=True:
      Attempts Android app checkout via ADB using saved payment method.
      If successful, confirms order via TGTG API and alerts via WhatsApp.
      If ADB is unavailable or fails, returns (False, 'failed') so snipe_item can retry or fallback.
    - If auto_buy=False:
      Alerts user on WhatsApp with 1-tap universal purchase link and macOS chime.
    """
    item_id = item_data["item_id"]
    info = item_data["info"]
    phone = os.environ.get("WHATSAPP_PHONE")
    apikey = os.environ.get("WHATSAPP_APIKEY")

    store_name = info.get("store_name", "Store")
    display_name = info.get("display_name", "Surprise Bag")
    stock = info.get("items_available", 1)
    price = info.get("price", "")
    rating_display = f" | {info['rating']}" if info.get("rating") else ""
    pickup_display = f" | 🧺 Pickup: {info['pickup_time']}" if info.get("pickup_time") else ""
    rating_part = f"  Rating: {info['rating']}\n" if info.get("rating") else ""
    pickup_part = f"  Pickup: 🧺 {info['pickup_time']}\n" if info.get("pickup_time") else ""
    qty_str = f" (Quantity: {quantity})" if quantity > 1 else ""
    qty_label = f" ({quantity} bag{'s' if quantity > 1 else ''})" if quantity > 1 else ""
    print(f"\n🚨 BAG IN STOCK RIGHT NOW: {store_name} - {display_name}!")
    print(f"⚡ Stock: {stock}{qty_str} | Price: {price}{rating_display}{pickup_display}")

    # Mode A: Automated ADB Checkout
    if auto_buy:
        from tgtg_bot.adb_buyer import auto_checkout_item, is_emulator_connected, get_order_quantity
        print("🤖 Auto-buy mode active: Connecting to Android emulator via ADB...")
        online, dev = is_emulator_connected(device_id)
        if online:
            print(f"⚡ Executing instant in-app checkout on {dev}{qty_label}...")
            success, msg, order = auto_checkout_item(
                item_id=item_id,
                client=client,
                device_id=dev,
                quantity=quantity,
                exclude_order_ids=exclude_order_ids,
            )
            if success:
                print(f"\n🎉 {msg}")
                order_id = order.get("order_id", "Confirmed") if order else "Confirmed"
                acquired_qty = get_order_quantity(order) if order else quantity
                item_data["last_order"] = order
                item_data["last_acquired_quantity"] = acquired_qty

                _notify_desktop(
                    title="🎉 Bags Auto-Purchased!",
                    message=f"{store_name} ({acquired_qty} bag(s)) was PURCHASED! (Order: {order_id})",
                    sound="Hero",
                )

                if phone and apikey:
                    qty_line = f"  Quantity: {acquired_qty}\n" if acquired_qty > 1 else ""
                    price_line = f"  Price: {price} each\n" if acquired_qty > 1 else f"  Price: {price}\n"
                    heading = f"🎉 *{acquired_qty} BAGS PURCHASED & CONFIRMED!*" if acquired_qty > 1 else "🎉 *BAG PURCHASED & CONFIRMED!*"
                    confirm_msg = (
                        f"{heading}\n\n"
                        f"• *{store_name}*\n"
                        f"  {display_name}\n"
                        f"{qty_line}{price_line}{rating_part}{pickup_part}"
                        f"  Order ID: {order_id}\n\n"
                        f"✅ Completed native checkout in your TGTG app via Android emulator!"
                    )
                    send_whatsapp_message(phone=phone, apikey=apikey, message=confirm_msg)
                return True, "purchased"
            print(f"\n❌ Auto-checkout failed on emulator: {msg}")
            return False, "failed"
        print(f"\n❌ Emulator not connected ({dev}). Auto-buy failed.")
        return False, "failed"

    # Mode B: Manual 1-Tap Universal Alert (only when auto_buy=False)
    item_data["last_acquired_quantity"] = quantity
    _notify_desktop(
        title="🚨 TGTG Bag In Stock!",
        message=f"{store_name}{qty_label} ({price}) is AVAILABLE!",
        sound="Glass",
    )

    if phone and apikey:
        item_link = f"https://share.toogoodtogo.com/item/{item_id}"
        qty_msg = f"  Target Quantity: {quantity}\n" if quantity > 1 else ""
        msg = (
            f"🚨 *BAG AVAILABLE NOW!*\n\n"
            f"• *{store_name}*\n"
            f"  {display_name}\n"
            f"  Price: {price}\n"
            f"  Stock: {stock}\n"
            f"{qty_msg}{rating_part}{pickup_part}\n"
            f"⚡ *Quick! Tap to open & buy with Apple Pay:*\n"
            f"{item_link}"
        )
        send_whatsapp_message(phone=phone, apikey=apikey, message=msg)

    return True, "alerted"


def check_item_stock(client: TgtgClient, item_id: str) -> int | None:
    """Check live stock count for a specific item ID. Returns None on network/API failure."""
    try:
        detail = client.get_item(item_id)
        return detail.get("items_available", 0) if isinstance(detail, dict) else 0
    except Exception as exc:
        print(f"⚠️ Stock check failed for item {item_id}: {exc}")
        return None


def snipe_item(
    client: TgtgClient,
    target: dict,
    poll_interval: float = 0.6,
    max_check_seconds: int = 120,
    auto_buy: bool = True,
    device_id: str | None = None,
    quantity: int = 1,
    exclude_order_ids: set[str] | list[str] | None = None,
    max_buy_attempts: int = 2,
    burst_poll_interval: float = 0.28,
    watch_redrop: bool = True,
    heartbeat_poll_interval: float = 7.0,
    redrop_timeout_seconds: float = 300.0,
    redrop_burst_seconds: float = 60.0,
) -> bool:
    """3-Phase drop & cart-abandonment sniper loop:
    Phase 1 (Primary Drop & 30-90s Queue Buffer):
      - Rapid polling around scheduled drop and through the 30s-90s late release window.
      - Tracks the timestamp when the item is first observed as sold out/claimed.
    Phase 2 (Cart-Lock Intermission, ~minutes 2 to 4.5):
      - Conserves API budget and resets DataDome rate limits with a relaxed heartbeat (~7s).
    Phase 3 (5-Minute Re-Drop Surge, ~minutes 4.5 to 5.5):
      - Ultra-burst polling (0.28s) right around the 5-minute cart expiration mark to snatch
        re-dropped inventory if checkout was abandoned or payment failed.
    """
    item_id = target["item_id"]
    info = target["info"]
    drop_time = target.get("drop_time")
    start_time = time.time()
    consecutive_errors = 0
    buy_attempts = 0

    first_sold_out_time: float | None = None
    redrop_target_time: float | None = None
    phase = "primary"
    announced_phases = set()

    print(f"\n⚡ Entering rapid sniper mode for {info['store_name']} ({info['display_name']})...")
    print(f"Phase 1: Active drop polling (~{poll_interval}s, burst ~{burst_poll_interval}s) for up to {max_check_seconds}s...")

    while True:
        now = time.time()
        now_dt = datetime.datetime.now(datetime.timezone.utc).astimezone()

        # Phase transitions
        if phase == "primary":
            if now - start_time >= max_check_seconds:
                if watch_redrop and first_sold_out_time is not None:
                    phase = "cart_lock_rest"
                else:
                    break
        elif phase == "cart_lock_rest":
            time_until_redrop = redrop_target_time - now
            if time_until_redrop <= 30.0:
                phase = "redrop_surge"
        elif phase == "redrop_surge":
            if now > redrop_target_time + redrop_burst_seconds:
                print(f"⏳ 5-minute re-drop window elapsed for {info['store_name']}. Bag was finalized by original buyer.")
                return False

        # Phase transition announcements
        if phase not in announced_phases:
            announced_phases.add(phase)
            if phase == "cart_lock_rest":
                time_until = int(max(0, redrop_target_time - now))
                print(f"\n⏳ Primary drop window ended for {info['store_name']}. Bag was claimed/sold out.")
                print(f"💤 Entering cart-lock resting phase (heartbeat ~{heartbeat_poll_interval}s) to conserve API budget.")
                print(f"🎯 Will surge at 5-minute checkout timeout mark in {time_until}s...")
            elif phase == "redrop_surge":
                print(f"\n⚡ Approaching 5-minute checkout timeout mark (re-drop window active)!")
                print(f"🚀 Entering ultra-burst sniper mode for {info['store_name']}...")
                if auto_buy:
                    try:
                        from tgtg_bot.adb_buyer import prewarm_target
                        prewarm_target(item_id, device_id=device_id)
                    except Exception:
                        pass

        # Perform stock check
        stock = check_item_stock(client, item_id)
        if stock is None:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                print(f"🚨 5 consecutive API errors for {info['store_name']}. Aborting sniper loop.")
                return False
            time.sleep(1.0)
            continue
        consecutive_errors = 0

        if stock > 0:
            target["info"]["items_available"] = stock
            if phase == "redrop_surge":
                print(f"\n🎉 5-MINUTE RE-DROP DETECTED! Bag released back to inventory!")
            elif phase == "cart_lock_rest":
                print(f"\n⚡ Early release detected during cart-lock rest! Securing bag immediately...")

            success, _ = secure_bag(
                client,
                target,
                auto_buy=auto_buy,
                device_id=device_id,
                quantity=1,
                exclude_order_ids=exclude_order_ids,
            )
            if success:
                return True

            buy_attempts += 1
            if buy_attempts >= max_buy_attempts:
                if auto_buy:
                    print("⚠️ Auto-buy failed while stock is available. Sending manual fallback alert...")
                    secure_bag(client, target, auto_buy=False, quantity=1)
                return False

        # Record first sold-out timestamp when stock is 0 at or after drop time
        if stock == 0 and first_sold_out_time is None:
            is_at_or_after_drop = (drop_time is None) or (now_dt >= drop_time)
            if is_at_or_after_drop:
                first_sold_out_time = now
                redrop_target_time = first_sold_out_time + redrop_timeout_seconds

        # Compute sleep interval based on current phase
        if phase == "primary":
            in_burst_window = False
            if isinstance(drop_time, datetime.datetime):
                delta = (now_dt - drop_time).total_seconds()
                in_burst_window = -3.0 <= delta <= 15.0

            if in_burst_window:
                effective_interval = min(poll_interval, burst_poll_interval)
                jitter = random.uniform(-0.04, 0.06)
                floor = min(0.15, poll_interval)
            else:
                effective_interval = poll_interval
                jitter = random.uniform(-0.15, 0.20)
                floor = min(0.25, poll_interval)

            time.sleep(max(floor, effective_interval + jitter))

        elif phase == "cart_lock_rest":
            effective_interval = heartbeat_poll_interval
            jitter = random.uniform(-0.8, 0.8)
            time.sleep(max(2.0, effective_interval + jitter))

        elif phase == "redrop_surge":
            effective_interval = burst_poll_interval
            jitter = random.uniform(-0.04, 0.06)
            time.sleep(max(0.15, effective_interval + jitter))

    print(f"⏳ Drop window ended for {info['store_name']} ({max_check_seconds}s elapsed). No stock captured.")
    return False


def _get_initial_active_order_ids(client: TgtgClient) -> set[str]:
    """Fetch existing active order IDs at startup so pre-existing orders are not mistaken for new purchases."""
    try:
        active = client.get_active()
        orders = active.get("orders", []) if isinstance(active, dict) else []
        return {str(o["order_id"]) for o in orders if isinstance(o, dict) and o.get("order_id")}
    except Exception:
        return set()


def _record_secured_target(
    target: dict,
    total_secured: int,
    total_bags_target: int,
    exclude_order_ids: set[str],
) -> tuple[int, bool]:
    """Record purchased order ID, increment total secured count, and check goal."""
    order = target.get("last_order")
    if order and "order_id" in order:
        exclude_order_ids.add(str(order["order_id"]))
    new_total = total_secured + 1
    store = target.get("info", {}).get("store_name", "Target")
    print(f"\n🎉 Secured bag from {store}! Total procured so far: {new_total}/{total_bags_target}")

    if new_total >= total_bags_target:
        print("\n🎉 Target bag goal achieved! All requested bags procured.")
        return new_total, True
    else:
        print(f"Continuing to next target to procure remaining {total_bags_target - new_total} bag(s)...")
        return new_total, False


_active_caffeinate_proc: subprocess.Popen | None = None


@contextlib.contextmanager
def prevent_system_sleep():
    """Prevent macOS from entering display/idle/AC system sleep while the sniper is running.

    Automatically releases the sleep assertion (`caffeinate -dims`) as soon as the sniper
    completes, is interrupted (Ctrl+C), or if the Python process exits.
    """
    global _active_caffeinate_proc
    if _active_caffeinate_proc is not None and _active_caffeinate_proc.poll() is None:
        yield _active_caffeinate_proc
        return

    proc = None
    if sys.platform == "darwin":
        try:
            proc = subprocess.Popen(
                ["caffeinate", "-dims", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _active_caffeinate_proc = proc
            print("☕ macOS sleep prevention active (caffeinate -dims) — will release automatically when done.")
        except Exception:
            proc = None
    try:
        yield proc
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
            if _active_caffeinate_proc is proc:
                _active_caffeinate_proc = None
            print("🌙 Released macOS sleep prevention (caffeinate disabled).")


def run_daily_sniper(
    client: TgtgClient,
    ranked_targets: list[dict],
    check_window_seconds: int = 120,
    auto_buy: bool = True,
    device_id: str | None = None,
    total_bags_target: int | None = None,
    watch_redrop: bool = True,
) -> bool:
    """Run sniper across chronological targets until total_bags_target is met."""
    with prevent_system_sleep():
        return _run_daily_sniper_loop(
            client=client,
            ranked_targets=ranked_targets,
            check_window_seconds=check_window_seconds,
            auto_buy=auto_buy,
            device_id=device_id,
            total_bags_target=total_bags_target,
            watch_redrop=watch_redrop,
        )


def _run_daily_sniper_loop(
    client: TgtgClient,
    ranked_targets: list[dict],
    check_window_seconds: int = 120,
    auto_buy: bool = True,
    device_id: str | None = None,
    total_bags_target: int | None = None,
    watch_redrop: bool = True,
) -> bool:
    """Internal sniper loop executed under the system sleep prevention lock."""
    if total_bags_target is None:
        total_bags_target = len(ranked_targets)

    total_secured = 0
    exclude_order_ids: set[str] = _get_initial_active_order_ids(client)

    print(f"\n🎯 Hunting goal: Procure {total_bags_target} total bag(s) (1 per location/store)")
    if watch_redrop:
        print("Cart re-drop monitoring: ACTIVE (watching for 5-minute checkout timeouts after sold-out drops)")

    for rank, target in enumerate(ranked_targets, 1):
        if total_secured >= total_bags_target:
            break

        remaining_needed = total_bags_target - total_secured
        desired_qty = 1
        target["target_quantity"] = 1

        info = target["info"]
        item_id = target["item_id"]
        drop_time = target["drop_time"]

        print(f"\n{'=' * 55}")
        print(f"🎯 Evaluating Target #{rank}: {info['store_name']} - {info['display_name']}")
        print(f"   Quota remaining: {remaining_needed} bag(s)")
        print(f"{'=' * 55}")

        # 1. Immediate Check: Already in stock?
        print("Checking if stock is already available right now...")
        current_stock = check_item_stock(client, item_id)
        if current_stock is not None and current_stock > 0:
            target["info"]["items_available"] = current_stock
            success, _ = secure_bag(
                client, target, auto_buy=auto_buy, device_id=device_id,
                quantity=1, exclude_order_ids=exclude_order_ids,
            )
            if success:
                total_secured, goal_met = _record_secured_target(
                    target, total_secured, total_bags_target, exclude_order_ids
                )
                if goal_met:
                    return True
                continue

        # 2. Check scheduled drop time (allow drops that started within check_window_seconds)
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        if not drop_time:
            print(f"⚠️ Target #{rank} has no upcoming drop scheduled today. Moving to next target.")
            continue

        seconds_until_drop = (drop_time - now).total_seconds()
        if seconds_until_drop <= -check_window_seconds:
            print(f"⚠️ Target #{rank} drop window ({target['drop_time_str']}) has already passed. Moving to next target.")
            continue

        if seconds_until_drop > 0:
            print(f"Target #{rank} drops at {target['drop_time_str']} (in {int(seconds_until_drop // 60)}m {int(seconds_until_drop % 60)}s).")
        else:
            print(f"⚡ Target #{rank} drop window started {int(-seconds_until_drop)}s ago ({target['drop_time_str']}) — entering sniper immediately!")

        # Sleep with 0% CPU until 15 seconds before drop
        lead_time = 15
        if seconds_until_drop > lead_time:
            sleep_duration = seconds_until_drop - lead_time
            print(f"💤 Sleeping for {int(sleep_duration // 60)}m {int(sleep_duration % 60)}s until drop time...")
            sleep_end = time.time() + sleep_duration
            while time.time() < sleep_end:
                time.sleep(min(5, sleep_end - time.time()))

        # Pre-warm Android emulator before drop if auto-buy is enabled
        if auto_buy:
            from tgtg_bot.adb_buyer import prewarm_target
            print(f"🤖 Pre-warming Android emulator for {info['store_name']} drop...")
            prewarm_target(item_id, device_id=device_id)

        # 3. Enter high-frequency snipe mode
        secured = snipe_item(
            client, target, poll_interval=0.6, max_check_seconds=check_window_seconds,
            auto_buy=auto_buy, device_id=device_id, quantity=1, exclude_order_ids=exclude_order_ids,
            watch_redrop=watch_redrop,
        )
        if secured:
            total_secured, goal_met = _record_secured_target(
                target, total_secured, total_bags_target, exclude_order_ids
            )
            if goal_met:
                return True
            continue

        print("Moving to next target on your priority list...")

    if total_secured > 0:
        print(f"\n🏁 Finished evaluating targets. Secured {total_secured}/{total_bags_target} requested bag(s).")
        return True
    print("\n🏁 Finished evaluating all daily targets. No bags secured today.")
    return False


def main(args=None):
    """CLI entrypoint for TGTG Daily Sniper."""
    parser = argparse.ArgumentParser(description="TGTG Daily Sniper & Auto-Reserver")
    parser.add_argument(
        "--top", type=str, default=None,
        help="Comma-separated indices of your top choices (e.g. 1, 2, 3) to skip prompt",
    )
    parser.add_argument(
        "--window", type=int, default=120,
        help="How many seconds to check around drop time (default: 120s)",
    )
    parser.add_argument(
        "--auto-buy", action=argparse.BooleanOptionalAction, default=True,
        help="Automatically purchase bag via Android emulator ADB (default: True, use --no-auto-buy for alert mode)",
    )
    parser.add_argument(
        "--watch-redrop", action=argparse.BooleanOptionalAction, default=True,
        help="Monitor 5-minute cart abandonment timeout re-drop after item sells out (default: True)",
    )
    parser.add_argument(
        "--device", type=str, default=None,
        help="Specific ADB device or emulator identifier (default: first online device)",
    )
    parsed_args = parser.parse_args(args)

    with prevent_system_sleep():
        client = get_client()
        print("Loading your favorites and drop schedules...")
        favorites = fetch_favorites_schedule(client)

        if not favorites:
            print("No favorite stores found in your account.")
            return

        display_menu(favorites)
        ranked_targets = prompt_user_ranking(favorites, user_input=parsed_args.top)
        run_daily_sniper(
            client, ranked_targets, check_window_seconds=parsed_args.window,
            auto_buy=parsed_args.auto_buy, device_id=parsed_args.device,
            watch_redrop=parsed_args.watch_redrop,
        )


if __name__ == "__main__":
    main()
