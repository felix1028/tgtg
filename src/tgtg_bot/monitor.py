import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from tgtg import TgtgClient

load_dotenv()

TOKENS_FILE = Path("tokens.json")
MATCHING_USER_AGENT = "TGTG/26.9.11 Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Pro Build/UP1A.231005.007)"

class PersistentTgtgClient(TgtgClient):
    """TgtgClient subclass that automatically persists refreshed tokens and cookies to disk."""

    def __init__(self, *args, tokens_path: Path | str = TOKENS_FILE, **kwargs):
        super().__init__(*args, **kwargs)
        self.tokens_path = Path(tokens_path)

    def save_tokens(self) -> None:
        """Save current credentials and cookies to tokens_path."""
        if not self.access_token and not self.refresh_token:
            return
        creds = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "cookie": self.cookie,
        }
        try:
            with open(self.tokens_path, "w") as f:
                json.dump(creds, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to persist refreshed tokens: {e}")

    def _refresh_token(self):
        """Override _refresh_token to auto-save freshly issued tokens."""
        super()._refresh_token()
        self.save_tokens()

def get_client(tokens_path: Path | str = TOKENS_FILE) -> PersistentTgtgClient:
    tokens_file = Path(tokens_path)
    if tokens_file.exists():
        with open(tokens_file, "r") as f:
            creds = json.load(f)
            return PersistentTgtgClient(
                access_token=creds.get("access_token"),
                refresh_token=creds.get("refresh_token"),
                cookie=creds.get("cookie"),
                user_agent=MATCHING_USER_AGENT,
                tokens_path=tokens_file,
            )
    elif os.environ.get("TGTG_ACCESS_TOKEN"):
        return PersistentTgtgClient(
            access_token=os.environ.get("TGTG_ACCESS_TOKEN"),
            refresh_token=os.environ.get("TGTG_REFRESH_TOKEN"),
            cookie=os.environ.get("TGTG_COOKIE"),
            user_agent=MATCHING_USER_AGENT,
            tokens_path=tokens_file,
        )
    else:
        print("Error: Not authenticated yet. Run: uv run python -m tgtg_bot.auth")
        sys.exit(1)

def format_item(item: dict) -> dict:
    display_name = item.get("display_name", "Unknown Item")
    store_name = item.get("store", {}).get("store_name", "Unknown Store")
    items_available = item.get("items_available", 0)
    price_info = (
        item.get("item", {}).get("item_price")
        or item.get("item", {}).get("price_including_taxes")
        or {}
    )
    decimals = price_info.get("decimals", 2)
    minor_units = price_info.get("minor_units", 0)
    code = price_info.get("code", "")
    price = f"{minor_units / (10 ** decimals):.2f} {code}".strip()
    status = f"✅ AVAILABLE: {items_available}" if items_available > 0 else "❌ SOLD OUT"
    return {
        "store_name": store_name,
        "display_name": display_name,
        "items_available": items_available,
        "price": price,
        "status": status,
        "is_available": items_available > 0,
    }

def check_favorites(client: TgtgClient):
    items = client.get_items()
    print(f"\nFound {len(items)} favorite item(s):")
    available_items = []
    
    for item in items:
        info = format_item(item)
        print(f"- [{info['status']}] {info['store_name']} - {info['display_name']} ({info['price']})")
        
        if info["is_available"]:
            available_items.append(item)
            
    return available_items

import argparse

def get_coordinates_from_favorites(client: TgtgClient) -> tuple[float, float] | None:
    """Derive search coordinates from the user's favorite stores."""
    try:
        items = client.get_items()
        for item in items:
            loc = item.get("pickup_location", {}).get("location")
            if loc and loc.get("latitude") and loc.get("longitude"):
                return (float(loc["latitude"]), float(loc["longitude"]))
    except Exception:
        pass
    return None

def check_nearby(client: TgtgClient, latitude: float, longitude: float, radius: int = 10):
    """Scan all nearby stores for bags in stock."""
    print(f"\nScanning stores within {radius}km of ({latitude:.4f}, {longitude:.4f}) with stock available...")
    items = client.get_items(
        latitude=latitude,
        longitude=longitude,
        radius=radius,
        favorites_only=False,
        with_stock_only=True,
    )
    print(f"\nFound {len(items)} nearby item(s) in stock:")
    available_items = []
    for item in items:
        info = format_item(item)
        print(f"- [{info['status']}] {info['store_name']} - {info['display_name']} ({info['price']})")
        if info["is_available"]:
            available_items.append(item)
    return available_items

def main(args=None):
    parser = argparse.ArgumentParser(description="Too Good To Go Monitor & WhatsApp Alerts")
    parser.add_argument(
        "--nearby",
        action="store_true",
        help="Scan all nearby stores with bags in stock (not just favorites)",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=10,
        help="Search radius in km for nearby scan (default: 10)",
    )
    parser.add_argument("--lat", type=float, default=None, help="Latitude for nearby scan")
    parser.add_argument("--lon", type=float, default=None, help="Longitude for nearby scan")
    
    parsed_args = parser.parse_args(args)
    client = get_client()

    if parsed_args.nearby:
        lat = parsed_args.lat or (float(os.environ["LATITUDE"]) if "LATITUDE" in os.environ else None)
        lon = parsed_args.lon or (float(os.environ["LONGITUDE"]) if "LONGITUDE" in os.environ else None)
        if lat is None or lon is None:
            coords = get_coordinates_from_favorites(client)
            if coords:
                lat, lon = coords
            else:
                print("Error: Could not determine location coordinates. Please provide --lat and --lon.")
                return
        available_items = check_nearby(client, latitude=lat, longitude=lon, radius=parsed_args.radius)
    else:
        print("Checking your favorite stores...")
        available_items = check_favorites(client)

    if available_items:
        from tgtg_bot.notifier import notify_available_items
        notify_available_items(available_items)
    else:
        print("\nNo bags currently available.")

if __name__ == "__main__":
    main()
