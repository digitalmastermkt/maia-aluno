#!/usr/bin/env python3
"""
image_composite.py - Compositing de retrato sobre background de slide.

Uso tipico (pipeline em 3 etapas):
  1. Gemini edit -> portrait com fundo solido (teal #3A9E9C). Boa qualidade do rosto.
  2. Imagen text2img -> background do slide (sem pessoa). Layout/cores/textos.
  3. Esta funcao -> remove fundo do portrait (rembg) e cola sobre o background.

API publica:
  composite_portrait_on_background(
      portrait_path, background_path, output_path,
      position="right_center", size_pct=45,
      feather=2, shadow=True
  ) -> Path

position: "right_center" | "left_center" | "center" | "bottom_right" | "bottom_left"
         | (x, y) tupla absoluta em px do canto superior-esquerdo da pessoa colada
size_pct: percentual da maior dimensao do background que a pessoa deve ocupar
          (default 45 = pessoa ocupa 45% da altura do slide)
feather: blur leve no alpha pra suavizar borda (default 2 px)
shadow: aplica drop-shadow sutil sob a pessoa (default True)

Background remover usado:
  - rembg + onnxruntime (modelo padrao u2net). Modelo 'isnet-general-use' tambem
    funciona bem; trocavel via env REMBG_MODEL.

Fallback: se rembg falhar ou nao estiver instalado, cai para chromakey/threshold
sobre o teal #3A9E9C (cor de fundo solida controlada pelo prompt do Gemini).
"""
from __future__ import annotations

import os
import sys
import io
import logging
from pathlib import Path
from typing import Tuple, Union

# Garante venv local (rembg, onnxruntime, PIL) se chamado por interpretador externo
_VENV_SITE = Path('/opt/MAIA/bot/venv/lib/python3.12/site-packages')
if _VENV_SITE.is_dir() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))

from PIL import Image, ImageFilter, ImageChops

log = logging.getLogger(__name__)

REMBG_MODEL = os.environ.get('REMBG_MODEL', 'u2net')

# Cor de fundo padrao gerada pelo Gemini quando pedimos "teal background"
DEFAULT_CHROMA = (0x3A, 0x9E, 0x9C)
CHROMA_TOLERANCE = 55  # 0..255 distancia maxima por canal


# ---------------------------------------------------------------------------
# Background removal
# ---------------------------------------------------------------------------

_rembg_session = None


def _get_rembg_session():
    global _rembg_session
    if _rembg_session is not None:
        return _rembg_session
    try:
        from rembg import new_session
        _rembg_session = new_session(REMBG_MODEL)
        log.info('rembg session criada (modelo=%s)', REMBG_MODEL)
        return _rembg_session
    except Exception as e:
        log.warning('rembg indisponivel: %s', e)
        return None


def remove_background_rembg(img: Image.Image) -> Image.Image:
    """Remove fundo via rembg/u2net. Retorna RGBA."""
    from rembg import remove
    sess = _get_rembg_session()
    out = remove(img, session=sess) if sess else remove(img)
    if out.mode != 'RGBA':
        out = out.convert('RGBA')
    return out


def remove_background_chromakey(
    img: Image.Image,
    chroma: Tuple[int, int, int] = DEFAULT_CHROMA,
    tolerance: int = CHROMA_TOLERANCE,
) -> Image.Image:
    """Fallback: zera alpha onde a cor bate o chroma de referencia."""
    img = img.convert('RGBA')
    px = img.load()
    cr, cg, cb = chroma
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if abs(r - cr) <= tolerance and abs(g - cg) <= tolerance and abs(b - cb) <= tolerance:
                px[x, y] = (r, g, b, 0)
    return img


def remove_background(img: Image.Image, prefer_rembg: bool = True) -> Image.Image:
    """Tenta rembg, cai para chromakey se indisponivel."""
    if prefer_rembg:
        try:
            return remove_background_rembg(img)
        except Exception as e:
            log.warning('rembg falhou (%s); usando chromakey', e)
    return remove_background_chromakey(img)


# ---------------------------------------------------------------------------
# Trim / refine alpha
# ---------------------------------------------------------------------------

def trim_to_alpha(img: Image.Image) -> Image.Image:
    """Recorta bbox da regiao com alpha > 0 (remove margem transparente)."""
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


def feather_alpha(img: Image.Image, radius: float = 2.0) -> Image.Image:
    """Suaviza a borda do alpha com gaussian blur leve."""
    if radius <= 0:
        return img
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    r, g, b, a = img.split()
    a = a.filter(ImageFilter.GaussianBlur(radius=radius))
    return Image.merge('RGBA', (r, g, b, a))


def make_drop_shadow(
    person: Image.Image,
    offset: Tuple[int, int] = (12, 18),
    blur: float = 18,
    opacity: float = 0.35,
) -> Tuple[Image.Image, Tuple[int, int]]:
    """Cria sombra a partir do alpha. Retorna (img_sombra, offset_relativo)."""
    if person.mode != 'RGBA':
        person = person.convert('RGBA')
    alpha = person.split()[-1]
    pad = int(blur * 2 + max(abs(offset[0]), abs(offset[1])))
    w, h = person.size
    canvas = Image.new('RGBA', (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    shadow_alpha = alpha.point(lambda v: int(v * opacity))
    shadow = Image.new('RGBA', person.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_alpha)
    canvas.paste(shadow, (pad + offset[0], pad + offset[1]), shadow)
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=blur))
    return canvas, (-pad, -pad)


# ---------------------------------------------------------------------------
# Posicionamento
# ---------------------------------------------------------------------------

def _resolve_position(
    position: Union[str, Tuple[int, int]],
    bg_size: Tuple[int, int],
    person_size: Tuple[int, int],
) -> Tuple[int, int]:
    bw, bh = bg_size
    pw, ph = person_size
    if isinstance(position, tuple):
        return position

    margin_x = int(bw * 0.04)
    margin_y = int(bh * 0.02)

    presets = {
        'right_center': (bw - pw - margin_x, (bh - ph) // 2),
        'left_center':  (margin_x,            (bh - ph) // 2),
        'center':       ((bw - pw) // 2,      (bh - ph) // 2),
        'bottom_right': (bw - pw - margin_x,  bh - ph - margin_y),
        'bottom_left':  (margin_x,            bh - ph - margin_y),
        'bottom_center':((bw - pw) // 2,      bh - ph - margin_y),
        'top_right':    (bw - pw - margin_x,  margin_y),
        'top_left':     (margin_x,            margin_y),
    }
    if position not in presets:
        raise ValueError(f'position invalida: {position!r}. Validas: {list(presets)}')
    return presets[position]


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def composite_portrait_on_background(
    portrait_path: Union[str, Path],
    background_path: Union[str, Path],
    output_path: Union[str, Path],
    position: Union[str, Tuple[int, int]] = 'right_center',
    size_pct: float = 45.0,
    feather: float = 2.0,
    shadow: bool = True,
    prefer_rembg: bool = True,
) -> Path:
    """Faz o pipeline completo. Retorna Path do PNG final."""
    portrait_path = Path(portrait_path)
    background_path = Path(background_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    log.info('composite: portrait=%s bg=%s -> %s (pos=%s, size_pct=%s)',
             portrait_path.name, background_path.name, output_path.name, position, size_pct)

    portrait = Image.open(portrait_path).convert('RGBA')
    background = Image.open(background_path).convert('RGBA')

    # 1. remove fundo
    person = remove_background(portrait, prefer_rembg=prefer_rembg)
    # 2. crop bbox (remove ar)
    person = trim_to_alpha(person)
    # 3. feather suave
    person = feather_alpha(person, radius=feather)

    # 4. resize: pessoa ocupa size_pct da ALTURA do background
    bw, bh = background.size
    target_h = int(bh * (size_pct / 100.0))
    scale = target_h / person.height
    target_w = max(1, int(person.width * scale))
    person = person.resize((target_w, target_h), Image.LANCZOS)

    # 5. posicao
    pos = _resolve_position(position, (bw, bh), person.size)

    # 6. compositing
    canvas = background.copy()
    if shadow:
        sh_img, sh_offset = make_drop_shadow(person)
        canvas.alpha_composite(sh_img, dest=(pos[0] + sh_offset[0], pos[1] + sh_offset[1]))
    canvas.alpha_composite(person, dest=pos)

    # 7. salvar
    canvas.convert('RGB').save(output_path, 'PNG', optimize=True)
    log.info('composite ok -> %s (%dx%d)', output_path, *canvas.size)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    p = argparse.ArgumentParser(description='Composita um retrato sobre um background.')
    p.add_argument('portrait', help='PNG/JPG do retrato (com fundo solido).')
    p.add_argument('background', help='PNG do background do slide.')
    p.add_argument('output', help='Caminho do PNG final.')
    p.add_argument('--position', default='right_center',
                   help='Posicao preset ou x,y (ex: 100,200).')
    p.add_argument('--size-pct', type=float, default=45.0,
                   help='%% da altura do background que a pessoa deve ocupar.')
    p.add_argument('--feather', type=float, default=2.0)
    p.add_argument('--no-shadow', action='store_true')
    p.add_argument('--no-rembg', action='store_true', help='Forca chromakey teal.')
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    pos = args.position
    if ',' in pos:
        x, y = pos.split(',', 1)
        pos = (int(x), int(y))

    out = composite_portrait_on_background(
        args.portrait, args.background, args.output,
        position=pos, size_pct=args.size_pct,
        feather=args.feather, shadow=not args.no_shadow,
        prefer_rembg=not args.no_rembg,
    )
    print(out)


if __name__ == '__main__':
    _cli()
