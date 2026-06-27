#!/usr/bin/env python3
"""
carousel_text.py - Tipografia precisa em slides do carrossel da marca.

Pipeline em 2 etapas:
  1. Imagen gera o FUNDO (layout/cores/diagonal/efeitos) SEM TEXTO.
  2. Esta funcao escreve o texto por cima com fonte real (ortografia 100%).

API publica:
  add_text_to_slide(background_path, output_path, elements) -> str
  add_text_to_slide(background_path, output_path, elements + STANDARD_FOOTER)

Cada elemento e um dict:
  {
    "text": "SEM IA",
    "font": "montserrat_extrabold",   # ver FONT_VARIANTS
    "size": 96,                        # px
    "color": (255, 255, 255),          # RGB
    "position": "top_left",            # preset OU (x, y)
    "opacity": 100,                    # 0-100  (default 100)
    "align": "left",                   # left | center | right (default left)
    "max_width_pct": 45,               # quebra linha automatica (% da largura)
    "rotate": 0,                       # graus, sentido horario (default 0)
    "letter_spacing": 0,               # px extra entre letras (default 0)
    "line_spacing": 1.15,              # multiplicador (default 1.15)
    "margin": None,                    # px de margem nas presets (default 4% w / 2% h)
  }

Posicoes preset:
  top_left | top_center | top_right
  middle_left | center | middle_right
  bottom_left | bottom_center | bottom_right

Fontes em /opt/MAIA/bot/fonts/ (variable fonts):
  Montserrat[wght].ttf            -> regular / bold / extrabold / etc.
  Montserrat-Italic[wght].ttf     -> italic / bold italic
  JetBrainsMono[wght].ttf         -> mono regular / bold

Bootstrap: se faltar alguma font, baixa automaticamente do google/fonts (GitHub).
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Iterable, Tuple, Union

# Garante venv local quando chamado por interpretador externo.
_VENV_SITE = Path('/opt/MAIA/bot/venv/lib/python3.12/site-packages')
if _VENV_SITE.is_dir() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))

from PIL import Image, ImageDraw, ImageFont

# Marca central: handle do rodape vem de /opt/MAIA/brand/brand.json via brand_loader.
# Se a marca ainda nao foi preenchida, footer_handle() retorna "" e o rodape some.
sys.path.insert(0, "/opt/MAIA")
try:
    from brand_loader import footer_handle
except Exception:
    def footer_handle():
        return ""

log = logging.getLogger(__name__)

FONTS_DIR = Path('/opt/MAIA/bot/fonts')

# Mapeia "alias" -> (caminho_da_font, nome_de_variacao_ou_None)
# Variable font usa set_variation_by_name(b'Bold') etc. Para arquivos
# nao-variaveis o segundo elemento e None.
FONT_VARIANTS = {
    'montserrat_regular':    ('Montserrat[wght].ttf',         b'Regular'),
    'montserrat_medium':     ('Montserrat[wght].ttf',         b'Medium'),
    'montserrat_semibold':   ('Montserrat[wght].ttf',         b'SemiBold'),
    'montserrat_bold':       ('Montserrat[wght].ttf',         b'Bold'),
    'montserrat_extrabold':  ('Montserrat[wght].ttf',         b'ExtraBold'),
    'montserrat_black':      ('Montserrat[wght].ttf',         b'Black'),
    'montserrat_italic':     ('Montserrat-Italic[wght].ttf',  b'Italic'),
    'montserrat_bold_italic':('Montserrat-Italic[wght].ttf',  b'Bold Italic'),
    'jetbrains_mono':        ('JetBrainsMono[wght].ttf',      b'Regular'),
    'jetbrains_mono_bold':   ('JetBrainsMono[wght].ttf',      b'Bold'),
}

# URLs do google/fonts no GitHub (raw). Usadas se o arquivo faltar.
FONT_DOWNLOAD_URLS = {
    'Montserrat[wght].ttf':
        'https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf',
    'Montserrat-Italic[wght].ttf':
        'https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat-Italic%5Bwght%5D.ttf',
    'JetBrainsMono[wght].ttf':
        'https://raw.githubusercontent.com/google/fonts/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf',
}

# Footer padrao do carrossel (handle vem do brand_loader). Adicionar ao fim dos elementos.
STANDARD_FOOTER = [
    {
        'text': footer_handle(),
        'font': 'jetbrains_mono',
        'size': 24,
        'color': (255, 255, 255),
        'position': 'bottom_left',
        'opacity': 50,
    },
    {
        'text': 'ARRASTE',
        'font': 'jetbrains_mono',
        'size': 18,
        'color': (255, 255, 255),
        'position': (980, 700),
        'opacity': 50,
        'rotate': 90,
        'letter_spacing': 4,
    },
]

# ---------------------------------------------------------------------------
# Bootstrap das fontes
# ---------------------------------------------------------------------------

def ensure_fonts(fonts_dir: Path = FONTS_DIR) -> None:
    """Baixa fontes faltantes do google/fonts (GitHub raw)."""
    fonts_dir.mkdir(parents=True, exist_ok=True)
    import urllib.request
    for filename, url in FONT_DOWNLOAD_URLS.items():
        target = fonts_dir / filename
        if target.exists() and target.stat().st_size > 1024:
            continue
        log.info('Baixando font %s', filename)
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        target.write_bytes(data)
        log.info('  -> %s (%d bytes)', target, len(data))


# ---------------------------------------------------------------------------
# Cache de fontes
# ---------------------------------------------------------------------------

_font_cache: dict = {}


def _load_font(alias: str, size: int) -> ImageFont.FreeTypeFont:
    if alias not in FONT_VARIANTS:
        raise ValueError(
            f'font alias desconhecido: {alias!r}. '
            f'Disponiveis: {sorted(FONT_VARIANTS)}'
        )
    cache_key = (alias, size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    filename, variation = FONT_VARIANTS[alias]
    path = FONTS_DIR / filename
    if not path.exists():
        ensure_fonts()
    font = ImageFont.truetype(str(path), size)
    if variation is not None:
        try:
            font.set_variation_by_name(variation)
        except Exception as exc:
            log.warning('font %s nao aceitou variation %s: %s', alias, variation, exc)
    _font_cache[cache_key] = font
    return font


# ---------------------------------------------------------------------------
# Quebra de linha
# ---------------------------------------------------------------------------

def _measure(font: ImageFont.FreeTypeFont, text: str, letter_spacing: int) -> int:
    """Largura aproximada considerando letter_spacing extra."""
    w = font.getlength(text)
    if letter_spacing and len(text) > 1:
        w += letter_spacing * (len(text) - 1)
    return int(w)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int, letter_spacing: int) -> list[str]:
    if max_w <= 0:
        return text.split('\n')
    lines: list[str] = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        if not words:
            lines.append('')
            continue
        cur = words[0]
        for word in words[1:]:
            cand = cur + ' ' + word
            if _measure(font, cand, letter_spacing) <= max_w:
                cur = cand
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Posicionamento
# ---------------------------------------------------------------------------

def _resolve_anchor(
    position: Union[str, Tuple[int, int]],
    canvas_size: Tuple[int, int],
    text_size: Tuple[int, int],
    margin: Tuple[int, int],
) -> Tuple[int, int]:
    """Retorna (x, y) do canto superior-esquerdo onde o bloco de texto vai colar."""
    cw, ch = canvas_size
    tw, th = text_size
    mx, my = margin

    if isinstance(position, tuple):
        return position

    presets = {
        'top_left':      (mx,           my),
        'top_center':    ((cw - tw) // 2, my),
        'top_right':     (cw - tw - mx, my),
        'middle_left':   (mx,           (ch - th) // 2),
        'center':        ((cw - tw) // 2, (ch - th) // 2),
        'middle_right':  (cw - tw - mx, (ch - th) // 2),
        'bottom_left':   (mx,           ch - th - my),
        'bottom_center': ((cw - tw) // 2, ch - th - my),
        'bottom_right':  (cw - tw - mx, ch - th - my),
    }
    if position not in presets:
        raise ValueError(
            f'position invalida: {position!r}. Validas: {list(presets)}'
        )
    return presets[position]


# ---------------------------------------------------------------------------
# Renderizacao de UM elemento em layer transparente
# ---------------------------------------------------------------------------

def _render_element(canvas_size: Tuple[int, int], element: dict) -> Tuple[Image.Image, Tuple[int, int]]:
    """
    Retorna (RGBA image, (paste_x, paste_y)) ja com rotacao aplicada.
    A image tem o tamanho do bbox final do texto rotacionado.
    """
    text = str(element.get('text', ''))
    if not text:
        return Image.new('RGBA', (1, 1), (0, 0, 0, 0)), (0, 0)

    font_alias = element.get('font', 'montserrat_regular')
    size = int(element.get('size', 48))
    color = tuple(element.get('color', (255, 255, 255)))
    opacity = int(element.get('opacity', 100))
    align = element.get('align', 'left')
    max_width_pct = element.get('max_width_pct')
    rotate = float(element.get('rotate', 0))
    letter_spacing = int(element.get('letter_spacing', 0))
    line_spacing = float(element.get('line_spacing', 1.15))
    position = element.get('position', 'top_left')
    margin = element.get('margin')

    cw, ch = canvas_size
    if margin is None:
        margin = (int(cw * 0.04), int(ch * 0.025))
    elif isinstance(margin, (int, float)):
        margin = (int(margin), int(margin))

    font = _load_font(font_alias, size)

    # Quebra de linha
    if max_width_pct:
        max_w = int(cw * (float(max_width_pct) / 100.0))
        lines = _wrap(text, font, max_w, letter_spacing)
    else:
        lines = text.split('\n')

    # Metrica
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * line_spacing)
    line_widths = [_measure(font, ln, letter_spacing) for ln in lines]
    block_w = max(line_widths) if line_widths else 0
    block_h = line_h * len(lines)

    if block_w == 0 or block_h == 0:
        return Image.new('RGBA', (1, 1), (0, 0, 0, 0)), (0, 0)

    # Layer do texto (com padding pequeno pra rotation nao cortar)
    pad = 4
    layer = Image.new('RGBA', (block_w + pad * 2, block_h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    a = max(0, min(255, int(round(opacity * 255 / 100))))
    fill = (color[0], color[1], color[2], a)

    y = pad
    for ln, lw in zip(lines, line_widths):
        if align == 'right':
            x = pad + (block_w - lw)
        elif align == 'center':
            x = pad + (block_w - lw) // 2
        else:
            x = pad

        if letter_spacing:
            cx = x
            for ch_ in ln:
                draw.text((cx, y), ch_, font=font, fill=fill)
                cx += int(font.getlength(ch_)) + letter_spacing
        else:
            draw.text((x, y), ln, font=font, fill=fill)
        y += line_h

    if rotate:
        layer = layer.rotate(-rotate, resample=Image.BICUBIC, expand=True)

    # Posicao final no canvas considera o tamanho ja rotacionado
    layer_w, layer_h = layer.size
    paste = _resolve_anchor(position, (cw, ch), (layer_w, layer_h), margin)
    return layer, paste


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def add_text_to_slide(
    background_path: Union[str, Path],
    output_path: Union[str, Path],
    elements: Iterable[dict],
) -> str:
    """
    Compoe o slide final escrevendo cada elemento do `elements` sobre o
    `background_path`. Ortografia e sempre perfeita porque usa fonte real.
    Retorna o `output_path` como string.
    """
    background_path = Path(background_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not background_path.exists():
        raise FileNotFoundError(background_path)

    bg = Image.open(background_path).convert('RGBA')
    canvas = bg.copy()

    n = 0
    for el in elements:
        layer, paste = _render_element(canvas.size, el)
        canvas.alpha_composite(layer, dest=paste)
        n += 1

    canvas.convert('RGB').save(output_path, 'PNG', optimize=True)
    log.info('add_text_to_slide ok: %d elementos -> %s', n, output_path)
    return str(output_path)


# ---------------------------------------------------------------------------
# CLI: pequeno selftest
# ---------------------------------------------------------------------------

def _cli():
    import argparse, json
    p = argparse.ArgumentParser(description='Renderiza texto em um slide.')
    p.add_argument('background')
    p.add_argument('output')
    p.add_argument('--elements', help='Caminho de JSON com lista de elementos.')
    p.add_argument('--ensure-fonts', action='store_true')
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    if args.ensure_fonts:
        ensure_fonts()

    if args.elements:
        with open(args.elements, 'r', encoding='utf-8') as f:
            elements = json.load(f)
    else:
        elements = [
            {'text': 'TESTE', 'font': 'montserrat_extrabold', 'size': 120,
             'color': (255, 255, 255), 'position': 'center', 'align': 'center'},
        ] + STANDARD_FOOTER

    out = add_text_to_slide(args.background, args.output, elements)
    print(out)


if __name__ == '__main__':
    _cli()
