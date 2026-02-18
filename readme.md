# MetaMask Connect + Link (Fly.io)

This repo deploys a small FastAPI app to Fly.io:
- Web UI with "Connect MetaMask"
- Optional "Link wallet" flow using a server-issued nonce + signature verification
- Stores user_key -> wallet_address in SQLite on a Fly volume

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
