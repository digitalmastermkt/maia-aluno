#!/usr/bin/env bash
# ============================================================================
# Digital Master - MAIA Esteira
# backup-config.sh - Backup de configuracao antes de atualizar
# ----------------------------------------------------------------------------
# Cria um snapshot compactado da configuracao atual (skills instaladas, .env,
# brand.json, openclaw.json) ANTES de aplicar um upgrade. O caminho do backup
# gerado e impresso na ultima linha do stdout para o rollback.sh consumir.
#
# Uso:
#   ./backup-config.sh [diretorio-de-instalacao]
# Saida (stdout, ultima linha): caminho absoluto do arquivo .tar.gz gerado.
# ============================================================================

set -euo pipefail

INSTALL_DIR="${1:-$HOME/.maia}"
BACKUP_DIR="$INSTALL_DIR/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/config-backup-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

# Itens a preservar (so inclui os que existirem).
declare -a ITEMS=()
[[ -d "$HOME/.claude/skills" ]] && ITEMS+=("$HOME/.claude/skills")
[[ -f "$INSTALL_DIR/.env" ]]    && ITEMS+=("$INSTALL_DIR/.env")
[[ -f "$INSTALL_DIR/brand/brand.json" ]] && ITEMS+=("$INSTALL_DIR/brand/brand.json")
[[ -f "$INSTALL_DIR/openclaw.json" ]]    && ITEMS+=("$INSTALL_DIR/openclaw.json")
[[ -f "$INSTALL_DIR/VERSION" ]] && ITEMS+=("$INSTALL_DIR/VERSION")

if [[ ${#ITEMS[@]} -eq 0 ]]; then
  echo ">> Nada para fazer backup (instalacao limpa)." >&2
else
  # -P preserva os caminhos absolutos; util para restauracao no mesmo lugar.
  tar -czPf "$BACKUP_FILE" "${ITEMS[@]}"
  echo ">> Backup criado: $BACKUP_FILE" >&2
fi

# Ultima linha = caminho do backup (contrato com update.sh / rollback.sh).
echo "$BACKUP_FILE"
exit 0
