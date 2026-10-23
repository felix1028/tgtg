"""Too Good To Go Store Monitor & Alert Module.

Workflow:
1. Loads persistent session tokens from tokens.json (auto-refreshing).
2. Queries the user's saved favorite stores (default) or scans nearby area (--nearby).
3. Formats item details, pricing (minor units -> currency), and availability.
4. If bags are in stock and WhatsApp credentials exist in .env, sends instant alerts.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from tgtg import TgtgClient

from tgtg_bot.auth import MATCHING_USER_AGENT
from tgtg_bot.items import format_item
import tgtg_bot.notifier as notifier

load_dotenv()

TOKENS_FILE = Path("tokens.json")


class PersistentTgtgClient(TgtgClient):
    """TgtgClient subclass that automatically persists refreshed tokens to disk.

    When Too Good To Go rotates the access token during normal API usage,
    this class intercepts _refresh_token() and writes the updated tokens
    back to tokens.json so subsequent bot runs stay authenticated.
    """

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
    """Create a PersistentTgtgClient using tokens.json or environment variables."""
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


def _print_and_filter_available(items: list[dict]) -> list[dict]:
    """Print formatted item status lines and return only items with stock available."""
    available_items = []
    for item in items:
        info = format_item(item)
        rating_str = f" | {info['rating']}" if info.get("rating") else ""
        pickup_str = f" | 🧺 {info['pickup_time']}" if info.get("pickup_time") else ""
        print(f"- [{info['status']}] {info['store_name']} - {info['display_name']} ({info['price']}){rating_str}{pickup_str}")
        if info["is_available"]:
            available_items.append(item)
    return available_items


def check_favorites(client: TgtgClient) -> list[dict]:
    """Scan all favorite stores saved in the user's account and report stock."""
    items = client.get_items(page_size=100)
    print(f"\nFound {len(items)} favorite item(s):")
    return _print_and_filter_available(items)


def get_coordinates_from_favorites(client: TgtgClient) -> tuple[float, float] | None:
    """Derive search coordinates from the user's first favorite store with GPS."""
    try:
        items = client.get_items(page_size=100)
        for item in items:
            loc = item.get("pickup_location", {}).get("location")
            if loc and loc.get("latitude") and loc.get("longitude"):
                return (float(loc["latitude"]), float(loc["longitude"]))
    except Exception:
        pass
    return None


def check_nearby(client: TgtgClient, latitude: float, longitude: float, radius: int = 10) -> list[dict]:
    """Scan all nearby stores within a radius for bags currently in stock."""
    print(f"\nScanning stores within {radius}km of ({latitude:.4f}, {longitude:.4f}) with stock available...")
    items = client.get_items(
        latitude=latitude,
        longitude=longitude,
        radius=radius,
        favorites_only=False,
        with_stock_only=True,
        page_size=100,
    )
    print(f"\nFound {len(items)} nearby item(s) in stock:")
    return _print_and_filter_available(items)


def main(args=None):
    """CLI entrypoint for TGTG monitor."""
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
        notifier.notify_available_items(available_items)
    else:
        print("\nNo bags currently available.")


if __name__ == "__main__":
    main()
