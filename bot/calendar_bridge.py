#!/usr/bin/env python3
"""
calendar_bridge.py — Bridge JSON entre o checklist (Node.js) e o Google Calendar.

Saida: stdout = JSON unica linha. Status nao-zero em erro.
Stderr = mensagem de erro humana.

Comandos:
    list_upcoming --days 7
        Lista eventos do calendar primario nos proximos N dias.

    insert_for_task --task_id 123 --title "..." --deadline "2026-05-15"
                    [--duration 30] [--description "..."]
        Cria evento de duration min ancorado em deadline (default 09:00 BRT)
        com extendedProperty private 'checklist_task_id' = task_id.

    update_for_task --task_id 123 --title "..." --deadline "2026-05-15"
                    [--duration 30] [--description "..."]
        Acha evento por checklist_task_id e atualiza. Se nao existe, cria.

    delete_for_task --task_id 123
        Acha e deleta evento associado ao task_id. No-op se nao existe.

    check_availability --start "2026-05-15T14:00" --duration 60
        Verifica conflitos com calendar primario.
        Saida inclui lista de eventos conflitantes em formato leve.

Variaveis de ambiente:
    CALENDAR_PRIMARY_ID  (default: 'primary')
    CALENDAR_TZ          (default: 'America/Sao_Paulo')

Exemplo de uso direto:
    /opt/MAIA/bot/venv/bin/python3 calendar_bridge.py list_upcoming --days 7
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Garante venv site-packages
_VENV_SP = "/opt/MAIA/bot/venv/lib/python3.12/site-packages"
if _VENV_SP not in sys.path:
    sys.path.insert(0, _VENV_SP)

sys.path.insert(0, "/opt/MAIA/bot")
import calendar_api as cal  # noqa: E402

PRIMARY_ID = os.environ.get("CALENDAR_PRIMARY_ID", "primary")
TZ_NAME = os.environ.get("CALENDAR_TZ", "America/Sao_Paulo")
TZ_BRT = timezone(timedelta(hours=-3))


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
    sys.stdout.write("\n")


def _err(msg: str, code: int = 1) -> int:
    _emit({"ok": False, "error": msg})
    return code


def _now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_event(ev: dict) -> dict:
    """Reduz evento Google a representacao leve para o frontend."""
    start = ev.get("start", {}) or {}
    end = ev.get("end", {}) or {}
    is_all_day = "date" in start and "dateTime" not in start
    return {
        "id": ev.get("id"),
        "summary": ev.get("summary") or "(sem titulo)",
        "description": ev.get("description"),
        "location": ev.get("location"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": is_all_day,
        "html_link": ev.get("htmlLink"),
        "status": ev.get("status"),
        "calendar_id": PRIMARY_ID,
        "extended_props": (ev.get("extendedProperties") or {}).get("private") or {},
        "creator": (ev.get("creator") or {}).get("email"),
    }


def cmd_list_upcoming(args) -> int:
    days = max(1, min(args.days, 30))
    time_min = _now_iso_z()
    time_max = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        events = cal.list_events(
            calendar_id=PRIMARY_ID,
            time_min=time_min,
            time_max=time_max,
            max_results=500,
        )
    except FileNotFoundError as e:
        return _err(f"OAuth nao configurado: {e}", 2)
    except Exception as e:
        return _err(f"calendar API: {e}")
    out = [_normalize_event(e) for e in events]
    _emit({"ok": True, "events": out, "count": len(out), "time_min": time_min, "time_max": time_max})
    return 0


def _deadline_to_event_window(deadline: str, duration_min: int = 30, default_hour: int = 9) -> tuple[str, str]:
    """
    Converte deadline ('YYYY-MM-DD' ou 'YYYY-MM-DDTHH:MM:SS' ou ISO completo)
    em janela ISO (start, end) com timezone BRT.
    Sem hora -> ancora em 09:00 BRT.
    """
    s = deadline.strip()
    if "T" in s:
        # Ja tem hora — preserva
        # Aceita 'YYYY-MM-DDTHH:MM' ou com segundos / fuso
        try:
            # Remove possivel 'Z' final
            base = s.rstrip("Z")
            if "+" in base or base.count("-") > 2:
                # Ja tem fuso explicito — parsea como aware
                dt = datetime.fromisoformat(base)
            else:
                dt = datetime.fromisoformat(base).replace(tzinfo=TZ_BRT)
        except ValueError:
            # Fallback: tenta sem segundos
            dt = datetime.strptime(base[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ_BRT)
    else:
        # So data
        d = datetime.strptime(s[:10], "%Y-%m-%d").date()
        dt = datetime(d.year, d.month, d.day, default_hour, 0, 0, tzinfo=TZ_BRT)

    end_dt = dt + timedelta(minutes=duration_min)
    # Formato sem timezone offset (vamos passar timezone separado pro Calendar)
    start_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    return start_iso, end_iso


def cmd_insert_for_task(args) -> int:
    try:
        start_iso, end_iso = _deadline_to_event_window(args.deadline, args.duration)
    except Exception as e:
        return _err(f"deadline invalido ({args.deadline}): {e}", 3)
    try:
        ev = cal.insert_event(
            summary=f"[Checklist] {args.title}",
            start_iso=start_iso,
            end_iso=end_iso,
            calendar_id=PRIMARY_ID,
            description=(args.description or "")[:8000] + f"\n\n— sincronizado do checklist (task #{args.task_id})",
            timezone=TZ_NAME,
            extended_props={
                "checklist_task_id": str(args.task_id),
                "synced_from": "checklist",
            },
            reminders_minutes=[60, 15],
        )
    except FileNotFoundError as e:
        return _err(f"OAuth nao configurado: {e}", 2)
    except Exception as e:
        return _err(f"calendar API: {e}")
    _emit({"ok": True, "event": _normalize_event(ev)})
    return 0


def cmd_update_for_task(args) -> int:
    """Procura evento pelo extended_prop checklist_task_id; atualiza ou cria."""
    try:
        existing = cal.find_event_by_extended_prop(
            "checklist_task_id", str(args.task_id),
            calendar_id=PRIMARY_ID,
        )
    except FileNotFoundError as e:
        return _err(f"OAuth nao configurado: {e}", 2)
    except Exception as e:
        return _err(f"calendar API (find): {e}")

    try:
        start_iso, end_iso = _deadline_to_event_window(args.deadline, args.duration)
    except Exception as e:
        return _err(f"deadline invalido: {e}", 3)

    desc = (args.description or "")[:8000] + f"\n\n— sincronizado do checklist (task #{args.task_id})"

    try:
        if existing:
            ev = cal.update_event(
                existing["id"],
                calendar_id=PRIMARY_ID,
                summary=f"[Checklist] {args.title}",
                start_iso=start_iso,
                end_iso=end_iso,
                description=desc,
                timezone=TZ_NAME,
            )
            action = "updated"
        else:
            ev = cal.insert_event(
                summary=f"[Checklist] {args.title}",
                start_iso=start_iso,
                end_iso=end_iso,
                calendar_id=PRIMARY_ID,
                description=desc,
                timezone=TZ_NAME,
                extended_props={
                    "checklist_task_id": str(args.task_id),
                    "synced_from": "checklist",
                },
                reminders_minutes=[60, 15],
            )
            action = "created"
    except Exception as e:
        return _err(f"calendar API (write): {e}")

    _emit({"ok": True, "action": action, "event": _normalize_event(ev)})
    return 0


def cmd_delete_for_task(args) -> int:
    try:
        existing = cal.find_event_by_extended_prop(
            "checklist_task_id", str(args.task_id),
            calendar_id=PRIMARY_ID,
        )
    except FileNotFoundError as e:
        return _err(f"OAuth nao configurado: {e}", 2)
    except Exception as e:
        return _err(f"calendar API (find): {e}")

    if not existing:
        _emit({"ok": True, "action": "noop", "note": "evento nao existia"})
        return 0

    try:
        cal.delete_event(existing["id"], calendar_id=PRIMARY_ID)
    except Exception as e:
        return _err(f"calendar API (delete): {e}")
    _emit({"ok": True, "action": "deleted", "event_id": existing["id"]})
    return 0


def cmd_check_availability(args) -> int:
    """
    Verifica se proposed_datetime + duration ta livre.
    Retorna lista de eventos conflitantes (normalizados).
    """
    try:
        # Aceita 'YYYY-MM-DD HH:MM' ou ISO
        s = args.start.replace(" ", "T")
        if len(s) == 16:
            s = s + ":00"
        dt = datetime.fromisoformat(s).replace(tzinfo=TZ_BRT) if "+" not in s and "Z" not in s else datetime.fromisoformat(s.rstrip("Z"))
    except Exception as e:
        return _err(f"start invalido: {e}", 3)

    end_dt = dt + timedelta(minutes=args.duration)

    # Para freeBusy precisa formato UTC Z
    def _to_utc_z(d: datetime) -> str:
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ_BRT)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    time_min = _to_utc_z(dt)
    time_max = _to_utc_z(end_dt)

    # Buffer de 30min antes/depois pra detectar coisas grudadas
    buf_min = _to_utc_z(dt - timedelta(minutes=30))
    buf_max = _to_utc_z(end_dt + timedelta(minutes=30))

    try:
        # FreeBusy estrito (so o intervalo)
        fb = cal.free_busy(time_min, time_max, [PRIMARY_ID])
        # Eventos detalhados no buffer pra dar contexto
        events = cal.list_events(PRIMARY_ID, buf_min, buf_max, max_results=50)
    except FileNotFoundError as e:
        return _err(f"OAuth nao configurado: {e}", 2)
    except Exception as e:
        return _err(f"calendar API: {e}")

    busy = fb.get(PRIMARY_ID, [])
    available = len(busy) == 0

    # Conflitos = eventos que se sobrepoem ao intervalo proposto
    def _overlap(ev: dict) -> bool:
        st = ev.get("start", {})
        en = ev.get("end", {})
        s_iso = st.get("dateTime") or st.get("date")
        e_iso = en.get("dateTime") or en.get("date")
        if not s_iso or not e_iso:
            return False
        try:
            es = datetime.fromisoformat(s_iso.rstrip("Z").replace("Z", ""))
            ee = datetime.fromisoformat(e_iso.rstrip("Z").replace("Z", ""))
            if es.tzinfo is None:
                es = es.replace(tzinfo=TZ_BRT)
            if ee.tzinfo is None:
                ee = ee.replace(tzinfo=TZ_BRT)
        except Exception:
            return False
        return es < end_dt and ee > dt

    conflicts = [_normalize_event(e) for e in events if _overlap(e)]
    nearby = [_normalize_event(e) for e in events if not _overlap(e)]

    _emit({
        "ok": True,
        "available": available,
        "proposed": {
            "start": dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_min": args.duration,
            "tz": TZ_NAME,
        },
        "busy_periods": busy,
        "conflicts": conflicts,
        "nearby_events": nearby,
    })
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Bridge Calendar <-> Checklist")
    sub = p.add_subparsers(dest="cmd", required=True)

    lu = sub.add_parser("list_upcoming")
    lu.add_argument("--days", type=int, default=7)

    ins = sub.add_parser("insert_for_task")
    ins.add_argument("--task_id", required=True)
    ins.add_argument("--title", required=True)
    ins.add_argument("--deadline", required=True)
    ins.add_argument("--duration", type=int, default=30)
    ins.add_argument("--description", default="")

    upd = sub.add_parser("update_for_task")
    upd.add_argument("--task_id", required=True)
    upd.add_argument("--title", required=True)
    upd.add_argument("--deadline", required=True)
    upd.add_argument("--duration", type=int, default=30)
    upd.add_argument("--description", default="")

    rm = sub.add_parser("delete_for_task")
    rm.add_argument("--task_id", required=True)

    ck = sub.add_parser("check_availability")
    ck.add_argument("--start", required=True, help="YYYY-MM-DD HH:MM ou ISO")
    ck.add_argument("--duration", type=int, default=60)

    args = p.parse_args()

    handlers = {
        "list_upcoming": cmd_list_upcoming,
        "insert_for_task": cmd_insert_for_task,
        "update_for_task": cmd_update_for_task,
        "delete_for_task": cmd_delete_for_task,
        "check_availability": cmd_check_availability,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
