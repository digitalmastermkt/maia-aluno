#!/usr/bin/env bash
# ============================================================================
# Digital Master - MAIA Esteira
# validate.sh - Validacao pos-instalacao (smoke test)
# ----------------------------------------------------------------------------
# Verifica se a base continua saudavel depois de aplicar um upgrade:
#  - cada skill instalada tem SKILL.md com frontmatter (name/description);
#  - o bot esta ativo (systemd OU tmux);
#  - (opcional) o banco semantico responde.
# Retorna 0 se tudo passou; != 0 dispara o rollback no update.sh.
#
# Uso:
#   ./validate.sh [array_de_skills...]
# ============================================================================

set -uo pipefail

SKILLS_DIR="$HOME/.claude/skills"
BOT_SERVICE="${MAIA_BOT_SERVICE:-maia-telegram-bot}"
TMUX_SESSION="${MAIA_TMUX_SESSION:-maia}"
errors=0

echo ">> Validando skills em $SKILLS_DIR"
for skill in "$@"; do
  dst="$SKILLS_DIR/$skill"
  if [[ ! -d "$dst" ]]; then
    echo "   FALHA: skill ausente: $skill" >&2
    errors=$((errors + 1))
    continue
  fi
  if [[ ! -f "$dst/SKILL.md" ]]; then
    echo "   FALHA: $skill sem SKILL.md" >&2
    errors=$((errors + 1))
    continue
  fi
  # Frontmatter minimo: campos name e description.
  if ! grep -qE '^name:' "$dst/SKILL.md" || ! grep -qE '^description:' "$dst/SKILL.md"; then
    echo "   AVISO: $skill com frontmatter incompleto (name/description)." >&2
  fi
  echo "   OK: $skill"
done

# --- Bot ativo? (systemd preferido, tmux como fallback) ---------------------
echo ">> Verificando o bot"
if command -v systemctl >/dev/null 2>&1 && systemctl list-units --type=service 2>/dev/null | grep -q "$BOT_SERVICE"; then
  if systemctl is-active --quiet "$BOT_SERVICE"; then
    echo "   OK: servico $BOT_SERVICE ativo."
  else
    echo "   FALHA: servico $BOT_SERVICE inativo." >&2
    errors=$((errors + 1))
  fi
elif command -v tmux >/dev/null 2>&1 && tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "   OK: sessao tmux '$TMUX_SESSION' viva."
else
  echo "   AVISO: nao foi possivel confirmar o bot (sem systemd nem tmux ativos)."
fi

# --- Banco semantico (opcional) ---------------------------------------------
if [[ -n "${MAIA_DB_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
  echo ">> Verificando banco semantico"
  if psql "$MAIA_DB_URL" -c 'SELECT 1;' >/dev/null 2>&1; then
    echo "   OK: banco semantico respondeu."
  else
    echo "   AVISO: banco semantico configurado mas sem resposta." >&2
  fi
fi

if [[ "$errors" -gt 0 ]]; then
  echo ">> Validacao FALHOU com $errors erro(s)." >&2
  exit 1
fi

echo ">> Validacao concluida com sucesso."
exit 0
