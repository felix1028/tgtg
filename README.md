# tgtg (Too Good To Go Bot & Monitor)

A Python tool for interacting with Too Good To Go (TGTG), monitoring favorite stores, tracking available bags, and automating notifications.

## Project Structure
```
tgtg/
├── .env.example          # Sample environment variables
├── pyproject.toml        # Dependencies and metadata
├── tokens.json           # Cached authentication tokens (git-ignored)
└── src/
    └── tgtg_bot/
        ├── __init__.py
        ├── auth.py       # Handles email login & token saving
        └── monitor.py    # Checks favorite stores and item stock
```

## Quick Start

### 1. Requirements
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (installed at `/opt/homebrew/bin/uv`)

### 2. Authentication
Too Good To Go uses passwordless email authentication:
```bash
uv run python -m tgtg_bot.auth
```
- Enter your account email.
- Open the login link in your inbox on your phone/browser.
- The script detects login and saves credentials to `tokens.json`.

### 3. Check Favorites / Stock
```bash
uv run python -m tgtg_bot.monitor
```
