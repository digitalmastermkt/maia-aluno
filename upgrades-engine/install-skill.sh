#!/usr/bin/env bash
# ============================================================================
# Digital Master - MAIA Esteira
# install-skill.sh - Instalador idempotente de UMA skill
# ----------------------------------------------------------------------------
# Copia uma pasta de skill para ~/.claude/skills/<nome>/ de forma idempotente.
# - Resolve o HOME real mesmo rodando sob sudo (via SUDO_USER).
# - Usa diff -rq para PULAR a copia se o destino ja estiver igual.
# - Faz chown para o dono correto quando rodado como root.
#
# Uso:
#   ./install-skill.sh <caminho-da-skill>
#   sudo ./install-skill.sh /opt/maia-upgrades/skill-dossie-sdr
#
# A "skill" e uma PASTA autodescoberta contendo SKILL.md (frontmatter YAML)
# e seus scripts/templates ao lado. O runtime do Claude Code descobre a skill
# lendo ~/.claude/skills/*/SKILL.md - nao existe registro central.
# ============================================================================

set -euo pipefail

# --- Argumentos -------------------------------------------------------------
SKILL_SRC="${1:-}"

if [[ -z "$SKILL_SRC" ]]; then
  echo "ERRO: informe o caminho da skill. Uso: $0 <caminho-da-skill>" >&2
  exit 1
fi

# Normaliza removendo barra final, se houver.
SKILL_SRC="${SKILL_SRC%/}"

if [[ ! -d "$SKILL_SRC" ]]; then
  echo "ERRO: pasta da skill nao encontrada: $SKILL_SRC" >&2
  exit 1
fi

if [[ ! -f "$SKILL_SRC/SKILL.md" ]]; then
  echo "ERRO: $SKILL_SRC nao contem SKILL.md (nao parece ser uma skill valida)." >&2
  exit 1
fi

SKILL_NAME="$(basename "$SKILL_SRC")"

# --- Resolve o HOME real (mesmo sob sudo) -----------------------------------
# Quando rodado com sudo, $HOME aponta para /root. Queremos o HOME do usuario
# que de fato chamou o comando (SUDO_USER), para instalar no ~/.claude dele.
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
  TARGET_USER="$SUDO_USER"
  TARGET_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
  TARGET_USER="$(id -un)"
  TARGET_HOME="$HOME"
fi

if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
  echo "ERRO: nao consegui resolver o HOME real do usuario ($TARGET_USER)." >&2
  exit 1
fi

SKILLS_DIR="$TARGET_HOME/.claude/skills"
SKILL_DST="$SKILLS_DIR/$SKILL_NAME"

# --- Idempotencia: pula se ja estiver identico ------------------------------
mkdir -p "$SKILLS_DIR"

if [[ -d "$SKILL_DST" ]] && diff -rq "$SKILL_SRC" "$SKILL_DST" >/dev/null 2>&1; then
  echo "OK (sem mudancas): skill '$SKILL_NAME' ja esta instalada e identica."
  exit 0
fi

# --- Copia (atualiza ou instala) --------------------------------------------
echo ">> Instalando skill '$SKILL_NAME' em $SKILL_DST"
# cp -a preserva atributos; o destino e recriado do zero para evitar arquivos
# orfaos de versoes anteriores.
rm -rf "$SKILL_DST"
cp -a "$SKILL_SRC" "$SKILL_DST"

# --- chown para o dono correto (quando root) --------------------------------
if [[ "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
  TARGET_GROUP="$(id -gn "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")"
  chown -R "$TARGET_USER:$TARGET_GROUP" "$SKILL_DST"
  echo ">> chown aplicado: $TARGET_USER:$TARGET_GROUP"
fi

echo "OK: skill '$SKILL_NAME' instalada com sucesso."
exit 0
