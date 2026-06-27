#!/usr/bin/env bash
# ============================================================================
# Digital Master - MAIA Esteira
# rollback.sh - Reverte para o backup mais recente (ou um informado)
# ----------------------------------------------------------------------------
# Restaura a configuracao a partir de um .tar.gz gerado pelo backup-config.sh.
# Chamado pelo update.sh quando o validate.sh falha. Apos restaurar, reinicia
# o bot para voltar ao estado bom conhecido.
#
# Uso:
#   ./rollback.sh [arquivo-de-backup.tar.gz] [diretorio-de-instalacao]
# Se o arquivo nao for informado, usa o backup mais recente em backups/.
# ============================================================================

set -euo pipefail

BACKUP_FILE="${1:-}"
INSTALL_DIR="${2:-$HOME/.maia}"
BACKUP_DIR="$INSTALL_DIR/backups"
BOT_SERVICE="${MAIA_BOT_SERVICE:-maia-telegram-bot}"
TMUX_SESSION="${MAIA_TMUX_SESSION:-maia}"

# Se nao passaram backup, pega o mais recente.
if [[ -z "$BACKUP_FILE" ]]; then
  BACKUP_FILE="$(ls -1t "$BACKUP_DIR"/config-backup-*.tar.gz 2>/dev/null | head -n1 || true)"
fi

if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "ERRO: nenhum backup encontrado para rollback ($BACKUP_DIR)." >&2
  exit 1
fi

echo ">> ROLLBACK a partir de: $BACKUP_FILE"
# -P porque o backup foi feito com caminhos absolutos (tar -P).
tar -xzPf "$BACKUP_FILE"
echo ">> Arquivos restaurados."

# --- Reinicia o bot para aplicar o estado restaurado ------------------------
if command -v systemctl >/dev/null 2>&1 && systemctl list-units --type=service 2>/dev/null | grep -q "$BOT_SERVICE"; then
  systemctl restart "$BOT_SERVICE" || echo "AVISO: falha ao reiniciar $BOT_SERVICE" >&2
  echo ">> Servico $BOT_SERVICE reiniciado."
elif command -v tmux >/dev/null 2>&1 && tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  tmux send-keys -t "$TMUX_SESSION" C-c
  echo ">> Sinal de restart enviado a sessao tmux '$TMUX_SESSION' (relancar manualmente se preciso)."
else
  echo ">> AVISO: reinicie o bot manualmente para concluir o rollback."
fi

echo ">> Rollback concluido."
exit 0
