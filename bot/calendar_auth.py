#!/usr/bin/env python3
"""
calendar_auth.py — Fluxo OAuth 2.0 do Google Calendar em DUAS ETAPAS,
projetado para servidor HEADLESS com o usuario (o dono) REMOTO via Telegram.

Como OOB (urn:ietf:wg:oauth:2.0:oob) foi descontinuado pelo Google e nao da pra
abrir navegador no servidor (run_local_server nao serve), usamos redirect_uri de
LOOPBACK e troca manual de codigo:

  ETAPA 1 (gerar URL):
      python3 calendar_auth.py --gen-url
    -> imprime a URL de autorizacao + redirect_uri + instrucao.
    -> salva o estado (client_id/secret, redirect_uri, scopes) em
       .calendar_oauth_state.json para a etapa 2.
    O dono abre a URL no navegador DELE, clica Permitir. O Google
    redireciona pra http://localhost:8765/?code=...&scope=...  (a pagina NAO
    carrega no PC dele, mas a URL da barra contem o code). Ele copia o code.

  ETAPA 2 (trocar code por token):
      python3 calendar_auth.py --exchange '<CODE>'
    -> le o estado salvo, troca o code por refresh_token, salva
       .calendar_token.json (chmod 600) e faz um teste (calendarList.list).

O CODE pode vir como o valor cru (4/0Ab...) OU como a URL inteira do localhost
colada (http://localhost:8765/?code=4%2F0Ab...&scope=...); o script extrai e faz
url-decode automaticamente.

Modo legado interativo (--interactive) mantido apenas para uso local manual.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

BOT_DIR = Path("/opt/MAIA/bot")
ENV_PATH = BOT_DIR / ".env"
TOKEN_PATH = BOT_DIR / ".calendar_token.json"
STATE_PATH = BOT_DIR / ".calendar_oauth_state.json"
LOG_PATH = BOT_DIR / "logs" / "calendar.log"

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Porta de loopback usada no redirect_uri. Precisa bater com o que o Google
# aceita para "Desktop app" (qualquer http://localhost ou 127.0.0.1 e aceito).
REDIRECT_URI = "http://localhost:8765/"


def _inject_venv() -> None:
    venv_sp = "/opt/MAIA/bot/venv/lib/python3.12/site-packages"
    if venv_sp not in sys.path:
        sys.path.insert(0, venv_sp)


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


def _client_config(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


def _build_flow(client_id: str, client_secret: str):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        _client_config(client_id, client_secret),
        scopes=SCOPES,
        # Desabilita PKCE: como o fluxo e em 2 etapas (processos separados), nao
        # da pra carregar o code_verifier gerado na etapa 1 na etapa 2. Desktop
        # app autentica via client_secret, entao PKCE e dispensavel aqui.
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = REDIRECT_URI
    return flow


def gen_url() -> int:
    """ETAPA 1: gera a URL de autorizacao e salva o estado."""
    _inject_venv()
    env = load_env()
    client_id = env.get("GOOGLE_CALENDAR_CLIENT_ID")
    client_secret = env.get("GOOGLE_CALENDAR_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("ERRO: GOOGLE_CALENDAR_CLIENT_ID / GOOGLE_CALENDAR_CLIENT_SECRET ausentes em .env", file=sys.stderr)
        return 2

    flow = _build_flow(client_id, client_secret)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    STATE_PATH.write_text(json.dumps({
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "scopes": SCOPES,
        "state": state,
    }, indent=2), encoding="utf-8")
    os.chmod(STATE_PATH, 0o600)

    print("AUTH_URL:")
    print(auth_url)
    print()
    print("REDIRECT_URI:", REDIRECT_URI)
    print("STATE:", state)
    print("STATE_FILE:", STATE_PATH)
    return 0


def _extract_code(raw: str) -> str:
    """Aceita code cru ou URL inteira do localhost colada; retorna o code decodificado."""
    raw = raw.strip().strip('"').strip("'")
    if raw.startswith("http://") or raw.startswith("https://"):
        q = parse_qs(urlparse(raw).query)
        if "code" in q:
            return q["code"][0]
    # Pode vir url-encoded (4%2F0Ab...) mesmo sem ser URL completa
    if "%2F" in raw or "%2f" in raw:
        return unquote(raw)
    return raw


def exchange(code_raw: str) -> int:
    """ETAPA 2: troca o code por refresh_token usando o estado salvo."""
    _inject_venv()
    from googleapiclient.discovery import build

    if not STATE_PATH.exists():
        print(f"ERRO: estado {STATE_PATH} nao existe. Rode --gen-url primeiro.", file=sys.stderr)
        return 2

    st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    code = _extract_code(code_raw)
    if not code:
        print("ERRO: code vazio apos extracao.", file=sys.stderr)
        return 3

    flow = _build_flow(st["client_id"], st["client_secret"])
    flow.redirect_uri = st.get("redirect_uri", REDIRECT_URI)

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        print(f"ERRO ao trocar code por token: {e}", file=sys.stderr)
        return 4

    creds = flow.credentials
    if not creds.refresh_token:
        print("AVISO: nenhum refresh_token retornado (Google pode ter omitido por ja ter consentido). "
              "Revogue o acesso em myaccount.google.com/permissions e refaca --gen-url com prompt=consent.",
              file=sys.stderr)

    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    TOKEN_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(TOKEN_PATH, 0o600)

    # Teste rapido
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    cals = service.calendarList().list(maxResults=10).execute()
    items = cals.get("items", [])

    print(f"OK — refresh_token salvo em {TOKEN_PATH} (refresh_token presente: {bool(creds.refresh_token)})")
    print(f"OK — list_calendars() retornou {len(items)} calendario(s):")
    for c in items:
        print(f"  - {c.get('summary')} (id={c.get('id')}, role={c.get('accessRole')})")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[calendar_auth] reautorizacao OK, refresh_token={bool(creds.refresh_token)}, {len(items)} calendarios\n")

    # Limpa o estado temporario
    try:
        STATE_PATH.unlink()
    except OSError:
        pass
    return 0


def interactive() -> int:
    """Modo legado: gera URL e le o code via input() (bloqueia)."""
    rc = gen_url()
    if rc != 0:
        return rc
    code = input("\nCole o code (ou a URL inteira do localhost): ").strip()
    if not code:
        print("ERRO: codigo vazio.", file=sys.stderr)
        return 3
    return exchange(code)


def main(argv: list[str]) -> int:
    if "--gen-url" in argv:
        return gen_url()
    if "--exchange" in argv:
        i = argv.index("--exchange")
        if i + 1 >= len(argv):
            print("ERRO: --exchange exige o code como argumento.", file=sys.stderr)
            return 2
        return exchange(argv[i + 1])
    if "--interactive" in argv:
        return interactive()
    print("Uso:\n"
          "  calendar_auth.py --gen-url            # ETAPA 1: gera URL de autorizacao\n"
          "  calendar_auth.py --exchange '<CODE>'  # ETAPA 2: troca code por token\n"
          "  calendar_auth.py --interactive        # modo manual local (bloqueia)\n",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
