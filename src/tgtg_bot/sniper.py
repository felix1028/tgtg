import argparse
import datetime
import os
import re
import sys
import time
from datetime import timezone
from pathlib import Path
from dotenv import load_dotenv
from tgtg import TgtgClient
from tgtg.exceptions import TgtgAPIError

from tgtg_bot.monitor import get_client, format_item
from tgtg_bot.notifier import send_whatsapp_message

load_dotenv()

def parse_drop_time(ts: str | None) -> datetime.datetime | None:
    """Parse ISO drop timestamp into local timezone datetime."""
    if not ts:
        return None
    try:
        # Normalize nanoseconds to microseconds if needed
        clean_ts = re.sub(r"(\.\d{6})\d+", r"\1", ts).replace("Z", "+00:00")
        dt_utc = datetime.datetime.fromisoformat(clean_ts)
        return dt_utc.astimezone()
    except Exception:
        return None

def fetch_favorites_schedule(client: TgtgClient) -> list[dict]:
    """Fetch all favorites and extract their upcoming drop times."""
    raw_items = client.get_items()
    scheduled_items = []
    
    for item in raw_items:
        info = format_item(item)
        item_id = item.get("item", {}).get("item_id")
        
        # Check if drop time is in item detail
        drop_time = None
        raw_drop_str = item.get("next_sales_window_purchase_start")
        if not raw_drop_str and item_id:
            try:
                detail = client.get_item(item_id)
                raw_drop_str = detail.get("next_sales_window_purchase_start")
            except Exception:
                pass
                
        if raw_drop_str:
            drop_time = parse_drop_time(raw_drop_str)
            
        now = datetime.datetime.now(timezone.utc).astimezone()
        is_future_drop = drop_time and drop_time > now
        
        scheduled_items.append({
            "item_id": item_id,
            "raw_item": item,
            "info": info,
            "drop_time": drop_time,
            "drop_time_str": drop_time.strftime("%I:%M:%S %p") if drop_time else "No drop time",
            "is_future_drop": is_future_drop,
            "items_available": info["items_available"],
        })
        
    return scheduled_items

def display_menu(favorites: list[dict]):
    """Print a clean numbered menu of favorites with drop schedules."""
    print("\n" + "=" * 65)
    print("🛍️  YOUR FAVORITES & UPCOMING DROP SCHEDULE")
    print("=" * 65)
    
    for idx, fav in enumerate(favorites, 1):
        info = fav["info"]
        if fav["items_available"] > 0:
            stock_badge = f"✅ IN STOCK ({fav['items_available']})"
            drop_badge = "Available right now!"
        elif fav["is_future_drop"]:
            stock_badge = "❌ Sold out"
            drop_badge = f"🕒 Drops at {fav['drop_time_str']}"
        else:
            stock_badge = "❌ Sold out"
            drop_badge = "⚠️ No drop scheduled today"
        
        print(f"[{idx:2d}] {info['store_name']}")
        print(f"     Bag: {info['display_name']} ({info['price']})")
        print(f"     Status: {stock_badge} | {drop_badge}\n")

def sort_targets_chronologically(targets: list[dict]) -> list[dict]:
    """Sort targets by their drop time (earliest drop first)."""
    now = datetime.datetime.now(timezone.utc).astimezone()

    def get_sort_key(target):
        dt = target.get("drop_time")
        if dt and dt > now:
            return (0, dt)
        return (1, dt or datetime.datetime.max.replace(tzinfo=timezone.utc))

    return sorted(targets, key=get_sort_key)

def prompt_user_ranking(favorites: list[dict], user_input: str | None = None) -> list[dict]:
    """Prompt the user to choose up to 3 favorites to hunt for today."""
    if not user_input:
        user_input = input("Enter up to 3 bags to target today (e.g. 2, 1, 3): ").strip()
        
    if not user_input:
        print("No choices entered. Exiting.")
        sys.exit(0)
        
    # Parse numbers separated by commas or spaces
    parts = re.split(r"[\s,]+", user_input.strip())
    selected = []
    seen = set()
    
    for part in parts:
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(favorites) and idx not in seen:
                selected.append(favorites[idx])
                seen.add(idx)
                if len(selected) == 3:
                    break
                    
    if not selected:
        print("Invalid selection. Exiting.")
        sys.exit(1)
        
    # Sort chronologically by drop time so the earliest drop is hunted first
    chronological_targets = sort_targets_chronologically(selected)
    
    print("\n🎯 Your Target Schedule for Today (Earliest Drop First):")
    for order, target in enumerate(chronological_targets, 1):
        info = target["info"]
        drop_str = target.get("drop_time_str", "No drop time")
        print(f"  #{order}: [{drop_str}] {info['store_name']} - {info['display_name']} ({info['price']})")
        
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

    print("\n💡 The bot will check each drop in order until ONE bag is secured, alert you on WhatsApp, and exit.")
    return chronological_targets

def secure_bag(client: TgtgClient, item_data: dict) -> tuple[bool, str]:
    """Attempt to reserve order, and send high-priority WhatsApp notification."""
    item_id = item_data["item_id"]
    info = item_data["info"]
    phone = os.environ.get("WHATSAPP_PHONE")
    apikey = os.environ.get("WHATSAPP_APIKEY")
    
    print(f"\n🚨 SECURING BAG: {info['store_name']} - {info['display_name']}...")
    
    reserved = False
    order_id = None
    try:
        order = client.create_order(item_id, 1)
        reserved = True
        order_id = order.get("id") if isinstance(order, dict) else None
        print(f"🎉 SUCCESS! Bag reserved in your TGTG account! (Order ID: {order_id})")
    except TgtgAPIError as e:
        print(f"ℹ️ Order auto-reservation note: {e}")
    except Exception as e:
        print(f"ℹ️ Reservation notice: {e}")
        
    # Send urgent WhatsApp alert
    if phone and apikey:
        if reserved:
            msg = (
                f"🎉 *BAG SECURED & RESERVED!*\n\n"
                f"• *{info['store_name']}*\n"
                f"  {info['display_name']}\n"
                f"  Price: {info['price']}\n\n"
                f"⚡ *Action Required:*\n"
                f"Open your Too Good To Go app within 5 minutes to confirm payment."
            )
        else:
            msg = (
                f"🚨 *BAG AVAILABLE NOW!*\n\n"
                f"• *{info['store_name']}*\n"
                f"  {info['display_name']}\n"
                f"  Price: {info['price']}\n\n"
                f"⚡ *Quick!* Open your Too Good To Go app right now to buy it before someone else does."
            )
        send_whatsapp_message(phone=phone, apikey=apikey, message=msg)
        
    return True, ("reserved" if reserved else "alerted")

def check_item_stock(client: TgtgClient, item_id: str) -> int:
    """Check live stock count for a specific item ID."""
    try:
        detail = client.get_item(item_id)
        return detail.get("items_available", 0)
    except Exception:
        return 0

def snipe_item(client: TgtgClient, target: dict, poll_interval: float = 2.0, max_check_seconds: int = 120) -> bool:
    """High-frequency snipe check around the scheduled drop window."""
    import random
    item_id = target["item_id"]
    info = target["info"]
    start_time = time.time()
    
    print(f"\n⚡ Entering rapid sniper mode for {info['store_name']} ({info['display_name']})...")
    print(f"Checking every ~{poll_interval}s for up to {max_check_seconds}s (with anti-bot jitter)...")
    
    attempt = 1
    while time.time() - start_time < max_check_seconds:
        stock = check_item_stock(client, item_id)
        if stock > 0:
            target["info"]["items_available"] = stock
            secure_bag(client, target)
            return True
            
        # Add slight natural jitter (e.g. 1.8s - 2.5s) to mimic human refreshing
        if poll_interval >= 1.0:
            jittered_sleep = max(0.5, poll_interval + random.uniform(-0.3, 0.4))
        else:
            jittered_sleep = poll_interval
        time.sleep(jittered_sleep)
        attempt += 1
        
    print(f"⏳ Drop window ended for {info['store_name']} ({max_check_seconds}s elapsed). No stock captured.")
    return False

def run_daily_sniper(client: TgtgClient, ranked_targets: list[dict], check_window_seconds: int = 120) -> bool:
    """Run the sniper through the user's top ranked targets until one is secured."""
    for rank, target in enumerate(ranked_targets, 1):
        info = target["info"]
        item_id = target["item_id"]
        drop_time = target["drop_time"]
        
        print(f"\n=======================================================")
        print(f"🎯 Evaluating Target #{rank}: {info['store_name']} - {info['display_name']}")
        print(f"=======================================================")
        
        # 1. Immediate check: Is it already in stock?
        print("Checking if stock is already available right now...")
        current_stock = check_item_stock(client, item_id)
        if current_stock > 0:
            target["info"]["items_available"] = current_stock
            secure_bag(client, target)
            print("\n✅ Secured! Closing app as requested.")
            return True
            
        # 2. Check scheduled drop time
        now = datetime.datetime.now(timezone.utc).astimezone()
        if not drop_time or drop_time <= now:
            print(f"⚠️ Target #{rank} has no upcoming drop scheduled today. Moving to next target.")
            continue
            
        seconds_until_drop = (drop_time - now).total_seconds()
        print(f"Target #{rank} drops at {target['drop_time_str']} (in {int(seconds_until_drop // 60)}m {int(seconds_until_drop % 60)}s).")
        
        # Sleep until 30 seconds before drop
        lead_time = 30
        if seconds_until_drop > lead_time:
            sleep_duration = seconds_until_drop - lead_time
            print(f"💤 Sleeping for {int(sleep_duration // 60)}m {int(sleep_duration % 60)}s until drop time...")
            
            # Sleep in intervals so user can Ctrl+C gracefully
            sleep_end = time.time() + sleep_duration
            while time.time() < sleep_end:
                time.sleep(min(5, sleep_end - time.time()))
                
        # 3. Enter high-frequency snipe mode
        secured = snipe_item(client, target, poll_interval=2.0, max_check_seconds=check_window_seconds)
        if secured:
            print("\n🎉 Secured! Target acquired. Closing app.")
            return True
            
        print(f"Moving to next target on your priority list...")
        
    print("\n🏁 Finished evaluating all daily targets. No bags secured today.")
    return False

def main(args=None):
    parser = argparse.ArgumentParser(description="TGTG Daily Sniper & Auto-Reserver")
    parser.add_argument(
        "--top",
        type=str,
        default=None,
        help="Comma-separated indices of your top choices (e.g. 2,1,3) to skip prompt",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=120,
        help="How many seconds to check around drop time (default: 120s)",
    )
    parsed_args = parser.parse_args(args)
    
    client = get_client()
    print("Loading your favorites and drop schedules...")
    favorites = fetch_favorites_schedule(client)
    
    if not favorites:
        print("No favorite stores found in your account.")
        return
        
    display_menu(favorites)
    ranked_targets = prompt_user_ranking(favorites, user_input=parsed_args.top)
    
    # Run sniper loop
    run_daily_sniper(client, ranked_targets, check_window_seconds=parsed_args.window)

if __name__ == "__main__":
    main()
