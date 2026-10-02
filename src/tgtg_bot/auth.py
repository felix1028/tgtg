import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from tgtg import TgtgClient

load_dotenv()

TOKENS_FILE = Path("tokens.json")

MATCHING_USER_AGENT = "TGTG/26.9.11 Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Pro Build/UP1A.231005.007)"

def authenticate(tokens_path: Path | str = TOKENS_FILE, email: str | None = None):
    if not email:
        email = os.environ.get("TGTG_EMAIL")
    if not email:
        email = input("Enter your Too Good To Go email address: ").strip()
    
    if not email:
        print("Error: Email address is required.")
        sys.exit(1)
        
    print(f"\nInitiating login for {email}...")
    
    client = TgtgClient(email=email, user_agent=MATCHING_USER_AGENT)
    try:
        credentials = client.get_credentials()
    except Exception as e:
        err_msg = str(e)
        if "geo.captcha-delivery.com" in err_msg:
            # Extract captcha URL if present
            import re
            url_match = re.search(r'https://geo\.captcha-delivery\.com[^\s\'"}\\]+', err_msg)
            captcha_url = url_match.group(0).replace(r"\/", "/") if url_match else None
            print("\n⚠️ DataDome anti-bot challenge encountered.")
            if captcha_url:
                print(f"Please open this verification link in your browser to verify:\n{captcha_url}\n")
                print("After solving the challenge, rerun: uv run python -m tgtg_bot.auth")
            else:
                print(f"Details: {err_msg}")
            sys.exit(1)
        raise
    
    tokens_path = Path(tokens_path)
    with open(tokens_path, "w") as f:
        json.dump(credentials, f, indent=2)
        
    print(f"\nAuthentication successful! Saved tokens to {tokens_path}")
    return credentials

if __name__ == "__main__":
    authenticate()
