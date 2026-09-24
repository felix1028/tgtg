import json
import os
import sys
from pathlib import Path
from tgtg import TgtgClient

TOKENS_FILE = Path("tokens.json")

def authenticate():
    email = os.environ.get("TGTG_EMAIL")
    if not email:
        email = input("Enter your Too Good To Go email address: ").strip()
    
    if not email:
        print("Error: Email address is required.")
        sys.exit(1)
        
    print(f"\nInitiating login for {email}...")
    print("Please check your email and click the confirmation link sent by Too Good To Go.")
    print("Waiting for email confirmation...")
    
    client = TgtgClient(email=email)
    credentials = client.get_credentials()
    
    with open(TOKENS_FILE, "w") as f:
        json.dump(credentials, f, indent=2)
        
    print(f"\nAuthentication successful! Saved tokens to {TOKENS_FILE}")
    return credentials

if __name__ == "__main__":
    authenticate()
