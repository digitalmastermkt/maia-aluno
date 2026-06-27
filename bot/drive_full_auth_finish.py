#!/usr/bin/env python3
"""Troca o code pelo refresh_token (escopo full drive). Salva .drive_full_token.json (naia).
Uso: venv/bin/python drive_full_auth_finish.py "<CODE>" """
import json, os, sys
from pathlib import Path
BOT=Path("/opt/MAIA/bot"); TOKEN=BOT/".drive_full_token.json"; STATE=BOT/".drive_full_auth_state.json"
SCOPES=["https://www.googleapis.com/auth/drive"]
if len(sys.argv)<2 or not sys.argv[1].strip():
    print("Uso: drive_full_auth_finish.py <code>",file=sys.stderr); sys.exit(1)
code=sys.argv[1].strip()
sys.path.insert(0,"/opt/MAIA/bot/venv/lib/python3.12/site-packages")
from google_auth_oauthlib.flow import InstalledAppFlow
st=json.loads(STATE.read_text())
cfg={"installed":{"client_id":st["client_id"],"client_secret":st["client_secret"],
     "auth_uri":"https://accounts.google.com/o/oauth2/auth",
     "token_uri":"https://oauth2.googleapis.com/token","redirect_uris":["http://localhost"]}}
flow=InstalledAppFlow.from_client_config(cfg,SCOPES,state=st["state"])
flow.redirect_uri=st["redirect_uri"]; flow.code_verifier=st["code_verifier"]
flow.fetch_token(code=code)
c=flow.credentials
TOKEN.write_text(json.dumps({"token":c.token,"refresh_token":c.refresh_token,
    "token_uri":c.token_uri,"client_id":c.client_id,"client_secret":c.client_secret,
    "scopes":c.scopes,"expiry":c.expiry.isoformat() if c.expiry else None},indent=2))
os.chmod(TOKEN,0o600)
try: STATE.unlink()
except: pass
print("OK token salvo em",TOKEN)
