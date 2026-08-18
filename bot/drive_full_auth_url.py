#!/usr/bin/env python3
"""Gera URL OAuth com escopo FULL drive (escreve em pastas existentes do dono).
Estado salvo em .drive_full_auth_state.json (legivel por maia).
Uso: venv/bin/python drive_full_auth_url.py"""
import json, os, sys
from pathlib import Path
BOT=Path("/opt/MAIA/bot"); ENV=BOT/".env"; STATE=BOT/".drive_full_auth_state.json"
SCOPES=["https://www.googleapis.com/auth/drive"]
def env():
    d={}
    for ln in ENV.read_text().splitlines():
        ln=ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k,_,v=ln.partition("="); d[k.strip()]=v.strip().strip('"').strip("'")
    return d
sys.path.insert(0,"/opt/MAIA/bot/venv/lib/python3.12/site-packages")
from google_auth_oauthlib.flow import InstalledAppFlow
e=env(); cid=e["GOOGLE_DRIVE_CLIENT_ID"]; cs=e["GOOGLE_DRIVE_CLIENT_SECRET"]
cfg={"installed":{"client_id":cid,"client_secret":cs,
     "auth_uri":"https://accounts.google.com/o/oauth2/auth",
     "token_uri":"https://oauth2.googleapis.com/token",
     "redirect_uris":["http://localhost"]}}
flow=InstalledAppFlow.from_client_config(cfg,SCOPES); flow.redirect_uri="http://localhost"
url,state=flow.authorization_url(access_type="offline",include_granted_scopes="false",prompt="consent")
STATE.write_text(json.dumps({"client_id":cid,"client_secret":cs,"redirect_uri":"http://localhost",
    "scopes":SCOPES,"state":state,"code_verifier":flow.code_verifier},indent=2))
os.chmod(STATE,0o600)
print(url)
