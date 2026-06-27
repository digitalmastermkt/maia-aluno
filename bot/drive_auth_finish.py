#!/usr/bin/env python3
"""
drive_auth_finish.py — Etapa 2 do OAuth Google Drive (headless).
Le .drive_auth_state.json, recebe o code via argv, troca por refresh_token e
salva em .drive_token.json. Testa criando a pasta raiz de arquivo no Drive.

Uso: /opt/MAIA/bot/venv/bin/python drive_auth_finish.py "<CODE>"
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

BOT_DIR = Path("/opt/MAIA/bot")
TOKEN_PATH = BOT_DIR / ".drive_token.json"
STATE_PATH = BOT_DIR / ".drive_auth_state.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
ROOT_FOLDER_NAME = "NAIA-Arquivo"


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Uso: drive_auth_finish.py <code>", file=sys.stderr)
        return 1
    code = sys.argv[1].strip()
    if not STATE_PATH.exists():
        print(f"ERRO: estado nao encontrado em {STATE_PATH}. Rode drive_auth_url.py primeiro.", file=sys.stderr)
        return 2

    venv_sp = "/opt/MAIA/bot/venv/lib/python3.12/site-packages"
    if venv_sp not in sys.path:
        sys.path.insert(0, venv_sp)
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    client_config = {
        "installed": {
            "client_id": st["client_id"],
            "client_secret": st["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES, state=st["state"])
    flow.redirect_uri = st["redirect_uri"]
    flow.code_verifier = st["code_verifier"]
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
    try:
        STATE_PATH.unlink()
    except Exception:
        pass

    # Teste: cria (ou acha) a pasta raiz de arquivo
    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        q = (f"name='{ROOT_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' "
             f"and trashed=false")
        found = service.files().list(q=q, spaces="drive", fields="files(id,name)").execute().get("files", [])
        if found:
            fid = found[0]["id"]
        else:
            meta = {"name": ROOT_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
            fid = service.files().create(body=meta, fields="id").execute()["id"]
        print(f"OK: token salvo em {TOKEN_PATH}")
        print(f"OK: pasta raiz '{ROOT_FOLDER_NAME}' pronta (id={fid})")
    except Exception as e:
        print(f"AVISO: token salvo mas teste do Drive falhou: {e}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
