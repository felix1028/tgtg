import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from tgtg import TgtgClient

load_dotenv()

TOKENS_FILE = Path("tokens.json")

def get_client() -> TgtgClient:
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE, "r") as f:
            creds = json.load(f)
            return TgtgClient(
                access_token=creds.get("access_token"),
                refresh_token=creds.get("refresh_token"),
                user_id=creds.get("user_id"),
                cookie=creds.get("cookie"),
            )
    elif os.environ.get("TGTG_ACCESS_TOKEN"):
        return TgtgClient(
            access_token=os.environ.get("TGTG_ACCESS_TOKEN"),
            refresh_token=os.environ.get("TGTG_REFRESH_TOKEN"),
            user_id=os.environ.get("TGTG_USER_ID"),
            cookie=os.environ.get("TGTG_COOKIE"),
        )
    else:
        print("Error: Not authenticated yet. Run: uv run python -m tgtg_bot.auth")
        sys.exit(1)

def check_favorites(client: TgtgClient):
    items = client.get_items()
    print(f"\nFound {len(items)} favorite item(s):")
    available_items = []
    
    for item in items:
        display_name = item.get("display_name", "Unknown Item")
        store_name = item.get("store", {}).get("store_name", "Unknown Store")
        items_available = item.get("items_available", 0)
        price_info = item.get("item", {}).get("price_including_taxes", {})
        price = f"{price_info.get('minor_units', 0) / (10 ** price_info.get('decimals', 2)):.2f} {price_info.get('code', '')}"
        
        status = f"✅ AVAILABLE: {items_available}" if items_available > 0 else "❌ SOLD OUT"
        print(f"- [{status}] {store_name} - {display_name} ({price})")
        
        if items_available > 0:
            available_items.append(item)
            
    return available_items

def main():
    client = get_client()
    print("Checking your favorite stores...")
    check_favorites(client)

if __name__ == "__main__":
    main()
