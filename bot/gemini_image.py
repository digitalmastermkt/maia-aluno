#!/usr/bin/env python3
"""
gemini_image.py - Geracao e edicao de imagens via Google Gemini/Imagen API
SDK: google-genai (unificado)

Geracao text-to-image (generate_image):
    Modelos: imagen-4.0-generate-001, imagen-4.0-fast-generate-001,
             imagen-4.0-ultra-generate-001, imagen-3.0-generate-002

Edicao image-to-image / com referencia (edit_image):
    Modelos: gemini-2.5-flash-image (estavel, "Nano Banana"),
             gemini-3-pro-image-preview, gemini-3.1-flash-image-preview
    Observacao: Imagen capability (imagen-3.0-capability-001) requer Vertex AI
    com service account; via Gemini Developer API key, usamos gemini multimodal.

Uso:
    from gemini_image import generate_image, edit_image
    paths = generate_image(prompt="...", model="imagen-4.0-generate-001", ...)
    paths = edit_image(reference_image_path="/abs/path.jpg", prompt="...",
                       model="gemini-2.5-flash-image", ...)
"""
import os
import base64
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

# Aspect ratios suportados pelo Imagen 4: 1:1, 3:4, 4:3, 9:16, 16:9
VALID_ASPECTS = {"1:1", "3:4", "4:3", "9:16", "16:9"}
DEFAULT_OUTPUT = Path("/opt/MAIA/bot/images/outgoing")
DEFAULT_EDIT_MODEL = "gemini-2.5-flash-image"


def _get_client(api_key: Optional[str] = None) -> genai.Client:
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY nao configurada")
    return genai.Client(api_key=key)


def generate_image(
    prompt: str,
    model: str = "imagen-4.0-generate-001",
    aspect_ratio: str = "1:1",
    n: int = 1,
    output_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    file_prefix: Optional[str] = None,
) -> List[Path]:
    """Gera N imagens via Imagen e salva como PNG.

    Args:
        prompt: descricao em linguagem natural (qualquer idioma; portugues ok)
        model: id do modelo Imagen (default: imagen-4.0-generate-001)
        aspect_ratio: "1:1", "3:4", "4:3", "9:16", "16:9"
        n: numero de imagens (1-4 para imagen-4, 1-8 para imagen-3)
        output_dir: diretorio destino (default: /opt/MAIA/bot/images/outgoing)
        api_key: override opcional da GEMINI_API_KEY
        file_prefix: prefixo do nome do arquivo (default: timestamp)

    Returns:
        Lista de Paths das imagens PNG geradas.

    Raises:
        RuntimeError, ValueError em casos de erro/config invalida.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt vazio")
    if aspect_ratio not in VALID_ASPECTS:
        raise ValueError(f"aspect_ratio invalido: {aspect_ratio}. validos: {VALID_ASPECTS}")
    if n < 1 or n > 8:
        raise ValueError("n deve ser 1..8")

    out = Path(output_dir) if output_dir else DEFAULT_OUTPUT
    out.mkdir(parents=True, exist_ok=True)

    client = _get_client(api_key)
    log.info(f"gemini_image: model={model} ratio={aspect_ratio} n={n} prompt={prompt[:80]}")

    config = types.GenerateImagesConfig(
        number_of_images=n,
        aspect_ratio=aspect_ratio,
        # output_mime_type default = image/png
    )

    response = client.models.generate_images(
        model=model,
        prompt=prompt,
        config=config,
    )

    if not response.generated_images:
        raise RuntimeError(f"Imagen retornou 0 imagens. response={response}")

    prefix = file_prefix or datetime.now().strftime("%Y%m%d_%H%M%S")
    saved: List[Path] = []
    for idx, gen in enumerate(response.generated_images):
        if not gen.image or not gen.image.image_bytes:
            log.warning(f"imagem #{idx} sem bytes, skip")
            continue
        path = out / f"{prefix}_{idx+1}.png"
        path.write_bytes(gen.image.image_bytes)
        saved.append(path)
        log.info(f"gemini_image: salvou {path} ({len(gen.image.image_bytes)} bytes)")

    if not saved:
        raise RuntimeError("nenhuma imagem foi salva (todas vazias)")
    return saved


def edit_image(
    reference_image_path: str,
    prompt: str,
    model: str = DEFAULT_EDIT_MODEL,
    output_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    file_prefix: Optional[str] = None,
    aspect_ratio: Optional[str] = None,  # aceito por compat, hoje ignorado pelo gemini-image
) -> List[Path]:
    """Edita uma imagem de referencia mantendo a identidade do sujeito.

    Usa Gemini multimodal (gemini-2.5-flash-image / "Nano Banana") por padrao,
    pois Imagen capability (edit_image em imagen-3.0-capability-001) requer
    Vertex AI client com service account, indisponivel via Gemini API key.

    Args:
        reference_image_path: caminho absoluto da foto de referencia (jpg/png)
        prompt: descricao da edicao desejada (manter feicoes, mudar roupa, etc)
        model: id do modelo de edicao (default: gemini-2.5-flash-image)
        output_dir: diretorio destino (default: /opt/MAIA/bot/images/outgoing)
        api_key: override opcional da GEMINI_API_KEY
        file_prefix: prefixo do nome do arquivo (default: timestamp)
        aspect_ratio: hoje nao aplicado pelo gemini-image (saida segue input);
                     reservado para futuros modelos

    Returns:
        Lista de Paths das imagens editadas (PNG).

    Raises:
        FileNotFoundError, ValueError, RuntimeError
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt vazio")
    ref = Path(reference_image_path)
    if not ref.is_file():
        raise FileNotFoundError(f"reference_image_path nao existe: {ref}")

    out = Path(output_dir) if output_dir else DEFAULT_OUTPUT
    out.mkdir(parents=True, exist_ok=True)

    client = _get_client(api_key)
    log.info(f"edit_image: model={model} ref={ref.name} prompt={prompt[:80]}")

    # Detecta mime por extensao
    ext = ref.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"

    ref_bytes = ref.read_bytes()
    ref_part = types.Part.from_bytes(data=ref_bytes, mime_type=mime)

    response = client.models.generate_content(
        model=model,
        contents=[prompt, ref_part],
    )

    if not response.candidates:
        raise RuntimeError(f"edit_image: 0 candidates. response={response}")

    prefix = file_prefix or datetime.now().strftime("%Y%m%d_%H%M%S_edit")
    saved: List[Path] = []
    idx = 0
    text_feedback: List[str] = []
    for cand in response.candidates:
        if not cand.content or not cand.content.parts:
            continue
        for part in cand.content.parts:
            if getattr(part, "text", None):
                text_feedback.append(part.text)
                continue
            inline = getattr(part, "inline_data", None)
            if not inline or not inline.data:
                continue
            data = inline.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            idx += 1
            path = out / f"{prefix}_{idx}.png"
            path.write_bytes(data)
            saved.append(path)
            log.info(f"edit_image: salvou {path} ({len(data)} bytes, mime={inline.mime_type})")

    if not saved:
        msg = " | ".join(text_feedback)[:400] or "sem dados"
        raise RuntimeError(f"edit_image: nenhuma imagem gerada. feedback={msg}")
    return saved


def edit_image_multi(
    reference_image_paths: List[str],
    prompt: str,
    model: str = DEFAULT_EDIT_MODEL,
    output_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    file_prefix: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
) -> List[Path]:
    """Edita/gera imagem usando MULTIPLAS referencias (preserva identidade).

    Passa todas as referencias como Parts da chamada generate_content.
    Gemini multimodal usa todas como contexto visual do mesmo sujeito ->
    fidelidade facial maior do que com uma unica referencia.

    Args:
        reference_image_paths: lista de caminhos absolutos (jpg/png/webp).
                              Recomenda-se 3-8 fotos do mesmo sujeito em
                              angulos variados (frente, perfil, 3/4).
        prompt: descricao da edicao desejada.
        model: id do modelo (default gemini-2.5-flash-image).
        output_dir: diretorio destino.
        api_key: override.
        file_prefix: prefixo do nome.
        aspect_ratio: hoje nao aplicado pelo gemini-image (saida segue prompt).

    Returns:
        Lista de Paths das imagens geradas (PNG).

    Raises:
        FileNotFoundError, ValueError, RuntimeError
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt vazio")
    if not reference_image_paths:
        raise ValueError("reference_image_paths vazio")

    refs: List[Path] = []
    for p in reference_image_paths:
        rp = Path(p)
        if not rp.is_file():
            raise FileNotFoundError(f"referencia nao existe: {rp}")
        refs.append(rp)

    out = Path(output_dir) if output_dir else DEFAULT_OUTPUT
    out.mkdir(parents=True, exist_ok=True)

    client = _get_client(api_key)
    log.info(
        f"edit_image_multi: model={model} refs={len(refs)} prompt={prompt[:80]}"
    )

    parts: List = [prompt]
    for rp in refs:
        ext = rp.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
        parts.append(types.Part.from_bytes(data=rp.read_bytes(), mime_type=mime))

    response = client.models.generate_content(
        model=model,
        contents=parts,
    )

    if not response.candidates:
        raise RuntimeError(f"edit_image_multi: 0 candidates. response={response}")

    prefix = file_prefix or datetime.now().strftime("%Y%m%d_%H%M%S_editmulti")
    saved: List[Path] = []
    idx = 0
    text_feedback: List[str] = []
    for cand in response.candidates:
        if not cand.content or not cand.content.parts:
            continue
        for part in cand.content.parts:
            if getattr(part, "text", None):
                text_feedback.append(part.text)
                continue
            inline = getattr(part, "inline_data", None)
            if not inline or not inline.data:
                continue
            data = inline.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            idx += 1
            path = out / f"{prefix}_{idx}.png"
            path.write_bytes(data)
            saved.append(path)
            log.info(
                f"edit_image_multi: salvou {path} ({len(data)} bytes, mime={inline.mime_type})"
            )

    if not saved:
        msg = " | ".join(text_feedback)[:400] or "sem dados"
        raise RuntimeError(f"edit_image_multi: nenhuma imagem gerada. feedback={msg}")
    return saved


if __name__ == "__main__":
    # Teste manual:
    #   python gemini_image.py "prompt"                       -> generate
    #   python gemini_image.py edit /abs/ref.jpg "prompt"     -> edit
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) >= 4 and sys.argv[1] == "edit":
        ref_path = sys.argv[2]
        p = sys.argv[3]
        paths = edit_image(ref_path, p)
        print("OK edit:", paths)
    else:
        p = sys.argv[1] if len(sys.argv) > 1 else "A bright orange tabby cat sitting on a vintage wooden desk, soft window light"
        paths = generate_image(p, n=1, aspect_ratio="1:1")
        print("OK:", paths)
