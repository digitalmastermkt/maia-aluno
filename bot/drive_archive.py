#!/usr/bin/env python3
"""
drive_archive.py — Camada 2: arquiva midia no Google Drive (scope drive.file).

Estrutura no Drive:  NAIA-Arquivo / <categoria> / <YYYY-MM> / <arquivo>
Token: .drive_token.json (gerado por drive_auth_finish.py). Refresh automatico.

Uso como modulo:
    from drive_archive import upload_file
    info = upload_file('/caminho/video.mp4', categoria='workspace')
    # -> {'id':..., 'link':..., 'size':..., 'name':...}

Uso CLI:
    drive_archive.py test
    drive_archive.py upload <arquivo_local> <categoria>
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from datetime import datetime, timezone

BOT_DIR = Path("/opt/MAIA/bot")
TOKEN_PATH = BOT_DIR / ".drive_token.json"
ROOT_FOLDER_NAME = "NAIA-Arquivo"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_venv_sp = "/opt/MAIA/bot/venv/lib/python3.12/site-packages"
if _venv_sp not in sys.path:
    sys.path.insert(0, _venv_sp)

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

_FOLDER_MIME = "application/vnd.google-apps.folder"
_folder_cache: dict = {}


def _load_service():
    if not TOKEN_PATH.exists():
        raise RuntimeError(f"Token nao encontrado: {TOKEN_PATH}. Rode o OAuth do Drive primeiro.")
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
        creds.refresh(Request())
        data["token"] = creds.token
        data["expiry"] = creds.expiry.isoformat() if creds.expiry else None
        TOKEN_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(TOKEN_PATH, 0o600)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _ensure_folder(service, name: str, parent_id: str | None) -> str:
    """Acha ou cria uma pasta com 'name' dentro de parent_id. Cacheado."""
    key = f"{parent_id or 'root'}/{name}"
    if key in _folder_cache:
        return _folder_cache[key]
    safe = name.replace("'", "\\'")
    q = f"name='{safe}' and mimeType='{_FOLDER_MIME}' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    found = service.files().list(q=q, spaces="drive", fields="files(id,name)").execute().get("files", [])
    if found:
        fid = found[0]["id"]
    else:
        meta = {"name": name, "mimeType": _FOLDER_MIME}
        if parent_id:
            meta["parents"] = [parent_id]
        fid = service.files().create(body=meta, fields="id").execute()["id"]
    _folder_cache[key] = fid
    return fid


def _archive_folder_id(service, categoria: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    root = _ensure_folder(service, ROOT_FOLDER_NAME, None)
    cat = _ensure_folder(service, categoria or "outros", root)
    return _ensure_folder(service, when.strftime("%Y-%m"), cat)


def upload_file(local_path, categoria: str = "outros", when: datetime | None = None) -> dict:
    """Sobe um arquivo pro Drive sob NAIA-Arquivo/<categoria>/<YYYY-MM>/.
    Retorna {'id','link','size','name'}. Levanta excecao em falha."""
    p = Path(local_path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    service = _load_service()
    folder_id = _archive_folder_id(service, categoria, when)
    media = MediaFileUpload(str(p), resumable=True, chunksize=8 * 1024 * 1024)
    meta = {"name": p.name, "parents": [folder_id]}
    req = service.files().create(body=meta, media_body=media,
                                 fields="id,name,size,webViewLink")
    resp = None
    while resp is None:
        _status, resp = req.next_chunk()
    return {
        "id": resp.get("id"),
        "link": resp.get("webViewLink"),
        "size": int(resp.get("size") or p.stat().st_size),
        "name": resp.get("name"),
    }


def _cli():
    if len(sys.argv) < 2:
        print("Uso: drive_archive.py test | upload <arquivo> <categoria>", file=sys.stderr)
        return 1
    cmd = sys.argv[1]
    if cmd == "test":
        service = _load_service()
        root = _ensure_folder(service, ROOT_FOLDER_NAME, None)
        about = service.about().get(fields="storageQuota,user").execute()
        q = about.get("storageQuota", {})
        used = int(q.get("usage") or 0) / 1e9
        limit = int(q.get("limit") or 0) / 1e9 if q.get("limit") else None
        print(f"OK: Drive acessivel. Pasta raiz id={root}")
        print(f"Usuario: {about.get('user', {}).get('emailAddress')}")
        if limit:
            print(f"Quota: {used:.2f} GB usados de {limit:.2f} GB")
        else:
            print(f"Quota: {used:.2f} GB usados (limite nao reportado)")
        return 0
    if cmd == "upload":
        if len(sys.argv) < 4:
            print("Uso: drive_archive.py upload <arquivo> <categoria>", file=sys.stderr)
            return 1
        info = upload_file(sys.argv[2], sys.argv[3])
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0
    print(f"Comando desconhecido: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
