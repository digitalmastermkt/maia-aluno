#!/usr/bin/env python3
"""
drive_auth_url.py — Etapa 1 do OAuth Google Drive (headless via Telegram/SSH).
Espelha calendar_auth_url.py mas com scope MINIMO drive.file (a Naia so enxerga
o que ela mesma cria no Drive). Reusa o mesmo app OAuth do Calendar (.env).

Uso: /opt/MAIA/bot/venv/bin/python /opt/MAIA/bot/drive_auth_url.py
Saida: stdout = URL de autorizacao.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

BOT_DIR = Path("/opt/MAIA/bot")
ENV_PATH = BOT_DIR / ".env"
STATE_PATH = BOT_DIR / ".drive_auth_state.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def load_env() -> dict:
    env = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    venv_sp = "/opt/MAIA/bot/venv/lib/python3.12/site-packages"
    if venv_sp not in sys.path:
        sys.path.insert(0, venv_sp)
    from google_auth_oauthlib.flow import InstalledAppFlow

    env = load_env()
    client_id = env.get("GOOGLE_DRIVE_CLIENT_ID")
    client_secret = env.get("GOOGLE_DRIVE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("ERRO: GOOGLE_DRIVE_CLIENT_ID/SECRET ausentes em .env", file=sys.stderr)
        return 2

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    flow.redirect_uri = "http://localhost"
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        prompt="consent",
    )
    persist = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "http://localhost",
        "scopes": SCOPES,
        "state": state,
        "code_verifier": flow.code_verifier,
    }
    STATE_PATH.write_text(json.dumps(persist, indent=2), encoding="utf-8")
    os.chmod(STATE_PATH, 0o600)
    print(auth_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
