#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archive_to_drive.py — Camada 2: arquiva midia fria no Google Drive e libera disco.

SEGURANCA:
  - DRY-RUN por padrao. So apaga/sobe com --apply.
  - Verifica o tamanho no Drive APOS upload; so apaga local se bater.
  - Manifesto append-only em data/drive_archive_manifest.jsonl (path -> link).
  - Nunca toca arquivo/pasta modificado ha menos de min_age dias.
  - EXCLUDE: pastas ativas nunca tocadas (ex: imersao-bonus = lancamento).

Uso:
  archive_to_drive.py                      # dry-run de TUDO (midia + workspace)
  archive_to_drive.py --apply              # arquiva midia do bot (NAO workspace)
  archive_to_drive.py --apply --workspace  # inclui pastas de workspace (zip)
  archive_to_drive.py --workspace          # dry-run incluindo workspace
"""
from __future__ import annotations
import sys, json, time, zipfile, shutil, os
from pathlib import Path
from datetime import datetime, timezone

BOT = Path("/opt/MAIA/bot")
WORKSPACE = Path("/opt/MAIA/workspace")
DATA = Path("/opt/MAIA/data")
MANIFEST = DATA / "drive_archive_manifest.jsonl"
LOG = BOT / "logs" / "drive_archive.log"
TMP = Path("/tmp")

sys.path.insert(0, str(BOT))
from drive_archive import upload_file  # noqa: E402

# (pasta, categoria_no_drive, idade_minima_dias). So arquivos > idade sao mexidos.
FILE_TARGETS = [
    (BOT / "videos",               "videos",     30),
    (BOT / "audio" / "incoming",   "audio",      30),
    (BOT / "images" / "incoming-user", "fotos",  30),
    (BOT / "documents",            "documentos", 30),
]
WORKSPACE_MIN_AGE = 30
# Pastas/nomes ATIVOS que NUNCA devem ser arquivados/apagados.
# Preencher com os nomes de pastas/servicos ao vivo do ambiente (ex: sites
# servidos pelo Caddy, servicos node/systemd, repos de codigo *-site/*-platform).
# Deixe o set vazio se nao houver nada a proteger.
EXCLUDE_NAMES = set()

NOW = datetime.now(timezone.utc).timestamp()


def log(msg: str):
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def age_days(p: Path) -> float:
    try:
        return (NOW - p.stat().st_mtime) / 86400.0
    except Exception:
        return 0.0


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except Exception:
                pass
    return total


def record(entry: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def free_bytes() -> int:
    return shutil.disk_usage("/").free


def archive_files(apply: bool):
    total_bytes = 0
    total_n = 0
    for base, categoria, min_age in FILE_TARGETS:
        if not base.exists():
            continue
        files = [f for f in base.rglob("*") if f.is_file()]
        old = [f for f in files if age_days(f) >= min_age]
        if not old:
            log(f"[{categoria}] {base}: 0 arquivos > {min_age}d (de {len(files)} total)")
            continue
        sz = sum(f.stat().st_size for f in old)
        log(f"[{categoria}] {base}: {len(old)} arquivos > {min_age}d = {human(sz)}")
        for f in old:
            if not apply:
                log(f"   DRY-RUN subiria+apagaria: {f.name} ({human(f.stat().st_size)})")
                total_bytes += f.stat().st_size
                total_n += 1
                continue
            local_size = f.stat().st_size
            try:
                info = upload_file(str(f), categoria)
            except Exception as e:
                log(f"   ERRO upload {f.name}: {e} — mantendo local")
                continue
            if int(info.get("size") or 0) != local_size:
                log(f"   MISMATCH tamanho {f.name} (local={local_size} drive={info.get('size')}) — NAO apago")
                continue
            record({"ts": datetime.now(timezone.utc).isoformat(), "tipo": "file",
                    "local": str(f), "categoria": categoria, "size": local_size,
                    "drive_id": info["id"], "drive_link": info["link"]})
            try:
                f.unlink()
                log(f"   OK arquivado+apagado: {f.name} -> {info['link']}")
                total_bytes += local_size
                total_n += 1
            except Exception as e:
                log(f"   AVISO subiu mas falhou apagar {f.name}: {e}")
    log(f"=== MIDIA: {total_n} arquivos, {human(total_bytes)} {'liberados' if apply else '(dry-run)'} ===")
    return total_bytes


def archive_workspace(apply: bool):
    if not WORKSPACE.exists():
        return 0
    folders = [d for d in WORKSPACE.iterdir() if d.is_dir()]
    cands = []
    for d in folders:
        if d.name in EXCLUDE_NAMES:
            log(f"[workspace] PULANDO (ativo/excluido): {d.name}")
            continue
        if age_days(d) < WORKSPACE_MIN_AGE:
            continue
        cands.append(d)
    cands.sort(key=lambda d: dir_size(d), reverse=True)
    total_bytes = 0
    total_n = 0
    for d in cands:
        sz = dir_size(d)
        if not apply:
            log(f"   DRY-RUN zip+subiria+apagaria: {d.name}/ ({human(sz)}, {age_days(d):.0f}d)")
            total_bytes += sz
            total_n += 1
            continue
        if free_bytes() < sz * 2:
            log(f"   PULANDO {d.name}: pouco espaco pra zip (precisa {human(sz)})")
            continue
        zip_path = TMP / f"maia-ws-{d.name}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for f in d.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(WORKSPACE))
            zip_size = zip_path.stat().st_size
            info = upload_file(str(zip_path), "workspace")
            if int(info.get("size") or 0) != zip_size:
                log(f"   MISMATCH zip {d.name} — NAO apago. (local={zip_size} drive={info.get('size')})")
                zip_path.unlink(missing_ok=True)
                continue
            record({"ts": datetime.now(timezone.utc).isoformat(), "tipo": "workspace_zip",
                    "local": str(d), "size": sz, "zip_size": zip_size,
                    "drive_id": info["id"], "drive_link": info["link"]})
            shutil.rmtree(d)
            zip_path.unlink(missing_ok=True)
            log(f"   OK arquivado+apagado: {d.name}/ ({human(sz)}) -> {info['link']}")
            total_bytes += sz
            total_n += 1
        except Exception as e:
            log(f"   ERRO workspace {d.name}: {e} — mantendo pasta")
            zip_path.unlink(missing_ok=True)
    log(f"=== WORKSPACE: {total_n} pastas, {human(total_bytes)} {'liberados' if apply else '(dry-run)'} ===")
    return total_bytes


def main():
    apply = "--apply" in sys.argv
    do_ws = "--workspace" in sys.argv
    log(f"### archive_to_drive iniciado (apply={apply}, workspace={do_ws}) ###")
    log(f"Disco antes: {human(free_bytes())} livre")
    archive_files(apply)
    if do_ws:
        archive_workspace(apply)
    log(f"Disco depois: {human(free_bytes())} livre")
    log("### fim ###")


if __name__ == "__main__":
    main()
