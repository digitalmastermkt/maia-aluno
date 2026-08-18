"""
calendar_api.py — Wrapper de alto nivel sobre Google Calendar API
para uso da Maia Master.

Pre-requisitos:
- /opt/MAIA/bot/.calendar_token.json existente (gerado por calendar_auth.py)
- Libs no venv: google-auth, google-auth-oauthlib, google-api-python-client

Funcoes expostas (calendarios):
    list_calendars()
    get_calendar(calendar_id)
    create_calendar(summary, timezone='America/Sao_Paulo', description=None)
    delete_calendar(calendar_id, confirm=False)
    unsubscribe_calendar(calendar_id)
    update_calendar_settings(calendar_id, summary=None, timezone=None, description=None)

Funcoes expostas (eventos):
    list_events(calendar_id='primary', time_min=None, time_max=None, max_results=250)
    insert_event(summary, start_iso, end_iso, calendar_id='primary', description=None, location=None, timezone='America/Sao_Paulo', extended_props=None)
    update_event(event_id, calendar_id='primary', **fields)
    delete_event(event_id, calendar_id='primary')
    free_busy(time_min, time_max, calendars=('primary',))
    find_event_by_extended_prop(key, value, calendar_id='primary', time_min=None, time_max=None)

Cada funcao reutiliza credenciais cacheadas e gera log em
/opt/MAIA/bot/logs/calendar.log.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone as _tz
from pathlib import Path
from typing import Any, Iterable, Optional

# Garante venv site-packages
_VENV_SP = "/opt/MAIA/bot/venv/lib/python3.12/site-packages"
if _VENV_SP not in sys.path:
    sys.path.insert(0, _VENV_SP)

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

BOT_DIR = Path("/opt/MAIA/bot")
TOKEN_PATH = BOT_DIR / ".calendar_token.json"
LOG_PATH = BOT_DIR / "logs" / "calendar.log"

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
_logger = logging.getLogger("calendar_api")
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    _h = logging.FileHandler(LOG_PATH, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _logger.addHandler(_h)


# --------------------------------------------------------------------------- #
# Credenciais
# --------------------------------------------------------------------------- #
_service_cache: Any = None


def _load_credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"{TOKEN_PATH} nao existe. Rode calendar_auth.py primeiro."
        )
    data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or SCOPES,
    )
    if creds.expired and creds.refresh_token:
        _logger.info("access token expirado; renovando via refresh_token")
        creds.refresh(Request())
        _persist_credentials(creds)
    return creds


def _persist_credentials(creds: Credentials) -> None:
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


def _service():
    global _service_cache
    if _service_cache is None:
        creds = _load_credentials()
        _service_cache = build(
            "calendar", "v3", credentials=creds, cache_discovery=False
        )
    return _service_cache


# --------------------------------------------------------------------------- #
# Calendars (gestao)
# --------------------------------------------------------------------------- #
def list_calendars() -> list[dict]:
    """Lista todos os calendarios visiveis ao usuario autenticado."""
    svc = _service()
    items: list[dict] = []
    page_token = None
    while True:
        resp = svc.calendarList().list(pageToken=page_token, maxResults=250).execute()
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    _logger.info("list_calendars -> %d itens", len(items))
    return items


def get_calendar(calendar_id: str) -> dict:
    """Busca metadados de um calendar (timezone, summary, descricao)."""
    svc = _service()
    cal = svc.calendars().get(calendarId=calendar_id).execute()
    _logger.info("get_calendar(%s) -> %s", calendar_id, cal.get("summary"))
    return cal


def create_calendar(
    summary: str,
    timezone: str = "America/Sao_Paulo",
    description: Optional[str] = None,
) -> dict:
    """Cria um novo calendar do tipo secundario, na conta do usuario."""
    svc = _service()
    body: dict = {"summary": summary, "timeZone": timezone}
    if description:
        body["description"] = description
    cal = svc.calendars().insert(body=body).execute()
    _logger.info(
        "create_calendar summary=%r tz=%s -> id=%s",
        summary, timezone, cal.get("id"),
    )
    return cal


def delete_calendar(calendar_id: str, confirm: bool = False) -> dict:
    """
    Apaga FISICAMENTE um calendar (irreversivel). Calendar primario nao pode
    ser deletado pela API; nesse caso retornamos erro.

    Use confirm=True para evitar deletar por engano.
    """
    if not confirm:
        msg = "delete_calendar exige confirm=True (protecao)"
        _logger.warning("%s [id=%s]", msg, calendar_id)
        raise ValueError(msg)

    svc = _service()
    try:
        svc.calendars().delete(calendarId=calendar_id).execute()
    except HttpError as e:
        _logger.error("delete_calendar(%s) HTTP %s: %s", calendar_id, e.status_code, e)
        raise

    result = {"deleted": True, "calendar_id": calendar_id, "ts": datetime.utcnow().isoformat()}
    _logger.info("delete_calendar OK id=%s", calendar_id)
    return result


def unsubscribe_calendar(calendar_id: str) -> dict:
    """
    Remove o calendar da lista do usuario, sem deletar fisicamente.
    Util para calendarios compartilhados / publicos (ex: Feriados no Brasil).
    """
    svc = _service()
    try:
        svc.calendarList().delete(calendarId=calendar_id).execute()
    except HttpError as e:
        _logger.error("unsubscribe_calendar(%s) HTTP %s: %s", calendar_id, e.status_code, e)
        raise

    result = {"unsubscribed": True, "calendar_id": calendar_id}
    _logger.info("unsubscribe_calendar OK id=%s", calendar_id)
    return result


def update_calendar_settings(
    calendar_id: str,
    summary: Optional[str] = None,
    timezone: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Atualiza nome (summary), timezone ou descricao de um calendar."""
    svc = _service()
    body: dict = {}
    if summary is not None:
        body["summary"] = summary
    if timezone is not None:
        body["timeZone"] = timezone
    if description is not None:
        body["description"] = description
    if not body:
        raise ValueError("nenhum campo para atualizar")

    cal = svc.calendars().patch(calendarId=calendar_id, body=body).execute()
    _logger.info("update_calendar_settings(%s) keys=%s OK", calendar_id, list(body.keys()))
    return cal


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def _now_iso_utc() -> str:
    return datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_events(
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 250,
) -> list[dict]:
    """
    Lista eventos do calendar entre time_min e time_max (RFC3339).
    Default: agora -> agora+7d. Expande recurrentes (singleEvents=True).
    """
    svc = _service()
    if not time_min:
        time_min = _now_iso_utc()
    if not time_max:
        # +7 dias
        end = datetime.now(_tz.utc) + timedelta(days=7)
        time_max = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    items: list[dict] = []
    page_token = None
    while True:
        resp = svc.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=min(max_results, 2500),
            pageToken=page_token,
        ).execute()
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token or len(items) >= max_results:
            break

    _logger.info(
        "list_events cal=%s [%s..%s] -> %d itens",
        calendar_id, time_min, time_max, len(items),
    )
    return items[:max_results]


def insert_event(
    summary: str,
    start_iso: str,
    end_iso: str,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
    timezone: str = "America/Sao_Paulo",
    extended_props: Optional[dict] = None,
    reminders_minutes: Optional[list[int]] = None,
    add_meet: bool = False,
    attendees: Optional[list[str]] = None,
) -> dict:
    """
    Cria um evento no calendar. start_iso/end_iso podem ser:
    - datetime ISO ('2026-05-15T14:00:00') -> evento com hora
    - date ISO ('2026-05-15') -> evento de dia inteiro

    add_meet=True gera automaticamente um link do Google Meet
    (conferenceData). O link fica em ev["hangoutLink"] ou em
    ev["conferenceData"]["entryPoints"][0]["uri"].
    attendees: lista de e-mails convidados (opcional).
    """
    svc = _service()

    def _time_field(iso: str) -> dict:
        if "T" in iso:
            return {"dateTime": iso, "timeZone": timezone}
        return {"date": iso}

    body: dict = {
        "summary": summary,
        "start": _time_field(start_iso),
        "end": _time_field(end_iso),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees]
    if extended_props:
        body["extendedProperties"] = {"private": {k: str(v) for k, v in extended_props.items()}}
    if reminders_minutes:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in reminders_minutes],
        }

    insert_kwargs: dict = {"calendarId": calendar_id, "body": body}
    if add_meet:
        import uuid as _uuid
        body["conferenceData"] = {
            "createRequest": {
                "requestId": _uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        insert_kwargs["conferenceDataVersion"] = 1

    ev = svc.events().insert(**insert_kwargs).execute()
    _logger.info(
        "insert_event cal=%s id=%s summary=%r [%s..%s] meet=%s",
        calendar_id, ev.get("id"), summary, start_iso, end_iso, add_meet,
    )
    return ev


def update_event(
    event_id: str,
    calendar_id: str = "primary",
    summary: Optional[str] = None,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    """Atualiza campos de um evento existente (patch)."""
    svc = _service()

    def _time_field(iso: str) -> dict:
        if "T" in iso:
            return {"dateTime": iso, "timeZone": timezone}
        return {"date": iso}

    body: dict = {}
    if summary is not None:
        body["summary"] = summary
    if start_iso:
        body["start"] = _time_field(start_iso)
    if end_iso:
        body["end"] = _time_field(end_iso)
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if not body:
        raise ValueError("nenhum campo para atualizar")

    ev = svc.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()
    _logger.info("update_event cal=%s id=%s keys=%s", calendar_id, event_id, list(body.keys()))
    return ev


def delete_event(event_id: str, calendar_id: str = "primary") -> dict:
    """Deleta evento."""
    svc = _service()
    try:
        svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as e:
        _logger.error("delete_event(%s/%s) HTTP %s", calendar_id, event_id, e.status_code)
        raise
    _logger.info("delete_event cal=%s id=%s OK", calendar_id, event_id)
    return {"deleted": True, "event_id": event_id}


def free_busy(
    time_min: str,
    time_max: str,
    calendars: Iterable[str] = ("primary",),
) -> dict:
    """
    Consulta janelas ocupadas via FreeBusy API. Retorna dict:
        { calendar_id: [ {start, end}, ... ] }
    """
    svc = _service()
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": c} for c in calendars],
    }
    resp = svc.freebusy().query(body=body).execute()
    out: dict = {}
    for cid, info in (resp.get("calendars") or {}).items():
        out[cid] = info.get("busy", [])
    _logger.info("free_busy [%s..%s] cals=%d", time_min, time_max, len(out))
    return out


def find_event_by_extended_prop(
    key: str,
    value: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
) -> Optional[dict]:
    """
    Busca evento que tenha extendedProperties.private[key] == value.
    Util pra encontrar evento sincronizado a partir do task_id (key='checklist_task_id').
    """
    svc = _service()
    params = {
        "calendarId": calendar_id,
        "privateExtendedProperty": f"{key}={value}",
        "singleEvents": True,
        "maxResults": 5,
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    resp = svc.events().list(**params).execute()
    items = resp.get("items", [])
    return items[0] if items else None


# --------------------------------------------------------------------------- #
# CLI manual (para testes e uso da Maia via subprocess)
# --------------------------------------------------------------------------- #
def _main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="CLI manual do calendar_api")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    g = sub.add_parser("get")
    g.add_argument("calendar_id")

    c = sub.add_parser("create")
    c.add_argument("summary")
    c.add_argument("--tz", default="America/Sao_Paulo")
    c.add_argument("--desc", default=None)

    d = sub.add_parser("delete")
    d.add_argument("calendar_id")
    d.add_argument("--confirm", action="store_true")

    u = sub.add_parser("unsubscribe")
    u.add_argument("calendar_id")

    up = sub.add_parser("update")
    up.add_argument("calendar_id")
    up.add_argument("--summary", default=None)
    up.add_argument("--tz", default=None)
    up.add_argument("--desc", default=None)

    # Events
    le = sub.add_parser("list_events")
    le.add_argument("--cal", default="primary")
    le.add_argument("--time_min", default=None)
    le.add_argument("--time_max", default=None)
    le.add_argument("--max", type=int, default=250)

    ie = sub.add_parser("insert_event")
    ie.add_argument("--cal", default="primary")
    ie.add_argument("--summary", required=True)
    ie.add_argument("--start", required=True, help="ISO datetime ou date")
    ie.add_argument("--end", required=True)
    ie.add_argument("--desc", default=None)
    ie.add_argument("--loc", default=None)
    ie.add_argument("--tz", default="America/Sao_Paulo")
    ie.add_argument(
        "--ext_prop", action="append", default=[],
        help="par chave=valor (pode repetir)"
    )
    ie.add_argument(
        "--reminder", action="append", type=int, default=[],
        help="minutos antes (pode repetir)"
    )

    ue = sub.add_parser("update_event")
    ue.add_argument("event_id")
    ue.add_argument("--cal", default="primary")
    ue.add_argument("--summary", default=None)
    ue.add_argument("--start", default=None)
    ue.add_argument("--end", default=None)
    ue.add_argument("--desc", default=None)
    ue.add_argument("--loc", default=None)
    ue.add_argument("--tz", default="America/Sao_Paulo")

    de = sub.add_parser("delete_event")
    de.add_argument("event_id")
    de.add_argument("--cal", default="primary")

    fb = sub.add_parser("free_busy")
    fb.add_argument("--time_min", required=True)
    fb.add_argument("--time_max", required=True)
    fb.add_argument("--cal", action="append", default=["primary"])

    fp = sub.add_parser("find_event_prop")
    fp.add_argument("key")
    fp.add_argument("value")
    fp.add_argument("--cal", default="primary")
    fp.add_argument("--time_min", default=None)
    fp.add_argument("--time_max", default=None)

    args = p.parse_args()

    def _dump(x):
        print(json.dumps(x, indent=2, ensure_ascii=False, default=str))

    if args.cmd == "list":
        for c in list_calendars():
            print(f"{c.get('id')}\t{c.get('accessRole')}\t{c.get('summary')}")
    elif args.cmd == "get":
        _dump(get_calendar(args.calendar_id))
    elif args.cmd == "create":
        _dump(create_calendar(args.summary, args.tz, args.desc))
    elif args.cmd == "delete":
        _dump(delete_calendar(args.calendar_id, confirm=args.confirm))
    elif args.cmd == "unsubscribe":
        _dump(unsubscribe_calendar(args.calendar_id))
    elif args.cmd == "update":
        _dump(update_calendar_settings(args.calendar_id, args.summary, args.tz, args.desc))
    elif args.cmd == "list_events":
        _dump(list_events(args.cal, args.time_min, args.time_max, args.max))
    elif args.cmd == "insert_event":
        ext = {}
        for pair in args.ext_prop:
            if "=" in pair:
                k, _, v = pair.partition("=")
                ext[k.strip()] = v.strip()
        _dump(insert_event(
            args.summary, args.start, args.end,
            calendar_id=args.cal, description=args.desc, location=args.loc,
            timezone=args.tz, extended_props=ext or None,
            reminders_minutes=args.reminder or None,
        ))
    elif args.cmd == "update_event":
        _dump(update_event(
            args.event_id, calendar_id=args.cal,
            summary=args.summary, start_iso=args.start, end_iso=args.end,
            description=args.desc, location=args.loc, timezone=args.tz,
        ))
    elif args.cmd == "delete_event":
        _dump(delete_event(args.event_id, args.cal))
    elif args.cmd == "free_busy":
        _dump(free_busy(args.time_min, args.time_max, args.cal))
    elif args.cmd == "find_event_prop":
        _dump(find_event_by_extended_prop(args.key, args.value, args.cal, args.time_min, args.time_max))

    return 0


if __name__ == "__main__":
    sys.exit(_main())
