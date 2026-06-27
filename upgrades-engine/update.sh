#!/usr/bin/env bash
# ============================================================================
# Digital Master - MAIA Esteira
# update.sh - Orquestrador de atualizacao (5 passos, com rollback)
# ----------------------------------------------------------------------------
# Aplica um pacote de upgrade na base MAIA, de forma guiada e reversivel.
# Fluxo:
#   PASSO 1: detect-version.sh   -> versao atual (de->para no log)
#   PASSO 2: backup-config.sh    -> snapshot antes de mexer
#   PASSO 3: loop install-skill.sh sobre o array SKILLS=(...)
#   PASSO 4: restart do bot      -> systemd ou tmux
#   PASSO 5: validate.sh         -> smoke test; se falhar -> rollback.sh
#
# A base (núcleo) NUNCA e reinstalada aqui: este script so PLUGA upgrades.
#
# Uso:
#   ./update.sh
#   UPGRADE_SRC=/opt/maia-upgrades/pacote-sdr ./update.sh
# ============================================================================

set -euo pipefail

# --- Onde estao os scripts da mecanica e a fonte das skills do upgrade -------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${MAIA_INSTALL_DIR:-$HOME/.maia}"
# Pasta do pacote de upgrade contendo as pastas de skill a instalar.
UPGRADE_SRC="${UPGRADE_SRC:-$HERE/../maia-upgrades}"
NEW_VERSION="${NEW_VERSION:-}"   # opcional: versao a gravar apos sucesso.

# --- Manifesto: skills deste pacote de upgrade ------------------------------
# Edite este array (ou gere-o a partir do manifest.json) para cada pacote.
SKILLS=(
  "skill-dossie-sdr"
  "skill-pagina-vendas"
  "skill-edicao-video-viral"
)

log() { echo "[$(date +%H:%M:%S)] $*"; }

# =============================== PASSO 1 ====================================
log "PASSO 1/5 - Detectando versao atual"
CURRENT_VERSION="$(bash "$HERE/detect-version.sh" "$INSTALL_DIR" || echo '0.0-desconhecida')"
log "Versao atual: $CURRENT_VERSION"

# =============================== PASSO 2 ====================================
log "PASSO 2/5 - Backup da configuracao"
BACKUP_FILE="$(bash "$HERE/backup-config.sh" "$INSTALL_DIR" | tail -n1)"
log "Backup em: $BACKUP_FILE"

# Helper de rollback + saida com erro.
abort_with_rollback() {
  log "ERRO detectado: iniciando rollback."
  bash "$HERE/rollback.sh" "$BACKUP_FILE" "$INSTALL_DIR" || log "AVISO: rollback tambem falhou."
  log "Update abortado e revertido."
  exit 1
}

# =============================== PASSO 3 ====================================
log "PASSO 3/5 - Instalando skills do upgrade (idempotente)"
for skill in "${SKILLS[@]}"; do
  src="$UPGRADE_SRC/$skill"
  if [[ ! -d "$src" ]]; then
    log "FALHA: skill nao encontrada na fonte: $src"
    abort_with_rollback
  fi
  if ! bash "$HERE/install-skill.sh" "$src"; then
    log "FALHA ao instalar $skill"
    abort_with_rollback
  fi
done

# =============================== PASSO 4 ====================================
log "PASSO 4/5 - Reiniciando o bot"
BOT_SERVICE="${MAIA_BOT_SERVICE:-maia-telegram-bot}"
TMUX_SESSION="${MAIA_TMUX_SESSION:-maia}"
if command -v systemctl >/dev/null 2>&1 && systemctl list-units --type=service 2>/dev/null | grep -q "$BOT_SERVICE"; then
  systemctl restart "$BOT_SERVICE" || { log "FALHA ao reiniciar $BOT_SERVICE"; abort_with_rollback; }
  log "Servico $BOT_SERVICE reiniciado."
elif command -v tmux >/dev/null 2>&1 && tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  tmux send-keys -t "$TMUX_SESSION" C-c
  log "Restart sinalizado na sessao tmux '$TMUX_SESSION'."
else
  log "AVISO: bot nao localizado (systemd/tmux); reinicie manualmente."
fi

# =============================== PASSO 5 ====================================
log "PASSO 5/5 - Validando"
if ! bash "$HERE/validate.sh" "${SKILLS[@]}"; then
  log "Validacao FALHOU."
  abort_with_rollback
fi

# --- Sucesso: grava nova versao (se informada) ------------------------------
if [[ -n "$NEW_VERSION" ]]; then
  echo "$NEW_VERSION" > "$INSTALL_DIR/VERSION"
  log "Versao atualizada: $CURRENT_VERSION -> $NEW_VERSION"
fi

log "UPDATE CONCLUIDO COM SUCESSO. Movimento gera resultado."
exit 0
