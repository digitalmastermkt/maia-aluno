#!/usr/bin/env python3
"""
carousel_premium_utils.py — Utilitarios Pillow premium para carrossel v3.

Funcoes (Juliana spec):
  apply_film_grain(img, intensity=0.05) -> Image
  apply_vignette(img, strength=0.10) -> Image
  apply_volumetric_spotlight(img, center, radius, color, intensity) -> Image
  composite_portrait_with_alpha_fade(portrait, bg, position, fade_direction,
                                      fade_start_pct, fade_end_pct,
                                      size_pct, feather, prefer_rembg) -> Image
  draw_hairline(canvas, x, y, length, orientation, color, opacity, thickness)
  apply_gaussian_blur(img, radius) -> Image
  render_floating_card(canvas, bbox, fill, shadow_blur, shadow_offset,
                       shadow_opacity, border_color, border_opacity)

Todos retornam/recebem Image RGBA quando apropriado. Sem efeito colateral
em arquivos — caller decide se salva.
"""
from __future__ import annotations

import sys
import math
import random
from pathlib import Path
from typing import Optional, Tuple, Union

# venv local
_VENV = Path('/opt/MAIA/bot/venv/lib/python3.12/site-packages')
if _VENV.is_dir() and str(_VENV) not in sys.path:
    sys.path.insert(0, str(_VENV))

from PIL import Image, ImageDraw, ImageFilter, ImageChops


# ---------------------------------------------------------------------------
# 6.1 Grao de filme
# ---------------------------------------------------------------------------

def apply_film_grain(img: Image.Image, intensity: float = 0.05,
                     seed: Optional[int] = 42) -> Image.Image:
    """Aplica noise gaussiano monocromatico sobre a imagem inteira.

    intensity 0.0..1.0 — fracao de variacao luminosa. 0.05 = 5%.
    Operacao: gera ruido cinza, depois usa multiply/add suave pra mesclar.
    """
    if intensity <= 0:
        return img
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    w, h = img.size
    rng = random.Random(seed)

    # Gera grao em escala menor (mais rapido) e upscala
    scale = 2  # 1=full res; 2=metade (suficiente pra grao fino)
    gw, gh = max(1, w // scale), max(1, h // scale)
    noise = Image.new('L', (gw, gh))
    px = noise.load()
    amp = int(255 * intensity * 2)  # amplitude pico-a-pico
    half = amp // 2
    for y in range(gh):
        for x in range(gw):
            px[x, y] = max(0, min(255, 128 + rng.randint(-half, half)))
    noise = noise.resize((w, h), Image.BILINEAR)

    # Converte ruido para overlay neutro: 128 vira no-op em soft-light
    # Usamos blend simples: img * (1 - 0.5*intensity) + (img + (noise-128)) * 0.5*intensity
    # Mas mais simples: usa ImageChops.add com noise-128 e clamp.
    r, g, b, a = img.split()
    noise_signed = noise.point(lambda v: v - 128 + 128)  # mantem 128=neutro
    # Soma do canal: cinza centrado em zero
    delta = noise.point(lambda v: int((v - 128) * intensity * 1.0) + 128)
    r2 = ImageChops.add(r, delta, scale=1.0, offset=-128)
    g2 = ImageChops.add(g, delta, scale=1.0, offset=-128)
    b2 = ImageChops.add(b, delta, scale=1.0, offset=-128)
    return Image.merge('RGBA', (r2, g2, b2, a))


# ---------------------------------------------------------------------------
# 6.2 Vinheta radial
# ---------------------------------------------------------------------------

def apply_vignette(img: Image.Image, strength: float = 0.10,
                   feather: float = 0.45) -> Image.Image:
    """Escurece bordas via gradiente radial.

    strength 0..1 — opacidade maxima do escurecimento nas bordas.
    feather 0..1 — quao longe do centro o degrade comeca (0=do centro, 1=so na borda).
    """
    if strength <= 0:
        return img
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    w, h = img.size
    cx, cy = w / 2, h / 2
    max_d = math.hypot(cx, cy)
    inner = max_d * feather

    # Mascara: 0 no centro -> 255 na borda
    mask = Image.new('L', (w, h), 0)
    px = mask.load()
    for y in range(h):
        for x in range(w):
            d = math.hypot(x - cx, y - cy)
            if d <= inner:
                v = 0
            else:
                t = (d - inner) / max(1.0, (max_d - inner))
                t = max(0.0, min(1.0, t))
                v = int(255 * t * t)  # easing quadratico
            px[x, y] = v
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, int(min(w, h) * 0.02))))

    # Camada preta com alpha = mask * strength
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    alpha = mask.point(lambda v: int(v * strength))
    overlay.putalpha(alpha)
    out = img.copy()
    out.alpha_composite(overlay)
    return out


# ---------------------------------------------------------------------------
# 6.3 Spotlight volumetrico
# ---------------------------------------------------------------------------

def apply_volumetric_spotlight(img: Image.Image, center: Tuple[int, int],
                                radius: int, color: Tuple[int, int, int],
                                intensity: float = 0.35) -> Image.Image:
    """Adiciona pool de luz colorida (gradiente radial) sobre a imagem.

    center: (x, y) do centro do feixe
    radius: raio em px ate desaparecer
    color: RGB da luz (ex: teal)
    intensity 0..1: pico de opacidade no centro
    """
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    w, h = img.size
    cx, cy = center

    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    px = overlay.load()
    alpha_max = int(255 * intensity)
    for y in range(h):
        for x in range(w):
            d = math.hypot(x - cx, y - cy)
            if d >= radius:
                continue
            t = 1.0 - (d / radius)
            a = int(alpha_max * (t ** 1.6))  # falloff suave
            px[x, y] = (color[0], color[1], color[2], a)
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(2, radius // 12)))
    out = img.copy()
    out.alpha_composite(overlay)
    return out


# ---------------------------------------------------------------------------
# 6.4 Composite com alpha fade
# ---------------------------------------------------------------------------

def _make_fade_mask(size: Tuple[int, int], direction: str,
                    start_pct: float, end_pct: float,
                    full_alpha: int = 255) -> Image.Image:
    """Cria mascara de gradiente.

    direction: 'bottom' | 'top' | 'left' | 'right' | 'radial'
    start_pct: 0..1 — posicao onde alpha ainda e full
    end_pct: 0..1 — posicao onde alpha vira 0 (>start_pct)
    Para 'radial': start=raio interno opaco, end=raio externo zero.
    """
    w, h = size
    mask = Image.new('L', (w, h), full_alpha)
    px = mask.load()

    if direction == 'bottom':
        sy = int(h * start_pct)
        ey = int(h * end_pct)
        if ey <= sy:
            ey = sy + 1
        for y in range(h):
            if y <= sy:
                v = full_alpha
            elif y >= ey:
                v = 0
            else:
                t = (y - sy) / (ey - sy)
                v = int(full_alpha * (1.0 - t))
            for x in range(w):
                px[x, y] = v
    elif direction == 'top':
        sy = int(h * (1 - start_pct))
        ey = int(h * (1 - end_pct))
        if ey >= sy:
            ey = sy - 1
        for y in range(h):
            if y >= sy:
                v = full_alpha
            elif y <= ey:
                v = 0
            else:
                t = (sy - y) / (sy - ey)
                v = int(full_alpha * (1.0 - t))
            for x in range(w):
                px[x, y] = v
    elif direction == 'radial':
        cx, cy = w / 2, h / 2
        max_d = math.hypot(cx, cy)
        r_in = max_d * start_pct
        r_out = max_d * end_pct
        if r_out <= r_in:
            r_out = r_in + 1
        for y in range(h):
            for x in range(w):
                d = math.hypot(x - cx, y - cy)
                if d <= r_in:
                    v = full_alpha
                elif d >= r_out:
                    v = 0
                else:
                    t = (d - r_in) / (r_out - r_in)
                    v = int(full_alpha * (1.0 - t))
                px[x, y] = v
    else:
        raise ValueError(f'direction invalido: {direction}')
    return mask


def composite_portrait_with_alpha_fade(
    portrait: Image.Image,
    bg: Image.Image,
    position: Union[str, Tuple[int, int]],
    fade_direction: str = 'bottom',
    fade_start_pct: float = 0.50,
    fade_end_pct: float = 0.95,
    size_pct: float = 70.0,
) -> Image.Image:
    """Composita retrato (RGBA, ja com fundo removido) sobre bg aplicando
    gradiente de alpha — rosto opaco, dissolve na direcao indicada.

    portrait: Image RGBA com alpha ja limpo (rembg).
    bg: Image RGBA do fundo.
    position: preset ('bottom_right', 'center', 'right_center', etc) ou (x,y)
    fade_direction: 'bottom' | 'top' | 'radial' | 'left' | 'right'
    fade_start_pct/end_pct: posicoes do degrade DENTRO do retrato (0..1).
    size_pct: % da altura do bg que o retrato ocupa.

    Retorna nova Image RGBA composta.
    """
    if portrait.mode != 'RGBA':
        portrait = portrait.convert('RGBA')
    if bg.mode != 'RGBA':
        bg = bg.convert('RGBA')

    bw, bh = bg.size
    target_h = int(bh * size_pct / 100.0)
    scale = target_h / portrait.height
    target_w = max(1, int(portrait.width * scale))
    portrait = portrait.resize((target_w, target_h), Image.LANCZOS)

    # Multiplica alpha existente pela mascara de fade
    fade = _make_fade_mask(portrait.size, fade_direction,
                            fade_start_pct, fade_end_pct, full_alpha=255)
    r, g, b, a = portrait.split()
    a_new = ImageChops.multiply(a, fade)
    portrait = Image.merge('RGBA', (r, g, b, a_new))

    # Posicao
    margin_x = int(bw * 0.04)
    margin_y = int(bh * 0.02)
    presets = {
        'right_center': (bw - target_w - margin_x, (bh - target_h) // 2),
        'left_center':  (margin_x,                  (bh - target_h) // 2),
        'center':       ((bw - target_w) // 2,      (bh - target_h) // 2),
        'bottom_right': (bw - target_w - margin_x,  bh - target_h - margin_y),
        'bottom_left':  (margin_x,                   bh - target_h - margin_y),
        'bottom_center':((bw - target_w) // 2,       bh - target_h - margin_y),
        'top_right':    (bw - target_w - margin_x,  margin_y),
        'top_left':     (margin_x,                   margin_y),
    }
    if isinstance(position, tuple):
        pos = position
    else:
        if position not in presets:
            raise ValueError(f'position invalida: {position}')
        pos = presets[position]

    canvas = bg.copy()
    canvas.alpha_composite(portrait, dest=pos)
    return canvas


# ---------------------------------------------------------------------------
# 6.5 Blur gaussiano
# ---------------------------------------------------------------------------

def apply_gaussian_blur(img: Image.Image, radius: float) -> Image.Image:
    if radius <= 0:
        return img
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    return img.filter(ImageFilter.GaussianBlur(radius=radius))


# ---------------------------------------------------------------------------
# 6.6 Card flutuante com drop shadow
# ---------------------------------------------------------------------------

def render_floating_card(canvas: Image.Image, bbox: Tuple[int, int, int, int],
                         fill: Tuple[int, int, int], fill_opacity: int = 245,
                         shadow_blur: float = 25, shadow_offset: Tuple[int, int] = (0, 12),
                         shadow_opacity: float = 0.30,
                         border_color: Optional[Tuple[int, int, int]] = None,
                         border_opacity: int = 60,
                         radius: int = 8) -> Image.Image:
    """Desenha card retangular com drop shadow gaussiano sobre canvas.
    Retorna canvas modificado (in-place compose).

    bbox: (x0, y0, x1, y1)
    fill: RGB do interior do card
    fill_opacity: 0..255
    """
    if canvas.mode != 'RGBA':
        canvas = canvas.convert('RGBA')
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    pad = int(shadow_blur * 2)
    layer = Image.new('RGBA', (w + pad * 2, h + pad * 2), (0, 0, 0, 0))

    # Sombra
    sh = Image.new('RGBA', layer.size, (0, 0, 0, 0))
    sh_draw = ImageDraw.Draw(sh)
    sh_alpha = int(255 * shadow_opacity)
    sh_draw.rounded_rectangle(
        [pad + shadow_offset[0], pad + shadow_offset[1],
         pad + w + shadow_offset[0], pad + h + shadow_offset[1]],
        radius=radius, fill=(0, 0, 0, sh_alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(radius=shadow_blur))

    # Card
    card = Image.new('RGBA', layer.size, (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card)
    card_draw.rounded_rectangle(
        [pad, pad, pad + w, pad + h],
        radius=radius, fill=(fill[0], fill[1], fill[2], fill_opacity),
        outline=(border_color + (border_opacity,)) if border_color else None,
        width=1 if border_color else 0,
    )

    layer.alpha_composite(sh)
    layer.alpha_composite(card)
    canvas.alpha_composite(layer, dest=(x0 - pad, y0 - pad))
    return canvas


# ---------------------------------------------------------------------------
# 6.7 Hairlines
# ---------------------------------------------------------------------------

def draw_hairline(canvas: Image.Image, x: int, y: int, length: int,
                  orientation: str = 'horizontal',
                  color: Tuple[int, int, int] = (244, 240, 232),
                  opacity: int = 40, thickness: int = 1) -> Image.Image:
    """Desenha linha hairline sobre canvas (in-place compose).

    opacity: 0..100 (percent)
    """
    if canvas.mode != 'RGBA':
        canvas = canvas.convert('RGBA')
    a = max(0, min(255, int(round(opacity * 255 / 100))))
    layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if orientation == 'horizontal':
        draw.rectangle([x, y, x + length, y + thickness], fill=(*color, a))
    elif orientation == 'vertical':
        draw.rectangle([x, y, x + thickness, y + length], fill=(*color, a))
    else:
        raise ValueError(f'orientation invalida: {orientation}')
    canvas.alpha_composite(layer)
    return canvas


# ---------------------------------------------------------------------------
# CLI selftest minimo
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # Smoke: aplica grain+vignette numa imagem 800x600 cinza
    img = Image.new('RGBA', (800, 600), (40, 40, 45, 255))
    out = apply_film_grain(img, 0.05)
    out = apply_vignette(out, 0.20)
    out = apply_volumetric_spotlight(out, (400, 300), 200, (58, 158, 156), 0.3)
    draw_hairline(out, 100, 100, 200, 'horizontal', (244, 240, 232), 60, 1)
    out_path = Path('/tmp/premium_utils_smoke.png')
    out.convert('RGB').save(out_path)
    print(f'OK smoke: {out_path} ({out_path.stat().st_size} bytes)')
