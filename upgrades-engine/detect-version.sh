#!/usr/bin/env bash
# ============================================================================
# Digital Master - MAIA Esteira
# detect-version.sh - Detecta a versao instalada da base/upgrade
# ----------------------------------------------------------------------------
# Le a versao corrente a partir de um arquivo de estado (VERSION) e/ou do
# CHANGELOG.md, e expoe via stdout. Usado pelo update.sh como PASSO 1 para
# decidir se ha atualizacao a aplicar e para registrar de->para no log.
#
# Convencao de versao: ANO.MINOR-upgrade-N  (ex: 2026.4-upgrade-1)
#
# Uso:
#   ./detect-version.sh [diretorio-de-instalacao]
# Saida (stdout): a versao detectada, ou "0.0-desconhecida" se nao houver.
# ============================================================================

set -euo pipefail

INSTALL_DIR="${1:-$HOME/.maia}"
VERSION_FILE="$INSTALL_DIR/VERSION"
CHANGELOG="$INSTALL_DIR/CHANGELOG.md"

detected="0.0-desconhecida"

# 1) Fonte preferida: arquivo VERSION (uma linha, so a versao).
if [[ -f "$VERSION_FILE" ]]; then
  detected="$(head -n1 "$VERSION_FILE" | tr -d '[:space:]')"

# 2) Fallback: primeira tag de versao encontrada no CHANGELOG.md.
elif [[ -f "$CHANGELOG" ]]; then
  # Procura padrao tipo "2026.4-upgrade-1" no inicio de um item/titulo.
  found="$(grep -oE '[0-9]{4}\.[0-9]+(-upgrade-[0-9]+)?' "$CHANGELOG" | head -n1 || true)"
  if [[ -n "$found" ]]; then
    detected="$found"
  fi
fi

echo "$detected"
exit 0
