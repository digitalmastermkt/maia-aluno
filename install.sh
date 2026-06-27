#!/usr/bin/env bash
# ============================================================
# MAIA — base enxuta (Claude CLI) — instalador idempotente
# Digital Master / Salatiel Batista — "Movimento gera resultado."
# Roda como root a partir da pasta da base:
#     sudo bash install.sh
# Instala o NUCLEO ENXUTO (produto de entrada): bot Telegram + MAIA
# orquestradora (Claude Code) + onboarding de marca + 3 skills leves +
# o motor de upgrades (upgrades-engine/) pronto pra plugar pacotes pagos.
#
# NAO instala (sao UPGRADES pagos, plugam depois sem tocar nesta base):
#   - memoria semantica (sqlite-vec/pgvector, porta 3007)
#   - time de 7 subagentes
#   - skills avancadas (carrosseis, paginas, SDR, edicao de video, etc.)
#
# Idempotente: pode rodar varias vezes sem quebrar nada.
# ============================================================
set -euo pipefail

MAIA_HOME=/opt/MAIA
MAIA_USER=maia
TZ_NAME=America/Sao_Paulo
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo -e "\n=== $* ==="; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode como root: sudo bash install.sh" >&2
  exit 1
fi

# ------------------------------------------------------------
log "1/8 Timezone -> $TZ_NAME"
timedatectl set-timezone "$TZ_NAME" 2>/dev/null || ln -sf "/usr/share/zoneinfo/$TZ_NAME" /etc/localtime

# ------------------------------------------------------------
log "2/8 Pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
# Python 3.12: algumas distros (ex.: Ubuntu 22.04 jammy) nao trazem python3.12.
# Se faltar, adiciona o PPA deadsnakes (Ubuntu) para garantir paridade.
if ! apt-cache show python3.12 >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -y
fi
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg git \
  python3.12 python3.12-venv python3-pip \
  ffmpeg tmux sqlite3 jq build-essential

# Node 22 (NodeSource) — so instala se ausente ou versao < 22
if ! command -v node >/dev/null 2>&1 || [ "$(node -v 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')" -lt 22 ] 2>/dev/null; then
  log "2b/8 Node 22 via NodeSource"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

# Claude Code CLI (o cerebro Anthropic desta base)
if ! command -v claude >/dev/null 2>&1; then
  log "2c/8 Claude Code CLI (npm -g)"
  npm install -g @anthropic-ai/claude-code
fi

# ------------------------------------------------------------
log "3/8 Swap 2GB (se nao houver swap)"
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile || true
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  echo "swap ja existe, pulando"
fi

# ------------------------------------------------------------
log "4/8 Usuario $MAIA_USER + $MAIA_HOME"
id -u "$MAIA_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$MAIA_USER"
mkdir -p "$MAIA_HOME"

# ------------------------------------------------------------
log "5/8 Copiando base -> $MAIA_HOME"
# Preserva o .env existente (nao sobrescreve credenciais ja preenchidas)
rsync -a --exclude 'venv' --exclude '__pycache__' --exclude '*.pyc' \
      --exclude '*.bak' --exclude 'bot/.env' --exclude '*.sqlite' \
      "$SRC_DIR"/ "$MAIA_HOME"/ 2>/dev/null || cp -a "$SRC_DIR"/. "$MAIA_HOME"/
mkdir -p "$MAIA_HOME/bot/logs" "$MAIA_HOME/memory" "$MAIA_HOME/brand"

# Materializa .claude a partir de dotclaude/ (o Claude Code protege qualquer
# pasta chamada .claude contra escrita por agentes; por isso a base versiona
# dotclaude/ e o instalador materializa .claude/ no destino). As skills SO
# funcionam em $MAIA_HOME/.claude/skills/. Idempotente: merge nao-destrutivo.
if [ -d "$MAIA_HOME/dotclaude" ]; then
  mkdir -p "$MAIA_HOME/.claude"
  cp -a "$MAIA_HOME/dotclaude/." "$MAIA_HOME/.claude/"
  echo "materializado: $MAIA_HOME/.claude (skills da base) a partir de dotclaude/"
fi

# .env a partir do template (so se nao existir)
if [ ! -f "$MAIA_HOME/bot/.env" ]; then
  cp "$MAIA_HOME/bot/.env.TEMPLATE" "$MAIA_HOME/bot/.env"
  echo "criado bot/.env a partir do TEMPLATE — PREENCHER antes de habilitar o servico"
fi

# brand.json vazio (so se nao existir)
if [ ! -f "$MAIA_HOME/brand/brand.json" ]; then
  cat > "$MAIA_HOME/brand/brand.json" <<'JSON'
{"brand_name":"","owner_name":"","instagram_handle":"","instagram_handle_personal":"","website":"","slogan":"","slogans":[],"whatsapp":"","city":"","niche":"","products":[],"colors":{"primary":"","secondary":"","accent":""},"_meta":{"filled":false}}
JSON
fi

# ------------------------------------------------------------
log "6/8 Venv do bot (sem Whisper local nem memoria semantica)"
PY=python3.12
if [ ! -x "$MAIA_HOME/bot/venv/bin/python" ]; then
  $PY -m venv "$MAIA_HOME/bot/venv"
fi
"$MAIA_HOME/bot/venv/bin/python" -m pip install --upgrade pip wheel setuptools
if [ -f "$MAIA_HOME/bot/requirements.txt" ]; then
  "$MAIA_HOME/bot/venv/bin/pip" install -r "$MAIA_HOME/bot/requirements.txt"
else
  "$MAIA_HOME/bot/venv/bin/pip" install requests google-genai pillow openai python-dotenv
fi
# OBS: a base NAO instala faster-whisper (Whisper local) nem sqlite-vec. A
# transcricao de audio, se usada, vai por API (Groq/OpenAI) — leve. O banco de
# memoria semantica (porta 3007) e o upgrade "Memoria Semantica Digital Master".

# ------------------------------------------------------------
log "7/8 Unidade systemd do bot (DISABLED + inativa)"
cat > /etc/systemd/system/maia-telegram-bot.service <<'UNIT'
[Unit]
Description=MAIA Telegram Bot (daemon externo, independente do Claude Code)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=maia
Group=maia
WorkingDirectory=/opt/MAIA/bot
EnvironmentFile=/opt/MAIA/bot/.env
ExecStart=/opt/MAIA/bot/venv/bin/python /opt/MAIA/bot/bot.py
Restart=always
RestartSec=5
StandardOutput=append:/opt/MAIA/bot/logs/service.log
StandardError=append:/opt/MAIA/bot/logs/service.err

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
# Garante DISABLED e parado ate o .env estar preenchido
systemctl disable maia-telegram-bot 2>/dev/null || true
systemctl stop maia-telegram-bot 2>/dev/null || true

# ------------------------------------------------------------
log "8/8 Permissoes"
chown -R "$MAIA_USER:$MAIA_USER" "$MAIA_HOME"
chmod +x "$MAIA_HOME/upgrades-engine/"*.sh 2>/dev/null || true

cat <<EOF

============================================================
 MAIA base enxuta instalada (servico DISABLED ate configurar)
============================================================
PROXIMOS PASSOS MANUAIS:

1) Preencher credenciais:
     nano $MAIA_HOME/bot/.env
   Obrigatorio: TELEGRAM_BOT_TOKEN, ALLOWED_USERS, ADMIN_CHAT_ID, GEMINI_API_KEY

2) Login OAuth da conta Claude do cliente (sessao tmux do Claude Code):
     sudo -u $MAIA_USER tmux new -s maia-master
     cd $MAIA_HOME && claude --model claude-opus-4-8 --dangerously-skip-permissions
   (faca o /login, depois detache com Ctrl-b d)

3) Habilitar e subir o bot (SO depois do .env preenchido):
     systemctl enable --now maia-telegram-bot

4) Mandar "oi" no bot do Telegram. A MAIA conduz o onboarding e grava
   $MAIA_HOME/brand/brand.json (a marca emerge do onboarding, nao vem pronta).

PARA APLICAR UPGRADES depois (skills/agentes/memoria semantica):
   ver INSTALL-CLAUDE.md e os scripts em $MAIA_HOME/upgrades-engine/

OBS: a sessao tmux do Claude NAO sobrevive a reboot — relancar conforme passo 2.
============================================================
EOF
