"""
brand_loader.py — Sistema central de marca da MAIA.

Le os dados de marca de /opt/MAIA/brand/brand.json e expoe helpers seguros
para as skills usarem. NUNCA lanca excecao: se o arquivo/campo faltar ou
estiver vazio, retorna string vazia (ou {} para cores). Assim, quando a marca
ainda nao foi preenchida pelo onboarding, rodapes/handles/slogans simplesmente
SOMEM do output em vez de exibir a marca errada de outra pessoa.

SNIPPET DE IMPORT CANONICO (use exatamente isto no topo de qualquer script de skill):

    import sys
    sys.path.insert(0, "/opt/MAIA")
    from brand_loader import (
        get_brand, brand_name, owner_name, handle, footer_handle,
        website_or_blank, slogan_or_blank, colors,
    )

Exemplo de uso defensivo:

    footer = footer_handle()          # "@marca" se preenchido, "" se vazio
    if footer:
        desenhar_rodape(footer)       # so desenha o rodape se houver handle

    pal = colors()                    # {"primary": "...", ...} ou {} se vazio
    cor = pal.get("primary") or "#000000"   # skill cai no default dela
"""

import json
import os

BRAND_PATH = "/opt/MAIA/brand/brand.json"


def get_brand():
    """Retorna o dict completo da marca. Se faltar arquivo ou JSON invalido,
    retorna {} (nunca lanca)."""
    try:
        with open(BRAND_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def get(key, default=""):
    """Retorna brand[key]. Se ausente/None, retorna default (str vazia por padrao)."""
    try:
        val = get_brand().get(key, default)
        return default if val is None else val
    except Exception:
        return default


def _str(key):
    """Helper interno: retorna o valor como string limpa, ou "" se vazio/ausente."""
    val = get(key, "")
    if val is None:
        return ""
    return str(val).strip()


def brand_name():
    """Nome da marca/empresa, ou "" se nao preenchido."""
    return _str("brand_name")


def owner_name():
    """Nome do dono/operador, ou "" se nao preenchido."""
    return _str("owner_name")


def handle():
    """Handle do Instagram normalizado com "@" (ex "@marca"), ou "" se vazio."""
    h = _str("instagram_handle")
    if not h:
        return ""
    return h if h.startswith("@") else "@" + h.lstrip("@")


def footer_handle():
    """Alias semantico de handle() para uso em rodapes. "@xxx" ou ""."""
    return handle()


def handle_personal():
    """Handle pessoal do Instagram normalizado com "@", ou "" se vazio."""
    h = _str("instagram_handle_personal")
    if not h:
        return ""
    return h if h.startswith("@") else "@" + h.lstrip("@")


def website_or_blank():
    """Site oficial, ou "" se vazio."""
    return _str("website")


def slogan_or_blank():
    """Bordao/slogan principal, ou "" se vazio."""
    return _str("slogan")


def slogans():
    """Lista de bordoes extras. Lista vazia se ausente/invalido."""
    val = get("slogans", [])
    return val if isinstance(val, list) else []


def whatsapp_or_blank():
    """WhatsApp, ou "" se vazio."""
    return _str("whatsapp")


def city_or_blank():
    """Cidade, ou "" se vazio."""
    return _str("city")


def niche_or_blank():
    """Nicho/segmento, ou "" se vazio."""
    return _str("niche")


def products():
    """Lista de produtos/servicos. Lista vazia se ausente/invalido."""
    val = get("products", [])
    return val if isinstance(val, list) else []


def colors():
    """Dict de cores da marca (primary/secondary/accent).
    Remove valores vazios. Se nenhuma cor estiver preenchida, retorna {} para
    que a skill caia no default visual dela."""
    val = get("colors", {})
    if not isinstance(val, dict):
        return {}
    cleaned = {}
    for k, v in val.items():
        if isinstance(v, str) and v.strip():
            cleaned[k] = v.strip()
        elif v not in ("", None):
            cleaned[k] = v
    return cleaned


def is_filled():
    """True se o onboarding ja preencheu a marca (_meta.filled), senao False."""
    try:
        meta = get_brand().get("_meta", {})
        return bool(meta.get("filled", False))
    except Exception:
        return False


if __name__ == "__main__":
    # Auto-teste rapido: mostra o estado atual da marca.
    print("filled        :", is_filled())
    print("brand_name    :", repr(brand_name()))
    print("owner_name    :", repr(owner_name()))
    print("footer_handle :", repr(footer_handle()))
    print("website       :", repr(website_or_blank()))
    print("slogan        :", repr(slogan_or_blank()))
    print("colors        :", colors())
