"""Transcricao hibrida de audio (faster-whisper local OU Groq/OpenAI Whisper API).

API publica:
    transcribe(audio_path, provider="auto", language="pt", word_timestamps=False) -> dict | str

Retorno:
    - Se word_timestamps=False (default): retorna str com texto puro.
    - Se word_timestamps=True: retorna dict {text, language, duration, words:[{word,start,end}], _provider, _elapsed_s}

Providers:
    - "local": faster-whisper rodando na CPU da VPS (modelo medium int8, ~1.5GB disco, ~1.5GB RAM ativa)
    - "api":   Groq Whisper API (whisper-large-v3-turbo) com fallback OpenAI (whisper-1)
    - "auto":  le ENV WHISPER_DEFAULT_PROVIDER (default "local"). Fallback automatico pra api se local falhar.

Migracao 2026-05-27: provider="api" agora usa Groq como primario (free tier 14.4k min/dia)
com fallback automatico OpenAI se Groq retornar erro. Custo Groq = 0.

Custo:
    - local:        0 (CPU VPS)
    - api (groq):   0 (free tier)
    - api (openai): USD 0.006/min (so se Groq quebrar)

Logging:
    Todo call gera log estruturado:
        whisper provider=<local|api_groq|api_openai> duration_s=<X> elapsed_s=<Y> cost_usd=<Z> file=<basename>
    Loga em /opt/MAIA/logs/whisper.log (alem do logger do caller).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Union

import requests

# ---------- Config ----------
ENV_FILE = Path("/opt/MAIA/bot/.env")
MODEL_CACHE = "/opt/MAIA/.cache/whisper-models"
DEFAULT_MODEL = os.environ.get("WHISPER_LOCAL_MODEL", "medium")
DEFAULT_PROVIDER = os.environ.get("WHISPER_DEFAULT_PROVIDER", "local")
WHISPER_LOG = Path("/opt/MAIA/logs/whisper.log")
WHISPER_LOG.parent.mkdir(parents=True, exist_ok=True)

# Modelo local carregado lazy + cacheado em modulo (1.5GB RAM)
_LOCAL_MODEL = None
_LOCAL_MODEL_NAME: Optional[str] = None

log = logging.getLogger("whisper_transcribe")


# ---------- ENV helpers ----------
def _load_env_key(var_name: str) -> str:
    if os.environ.get(var_name):
        return os.environ[var_name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == var_name:
                return v.strip().strip('"').strip("'")
    return ""


def _load_openai_key() -> str:
    return _load_env_key("OPENAI_API_KEY")


def _load_groq_key() -> str:
    return _load_env_key("GROQ_API_KEY")


# ---------- Logging unificado ----------
def _log_call(provider: str, audio_path: Path, audio_duration_s: float,
              elapsed_s: float, ok: bool, error: str = "") -> None:
    cost = 0.0
    # OpenAI cobra USD 0.006/min. Groq (free tier) e local sao gratis.
    if provider in ("api", "api_openai") and audio_duration_s > 0:
        cost = round((audio_duration_s / 60.0) * 0.006, 6)
    line = (
        f"{time.strftime('%Y-%m-%dT%H:%M:%S')} "
        f"provider={provider} "
        f"duration_s={audio_duration_s:.1f} "
        f"elapsed_s={elapsed_s:.1f} "
        f"cost_usd={cost:.6f} "
        f"ok={int(ok)} "
        f"file={audio_path.name}"
    )
    if error:
        line += f' error="{error[:200]}"'
    try:
        with WHISPER_LOG.open("a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    log.info(line)

    # Monitor RAM: warning se >6.5GB usado
    try:
        with open("/proc/meminfo") as fh:
            meminfo = dict(
                (l.split(":")[0], int(l.split()[1])) for l in fh if ":" in l
            )
        total_kb = meminfo.get("MemTotal", 0)
        avail_kb = meminfo.get("MemAvailable", 0)
        used_gb = (total_kb - avail_kb) / 1024 / 1024
        if used_gb > 6.5:
            warn = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} WARNING ram_used_gb={used_gb:.2f} (>6.5GB threshold)"
            with WHISPER_LOG.open("a") as fh:
                fh.write(warn + "\n")
            log.warning(warn)
    except Exception:
        pass


# ---------- ffprobe duration ----------
def _audio_duration(audio_path: Path) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return float(out)
    except Exception:
        return 0.0


# ---------- Local provider ----------
def _get_local_model(model_name: str):
    """Lazy load + cache do modelo. Reaproveita instancia entre chamadas."""
    global _LOCAL_MODEL, _LOCAL_MODEL_NAME
    if _LOCAL_MODEL is not None and _LOCAL_MODEL_NAME == model_name:
        return _LOCAL_MODEL
    from faster_whisper import WhisperModel
    t0 = time.time()
    _LOCAL_MODEL = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        download_root=MODEL_CACHE,
    )
    _LOCAL_MODEL_NAME = model_name
    log.info(f"local model '{model_name}' loaded in {time.time()-t0:.1f}s")
    return _LOCAL_MODEL


def _transcribe_local(audio_path: Path, language: str, word_timestamps: bool,
                      model_name: str = DEFAULT_MODEL) -> dict:
    model = _get_local_model(model_name)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=word_timestamps,
        vad_filter=False,
    )
    text_parts = []
    words = []
    for seg in segments_iter:
        text_parts.append(seg.text)
        if word_timestamps and seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word,
                    "start": float(w.start) if w.start is not None else 0.0,
                    "end": float(w.end) if w.end is not None else 0.0,
                })
    text = "".join(text_parts).strip()
    return {
        "text": text,
        "language": info.language,
        "duration": float(info.duration),
        "words": words,
        "_provider": f"local_faster-whisper_{model_name}",
    }


# ---------- API providers ----------
def _mime_for(audio_path: Path) -> str:
    sfx = audio_path.suffix.lower()
    if sfx in (".ogg", ".oga"):
        return "audio/ogg"
    if sfx == ".mp3":
        return "audio/mpeg"
    if sfx == ".wav":
        return "audio/wav"
    if sfx in (".m4a", ".mp4"):
        return "audio/mp4"
    return "application/octet-stream"


def _transcribe_groq(audio_path: Path, language: str, word_timestamps: bool,
                     api_key: str) -> dict:
    """Transcricao via Groq Whisper (whisper-large-v3-turbo). Free tier 14.4k min/dia.

    Headers User-Agent/Accept obrigatorios — sem isso o Cloudflare do Groq
    bloqueia com erro 1010.
    """
    data = {"model": "whisper-large-v3-turbo", "language": language,
            "response_format": "verbose_json"}
    if word_timestamps:
        data["timestamp_granularities[]"] = "word"

    mime = _mime_for(audio_path)
    # Groq nao aceita extensao .oga (Telegram entrega assim). Normaliza pra .ogg
    # — o conteudo binario e Ogg Opus, so o nome muda no multipart.
    upload_name = audio_path.name
    if audio_path.suffix.lower() == ".oga":
        upload_name = audio_path.stem + ".ogg"
    with audio_path.open("rb") as f:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "curl/7.88.1",
                "Accept": "*/*",
            },
            files={"file": (upload_name, f, mime)},
            data=data,
            timeout=300,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Groq Whisper HTTP {r.status_code}: {r.text[:300]}")

    payload = r.json()
    return {
        "text": payload.get("text", "").strip(),
        "language": payload.get("language", language),
        "duration": float(payload.get("duration", 0.0) or 0.0),
        "words": payload.get("words", []) if word_timestamps else [],
        "_provider": "api_groq_whisper-large-v3-turbo",
    }


def _transcribe_openai(audio_path: Path, language: str, word_timestamps: bool,
                       api_key: str) -> dict:
    data = {"model": "whisper-1", "language": language}
    if word_timestamps:
        data["response_format"] = "verbose_json"
        data["timestamp_granularities[]"] = "word"

    mime = _mime_for(audio_path)
    with audio_path.open("rb") as f:
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_path.name, f, mime)},
            data=data,
            timeout=300,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Whisper API HTTP {r.status_code}: {r.text[:300]}")

    if word_timestamps:
        payload = r.json()
        return {
            "text": payload.get("text", "").strip(),
            "language": payload.get("language", language),
            "duration": float(payload.get("duration", 0.0) or 0.0),
            "words": payload.get("words", []),
            "_provider": "api_openai_whisper-1",
        }
    return {
        "text": r.json().get("text", "").strip(),
        "language": language,
        "duration": 0.0,
        "words": [],
        "_provider": "api_openai_whisper-1",
    }


def _openai_fallback_enabled() -> bool:
    """Corte de custo 2026-06-09: OpenAI (pago) so e usado como fallback se
    WHISPER_OPENAI_FALLBACK estiver explicitamente ligado (1/true/yes).
    Default = desligado. Assim, se Groq cair, o caller cai pra LOCAL (free),
    nunca silenciosamente pra OpenAI cobrado.
    """
    val = _load_env_key("WHISPER_OPENAI_FALLBACK").strip().lower()
    return val in ("1", "true", "yes", "on")


def _transcribe_api(audio_path: Path, language: str, word_timestamps: bool,
                    api_key_openai: str = "") -> dict:
    """Tenta Groq primeiro (gratis). Se Groq falhar, so cai pra OpenAI (pago)
    quando WHISPER_OPENAI_FALLBACK estiver ligado. Caso contrario propaga o erro
    do Groq pro caller, que entao tenta whisper LOCAL (free).
    """
    groq_key = _load_groq_key()
    if groq_key:
        try:
            return _transcribe_groq(audio_path, language, word_timestamps, groq_key)
        except Exception as e:
            log.warning(f"groq failed: {e}")
            if not (api_key_openai and _openai_fallback_enabled()):
                # Sem OpenAI pago como fallback: deixa o caller cair pra local.
                raise
            log.warning("groq failed, using PAID openai fallback (WHISPER_OPENAI_FALLBACK=on)")
            return _transcribe_openai(audio_path, language, word_timestamps, api_key_openai)
    # Sem chave Groq: so usa OpenAI se explicitamente habilitado.
    if api_key_openai and _openai_fallback_enabled():
        return _transcribe_openai(audio_path, language, word_timestamps, api_key_openai)
    raise RuntimeError("GROQ_API_KEY ausente e OpenAI fallback desabilitado (WHISPER_OPENAI_FALLBACK=0)")


# ---------- Public API ----------
def transcribe(
    audio_path: Union[str, Path],
    provider: str = "auto",
    language: str = "pt",
    word_timestamps: bool = False,
    model_name: str = DEFAULT_MODEL,
    api_fallback: bool = True,
) -> Union[str, dict]:
    """Transcreve audio. Retorna str (texto) ou dict (se word_timestamps=True)."""
    audio_path = Path(audio_path).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"audio nao encontrado: {audio_path}")

    if provider == "auto":
        provider = DEFAULT_PROVIDER

    audio_dur = _audio_duration(audio_path)
    t0 = time.time()
    used = provider
    data = None
    error = ""

    try:
        if provider == "local":
            try:
                data = _transcribe_local(audio_path, language, word_timestamps, model_name)
            except Exception as e:
                error = f"local_failed: {e}"
                log.error(error)
                if api_fallback:
                    if _load_groq_key() or _load_openai_key():
                        log.warning("fallback: api")
                        used = "api"
                        data = _transcribe_api(
                            audio_path, language, word_timestamps,
                            api_key_openai=_load_openai_key(),
                        )
                    else:
                        raise
                else:
                    raise
        elif provider == "api":
            if not (_load_groq_key() or _load_openai_key()):
                raise RuntimeError("GROQ_API_KEY e OPENAI_API_KEY ambas ausentes")
            try:
                data = _transcribe_api(
                    audio_path, language, word_timestamps,
                    api_key_openai=_load_openai_key(),
                )
            except Exception as e:
                # Corte de custo 2026-06-09: se Groq cair e OpenAI estiver
                # desligado, cai pra whisper LOCAL (free) ao inves de falhar.
                error = f"api_failed: {e}"
                log.error(error)
                if api_fallback:
                    log.warning("fallback: local (api falhou, evitando OpenAI pago)")
                    used = "local"
                    data = _transcribe_local(audio_path, language, word_timestamps, model_name)
                else:
                    raise
            # marca provider real no log (groq ou openai ou local)
            if data.get("_provider", "").startswith("api_groq"):
                used = "api_groq"
            elif data.get("_provider", "").startswith("api_openai"):
                used = "api_openai"
            elif data.get("_provider", "").startswith("local"):
                used = "local"
        else:
            raise ValueError(f"provider invalido: {provider}")
    except Exception as e:
        elapsed = time.time() - t0
        _log_call(used, audio_path, audio_dur, elapsed, ok=False, error=str(e))
        raise

    elapsed = time.time() - t0
    data["_elapsed_s"] = round(elapsed, 2)
    _log_call(used, audio_path, audio_dur, elapsed, ok=True, error=error)

    if word_timestamps:
        return data
    return data["text"]


# ---------- CLI ----------
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Transcricao hibrida (local/api)")
    p.add_argument("audio", help="caminho do audio")
    p.add_argument("--provider", default="auto", choices=["auto", "local", "api"])
    p.add_argument("--language", default="pt")
    p.add_argument("--words", action="store_true", help="word-level timestamps (output JSON)")
    p.add_argument("--model", default=DEFAULT_MODEL, help="tiny/base/small/medium/large-v3")
    p.add_argument("--out", help="salvar output em arquivo (json se --words, txt se nao)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = transcribe(
        args.audio,
        provider=args.provider,
        language=args.language,
        word_timestamps=args.words,
        model_name=args.model,
    )
    if args.words:
        out = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        out = result
    if args.out:
        Path(args.out).write_text(out)
        print(f"saved: {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    _cli()
