import os
import urllib.parse
import requests
from tgtg_bot.monitor import format_item

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

def format_notification_message(items: list[dict], max_items: int = 10) -> str:
    """Format a list of available TGTG items into a clear WhatsApp message."""
    if not items:
        return ""
    
    count = len(items)
    header = f"🛍️ *Too Good To Go Alert!*\nFound {count} available bag{'s' if count > 1 else ''}:\n"
    
    lines = []
    for item in items[:max_items]:
        info = format_item(item)
        lines.append(
            f"• *{info['store_name']}*\n"
            f"  {info['display_name']}\n"
            f"  Stock: {info['items_available']} | Price: {info['price']}"
        )
        
    if count > max_items:
        lines.append(f"\n_...and {count - max_items} more items nearby._")
        
    return header + "\n".join(lines)

def send_whatsapp_message(phone: str, apikey: str, message: str, timeout: int = 10) -> bool:
    """Send a WhatsApp message via CallMeBot API."""
    if not phone or not apikey or not message:
        return False
    
    # Strip spaces and leading '+' if present for standard format
    clean_phone = phone.strip().replace(" ", "").replace("-", "")
    
    params = {
        "phone": clean_phone,
        "text": message,
        "apikey": apikey.strip(),
    }
    
    try:
        response = requests.get(CALLMEBOT_URL, params=params, timeout=timeout)
        if response.status_code == 200:
            print(f"✅ WhatsApp notification sent to {clean_phone}")
            return True
        else:
            print(f"❌ Failed to send WhatsApp notification (HTTP {response.status_code}): {response.text}")
            return False
    except requests.RequestException as e:
        print(f"❌ Error sending WhatsApp notification: {e}")
        return False

def notify_available_items(
    items: list[dict],
    phone: str | None = None,
    apikey: str | None = None,
) -> bool:
    """Check configuration and send WhatsApp notification for available items."""
    if not items:
        return False
        
    phone = phone or os.environ.get("WHATSAPP_PHONE")
    apikey = apikey or os.environ.get("WHATSAPP_APIKEY") or os.environ.get("CALLMEBOT_API_KEY")
    
    if not phone or not apikey:
        print("ℹ️ WhatsApp notifications skipped: WHATSAPP_PHONE or WHATSAPP_APIKEY not configured.")
        return False
        
    message = format_notification_message(items)
    return send_whatsapp_message(phone=phone, apikey=apikey, message=message)
