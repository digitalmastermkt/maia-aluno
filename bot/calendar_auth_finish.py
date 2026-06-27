#!/usr/bin/env python3
"""
calendar_auth_finish.py — Etapa 2 do OAuth Calendar (modo headless via Telegram).

Le o estado salvo em .calendar_auth_state.json (escrito pelo calendar_auth_url.py),
recebe o code via argv, troca por refresh_token e salva em
/opt/MAIA/bot/.calendar_token.json.

Uso:
    /opt/MAIA/bot/venv/bin/python /opt/MAIA/bot/calendar_auth_finish.py "<CODE>"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BOT_DIR = Path("/opt/MAIA/bot")
TOKEN_PATH = BOT_DIR / ".calendar_token.json"
STATE_PATH = BOT_DIR / ".calendar_auth_state.json"
LOG_PATH = BOT_DIR / "logs" / "calendar.log"

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: calendar_auth_finish.py <code>", file=sys.stderr)
        return 1

    code = sys.argv[1].strip()
    if not code:
        print("ERRO: codigo vazio.", file=sys.stderr)
        return 1

    if not STATE_PATH.exists():
        print(f"ERRO: estado nao encontrado em {STATE_PATH}. Rode calendar_auth_url.py primeiro.", file=sys.stderr)
        return 2

    venv_sp = "/opt/MAIA/bot/venv/lib/python3.12/site-packages"
    if venv_sp not in sys.path:
        sys.path.insert(0, venv_sp)

    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    state_data = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    client_config = {
        "installed": {
            "client_id": state_data["client_id"],
            "client_secret": state_data["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(
        client_config,
        SCOPES,
        state=state_data["state"],
    )
    flow.redirect_uri = state_data["redirect_uri"]
    # Restaurar o code_verifier (PKCE) salvo na fase URL
    flow.code_verifier = state_data["code_verifier"]

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        print(f"ERRO ao trocar code por token: {e}", file=sys.stderr)
        return 3

    creds = flow.credentials

    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }

    TOKEN_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(TOKEN_PATH, 0o600)

    # Limpa estado intermediario (nao serve mais)
    try:
        STATE_PATH.unlink()
    except Exception:
        pass

    # Teste: listar calendarios
    items = []
    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        cals = service.calendarList().list(maxResults=10).execute()
        items = cals.get("items", [])
    except Exception as e:
        print(f"AVISO: token salvo mas teste falhou: {e}", file=sys.stderr)

    print(f"OK: token salvo em {TOKEN_PATH}")
    print(f"OK: {len(items)} calendario(s) acessivel(is):")
    for c in items:
        print(f"  - {c.get('summary')} (id={c.get('id')}, role={c.get('accessRole')})")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[calendar_auth_finish] refresh_token salvo, {len(items)} calendarios\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
