#!/usr/bin/env python3
"""
Maia Telegram Bot - External daemon
Independente do Claude Code. NUNCA morre quando Claude reinicia.
- Recebe msgs via long polling (resilient)
- Salva em inbox/, notifica Maia via tmux send-keys
- Watch outbox/ e envia respostas via API
"""
import os, json, time, logging, signal, sys, subprocess, threading, re, hashlib, uuid, base64, wave
from pathlib import Path
from datetime import datetime, timezone

# Inclui site-packages do venv local (google-genai, pillow) antes de imports externos
_VENV_SITE = Path('/opt/MAIA/bot/venv/lib/python3.12/site-packages')
if _VENV_SITE.is_dir() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))

import requests

BOT_DIR = Path('/opt/MAIA/bot')
INBOX = BOT_DIR / 'inbox'
OUTBOX = BOT_DIR / 'outbox'
SENT = BOT_DIR / 'sent'
PROCESSED = BOT_DIR / 'processed'
STATE = BOT_DIR / 'state'
LOGS = BOT_DIR / 'logs'
ENV_FILE = BOT_DIR / '.env'

env = {}
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

TOKEN = env.get('TELEGRAM_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    sys.exit('TELEGRAM_BOT_TOKEN missing')

ALLOWED_USERS = set((env.get('ALLOWED_USERS') or env.get('ADMIN_CHAT_ID') or os.environ.get('ADMIN_CHAT_ID', '')).split(','))
TMUX_SESSION = env.get('TMUX_SESSION', 'maia')
TMUX_USER = env.get('TMUX_USER', 'maia')
# Nome do assistente exibido ao usuario final (white-label). Default: Maia
ASSISTANT_NAME = env.get('ASSISTANT_NAME') or os.environ.get('ASSISTANT_NAME', 'Maia')
# Handle minusculo derivado do nome (ex.: @maia) usado nas instrucoes ao usuario
ASSISTANT_HANDLE = '@' + ASSISTANT_NAME.lower()

for d in (INBOX, OUTBOX, SENT, PROCESSED, STATE, LOGS):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.FileHandler(LOGS / 'bot.log'), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

API = f'https://api.telegram.org/bot{TOKEN}'
OPENAI_KEY = env.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY', '')
ELEVENLABS_KEY = env.get('ELEVENLABS_API_KEY') or os.environ.get('ELEVENLABS_API_KEY', '')
ELEVENLABS_VOICE = env.get('ELEVENLABS_VOICE_ID') or os.environ.get('ELEVENLABS_VOICE_ID', '21m00Tcm4TlvDq8ikWAM')
# Voz (TTS) - engine padrao Gemini, fallback ElevenLabs
GEMINI_KEY = env.get('GEMINI_API_KEY') or os.environ.get('GEMINI_API_KEY', '')
GEMINI_TTS_MODEL = env.get('GEMINI_TTS_MODEL') or os.environ.get('GEMINI_TTS_MODEL', 'gemini-2.5-flash-preview-tts')
GEMINI_TTS_VOICE = env.get('GEMINI_TTS_VOICE') or os.environ.get('GEMINI_TTS_VOICE', 'Kore')
# VOZ OFICIAL DA MAIA (escolha Chefe opcao 3, 2026-06-05): voz Kore + sotaque
# nordestino. O sotaque vem de PREPENDAR esta instrucao de estilo ao texto.
# Configuravel por env MAIA_VOICE_STYLE; default ja vale a nordestina.
MAIA_VOICE_STYLE = (env.get('MAIA_VOICE_STYLE') or os.environ.get(
    'MAIA_VOICE_STYLE',
    'Narre com sotaque nordestino brasileiro marcante, tom caloroso e animado'))
VOICE_ENGINE = (env.get('VOICE_ENGINE') or os.environ.get('VOICE_ENGINE', 'gemini')).strip().lower()
AUDIO_IN = BOT_DIR / 'audio' / 'incoming'
AUDIO_OUT = BOT_DIR / 'audio' / 'outgoing'
AUDIO_IN.mkdir(parents=True, exist_ok=True)
AUDIO_OUT.mkdir(parents=True, exist_ok=True)
# Video original recebido no grupo/DM (necessario pra edicao posterior)
VIDEO_IN = BOT_DIR / 'videos' / 'incoming'
VIDEO_IN.mkdir(parents=True, exist_ok=True)
# Fotos/PDFs/documentos recebidos no grupo/DM (Maia analisa via referencia no inbox).
PHOTO_IN = BOT_DIR / 'images' / 'incoming-user'
DOC_IN = BOT_DIR / 'documents' / 'incoming'
PHOTO_IN.mkdir(parents=True, exist_ok=True)
DOC_IN.mkdir(parents=True, exist_ok=True)

# Image generation (Gemini/Imagen)
GEMINI_KEY = env.get('GEMINI_API_KEY') or os.environ.get('GEMINI_API_KEY', '')
GEMINI_IMAGE_MODEL = env.get('GEMINI_IMAGE_MODEL', 'imagen-4.0-generate-001')
GEMINI_DEFAULT_ASPECT = env.get('GEMINI_DEFAULT_ASPECT', '1:1')
IMG_IN = BOT_DIR / 'images' / 'incoming'
IMG_OUT = BOT_DIR / 'images' / 'outgoing'
IMG_IN.mkdir(parents=True, exist_ok=True)
IMG_OUT.mkdir(parents=True, exist_ok=True)
# Propaga GEMINI_API_KEY pro env do processo (gemini_image.py le de os.environ)
if GEMINI_KEY:
    os.environ['GEMINI_API_KEY'] = GEMINI_KEY

try:
    from gemini_image import generate_image as _gemini_generate
    from gemini_image import edit_image as _gemini_edit
except Exception as _e:
    _gemini_generate = None
    _gemini_edit = None
    logging.getLogger(__name__).warning(f'gemini_image indisponivel: {_e}')

GEMINI_EDIT_MODEL = env.get('GEMINI_EDIT_MODEL', 'gemini-2.5-flash-image')

# Typing indicator: chat_id -> timestamp ate quando manter typing ativo
typing_until = {}
typing_lock = threading.Lock()
running = True

# Debounce: aguarda N segundos sem nova msg antes de injetar pra Maia.
# Quando chega nova msg, reseta o timer. Permite o usuario mandar msgs quebradas
# em sequencia que sao agrupadas como contexto unico antes de chegar na Maia.
DEBOUNCE_SECONDS = float(env.get('DEBOUNCE_SECONDS', '8'))
pending_buffer = []  # list of dicts: {msg_id, text, user, chat_id}
debounce_timer = None
debounce_lock = threading.Lock()

# Watchdog: detecta Maia silenciosa (rate limit, erro 400, Claude travada, tmux quebrada)
MAIA_TIMEOUT_SECONDS = float(env.get('MAIA_TIMEOUT_SECONDS', '600'))  # 10min sem resposta = alerta (tarefa com imagem passa de 5min)
HEALTHCHECK_INTERVAL = float(env.get('HEALTHCHECK_INTERVAL', '120'))
HEALTHCHECK_ALERT_THROTTLE = float(env.get('HEALTHCHECK_ALERT_THROTTLE', '1800'))
TMUX_SOCKET = env.get('TMUX_SOCKET', '/tmp/tmux-1001/default')
# ADMIN_CHAT_ID: ID do Telegram do dono/admin. Vem do env (ADMIN_CHAT_ID) ou,
# se ausente, do primeiro ID de ALLOWED_USERS. Sem nenhum configurado fica 0
# (nenhum usuario passa nos gates de admin ate o .env ser preenchido).
try:
    ADMIN_CHAT_ID = int((env.get('ADMIN_CHAT_ID') or os.environ.get('ADMIN_CHAT_ID') or next(iter(ALLOWED_USERS))).strip())
except Exception:
    ADMIN_CHAT_ID = 0
pending_maia = {}  # msg_id -> {'notified_at', 'chat_id', 'last_alert_at'}
pending_maia_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Group mode (adicionado 2026-05-21)
# Bot opera em DM (compat antigo) + 1 grupo whitelisted.
# Mensagens em grupo so sao processadas quando:
#   - chat_id == MAIA_GROUP_CHAT_ID (configurado/auto-capturado)
#   - texto contem MAIA_GROUP_TRIGGER (case-insensitive)
#   - user_id pertence a MAIA_GROUP_USER_WHITELIST (CSV)
# Tudo fora disso e ignorado silenciosamente (log em logs/group.log).
# Comandos admin sao aceitos APENAS no DM do Chefe:
#   /group_status, /add_user_to_group_wl, /remove_user_from_group_wl, /confirmgroup
# ---------------------------------------------------------------------------
GROUP_CONFIG_LOCK = threading.Lock()

def _read_env_value(key, default=''):
    """Le valor atual do .env do disco (pode ter sido modificado por comando admin)."""
    try:
        if not ENV_FILE.exists():
            return default
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f'{key}='):
                return line.split('=', 1)[1].strip()
        return default
    except Exception:
        return default

def _write_env_value(key, value):
    """Atualiza (ou cria) chave no .env de forma atomica.
    Preserva ordem das linhas. Se key nao existir, anexa no fim."""
    with GROUP_CONFIG_LOCK:
        try:
            lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
            found = False
            new_lines = []
            for line in lines:
                if line.startswith(f'{key}='):
                    new_lines.append(f'{key}={value}')
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f'{key}={value}')
            tmp = ENV_FILE.with_suffix('.env.tmp')
            tmp.write_text('\n'.join(new_lines) + '\n')
            os.chmod(tmp, 0o600)
            tmp.replace(ENV_FILE)
            env[key] = value
            log.info(f'env atualizado: {key}={value}')
            return True
        except Exception as e:
            log.error(f'_write_env_value({key}) erro: {e}')
            return False

def get_group_chat_id():
    """Le chat_id do grupo do .env. Vazio = nao capturado ainda."""
    raw = _read_env_value('MAIA_GROUP_CHAT_ID', '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None

def get_group_trigger():
    return _read_env_value('MAIA_GROUP_TRIGGER', ASSISTANT_HANDLE).strip().lower()

def get_group_user_whitelist():
    """Retorna set de user_ids (str) autorizados a falar no grupo."""
    raw = _read_env_value('MAIA_GROUP_USER_WHITELIST', str(ADMIN_CHAT_ID))
    return {x.strip() for x in raw.split(',') if x.strip()}

def get_group_open_membership():
    """Se True, qualquer user do grupo confirmado pode interagir (sem whitelist por user).
    Mantém alerta DM informativo pro Chefe na 1a interação de cada user novo.
    """
    raw = _read_env_value('MAIA_GROUP_OPEN_MEMBERSHIP', 'false')
    return str(raw).strip().lower() in ('true', '1', 'yes', 'on')

def get_group_always_on():
    """Se True, o grupo confirmado opera em modo SEMPRE-ABERTO: toda mensagem
    (de users autorizados) e processada SEM exigir @trigger nem sessao.
    Usado pra grupos dedicados (ex: grupo que so usuarios autorizados usam pra falar
    com a Maia). Se False, mantem o comportamento antigo (trigger + sessao).
    """
    raw = _read_env_value('MAIA_GROUP_ALWAYS_ON', 'false')
    return str(raw).strip().lower() in ('true', '1', 'yes', 'on')

def set_group_chat_id(chat_id):
    return _write_env_value('MAIA_GROUP_CHAT_ID', str(chat_id))

def add_user_to_group_whitelist(user_id):
    current = get_group_user_whitelist()
    current.add(str(user_id).strip())
    return _write_env_value('MAIA_GROUP_USER_WHITELIST', ','.join(sorted(current)))

def remove_user_from_group_whitelist(user_id):
    current = get_group_user_whitelist()
    current.discard(str(user_id).strip())
    return _write_env_value('MAIA_GROUP_USER_WHITELIST', ','.join(sorted(current)))

# ---------------------------------------------------------------------------
# Group session mode (2026-05-21)
# Primeira msg com @maia no grupo abre uma SESSAO de N minutos (default 10).
# Durante a sessao, TODAS as msgs do grupo sao processadas sem precisar de @maia.
# Sessao encerra com /sair ou apos N min sem nova msg (sliding window).
# Sessao e por chat_id (todos no grupo participam da mesma sessao).
# Estado persistido em /opt/MAIA/bot/group_sessions.json.
# ---------------------------------------------------------------------------
GROUP_SESSIONS_FILE = BOT_DIR / 'group_sessions.json'
GROUP_SESSIONS_LOCK = threading.Lock()

def get_group_session_timeout_min():
    """Le timeout (em minutos) da sessao de grupo do .env. Default 10."""
    raw = _read_env_value('MAIA_GROUP_SESSION_TIMEOUT_MIN', '10').strip()
    try:
        v = int(raw)
        return v if v > 0 else 10
    except ValueError:
        return 10

def _load_sessions():
    """Le arquivo de sessoes. Retorna dict vazio se nao existir ou corrompido."""
    with GROUP_SESSIONS_LOCK:
        try:
            if not GROUP_SESSIONS_FILE.exists():
                return {}
            content = GROUP_SESSIONS_FILE.read_text().strip()
            if not content:
                return {}
            return json.loads(content)
        except Exception as e:
            log.error(f'_load_sessions erro: {e}')
            return {}

def _save_sessions(sessions):
    """Salva sessoes de forma atomica (tmp + rename)."""
    with GROUP_SESSIONS_LOCK:
        try:
            tmp = GROUP_SESSIONS_FILE.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=2))
            tmp.replace(GROUP_SESSIONS_FILE)
            return True
        except Exception as e:
            log.error(f'_save_sessions erro: {e}')
            return False

def open_group_session(chat_id, user_id):
    """Abre sessao de N min pro grupo. Reseta msg_count se ja estava aberta.
    Retorna True se abriu uma sessao NOVA (estava fechada/expirada).
    """
    sessions = _load_sessions()
    now = datetime.now(timezone.utc)
    key = str(chat_id)
    is_new = key not in sessions or not _is_session_active_dict(sessions.get(key))
    sessions[key] = {
        'opened_at': now.isoformat(),
        'last_msg_at': now.isoformat(),
        'opened_by_user_id': user_id,
        'msg_count': 0,
    }
    _save_sessions(sessions)
    return is_new

def _is_session_active_dict(sess, timeout_minutes=None):
    """Helper interno: dado o dict da sessao, verifica se ainda esta ativa."""
    if not sess:
        return False
    if timeout_minutes is None:
        timeout_minutes = get_group_session_timeout_min()
    try:
        last = datetime.fromisoformat(sess['last_msg_at'])
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
        return elapsed < timeout_minutes
    except Exception:
        return False

def is_group_session_active(chat_id, timeout_minutes=None):
    """Verifica se sessao tah ativa pro grupo. Retorna True/False."""
    sessions = _load_sessions()
    sess = sessions.get(str(chat_id))
    return _is_session_active_dict(sess, timeout_minutes)

def touch_group_session(chat_id):
    """Reseta o timer da sessao (sliding window) e incrementa msg_count."""
    sessions = _load_sessions()
    sess = sessions.get(str(chat_id))
    if sess:
        sess['last_msg_at'] = datetime.now(timezone.utc).isoformat()
        sess['msg_count'] = int(sess.get('msg_count', 0)) + 1
        sessions[str(chat_id)] = sess
        _save_sessions(sessions)
        return True
    return False

def close_group_session(chat_id):
    """Encerra sessao imediato (ex: /sair). Retorna True se havia sessao aberta."""
    sessions = _load_sessions()
    key = str(chat_id)
    if key in sessions:
        del sessions[key]
        _save_sessions(sessions)
        return True
    return False

def _send_group_text(chat_id, text, reply_to=None):
    """Envia texto ao grupo via outbox (NUNCA via API direta — regra critica CLAUDE.md).
    Marca como auto-mensagem pra nao disparar mark_maia_responded (so reage a outbox
    de resposta direta a usuario). Como o bot proprio gera, marcamos suffix _bot.
    """
    try:
        payload = {'chat_id': chat_id, 'text': text}
        if reply_to:
            payload['reply_to_message_id'] = int(reply_to)
        fname = f'group_session_{int(time.time()*1000)}.json'
        (OUTBOX / fname).write_text(json.dumps(payload, ensure_ascii=False))
        log.info(f'outbox grupo (sessao) criado: {fname}')
        return True
    except Exception as e:
        log.error(f'_send_group_text erro: {e}')
        return False

# Garante que o arquivo de sessoes exista (idempotente)
if not GROUP_SESSIONS_FILE.exists():
    try:
        GROUP_SESSIONS_FILE.write_text('{}\n')
    except Exception:
        pass

# Background cleanup: thread que verifica sessoes expiradas e dispara aviso de
# encerramento por timeout. Roda a cada 30s.
_session_timeout_notified = set()  # chat_ids ja notificados (evita re-aviso)
_session_timeout_lock = threading.Lock()

def _session_cleanup_loop():
    while running:
        try:
            sessions = _load_sessions()
            timeout = get_group_session_timeout_min()
            now = datetime.now(timezone.utc)
            expired = []
            for key, sess in list(sessions.items()):
                try:
                    last = datetime.fromisoformat(sess['last_msg_at'])
                    elapsed = (now - last).total_seconds() / 60.0
                    if elapsed >= timeout:
                        expired.append((key, sess))
                except Exception:
                    expired.append((key, sess))
            for key, sess in expired:
                try:
                    cid = int(key)
                except ValueError:
                    cid = key
                with _session_timeout_lock:
                    already = key in _session_timeout_notified
                    _session_timeout_notified.add(key)
                if not already:
                    _send_group_text(
                        cid,
                        f'Sessao encerrada por inatividade. Pra falar comigo de novo, manda {ASSISTANT_HANDLE}.'
                    )
                    _group_logger.info(
                        f'sessao expirada por timeout chat_id={key} '
                        f'msg_count={sess.get("msg_count", 0)}'
                    )
                # remove do estado
                close_group_session(cid if isinstance(cid, int) else key)
            # Limpa dedupe de notificacao quando a chave nao existe mais como sessao ativa
            # (permite re-alertar se o grupo abrir nova sessao depois)
            with _session_timeout_lock:
                active_keys = set(_load_sessions().keys())
                _session_timeout_notified.intersection_update(active_keys)
        except Exception as e:
            log.error(f'_session_cleanup_loop erro: {e}')
        time.sleep(30)

# Logger dedicado pro grupo (mensagens ignoradas, capturas, alertas)
_group_logger = logging.getLogger('maia.group')
_group_logger.setLevel(logging.INFO)
_group_logger.propagate = False
_group_handler = logging.FileHandler(LOGS / 'group.log')
_group_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
_group_logger.addHandler(_group_handler)

# Dedupe de alertas de captura de chat_id e de user-novo (evita spam ao Chefe)
_group_capture_alerted = False
_group_capture_lock = threading.Lock()
_unknown_user_alerted = {}   # user_id -> timestamp do ultimo alerta
_unknown_user_lock = threading.Lock()
UNKNOWN_USER_ALERT_THROTTLE = 3600  # 1h: re-avisa do mesmo user no maximo 1x por hora

def _send_admin_outbox_text(text, prefix='group_admin'):
    """Cria outbox de texto pro Chefe via canal oficial (regra critica CLAUDE.md)."""
    try:
        payload = {'chat_id': ADMIN_CHAT_ID, 'text': text}
        fname = f'{prefix}_{int(time.time()*1000)}.json'
        (OUTBOX / fname).write_text(json.dumps(payload, ensure_ascii=False))
        log.info(f'outbox admin criado: {fname}')
        return True
    except Exception as e:
        log.error(f'_send_admin_outbox_text erro: {e}')
        return False


# ---------------------------------------------------------------------------
# Claude Code interactive prompt interception
# ---------------------------------------------------------------------------
# A Maia roda num Claude Code CLI dentro da sessao tmux. Quando o Claude pede
# autorizacao do usuario (editar arquivo, rodar comando, telemetria) ele renderiza
# um prompt interativo do tipo:
#
#   Do you want to make this edit to /tmp/foo.txt?
#   ❯ 1. Yes
#     2. Yes, and don't ask again this session (shift+tab)
#     3. No, and tell Claude what to do differently (esc)
#
# Como o Chefe so interage por Telegram, sem essa interceptacao a Maia trava
# indefinidamente. Esse modulo detecta esses prompts no capture-pane, manda
# InlineKeyboardMarkup pro Telegram, e quando o Chefe clica no botao envia o
# numero correspondente via `tmux send-keys` pra desbloquear.
#
# IMPORTANTE (regra critica do CLAUDE.md): toda mensagem enviada ao Chefe
# fora do canal /opt/MAIA/bot/outbox/ DEVE chamar `mark_maia_responded()`
# pra nao estourar o watchdog. Isso vale tanto pra mensagem com botoes quanto
# pra resposta do callback.
CLAUDE_PROMPT_POLL_INTERVAL = float(env.get('CLAUDE_PROMPT_POLL_INTERVAL', '2'))
CLAUDE_PROMPT_DEDUP_TTL = float(env.get('CLAUDE_PROMPT_DEDUP_TTL', '60'))

# ---------------------------------------------------------------------------
# AUTO-APPROVE: aprova automaticamente prompts em paths seguros sem mandar
# botoes pro Telegram. Evita spam de aprovacoes em edicoes de scripts de skill.
# ---------------------------------------------------------------------------
# Habilita/desabilita globalmente (default ON; setar 0/false desliga).
AUTO_APPROVE_ENABLED = env.get('AUTO_APPROVE_ENABLED', '1').strip().lower() not in ('0', 'false', 'no', 'off', '')
# Cap deslizante: max auto-aprovacoes por janela de AUTO_APPROVE_WINDOW_SEC.
AUTO_APPROVE_CAP_PER_HOUR = int(env.get('AUTO_APPROVE_CAP_PER_HOUR', '50'))
AUTO_APPROVE_WINDOW_SEC = float(env.get('AUTO_APPROVE_WINDOW_SEC', '3600'))
# Janela de fallback quando o cap estoura: bot volta a mandar botoes
# por esse tempo antes de retomar o auto-approve.
AUTO_APPROVE_COOLDOWN_SEC = float(env.get('AUTO_APPROVE_COOLDOWN_SEC', '3600'))
AUTO_APPROVE_LOG = LOGS / 'auto_approved.log'

# Regex dos paths que SAO seguros pra auto-aprovar (whitelist).
# Cobre /opt/MAIA/.claude/skills/<qualquer-skill>/{scripts,assets,templates}/<qualquer subpath>
AUTO_APPROVE_SAFE_PATH_RE = re.compile(
    r'^/opt/MAIA/\.claude/skills/[^/]+/(?:scripts|assets|templates)/.+',
    re.IGNORECASE,
)

# Regex dos paths que NUNCA podem ser auto-aprovados, mesmo que casem na
# whitelist por acidente (blacklist tem prioridade). SKILL.md fica dentro de
# .claude/skills/<skill>/ entao vai ser barrado aqui mesmo que `scripts/SKILL.md`
# nao seja um caso real — defesa em profundidade.
AUTO_APPROVE_DENY_PATTERNS = [
    re.compile(r'/SKILL\.md$', re.IGNORECASE),
    re.compile(r'/opt/MAIA/\.claude/agents(?:/|$)', re.IGNORECASE),
    re.compile(r'/opt/MAIA/\.claude/hooks(?:/|$)', re.IGNORECASE),
    re.compile(r'/opt/MAIA/CLAUDE\.md$', re.IGNORECASE),
    re.compile(r'/settings(?:\.local)?\.json$', re.IGNORECASE),
    re.compile(r'(?:^|/)\.env(?:\.[\w\-]+)?$', re.IGNORECASE),
]

# Verbos que sao elegiveis pra auto-approve. Bash/run/execute NAO entram —
# brief explicito: apenas Edit/Write em arquivo (make/apply/create/overwrite).
AUTO_APPROVE_SAFE_VERBS = {'make', 'apply', 'create', 'overwrite'}

# Contador deslizante de auto-aprovacoes (lista de timestamps).
_auto_approve_history = []   # list[float] timestamps
_auto_approve_cooldown_until = 0.0  # epoch ate quando fica em fallback
_auto_approve_cap_alert_sent_at = 0.0  # evita repetir alerta no Telegram
_auto_approve_lock = threading.Lock()

# Padroes de prompts (regex case-insensitive, MULTILINE/DOTALL onde necessario).
# Cobrem variacoes documentadas do Claude Code CLI (Sonnet/Opus). O texto da
# pergunta nem sempre termina no `?` da mesma linha — usar lookahead na lista
# numerada e mais robusto.
#
# HISTORICO DE EVOLUCAO:
# - 2026-05-05: regex inicial com 2 padroes especificos (edit + proceed). Falhou
#   quando Claude Code pediu autorizacao pra CREATE arquivo novo em path protegido
#   (ex: .claude/agents/**), porque o regex de "edit" so cobria "make this edit" e
#   "apply this edit", mas a string real era "Do you want to create <path>?".
# - 2026-05-12: substituido por UM regex GENERICO que casa qualquer verbo apos
#   "Do you want to". Captura o verbo no grupo 1 pra usar dinamicamente na msg
#   do Telegram. Validacao por menu numerado (CLAUDE_PROMPT_MENU_RE) impede
#   falso positivo em logs/output que contenham acidentalmente "Do you want to X?".
#
# VERBOS CONHECIDOS DO CLAUDE CODE (cobertos automaticamente pelo regex generico):
#   - make    -> "Do you want to make this edit to <path>?"        (Edit em arquivo existente)
#   - create  -> "Do you want to create <path>?"                   (Write em arquivo novo)
#   - proceed -> "Do you want to proceed?"                          (Bash interativo, generico)
#   - run     -> "Do you want to run this command?"                 (Bash, variante)
#   - execute -> "Do you want to execute this script?"              (Bash, variante)
#   - apply   -> "Do you want to apply this edit to <path>?"        (Edit, variante)
#   - delete  -> "Do you want to delete <path>?"                    (rm/Delete tools)
#   - continue-> "Do you want to continue?"                          (confirmacao generica)
# Qualquer verbo novo que a Anthropic introduzir tambem casa, desde que o pane tenha
# o menu numerado 1.Yes/2.Yes-dont-ask/3.No abaixo da pergunta.
CLAUDE_PROMPT_PATTERNS = {
    # PADRAO GENERICO: qualquer "Do you want to <verbo> ... ?"
    # O verbo (grupo 1) e usado pra montar a msg do Telegram dinamicamente.
    # Limite de 300 chars entre verbo e '?' pra evitar match em texto solto.
    # NAO usa $ no final porque a pergunta as vezes quebra linha antes do '?'.
    'generic': re.compile(
        r'Do you want to (\w+)\b[^?\n]{0,300}\?',
        re.IGNORECASE,
    ),
    # PADRAO RATING: Session rating (auto-dismiss, NAO envia pro Telegram)
    # Ex: "How is Claude doing this session?"
    # Mantido separado porque tem comportamento diferente (auto-resposta com 0+Enter).
    'rating': re.compile(
        r"How is Claude doing this session\??",
        re.IGNORECASE,
    ),
}

# Verifica se o pane tem o menu numerado tipico (1. Yes / 2. Yes... / 3. No)
# que confirma que e um prompt vivo aguardando input — evita falso positivo se
# a pergunta aparece no meio de um texto qualquer.
CLAUDE_PROMPT_MENU_RE = re.compile(
    r'(?:❯\s*)?1\.\s*Yes.*?2\.\s*Yes,\s*and\s+don.t\s+ask\s+again.*?3\.\s*No',
    re.IGNORECASE | re.DOTALL,
)

# Dedupe: hash -> timestamp (TTL CLAUDE_PROMPT_DEDUP_TTL). Quando o mesmo prompt
# fica visivel por varios ciclos de poll, so envia 1 botao ao Telegram.
recent_prompts = {}  # hash -> {'first_seen', 'telegram_msg_id', 'kind', 'callback_id'}
recent_prompts_lock = threading.Lock()

# Mapeia callback_id (UUID curto incluido no callback_data dos botoes) -> info
# necessaria pro handler: chat_id, message_id da msg com botoes, kind do prompt,
# e o pane snippet pra historico.
pending_callbacks = {}  # callback_id -> {'chat_id', 'message_id', 'kind', 'created_at'}
pending_callbacks_lock = threading.Lock()

def flush_pending():
    """Chamado pelo timer quando passa DEBOUNCE_SECONDS sem nova msg."""
    global debounce_timer
    with debounce_lock:
        if not pending_buffer:
            debounce_timer = None
            return
        items = list(pending_buffer)
        pending_buffer.clear()
        debounce_timer = None
    # Combina todas as mensagens em uma so injecao
    user = items[0]['user']
    chat_id = items[-1]['chat_id']
    if len(items) == 1:
        msg_id = items[0]['msg_id']
        text = items[0]['text']
    else:
        # Multiplas msgs: junta com separador, usa msg_id da ultima pra reply_to
        msg_id = items[-1]['msg_id']
        ids = ','.join(str(i['msg_id']) for i in items)
        joined = '\n'.join(i['text'] for i in items)
        text = f'[debounced {len(items)} msgs ids={ids}] {joined}'
        log.info(f'debounce flush: {len(items)} msgs combinadas, last_id={msg_id}')
    notify_maia(msg_id, text, user, chat_id)

def enqueue_message(msg_id, text, user, chat_id):
    """Adiciona msg ao buffer e (re)agenda o flush para DEBOUNCE_SECONDS."""
    global debounce_timer
    with debounce_lock:
        pending_buffer.append({
            'msg_id': msg_id, 'text': text, 'user': user, 'chat_id': chat_id
        })
        if debounce_timer is not None:
            try:
                debounce_timer.cancel()
            except Exception:
                pass
        debounce_timer = threading.Timer(DEBOUNCE_SECONDS, flush_pending)
        debounce_timer.daemon = True
        debounce_timer.start()
        log.info(f'enqueue msg_id={msg_id} (buffer size={len(pending_buffer)}, timer={DEBOUNCE_SECONDS}s)')

def signal_handler(sig, frame):
    global running
    log.info(f'Signal {sig} - parando graciosamente')
    running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGHUP, signal_handler)

def get_offset():
    f = STATE / 'last-update-id.txt'
    return int(f.read_text().strip()) + 1 if f.exists() else 0

def save_offset(uid):
    (STATE / 'last-update-id.txt').write_text(str(uid))

def notify_maia(msg_id, text, user, chat_id=None):
    try:
        # Escapa aspas e quebras de linha pra tmux
        safe = text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')
        # Telegram limit is 4096 chars per message; keep some margin for tmux escaping
        if len(safe) > 4000:
            safe = safe[:4000] + '...'
        prompt = f'[telegram from {user} chat_id={chat_id} msg_id={msg_id}] {safe}'
        # Manda texto literal e depois Enter separado (mais confiavel)
        subprocess.run(
            ['tmux', 'send-keys', '-t', TMUX_SESSION, '-l', prompt],
            check=False, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.3)  # tmux precisa de um pouco pra registrar input
        subprocess.run(
            ['tmux', 'send-keys', '-t', TMUX_SESSION, 'C-m'],
            check=False, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        log.info(f'Maia notificada msg_id={msg_id}')
        mark_maia_notified(msg_id, chat_id)
    except Exception as e:
        log.error(f'notify_maia error: {e}')

def mark_maia_notified(msg_id, chat_id):
    with pending_maia_lock:
        pending_maia[msg_id] = {
            'notified_at': time.time(),
            'chat_id': chat_id or ADMIN_CHAT_ID,
            'last_alert_at': 0,
        }

def mark_maia_responded():
    """Chamado quando uma resposta sai pro Telegram. Limpa todas as expectativas
    pendentes (Maia normalmente envia uma resposta consolidando todas as msgs do debounce)."""
    with pending_maia_lock:
        if pending_maia:
            pending_maia.clear()

def capture_maia_pane():
    try:
        r = subprocess.run(
            ['tmux', '-S', TMUX_SOCKET, 'capture-pane', '-t', TMUX_SESSION, '-p'],
            capture_output=True, timeout=5, text=True
        )
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        return '\n'.join(lines[-25:])
    except Exception as e:
        return f'(captura falhou: {e})'

def send_admin_alert(text):
    try:
        requests.post(f'{API}/sendMessage', json={
            'chat_id': ADMIN_CHAT_ID,
            'text': text,
            'parse_mode': 'Markdown',
        }, timeout=10)
    except Exception as e:
        log.error(f'admin alert send error: {e}')

def diagnose_pane(pane_text):
    low = pane_text.lower()
    if "you've hit your limit" in low or 'rate limit' in low or 'usage limit' in low:
        return 'limite do plano (Pro/Max) estourou — cota da API esgotou'
    if 'error 400' in low or 'invalid_request_error' in low:
        return 'erro 400 da API (input invalido — talvez imagem/anexo corrompido)'
    if 'error 401' in low or 'unauthorized' in low:
        return 'erro de autenticacao (token expirado/invalido)'
    if 'error 500' in low or 'internal server error' in low or 'overloaded' in low:
        return 'API da Anthropic instavel/sobrecarregada'
    if 'connection' in low and ('error' in low or 'reset' in low or 'refused' in low):
        return 'problema de rede/conectividade'
    if 'enotfound' in low or 'getaddrinfo' in low:
        return 'falha de DNS'
    return None

# Sinais de que a Maia esta ATIVAMENTE trabalhando (turn em andamento) — usado
# pelo watchdog pra NAO confundir "lenta" com "travada". Regra de memoria:
# "Maia lenta != travada — medir antes de propor reset".
MAIA_WORKING_RE = re.compile(
    r'esc to interrupt'            # Claude Code so mostra isso com um turn rodando
    r'|still thinking'             # raciocinio em andamento
    r'|[\u2191\u2193]\s*[\d.,]+\s*tokens',  # streaming de tokens (up/down)
    re.IGNORECASE,
)

def is_maia_working(pane_text):
    """True se o pane mostra que ela esta gerando/pensando agora (nao travada)."""
    if not pane_text:
        return False
    return bool(MAIA_WORKING_RE.search(pane_text))

# Ruido do TUI que nao ajuda no diagnostico e so polui o alerta no DM.
_ALERT_NOISE = (
    'bypass permissions', 'auto-update', 'shift+tab', 'ctrl+o to expand',
    'connect claude to your ide', '/ide', '/doctor', 'esc to interrupt',
)

def _clean_pane_for_alert(pane_text):
    """Deixa so as linhas uteis das ultimas do pane pro alerta ficar legivel."""
    out = []
    for l in (pane_text or '').splitlines():
        s = l.strip()
        if not s:
            continue
        if s[0] in '\u2500\u2014-' and len(set(s)) <= 2:  # linhas separadoras
            continue
        low = s.lower()
        if any(d in low for d in _ALERT_NOISE):
            continue
        out.append(s)
    return '\n'.join(out[-10:])

def _tmux_send_keys(keys, literal=False):
    """Manda keys pra sessao do Claude Code (mesma sessao que recebe inputs do Chefe).
    Usa o socket explicito e o session name do .env. Retorna True/False.
    `literal=True` usa `-l` (texto literal, sem interpretar como key-binding).
    """
    cmd = ['tmux', '-S', TMUX_SOCKET, 'send-keys', '-t', TMUX_SESSION]
    if literal:
        cmd.append('-l')
    cmd.append(keys)
    try:
        r = subprocess.run(cmd, check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode != 0:
            log.warning(f'tmux send-keys falhou rc={r.returncode}: {r.stderr.decode()[:200]}')
            return False
        return True
    except Exception as e:
        log.error(f'tmux send-keys erro: {e}')
        return False


def _prompt_hash(pane_text):
    """Hash estavel do prompt: primeira linha que casa com pergunta + linha do '❯'.
    TTL impede duplicacao quando o pane reaparece no proximo poll.
    """
    lines = [l for l in pane_text.splitlines() if l.strip()]
    question_line = ''
    cursor_line = ''
    for l in lines:
        if not question_line and 'Do you want to' in l:
            question_line = l.strip()
        if not cursor_line and l.strip().startswith('❯'):
            cursor_line = l.strip()
        if question_line and cursor_line:
            break
    key = (question_line + '||' + cursor_line).encode('utf-8', errors='ignore')
    return hashlib.sha1(key).hexdigest()[:16]


def _prune_recent_prompts():
    """Remove entradas do dedupe map cuja idade > TTL."""
    now = time.time()
    with recent_prompts_lock:
        stale = [h for h, st in recent_prompts.items()
                 if now - st['first_seen'] > CLAUDE_PROMPT_DEDUP_TTL]
        for h in stale:
            del recent_prompts[h]


def _extract_target_from_question(question_line, verb):
    """Tenta extrair o alvo (caminho/arquivo) da pergunta com base no verbo.
    Heuristica simples — caso nao consiga, retorna ''.
    Exemplos:
      "Do you want to make this edit to /tmp/foo.txt?" verb=make  -> "/tmp/foo.txt"
      "Do you want to create /opt/foo/bar.py?"          verb=create -> "/opt/foo/bar.py"
      "Do you want to delete /tmp/x?"                   verb=delete -> "/tmp/x"
      "Do you want to proceed?"                         verb=proceed -> ""
    """
    if not question_line:
        return ''
    # Remove o prefixo "Do you want to <verbo> [this edit to|this change to]" pra
    # nao casar pedacos errados. Pega tudo depois do ultimo "to " se houver.
    tail = question_line
    m_to = re.search(r'\bto\s+(.+?)\??\s*$', question_line, re.IGNORECASE)
    if m_to:
        tail = m_to.group(1).strip()
    # Ordem de preferencia (tenta cada regex; aceita o mais completo):
    # 1) Path absoluto OU dotfile/path relativo com extensao
    #    (ex: /opt/foo/bar.py, .claude/agents/x.md, src/file.py, bot.py).
    #    Inicia com '/', '.' ou letra/digit. Aceita ate qualquer extensao curta.
    m = re.search(r'((?:/|\.[\w]|\w)[\w\-./]*\.\w{1,8})', tail)
    if m:
        return m.group(1).rstrip('?.,;:')
    # 2) Path absoluto sem extensao (ex: /tmp/old)
    m = re.search(r'(/[\w\-./]+)', tail)
    if m:
        return m.group(1).rstrip('?.,;:')
    # 3) Path relativo sem extensao mas com '/' (ex: scripts/deploy)
    m = re.search(r'([\w\-./]+/[\w\-./]+)', tail)
    if m:
        return m.group(1).rstrip('?.,;:')
    return ''


def detect_claude_prompt(pane_text):
    """Inspeciona pane_text e retorna (kind, match_extra) se identificar prompt interativo.
    kind in {'generic', 'rating', None}. match_extra e dict opcional com info do regex:
        - verb: verbo capturado (make/create/proceed/run/delete/...)
        - target: caminho/arquivo extraido da pergunta (vazio se nao tiver)
        - question: linha completa da pergunta (pra debug/log)
    Retorna (None, None) se nao houver prompt valido.

    REGRA CRITICA: padrao 'generic' EXIGE o menu numerado abaixo da pergunta
    (CLAUDE_PROMPT_MENU_RE). Sem essa validacao, qualquer texto com "Do you want
    to X?" no terminal disparava falso positivo. Com o gate, so prompts vivos
    do Claude Code passam.
    """
    if not pane_text:
        return None, None
    # Rating: telemetria — auto-dismiss, independente do menu numerado.
    if CLAUDE_PROMPT_PATTERNS['rating'].search(pane_text):
        return 'rating', {}
    # Generic: exige o menu numerado 1.Yes/2.Yes/3.No pra confirmar
    if not CLAUDE_PROMPT_MENU_RE.search(pane_text):
        return None, None
    m = CLAUDE_PROMPT_PATTERNS['generic'].search(pane_text)
    if not m:
        return None, None
    verb = (m.group(1) or '').lower()
    # Extrai a linha completa da pergunta pra heuristica de target e log
    question_line = ''
    for l in pane_text.splitlines():
        if 'Do you want to' in l:
            question_line = l.strip()
            break
    target = _extract_target_from_question(question_line, verb)
    return 'generic', {
        'verb': verb,
        'target': target,
        'question': question_line,
    }


def extract_prompt_snippet(pane_text, kind):
    """Retorna trecho relevante do pane pra colocar no Telegram (caption do botao)."""
    lines = pane_text.splitlines()
    # acha a linha do "Do you want to" e pega +-6 linhas em volta
    idx = -1
    for i, l in enumerate(lines):
        if 'Do you want to' in l or 'How is Claude' in l:
            idx = i
            break
    if idx < 0:
        return ''
    start = max(0, idx - 4)
    end = min(len(lines), idx + 8)
    snippet_lines = [l for l in lines[start:end] if l.strip()]
    snippet = '\n'.join(snippet_lines)
    # Telegram permite ate ~4096 chars; deixa folga
    if len(snippet) > 1200:
        snippet = snippet[:1200] + '...'
    return snippet


# Mapa verbo -> rotulo amigavel em PT-BR pra mensagem do Telegram.
# Adicionar novos verbos aqui conforme forem descobertos em producao.
# Fallback: verbo nao mapeado vira ".upper()" no titulo (ex: "Claude pediu autorizacao para FROBNICATE").
CLAUDE_VERB_LABELS = {
    'make':    ('EDITAR',    'editar arquivo existente'),
    'apply':   ('EDITAR',    'aplicar edicao'),
    'create':  ('CRIAR',     'criar arquivo novo'),
    'delete':  ('DELETAR',   'deletar arquivo'),
    'remove':  ('REMOVER',   'remover'),
    'proceed': ('CONTINUAR', 'continuar execucao'),
    'continue':('CONTINUAR', 'continuar'),
    'run':     ('EXECUTAR',  'rodar comando'),
    'execute': ('EXECUTAR',  'executar script'),
    'install': ('INSTALAR',  'instalar dependencia'),
    'update':  ('ATUALIZAR', 'atualizar'),
    'overwrite': ('SOBRESCREVER', 'sobrescrever arquivo'),
}


def send_claude_prompt_buttons(kind, extra, pane_text):
    """Envia mensagem com InlineKeyboardMarkup pro Chefe pedindo decisao.
    Retorna (ok, telegram_message_id, callback_id).
    Apos enviar com sucesso, chama mark_maia_responded() — regra critica do CLAUDE.md.
    """
    callback_id = uuid.uuid4().hex[:12]
    snippet = extract_prompt_snippet(pane_text, kind)
    if kind == 'generic':
        verb = (extra.get('verb') or '').lower()
        target = extra.get('target') or ''
        label, desc = CLAUDE_VERB_LABELS.get(verb, (verb.upper() or 'AUTORIZAR', f'`{verb}`'))
        if target:
            title = f"Claude pediu autorizacao para {label}: `{target}`"
        else:
            title = f"Claude pediu autorizacao para {label} ({desc})"
    else:
        title = "Claude pediu autorizacao"
    body = title
    if snippet:
        # usa code block pra preservar layout do prompt original
        body = f"{title}\n\n```\n{snippet}\n```"
    if len(body) > 3800:
        body = body[:3800] + '...'
    keyboard = {
        'inline_keyboard': [
            [{'text': 'Sim', 'callback_data': f'claude_prompt:{callback_id}:1'}],
            [{'text': "Sim + nao perguntar de novo", 'callback_data': f'claude_prompt:{callback_id}:2'}],
            [{'text': 'Nao', 'callback_data': f'claude_prompt:{callback_id}:3'}],
        ]
    }
    try:
        payload = {
            'chat_id': ADMIN_CHAT_ID,
            'text': body,
            'parse_mode': 'Markdown',
            'reply_markup': json.dumps(keyboard),
        }
        r = requests.post(f'{API}/sendMessage', json=payload, timeout=10)
        if r.status_code != 200 or not r.json().get('ok'):
            log.warning(f'send_claude_prompt_buttons http {r.status_code}: {r.text[:200]}')
            return False, None, None
        tg_msg_id = r.json()['result']['message_id']
        with pending_callbacks_lock:
            pending_callbacks[callback_id] = {
                'chat_id': ADMIN_CHAT_ID,
                'message_id': tg_msg_id,
                'kind': kind,
                'extra': extra,
                'created_at': time.time(),
            }
        # CRITICO: avisa o watchdog que respondemos algo ao Chefe fora do outbox.
        mark_maia_responded()
        stop_typing(ADMIN_CHAT_ID)
        log.info(f'claude_prompt botoes enviados kind={kind} callback_id={callback_id} tg_msg_id={tg_msg_id}')
        return True, tg_msg_id, callback_id
    except Exception as e:
        log.error(f'send_claude_prompt_buttons erro: {e}')
        return False, None, None


def auto_dismiss_rating(pane_text):
    """Padrao C: telemetria 'How is Claude doing this session?' — manda '0' Enter
    pra dispensar sem incomodar o Chefe. Apenas loga."""
    log.info("claude_prompt rating detectado — auto-dismiss (envia 0+Enter)")
    # 0 fecha o dialogo de rating sem dar nota
    _tmux_send_keys('0', literal=True)
    time.sleep(0.2)
    _tmux_send_keys('C-m', literal=False)


def _is_safe_auto_approve_path(target_path):
    """Decide se `target_path` esta na whitelist de auto-aprovacao.
    Retorna (ok, reason). Blacklist tem prioridade sobre whitelist.
    """
    if not target_path:
        return False, 'sem_path_extraido'
    # Tenta normalizar pra path absoluto se vier relativo (algumas perguntas
    # do Claude vem com path relativo a partir do cwd da sessao).
    candidates = [target_path]
    if not target_path.startswith('/'):
        candidates.append('/opt/MAIA/' + target_path.lstrip('./'))
    matched_safe = False
    for cand in candidates:
        # 1) Blacklist primeiro
        for pat in AUTO_APPROVE_DENY_PATTERNS:
            if pat.search(cand):
                return False, f'deny_pattern:{pat.pattern}'
        # 2) Whitelist
        if AUTO_APPROVE_SAFE_PATH_RE.search(cand):
            matched_safe = True
    if matched_safe:
        return True, 'auto_safe_path'
    return False, 'fora_da_whitelist'


def _record_auto_approve():
    """Adiciona timestamp atual ao historico e remove entradas fora da janela.
    Retorna (cap_atingido_agora, total_na_janela).
    Quando cap_atingido_agora=True, dispara cooldown."""
    global _auto_approve_cooldown_until
    now = time.time()
    with _auto_approve_lock:
        cutoff = now - AUTO_APPROVE_WINDOW_SEC
        # Compacta o historico (lista nao deve crescer indefinidamente)
        _auto_approve_history[:] = [t for t in _auto_approve_history if t >= cutoff]
        _auto_approve_history.append(now)
        total = len(_auto_approve_history)
        cap_hit = total > AUTO_APPROVE_CAP_PER_HOUR
        if cap_hit and _auto_approve_cooldown_until < now:
            _auto_approve_cooldown_until = now + AUTO_APPROVE_COOLDOWN_SEC
        return cap_hit, total


def _is_in_auto_approve_cooldown():
    """True se ainda estamos no periodo de fallback pos-cap."""
    with _auto_approve_lock:
        return time.time() < _auto_approve_cooldown_until


def _write_auto_approve_log(verb, target, reason, extra_note=''):
    """Anexa linha ao auto_approved.log no formato:
       ISO_TIMESTAMP | verb | target_path | reason=...
    """
    try:
        line = '{ts} | {verb} | {target} | reason={reason}'.format(
            ts=datetime.now(timezone.utc).isoformat(),
            verb=verb or '?',
            target=target or '(sem_path)',
            reason=reason or '?',
        )
        if extra_note:
            line += ' | ' + extra_note
        with open(AUTO_APPROVE_LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception as e:
        log.error(f'auto_approve log erro: {e}')


def _send_auto_approve_cap_alert(total_in_window):
    """Manda outbox de alerta ao Chefe quando o cap estoura.
    Throttled: max 1 alerta por cooldown pra nao spammar.
    """
    global _auto_approve_cap_alert_sent_at
    now = time.time()
    with _auto_approve_lock:
        if now - _auto_approve_cap_alert_sent_at < AUTO_APPROVE_COOLDOWN_SEC:
            return
        _auto_approve_cap_alert_sent_at = now
    try:
        # Cria outbox em vez de chamar API direto (regra critica CLAUDE.md)
        payload = {
            'chat_id': ADMIN_CHAT_ID,
            'text': (
                'Atencao: cap de auto-aprovacao atingido '
                f'({total_in_window} em {int(AUTO_APPROVE_WINDOW_SEC/60)}min). '
                f'Suspeita de loop. Voltei pra modo botao por '
                f'{int(AUTO_APPROVE_COOLDOWN_SEC/60)}min. '
                f'Veja /opt/MAIA/bot/logs/auto_approved.log pra entender o que rodou.'
            ),
        }
        fname = f'auto_approve_cap_{int(now)}.json'
        (OUTBOX / fname).write_text(json.dumps(payload, ensure_ascii=False))
        log.warning(f'auto_approve CAP atingido (total={total_in_window}); outbox={fname}')
    except Exception as e:
        log.error(f'_send_auto_approve_cap_alert erro: {e}')


def auto_approve_prompt(extra, pane_text):
    """Tenta auto-aprovar o prompt detectado. Retorna True se auto-aprovou
    (chamador deve pular o envio de botoes). Retorna False se fora da
    whitelist, em cooldown, ou desabilitado — chamador segue fluxo normal.

    Side effects (quando aprova):
      - envia '1' + Enter via tmux send-keys
      - escreve linha em auto_approved.log
      - atualiza contador deslizante; se estourar cap, dispara cooldown
        e manda outbox de alerta ao Chefe.
    """
    if not AUTO_APPROVE_ENABLED:
        return False
    if _is_in_auto_approve_cooldown():
        # Em cooldown, nao auto-aprova nada — manda botao
        return False
    verb = (extra.get('verb') or '').lower()
    target = extra.get('target') or ''
    if verb not in AUTO_APPROVE_SAFE_VERBS:
        return False
    ok, reason = _is_safe_auto_approve_path(target)
    if not ok:
        # Loga apenas em DEBUG; nao polui o log de auditoria com nao-decisoes
        log.debug(f'auto_approve REJEITADO verb={verb} target={target!r} reason={reason}')
        return False
    # Envia "1" + Enter — mesma mecanica do callback Sim
    try:
        _tmux_send_keys('1', literal=True)
        time.sleep(0.2)
        _tmux_send_keys('C-m', literal=False)
    except Exception as e:
        log.error(f'auto_approve tmux falhou verb={verb} target={target}: {e}')
        return False
    # Registra auditoria + atualiza contador
    _write_auto_approve_log(verb, target, reason)
    cap_hit, total = _record_auto_approve()
    log.info(f'auto_approve OK verb={verb} target={target} total={total}/{AUTO_APPROVE_CAP_PER_HOUR}')
    if cap_hit:
        _write_auto_approve_log(verb, target, 'RATE_LIMIT_HIT', extra_note=f'total={total}')
        _send_auto_approve_cap_alert(total)
    return True


def claude_prompt_watcher_loop():
    """Thread paralela que captura o pane a cada CLAUDE_PROMPT_POLL_INTERVAL
    segundos e detecta prompts interativos. Independente do debounce do poll_loop:
    prompts sao prioridade absoluta sobre texto normal e nao podem esperar 8s.
    """
    log.info(
        f'claude_prompt_watcher iniciado '
        f'(poll={CLAUDE_PROMPT_POLL_INTERVAL}s, dedup_ttl={CLAUDE_PROMPT_DEDUP_TTL}s)'
    )
    while running:
        try:
            _prune_recent_prompts()
            # capture-pane completo (nao so as ultimas 25 linhas do diagnose), pra
            # garantir que pegamos a linha "Do you want to" mesmo se houver muito
            # scrollback abaixo do menu.
            try:
                r = subprocess.run(
                    ['tmux', '-S', TMUX_SOCKET, 'capture-pane', '-t', TMUX_SESSION, '-p'],
                    capture_output=True, timeout=5, text=True
                )
                pane_text = r.stdout or ''
            except Exception as e:
                log.debug(f'claude_prompt capture falhou: {e}')
                pane_text = ''
            if not pane_text:
                time.sleep(CLAUDE_PROMPT_POLL_INTERVAL)
                continue
            kind, extra = detect_claude_prompt(pane_text)
            if not kind:
                time.sleep(CLAUDE_PROMPT_POLL_INTERVAL)
                continue
            # Auto-dismiss rating
            if kind == 'rating':
                rhash = _prompt_hash(pane_text + '||rating')
                with recent_prompts_lock:
                    if rhash in recent_prompts:
                        time.sleep(CLAUDE_PROMPT_POLL_INTERVAL)
                        continue
                    recent_prompts[rhash] = {
                        'first_seen': time.time(),
                        'telegram_msg_id': None,
                        'kind': 'rating',
                        'callback_id': None,
                    }
                auto_dismiss_rating(pane_text)
                time.sleep(CLAUDE_PROMPT_POLL_INTERVAL)
                continue
            # Generic prompt (qualquer verbo): dedupe + auto-approve ou botoes
            phash = _prompt_hash(pane_text)
            with recent_prompts_lock:
                if phash in recent_prompts:
                    time.sleep(CLAUDE_PROMPT_POLL_INTERVAL)
                    continue
                # reserva o hash ja pra evitar 2 envios concorrentes do mesmo prompt
                recent_prompts[phash] = {
                    'first_seen': time.time(),
                    'telegram_msg_id': None,
                    'kind': kind,
                    'callback_id': None,
                }
            # AUTO-APPROVE: se o prompt e Edit/Write em path seguro de skill,
            # auto-aprova sem incomodar o Chefe. Mantem o hash no dedupe pra
            # nao re-processar o mesmo prompt no proximo poll.
            if auto_approve_prompt(extra, pane_text):
                # Marca o slot como auto-aprovado (sem telegram_msg_id)
                with recent_prompts_lock:
                    if phash in recent_prompts:
                        recent_prompts[phash]['kind'] = 'auto_approved'
                time.sleep(CLAUDE_PROMPT_POLL_INTERVAL)
                continue
            ok, tg_msg_id, callback_id = send_claude_prompt_buttons(kind, extra, pane_text)
            if ok:
                with recent_prompts_lock:
                    if phash in recent_prompts:
                        recent_prompts[phash]['telegram_msg_id'] = tg_msg_id
                        recent_prompts[phash]['callback_id'] = callback_id
            else:
                # Falhou envio — libera o hash pra tentar dnv no proximo poll
                with recent_prompts_lock:
                    recent_prompts.pop(phash, None)
        except Exception as e:
            log.error(f'claude_prompt_watcher_loop erro: {e}')
        time.sleep(CLAUDE_PROMPT_POLL_INTERVAL)


def handle_claude_prompt_callback(callback_query):
    """Processa clique do Chefe num botao inline do prompt do Claude.
    Envia o numero (1/2/3) pro tmux, responde answerCallbackQuery, edita a msg
    original removendo os botoes.
    """
    try:
        cq_id = callback_query.get('id')
        data = callback_query.get('data', '')
        # formato: claude_prompt:<callback_id>:<choice>
        parts = data.split(':')
        if len(parts) != 3 or parts[0] != 'claude_prompt':
            return False
        _, cb_id, choice = parts
        if choice not in ('1', '2', '3'):
            return False
        with pending_callbacks_lock:
            ctx = pending_callbacks.pop(cb_id, None)
        # Mesmo se ctx nao existir (ex: bot reiniciou), ainda manda keys pro tmux —
        # so nao consegue editar a msg original.
        # Envia a tecla pro Claude Code
        ok_keys = _tmux_send_keys(choice, literal=True)
        time.sleep(0.2)
        _tmux_send_keys('C-m', literal=False)
        # Texto pra resposta do callback (toast)
        label_map = {'1': 'Sim', '2': 'Sim + nao perguntar de novo', '3': 'Nao'}
        toast = f'Respondido: {label_map[choice]}'
        try:
            requests.post(f'{API}/answerCallbackQuery', json={
                'callback_query_id': cq_id,
                'text': toast,
                'show_alert': False,
            }, timeout=5)
        except Exception as e:
            log.debug(f'answerCallbackQuery erro: {e}')
        # Edita a mensagem original removendo botoes
        if ctx:
            try:
                orig_chat = ctx['chat_id']
                orig_msg = ctx['message_id']
                # tenta recuperar o texto original
                orig_text = callback_query.get('message', {}).get('text') or ''
                if not orig_text:
                    orig_text = callback_query.get('message', {}).get('caption') or 'Prompt do Claude'
                new_text = f"{orig_text}\n\n*Respondido:* {label_map[choice]}"
                if len(new_text) > 4000:
                    new_text = new_text[:4000]
                requests.post(f'{API}/editMessageText', json={
                    'chat_id': orig_chat,
                    'message_id': orig_msg,
                    'text': new_text,
                    'parse_mode': 'Markdown',
                    'reply_markup': json.dumps({'inline_keyboard': []}),
                }, timeout=5)
            except Exception as e:
                log.warning(f'editMessageText erro: {e}')
        # Regra critica: resposta enviada ao Chefe fora do outbox -> avisa watchdog
        mark_maia_responded()
        log.info(f'claude_prompt callback processado callback_id={cb_id} choice={choice} keys_ok={ok_keys}')
        return True
    except Exception as e:
        log.error(f'handle_claude_prompt_callback erro: {e}')
        return False


def watchdog_loop():
    log.info(
        f'watchdog iniciado (timeout={MAIA_TIMEOUT_SECONDS}s, '
        f'check={HEALTHCHECK_INTERVAL}s, throttle={HEALTHCHECK_ALERT_THROTTLE}s, '
        f'admin_chat_id={ADMIN_CHAT_ID})'
    )
    while running:
        end = time.time() + HEALTHCHECK_INTERVAL
        while running and time.time() < end:
            time.sleep(1)
        if not running:
            break
        try:
            now = time.time()
            stale = []
            with pending_maia_lock:
                for mid, st in pending_maia.items():
                    age = now - st['notified_at']
                    since_alert = now - st['last_alert_at']
                    if age > MAIA_TIMEOUT_SECONDS and since_alert > HEALTHCHECK_ALERT_THROTTLE:
                        stale.append((mid, age, st))
            for mid, age, st in stale:
                pane = capture_maia_pane()
                # Mede antes de gritar: se ela esta gerando/pensando agora, NAO e
                # travamento — e tarefa longa. Suprime o alerta e segue vigiando.
                if is_maia_working(pane):
                    log.info(
                        f'WATCHDOG: msg_id={mid} idade={int(age)}s — Maia ATIVA '
                        f'(turn em andamento), alerta suprimido'
                    )
                    continue
                cause = diagnose_pane(pane)
                cause_line = f'\n\n*Causa provavel:* {cause}' if cause else ''
                snippet = _clean_pane_for_alert(pane)
                msg = (
                    f'⚠️ *{ASSISTANT_NAME} silenciosa ha {int(age/60)}min* (msg\\_id={mid}).{cause_line}\n\n'
                    f'_Ultimas linhas da tmux:_\n```\n{snippet}\n```'
                )
                send_admin_alert(msg)
                with pending_maia_lock:
                    if mid in pending_maia:
                        pending_maia[mid]['last_alert_at'] = now
                log.warning(f'WATCHDOG alerta enviado: msg_id={mid} idade={int(age)}s causa={cause!r}')
        except Exception as e:
            log.error(f'watchdog loop error: {e}')

def react(chat_id, msg_id, emoji='👀'):
    try:
        requests.post(f'{API}/setMessageReaction', json={
            'chat_id': chat_id, 'message_id': msg_id,
            'reaction': [{'type': 'emoji', 'emoji': emoji}]
        }, timeout=5)
    except Exception:
        pass

def start_typing(chat_id, duration=600):
    with typing_lock:
        typing_until[int(chat_id)] = time.time() + duration

def stop_typing(chat_id):
    with typing_lock:
        typing_until.pop(int(chat_id), None)

def typing_loop():
    log.info('typing indicator loop iniciado')
    while running:
        try:
            now = time.time()
            with typing_lock:
                active = [cid for cid, until in typing_until.items() if until > now]
                expired = [cid for cid, until in typing_until.items() if until <= now]
                for cid in expired:
                    del typing_until[cid]
            for cid in active:
                try:
                    requests.post(f'{API}/sendChatAction',
                        json={'chat_id': cid, 'action': 'typing'},
                        timeout=3)
                except Exception:
                    pass
        except Exception as e:
            log.debug(f'typing error: {e}')
        time.sleep(4)

def download_telegram_file(file_id, dest_dir, msg_id):
    """Baixa arquivo do Telegram. Retorna path ou None"""
    try:
        r = requests.get(f'{API}/getFile', params={'file_id': file_id}, timeout=10)
        if r.status_code != 200 or not r.json().get('ok'):
            log.error(f'getFile failed: {r.text[:200]}')
            return None
        fp = r.json()['result']['file_path']
        url = f'https://api.telegram.org/file/bot{TOKEN}/{fp}'
        ext = fp.split('.')[-1] if '.' in fp else 'ogg'
        dest = dest_dir / f'{msg_id}.{ext}'
        r2 = requests.get(url, timeout=30)
        if r2.status_code != 200:
            log.error(f'download failed: {r2.status_code}')
            return None
        dest.write_bytes(r2.content)
        log.info(f'audio baixado msg_id={msg_id} ({len(r2.content)} bytes)')
        return dest
    except Exception as e:
        log.error(f'download_telegram_file error: {e}')
        return None

def transcribe_whisper(audio_path):
    """Transcreve audio via modulo hibrido (faster-whisper local por default, fallback API).

    Migrado em 2026-05-15: usa /opt/MAIA/lib/whisper_transcribe.py.
    Default provider = 'local' (gratis, ~25s pra audio de 13s). Fallback automatico pra OpenAI API.
    Override via ENV WHISPER_DEFAULT_PROVIDER=api.
    """
    try:
        import sys
        if '/opt/MAIA' not in sys.path:
            sys.path.insert(0, '/opt/MAIA')
        from lib.whisper_transcribe import transcribe as hybrid_transcribe
        text = hybrid_transcribe(audio_path, provider='api', language='pt')
        log.info(f'whisper transcribed (hybrid): {text[:100]}')
        return text
    except Exception as e:
        log.error(f'whisper error (hybrid): {e}')
        return None

# Limite duro do Whisper API (OpenAI): 25 MB por arquivo
WHISPER_MAX_BYTES = 25 * 1024 * 1024
# Custo Whisper API: USD 0.006/min. Cotacao BRL aproximada (atualiza se mudar muito).
WHISPER_USD_PER_MIN = 0.006
WHISPER_BRL_PER_USD = 5.10

def extract_audio_from_msg(msg):
    """Detecta voice/audio/video_note/video em uma mensagem do Telegram.
    Retorna (file_id, kind, duration_s, file_size) ou (None, None, 0, 0).

    'video' = MP4 normal enviado como video (nao video_note redondo).
    Para video, o pipeline depois extrai a faixa de audio via ffmpeg.
    """
    for kind in ('voice', 'audio', 'video_note', 'video'):
        if kind in msg:
            obj = msg[kind] or {}
            return (
                obj.get('file_id'),
                kind,
                int(obj.get('duration') or 0),
                int(obj.get('file_size') or 0),
            )
    return None, None, 0, 0


# Limite duro para download de mídia visual (foto/documento). 50MB casa com o
# limite máximo de upload de bot do Telegram (sendDocument).
VISUAL_MEDIA_MAX_BYTES = 50 * 1024 * 1024


def extract_visual_media_from_msg(msg, msg_id):
    """Detecta photo/document na mensagem, baixa o arquivo e retorna metadata.

    Adicionado 2026-05-25 pra suportar fotos e PDFs enviados pelo usuario em
    grupo (e por qualquer user em DM). NÃO inclui voice/audio/video — esses
    seguem em extract_audio_from_msg/transcribe_telegram_audio.

    Retorna dict:
      {
        'kind': 'photo' | 'document' | None,
        'file_id': str | None,
        'file_path': Path | None,
        'file_size': int,
        'mime_type': str,
        'file_name': str,
        'too_large': bool,
        'error': str | None,
      }
    'kind' = None significa "msg não tinha photo nem document" (delegar pro
    fluxo de áudio/texto/non-text normalmente).
    """
    result = {
        'kind': None, 'file_id': None, 'file_path': None,
        'file_size': 0, 'mime_type': '', 'file_name': '',
        'too_large': False, 'error': None,
    }
    if 'photo' in msg and isinstance(msg['photo'], list) and msg['photo']:
        # Telegram envia varias resolucoes — pega a maior.
        photos = msg['photo']
        biggest = max(photos, key=lambda p: int(p.get('file_size') or 0))
        result['kind'] = 'photo'
        result['file_id'] = biggest.get('file_id')
        result['file_size'] = int(biggest.get('file_size') or 0)
        result['mime_type'] = 'image/jpeg'
        if result['file_size'] > VISUAL_MEDIA_MAX_BYTES:
            result['too_large'] = True
            return result
        path = _download_visual_file(result['file_id'], PHOTO_IN, msg_id, 'jpg')
        if path is None:
            result['error'] = 'download_failed'
        else:
            result['file_path'] = path
            result['file_name'] = path.name
        return result
    if 'document' in msg and isinstance(msg['document'], dict):
        doc = msg['document']
        result['kind'] = 'document'
        result['file_id'] = doc.get('file_id')
        result['file_size'] = int(doc.get('file_size') or 0)
        result['mime_type'] = doc.get('mime_type', '') or ''
        result['file_name'] = doc.get('file_name', '') or ''
        if result['file_size'] > VISUAL_MEDIA_MAX_BYTES:
            result['too_large'] = True
            return result
        # Mantem extensao original quando possivel (importante pra PDF/docx etc).
        ext = ''
        if result['file_name'] and '.' in result['file_name']:
            ext = result['file_name'].rsplit('.', 1)[-1].lower()
        elif result['mime_type']:
            ext = result['mime_type'].split('/')[-1].lower()
        if not ext or len(ext) > 8:
            ext = 'bin'
        path = _download_visual_file(result['file_id'], DOC_IN, msg_id, ext)
        if path is None:
            result['error'] = 'download_failed'
        else:
            result['file_path'] = path
            if not result['file_name']:
                result['file_name'] = path.name
        return result
    return result


def _download_visual_file(file_id, dest_dir, msg_id, default_ext):
    """Baixa arquivo visual (foto/documento) do Telegram. Retorna Path ou None.

    Versão paralela ao download_telegram_file mas:
    - respeita extensão fornecida (pra PDFs, JPGs, etc — não força .ogg)
    - log mais explícito (mídia visual, não áudio)
    """
    try:
        r = requests.get(f'{API}/getFile', params={'file_id': file_id}, timeout=10)
        if r.status_code != 200 or not r.json().get('ok'):
            log.error(f'getFile (visual) falhou msg_id={msg_id}: {r.text[:200]}')
            return None
        fp = r.json()['result']['file_path']
        url = f'https://api.telegram.org/file/bot{TOKEN}/{fp}'
        # Tenta usar extensao do file_path remoto se houver, senao default_ext.
        remote_ext = fp.rsplit('.', 1)[-1].lower() if '.' in fp else default_ext
        if not remote_ext or len(remote_ext) > 8:
            remote_ext = default_ext
        dest = dest_dir / f'{msg_id}.{remote_ext}'
        r2 = requests.get(url, timeout=60)
        if r2.status_code != 200:
            log.error(f'download visual falhou msg_id={msg_id}: http={r2.status_code}')
            return None
        dest.write_bytes(r2.content)
        log.info(
            f'midia visual baixada msg_id={msg_id} kind={dest_dir.name} '
            f'ext={remote_ext} bytes={len(r2.content)}'
        )
        return dest
    except Exception as e:
        log.error(f'_download_visual_file erro msg_id={msg_id}: {e}')
        return None


def video_has_audio_stream(video_path):
    """Retorna True se o video tem pelo menos uma stream de audio, False caso contrario.
    Usa ffprobe. Em caso de erro/timeout assume True (deixa ffmpeg decidir).
    """
    try:
        r = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-select_streams', 'a',
                '-show_entries', 'stream=codec_type',
                '-of', 'csv=p=0',
                str(video_path),
            ],
            capture_output=True, timeout=15, text=True,
        )
        return 'audio' in (r.stdout or '').lower()
    except Exception:
        return True  # fallback otimista


def extract_audio_track_from_video(video_path, msg_id):
    """Extrai a faixa de audio de um MP4 para MP3 mono 16kHz 64kbps via ffmpeg.

    Output otimizado pro Whisper (reduz drasticamente o tamanho do arquivo
    sem perda perceptivel de qualidade pra transcricao).
    Retorna:
      Path do MP3 se sucesso
      'NO_AUDIO' (str sentinel) se video nao tem stream de audio (mute)
      None se ffmpeg falhou por outro motivo
    """
    # Pre-check: video tem audio?
    if not video_has_audio_stream(video_path):
        log.warning(f'video sem stream de audio (mute) msg_id={msg_id}')
        return 'NO_AUDIO'

    out_path = AUDIO_IN / f'{msg_id}_from_video.mp3'
    try:
        result = subprocess.run(
            [
                'ffmpeg', '-y', '-i', str(video_path),
                '-vn',  # sem video
                '-acodec', 'libmp3lame',
                '-ar', '16000',  # 16kHz (suficiente pra Whisper)
                '-ac', '1',      # mono
                '-b:a', '64k',   # bitrate baixo
                str(out_path),
            ],
            capture_output=True, timeout=180,
        )
        if result.returncode != 0:
            log.error(
                f'ffmpeg falha extracao audio msg_id={msg_id}: '
                f'{result.stderr.decode(errors="ignore")[:300]}'
            )
            return None
        if not out_path.exists() or out_path.stat().st_size == 0:
            log.error(f'ffmpeg gerou arquivo vazio msg_id={msg_id}')
            return None
        log.info(
            f'audio extraido via ffmpeg msg_id={msg_id} '
            f'src_size={video_path.stat().st_size}B '
            f'out_size={out_path.stat().st_size}B'
        )
        return out_path
    except subprocess.TimeoutExpired:
        log.error(f'ffmpeg timeout extraindo audio msg_id={msg_id}')
        return None
    except Exception as e:
        log.error(f'ffmpeg exception msg_id={msg_id}: {e}')
        return None


def transcribe_telegram_audio(msg, *, context_label='dm'):
    """Pipeline completo: detecta audio em msg, baixa, transcreve, retorna dict.

    Reaproveitado por DM e grupo. Loga em log principal (bot.log) e tambem
    no _group_logger quando context_label='group'.

    Tambem trata video MP4 (kind='video'): baixa o video, salva o original em
    VIDEO_IN (para edicao posterior) e extrai a faixa de audio via ffmpeg
    antes de mandar pro Whisper.

    Retorna dict:
      {
        'file_id': str|None,
        'kind': 'voice'|'audio'|'video_note'|'video'|None,
        'duration': int (segundos),
        'audio_path': Path|None,
        'video_path': Path|None,        # so quando kind='video'
        'video_size_mb': float,         # so quando kind='video'
        'transcript': str|None,
        'too_large': bool,
        'error': str|None,
        'cost_brl': float,
      }
    Se nao tem audio na msg, retorna kind=None (chamador segue fluxo de texto).
    """
    result = {
        'file_id': None, 'kind': None, 'duration': 0,
        'audio_path': None, 'video_path': None, 'video_size_mb': 0.0,
        'transcript': None,
        'too_large': False, 'error': None, 'cost_brl': 0.0,
    }
    file_id, kind, duration, file_size = extract_audio_from_msg(msg)
    if not file_id:
        return result
    result['file_id'] = file_id
    result['kind'] = kind
    result['duration'] = duration
    msg_id = msg.get('message_id')

    if duration > 300:
        log.warning(
            f'audio longo (>5min) duration={duration}s msg_id={msg_id} '
            f'context={context_label} — vai processar mesmo assim'
        )
        if context_label == 'group':
            _group_logger.warning(
                f'audio longo (>5min) duration={duration}s msg_id={msg_id}'
            )

    log.info(
        f'audio recebido, transcrevendo... duration={duration}s '
        f'kind={kind} size={file_size}B msg_id={msg_id} context={context_label}'
    )
    if context_label == 'group':
        _group_logger.info(
            f'audio recebido, transcrevendo... duration={duration}s '
            f'kind={kind} size={file_size}B msg_id={msg_id}'
        )

    # ---------- VIDEO MP4 normal ----------
    if kind == 'video':
        log.info(
            f'video recebido, baixando... duration={duration}s '
            f'size={file_size}B (~{file_size/1024/1024:.2f}MB) msg_id={msg_id}'
        )
        if context_label == 'group':
            _group_logger.info(
                f'video recebido, baixando... duration={duration}s '
                f'size={file_size}B msg_id={msg_id}'
            )
        # Telegram bot API limita download a ~20MB. Se vier maior, getFile falha.
        video_path = download_telegram_file(file_id, VIDEO_IN, msg_id)
        if not video_path:
            result['error'] = 'download de video falhou (provavel >20MB no Telegram bot API)'
            log.error(
                f'video download falhou msg_id={msg_id} context={context_label} '
                f'size={file_size}B (Telegram bot API limita ~20MB)'
            )
            if context_label == 'group':
                _group_logger.error(
                    f'video download falhou msg_id={msg_id} size={file_size}B'
                )
            return result
        result['video_path'] = video_path
        try:
            real_size = video_path.stat().st_size
            result['video_size_mb'] = round(real_size / 1024 / 1024, 2)
        except Exception:
            real_size = file_size
        log.info(f'video salvo em {video_path} ({result["video_size_mb"]}MB)')

        # Extrai faixa de audio do video pra MP3 pequeno
        audio_path = extract_audio_track_from_video(video_path, msg_id)
        # Sentinel 'NO_AUDIO': video mudo, ignora silenciosamente (sem alarme)
        if audio_path == 'NO_AUDIO':
            result['error'] = 'video_silent'
            log.warning(
                f'video sem audio (mute) msg_id={msg_id} context={context_label}'
            )
            if context_label == 'group':
                _group_logger.warning(
                    f'video sem audio (mute) msg_id={msg_id}'
                )
            return result
        if not audio_path:
            result['error'] = 'ffmpeg falhou extraindo audio do video'
            log.error(
                f'ffmpeg falhou msg_id={msg_id} context={context_label} '
                f'video={video_path}'
            )
            if context_label == 'group':
                _group_logger.error(
                    f'ffmpeg falhou msg_id={msg_id} video={video_path}'
                )
            # Alerta admin via DM (canal de observabilidade)
            try:
                _send_admin_outbox_text(
                    f'ffmpeg falhou extraindo audio de video.\n'
                    f'msg_id={msg_id}\n'
                    f'video_path={video_path}\n'
                    f'context={context_label}',
                    prefix='video_ffmpeg_fail',
                )
            except Exception:
                pass
            return result
        result['audio_path'] = audio_path
    else:
        # voice / audio / video_note: pipeline original
        audio_path = download_telegram_file(file_id, AUDIO_IN, msg_id)
        if not audio_path:
            result['error'] = 'download falhou'
            log.error(f'audio download falhou msg_id={msg_id} context={context_label}')
            if context_label == 'group':
                _group_logger.error(f'audio download falhou msg_id={msg_id}')
            return result
        result['audio_path'] = audio_path

    try:
        size_bytes = audio_path.stat().st_size
    except Exception:
        size_bytes = 0
    if size_bytes > WHISPER_MAX_BYTES:
        result['too_large'] = True
        msg_err = (
            f'audio muito grande size={size_bytes}B (>25MB) — Whisper recusa. '
            f'msg_id={msg_id} context={context_label}'
        )
        log.error(msg_err)
        if context_label == 'group':
            _group_logger.error(msg_err)
        return result

    try:
        transcript = transcribe_whisper(audio_path)
    except Exception as e:
        transcript = None
        result['error'] = f'whisper exception: {e}'
        log.error(f'whisper exception msg_id={msg_id}: {e}')

    if not transcript:
        # Caso especial: video sem audio (mute) — nao alarma, so loga warning
        if kind == 'video':
            result['error'] = 'video_silent'
            log.warning(
                f'video sem audio (mute) ou transcricao vazia msg_id={msg_id} '
                f'context={context_label}'
            )
            if context_label == 'group':
                _group_logger.warning(
                    f'video sem audio (mute) msg_id={msg_id}'
                )
            return result
        if not result['error']:
            result['error'] = 'transcricao retornou vazio'
        log.error(
            f'transcricao falhou: {result["error"]} msg_id={msg_id} '
            f'context={context_label}'
        )
        if context_label == 'group':
            _group_logger.error(
                f'transcricao falhou: {result["error"]} msg_id={msg_id}'
            )
        return result

    result['transcript'] = transcript
    # Custo estimado (apenas se foi API; transcribe_whisper usa provider='api')
    minutes = max(duration, 1) / 60.0
    cost_usd = minutes * WHISPER_USD_PER_MIN
    result['cost_brl'] = cost_usd * WHISPER_BRL_PER_USD

    log_msg = (
        f'transcricao OK len={len(transcript)} chars duration={duration}s '
        f'kind={kind} msg_id={msg_id} custo=R${result["cost_brl"]:.3f} '
        f'texto="{transcript[:120]}"'
    )
    log.info(log_msg)
    if context_label == 'group':
        _group_logger.info(log_msg)
    return result


def synthesize_gemini(text, msg_id):
    """Gera audio via Gemini TTS - VOZ OFICIAL DA MAIA (voz Kore + sotaque
    nordestino, escolha Chefe opcao 3 / 2026-06-05). Converte pra OGG opus
    (formato Telegram voice). Gemini retorna PCM 16-bit mono 24kHz.
    O sotaque vem de PREPENDAR MAIA_VOICE_STYLE ao texto a narrar.
    Receita espelhada em /opt/MAIA/integrations/maia-voice/gerar_voz_maia.py.
    Retorna path do .ogg ou None (None dispara fallback ElevenLabs)."""
    if not GEMINI_KEY:
        log.error('GEMINI_API_KEY missing - sem Gemini TTS')
        return None
    try:
        # Prependa a instrucao de estilo (sotaque nordestino) ao texto
        style = (MAIA_VOICE_STYLE or '').strip()
        prompt = f'{style}: {text}' if style else text
        url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
               f'{GEMINI_TTS_MODEL}:generateContent?key={GEMINI_KEY}')
        body = {
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {
                'responseModalities': ['AUDIO'],
                'speechConfig': {
                    'voiceConfig': {
                        'prebuiltVoiceConfig': {'voiceName': GEMINI_TTS_VOICE}
                    }
                },
            },
        }
        r = requests.post(url, json=body,
                          headers={'Content-Type': 'application/json'}, timeout=90)
        if r.status_code != 200:
            log.error(f'gemini tts http {r.status_code}: {r.text[:200]}')
            return None
        resp = r.json()
        part = resp['candidates'][0]['content']['parts'][0]
        inline = part['inlineData']
        mime = inline.get('mimeType', '')
        pcm = base64.b64decode(inline['data'])
        rate = 24000
        for tok in mime.split(';'):
            tok = tok.strip()
            if tok.startswith('rate='):
                try:
                    rate = int(tok.split('=')[1])
                except ValueError:
                    pass
        wav_path = AUDIO_OUT / f'{msg_id}_gemini.wav'
        ogg_path = AUDIO_OUT / f'{msg_id}.ogg'
        with wave.open(str(wav_path), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(rate)
            w.writeframes(pcm)
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', str(wav_path), '-c:a', 'libopus', '-b:a', '48k', str(ogg_path)],
            capture_output=True, timeout=30
        )
        if result.returncode != 0:
            log.error(f'ffmpeg failed (gemini): {result.stderr.decode()[:200]}')
            return None
        log.info(f'gemini tts gerou audio (voz {GEMINI_TTS_VOICE}): {ogg_path} '
                 f'({ogg_path.stat().st_size} bytes)')
        return ogg_path
    except Exception as e:
        log.error(f'gemini tts error: {e}')
        return None

def synthesize_voice(text, msg_id):
    """Dispatcher de TTS pra voz do Telegram.
    Engine padrao: Gemini (voz Kore). Fallback automatico: ElevenLabs.
    VOICE_ENGINE=elevenlabs forca ElevenLabs direto. Retorna path .ogg ou None."""
    if VOICE_ENGINE == 'elevenlabs':
        return synthesize_elevenlabs(text, msg_id)
    # Default: Gemini primeiro (VOZ OFICIAL Kore+nordestino), fallback ElevenLabs.
    ogg = synthesize_gemini(text, msg_id)
    if ogg:
        return ogg
    # ATENCAO: se este aviso aparecer, o audio NAO saiu na voz oficial da Maia.
    # E o sinal de "voz errada" (ElevenLabs Rachel). Investigar a falha do Gemini acima.
    log.warning('VOZ-ERRADA: gemini tts falhou - caindo pro fallback ElevenLabs '
                '(audio NAO sai na voz oficial nordestina)')
    return synthesize_elevenlabs(text, msg_id)

def synthesize_elevenlabs(text, msg_id):
    """Gera audio MP3 via ElevenLabs e converte pra OGG opus (formato Telegram voice).
    Retorna path do .ogg ou None"""
    if not ELEVENLABS_KEY:
        log.error('ELEVENLABS_API_KEY missing')
        return None
    try:
        url = f'https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE}'
        r = requests.post(url,
            headers={
                'xi-api-key': ELEVENLABS_KEY,
                'Content-Type': 'application/json',
                'Accept': 'audio/mpeg'
            },
            json={
                'text': text,
                'model_id': 'eleven_multilingual_v2',
                'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75}
            },
            timeout=60
        )
        if r.status_code != 200:
            log.error(f'elevenlabs http {r.status_code}: {r.text[:200]}')
            return None
        mp3_path = AUDIO_OUT / f'{msg_id}.mp3'
        ogg_path = AUDIO_OUT / f'{msg_id}.ogg'
        mp3_path.write_bytes(r.content)
        # Converte mp3 -> ogg opus (formato voice do Telegram)
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', str(mp3_path), '-c:a', 'libopus', '-b:a', '48k', str(ogg_path)],
            capture_output=True, timeout=30
        )
        if result.returncode != 0:
            log.error(f'ffmpeg failed: {result.stderr.decode()[:200]}')
            return None
        log.info(f'elevenlabs gerou audio: {ogg_path} ({ogg_path.stat().st_size} bytes)')
        return ogg_path
    except Exception as e:
        log.error(f'elevenlabs error: {e}')
        return None

def send_images(chat_id, paths, caption=None, reply_to=None):
    """Envia uma ou mais imagens via Telegram.
    1 imagem: sendPhoto. 2-10 imagens: sendMediaGroup (carrossel).
    Retorna (ok: bool, response_json: dict|None)
    """
    paths = [Path(p) for p in paths if Path(p).exists()]
    if not paths:
        return False, None
    try:
        if len(paths) == 1:
            with open(paths[0], 'rb') as ph:
                files = {'photo': (paths[0].name, ph, 'image/png')}
                form = {'chat_id': chat_id}
                if caption:
                    form['caption'] = caption[:1024]  # telegram caption limit
                if reply_to:
                    form['reply_parameters'] = json.dumps({'message_id': int(reply_to)})
                r = requests.post(f'{API}/sendPhoto', data=form, files=files, timeout=60)
            return (r.status_code == 200 and r.json().get('ok')), (r.json() if r.content else None)
        # carrossel: max 10 itens
        items = paths[:10]
        media = []
        files = {}
        for i, p in enumerate(items):
            attach = f'file{i}'
            entry = {'type': 'photo', 'media': f'attach://{attach}'}
            if i == 0 and caption:
                entry['caption'] = caption[:1024]
            media.append(entry)
            files[attach] = (p.name, open(p, 'rb'), 'image/png')
        form = {'chat_id': chat_id, 'media': json.dumps(media)}
        if reply_to:
            form['reply_parameters'] = json.dumps({'message_id': int(reply_to)})
        try:
            r = requests.post(f'{API}/sendMediaGroup', data=form, files=files, timeout=120)
        finally:
            for _, fh, _ in files.values():
                try: fh.close()
                except: pass
        return (r.status_code == 200 and r.json().get('ok')), (r.json() if r.content else None)
    except Exception as e:
        log.error(f'send_images error: {e}')
        return False, None


def send_documents(chat_id, paths, caption=None, reply_to=None):
    """Envia um ou mais documentos via Telegram (sendDocument).
    Multiplos arquivos: envia um por vez (caption so no primeiro).
    Telegram limita 50 MB por arquivo via Bot API.
    Retorna (ok: bool, results: list[dict])
    """
    MAX_BYTES = 50 * 1024 * 1024  # 50 MB Telegram Bot API limit
    paths = [Path(p) for p in paths]
    valid = []
    for p in paths:
        if not p.exists():
            log.error(f'send_documents: arquivo nao existe: {p}')
            continue
        if not p.is_file():
            log.error(f'send_documents: nao e arquivo: {p}')
            continue
        size = p.stat().st_size
        if size > MAX_BYTES:
            log.error(f'send_documents: arquivo > 50MB ({size} bytes): {p}')
            continue
        valid.append(p)
    if not valid:
        return False, []
    results = []
    all_ok = True
    for i, p in enumerate(valid):
        try:
            with open(p, 'rb') as fh:
                files = {'document': (p.name, fh, 'application/octet-stream')}
                form = {'chat_id': chat_id}
                if caption and i == 0:
                    form['caption'] = caption[:1024]
                if reply_to and i == 0:
                    form['reply_parameters'] = json.dumps({'message_id': int(reply_to)})
                r = requests.post(f'{API}/sendDocument', data=form, files=files, timeout=120)
            ok = (r.status_code == 200 and r.json().get('ok'))
            if not ok:
                log.warning(f'sendDocument falhou {p.name}: {r.text[:200]}')
                all_ok = False
            results.append({
                'path': str(p),
                'ok': ok,
                'response': (r.json() if r.content else None),
            })
        except Exception as e:
            log.error(f'send_documents erro {p}: {e}')
            all_ok = False
            results.append({'path': str(p), 'ok': False, 'error': str(e)})
    return all_ok, results


def process_document_outbox(outbox_file, data):
    """Processa um item de outbox que contem campo 'document'.
    Roda em thread separada (envio pode demorar para arquivos grandes).
    Move arquivo pra sent/ ou .failed conforme resultado.
    """
    try:
        chat_id = data.get('chat_id', ADMIN_CHAT_ID)
        reply_to = data.get('reply_to_message_id')
        doc = data.get('document') or {}
        caption = data.get('text') or doc.get('caption')
        raw_paths = doc.get('paths') or []
        if not raw_paths:
            log.warning(f'document sem paths: {outbox_file.name}')
            outbox_file.rename(outbox_file.with_suffix('.failed'))
            return
        paths = [Path(p) for p in raw_paths]
        ok, results = send_documents(chat_id, paths, caption=caption, reply_to=reply_to)
        if ok:
            stop_typing(chat_id)
            sent_file = SENT / outbox_file.name
            sent_file.write_text(json.dumps({
                **data,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'document_results': results,
            }, indent=2, ensure_ascii=False))
            outbox_file.unlink()
            log.info(f'sent document(s) {outbox_file.name} n={len(results)}')
            mark_maia_responded()
        else:
            log.warning(f'send_documents falhou {outbox_file.name}: {results}')
            failed_path = outbox_file.with_suffix('.failed')
            try:
                failed_path.write_text(json.dumps({
                    **data,
                    'failed_at': datetime.now(timezone.utc).isoformat(),
                    'document_results': results,
                }, indent=2, ensure_ascii=False))
                outbox_file.unlink()
            except Exception:
                try: outbox_file.rename(failed_path)
                except: pass
    except Exception as e:
        log.error(f'process_document_outbox erro {outbox_file.name}: {e}')
        try: outbox_file.rename(outbox_file.with_suffix('.failed'))
        except: pass


def process_image_outbox(outbox_file, data):
    """Processa um item de outbox que contem campo 'image'.
    Roda em thread separada (geracao pode demorar 5-15s).
    Move arquivo pra sent/ ou .failed conforme resultado.
    """
    try:
        chat_id = data.get('chat_id', ADMIN_CHAT_ID)
        reply_to = data.get('reply_to_message_id')
        img = data.get('image') or {}
        caption = data.get('text') or img.get('caption')
        paths = []
        ref_path = img.get('reference_path')
        if img.get('paths'):
            paths = [Path(p) for p in img['paths'] if Path(p).exists()]
            if not paths:
                log.warning(f'image.paths sem arquivos validos: {outbox_file.name}')
        elif ref_path and img.get('prompt'):
            # Image editing com foto de referencia (mantem identidade do sujeito)
            if not _gemini_edit:
                log.error(f'gemini_image.edit indisponivel: {outbox_file.name}')
                outbox_file.rename(outbox_file.with_suffix('.failed'))
                return
            if not Path(ref_path).is_file():
                log.error(f'reference_path nao existe: {ref_path}')
                outbox_file.rename(outbox_file.with_suffix('.failed'))
                return
            try:
                paths = _gemini_edit(
                    reference_image_path=ref_path,
                    prompt=img['prompt'],
                    model=img.get('model', GEMINI_EDIT_MODEL),
                    aspect_ratio=img.get('aspect_ratio'),
                    output_dir=str(IMG_OUT),
                    file_prefix=outbox_file.stem,
                )
            except Exception as e:
                log.error(f'edicao falhou {outbox_file.name}: {e}')
                outbox_file.rename(outbox_file.with_suffix('.failed'))
                return
        elif img.get('prompt'):
            if not _gemini_generate:
                log.error(f'gemini_image indisponivel, nao posso gerar: {outbox_file.name}')
                outbox_file.rename(outbox_file.with_suffix('.failed'))
                return
            try:
                paths = _gemini_generate(
                    prompt=img['prompt'],
                    model=img.get('model', GEMINI_IMAGE_MODEL),
                    aspect_ratio=img.get('aspect_ratio', GEMINI_DEFAULT_ASPECT),
                    n=int(img.get('n', 1)),
                    output_dir=str(IMG_OUT),
                    file_prefix=outbox_file.stem,
                )
            except Exception as e:
                log.error(f'geracao falhou {outbox_file.name}: {e}')
                outbox_file.rename(outbox_file.with_suffix('.failed'))
                return
        else:
            log.warning(f'image sem prompt nem paths: {outbox_file.name}')
            outbox_file.rename(outbox_file.with_suffix('.failed'))
            return

        ok, resp = send_images(chat_id, paths, caption=caption, reply_to=reply_to)
        if ok:
            stop_typing(chat_id)
            sent_file = SENT / outbox_file.name
            sent_file.write_text(json.dumps({
                **data,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'image_paths': [str(p) for p in paths],
                'response': (resp or {}).get('result', {})
            }, indent=2, ensure_ascii=False))
            outbox_file.unlink()
            log.info(f'sent image(s) {outbox_file.name} n={len(paths)}')
            mark_maia_responded()
        else:
            log.warning(f'send_images falhou {outbox_file.name}: {resp}')
            outbox_file.rename(outbox_file.with_suffix('.failed'))
    except Exception as e:
        log.error(f'process_image_outbox erro {outbox_file.name}: {e}')
        try: outbox_file.rename(outbox_file.with_suffix('.failed'))
        except: pass


def process_instagram_publish_outbox(outbox_file, data):
    """Processa outbox com campo 'instagram_publish'.

    Estrutura esperada:
      {
        "chat_id": ADMIN_CHAT_ID,
        "text": "msg confirmacao opcional",
        "reply_to_message_id": 123,
        "instagram_publish": {
          "type": "reel" | "feed" | "story",
          ...campos especificos do tipo
        }
      }

    Tipos suportados:
      - reel:  {video_url, caption, share_to_feed?, cover_url?}
      - feed:  {image_urls: [str, ...1-10], caption}
      - story: {media_url, media_type: "image"|"video"}
    """
    try:
        import sys as _sys
        if '/opt/MAIA/integrations/instagram-publisher' not in _sys.path:
            _sys.path.insert(0, '/opt/MAIA/integrations/instagram-publisher')
        from instagram_publisher import (
            publish_reel, publish_feed, publish_story, IGPublishError,
        )
    except Exception as e:
        log.error(f'instagram_publisher indisponivel: {e}')
        outbox_file.rename(outbox_file.with_suffix('.failed'))
        return

    chat_id = data.get('chat_id', ADMIN_CHAT_ID)
    reply_to = data.get('reply_to_message_id')
    base_text = (data.get('text') or '').strip()
    ig = data.get('instagram_publish') or {}
    ig_type = (ig.get('type') or '').lower()

    try:
        if ig_type == 'reel':
            video_url = ig.get('video_url')
            caption = ig.get('caption', '')
            if not video_url:
                raise ValueError('reel: video_url obrigatorio')
            result = publish_reel(
                video_url=video_url,
                caption=caption,
                share_to_feed=ig.get('share_to_feed', True),
                cover_url=ig.get('cover_url'),
                audio_name=ig.get('audio_name'),
            )
        elif ig_type == 'feed':
            image_urls = ig.get('image_urls') or []
            caption = ig.get('caption', '')
            if not image_urls:
                raise ValueError('feed: image_urls obrigatorio (lista)')
            result = publish_feed(image_urls, caption)
        elif ig_type == 'story':
            media_url = ig.get('media_url')
            media_type = ig.get('media_type', 'image')
            if not media_url:
                raise ValueError('story: media_url obrigatorio')
            result = publish_story(media_url, media_type)
        else:
            raise ValueError(f'instagram_publish.type invalido: "{ig_type}" (use reel|feed|story)')

        permalink = result.get('permalink') or '(permalink pendente)'
        media_id = result.get('id', '?')
        msg = (base_text + '\n\n' if base_text else '') + \
              f'Publicado no Instagram. {permalink}\nmedia_id: {media_id}'
        payload = {'chat_id': chat_id, 'text': msg}
        if reply_to:
            payload['reply_parameters'] = {'message_id': int(reply_to)}
        r = requests.post(f'{API}/sendMessage', json=payload, timeout=10)

        if r.status_code == 200 and r.json().get('ok'):
            stop_typing(chat_id)
            sent_file = SENT / outbox_file.name
            sent_file.write_text(json.dumps({
                **data,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'ig_result': result,
            }, indent=2, ensure_ascii=False))
            outbox_file.unlink()
            log.info(f'instagram publish OK {outbox_file.name} -> {permalink}')
            try:
                mark_maia_responded()
            except Exception:
                pass
        else:
            log.warning(f'instagram publicou mas falhou ao notificar telegram: {r.status_code}')
            outbox_file.rename(outbox_file.with_suffix('.failed'))

    except (IGPublishError, ValueError) as e:
        log.error(f'instagram_publish falhou {outbox_file.name}: {e}')
        err_msg = (base_text + '\n\n' if base_text else '') + \
                  f'Falha ao publicar Instagram: {e}'
        try:
            payload = {'chat_id': chat_id, 'text': err_msg}
            if reply_to:
                payload['reply_parameters'] = {'message_id': int(reply_to)}
            requests.post(f'{API}/sendMessage', json=payload, timeout=10)
            try:
                mark_maia_responded()
            except Exception:
                pass
        except Exception:
            pass
        outbox_file.rename(outbox_file.with_suffix('.failed'))
    except Exception as e:
        log.error(f'process_instagram_publish_outbox erro {outbox_file.name}: {e}')
        try: outbox_file.rename(outbox_file.with_suffix('.failed'))
        except: pass


def handle_stop_command(chat_id, msg_id, user_id):
    """
    Processa comando STOP do admin: le ultimas notificacoes de limpeza e
    adiciona pastas mencionadas em /opt/MAIA/data/preservar.json com TTL 30d.
    """
    try:
        from datetime import datetime, timezone, timedelta
        import re as _re
        if str(user_id) != str(ADMIN_CHAT_ID):
            return False
        sent_dir = BOT_DIR / 'sent'
        if not sent_dir.exists():
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        pastas_encontradas = set()
        for fp in sent_dir.glob('limpeza_*.json'):
            try:
                mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    continue
                payload = json.loads(fp.read_text())
                text_field = payload.get('text', '')
                # encontra padroes do tipo "- cards-instagram/2026-04-01-aula-x"
                for m in _re.findall(r'-\s+([a-z0-9\-]+/[a-z0-9\-]+(?:/[a-z0-9\-]+)?)', text_field):
                    pastas_encontradas.add(m)
            except Exception:
                continue
        if not pastas_encontradas:
            ack = {
                'chat_id': chat_id,
                'text': 'STOP recebido, mas nao achei avisos de limpeza recentes (7 dias) com pastas pra preservar.',
                'reply_to_message_id': msg_id,
            }
            (BOT_DIR / 'outbox' / f'stop_ack_{msg_id}.json').write_text(json.dumps(ack, ensure_ascii=False))
            return True
        data_dir = Path('/opt/MAIA/data')
        data_dir.mkdir(parents=True, exist_ok=True)
        preservar_path = data_dir / 'preservar.json'
        if preservar_path.exists():
            try:
                preservar = json.loads(preservar_path.read_text())
            except Exception:
                preservar = {'preserved': []}
        else:
            preservar = {'preserved': []}
        preservar.setdefault('preserved', [])
        existentes = {e.get('path'): e for e in preservar['preserved']}
        novo_ttl = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        adicionados = []
        for pasta in sorted(pastas_encontradas):
            existentes[pasta] = {'path': pasta, 'expires_at': novo_ttl}
            adicionados.append(pasta)
        preservar['preserved'] = list(existentes.values())
        preservar_path.write_text(json.dumps(preservar, indent=2, ensure_ascii=False))
        ack = {
            'chat_id': chat_id,
            'text': f'STOP processado. Preservadas por 30 dias:\n' + '\n'.join(f'- {p}' for p in adicionados),
            'reply_to_message_id': msg_id,
        }
        (BOT_DIR / 'outbox' / f'stop_ack_{msg_id}.json').write_text(json.dumps(ack, ensure_ascii=False))
        return True
    except Exception as e:
        log.error(f'stop handler error: {e}')
        return False


def handle_admin_group_command(chat_id, msg_id, user_id, text):
    """Trata comandos admin de gerenciamento de grupo no DM do Chefe.
    Retorna True se processou comando (chamador para fluxo normal).
    Comandos:
      /group_status
      /confirmgroup <chat_id>
      /add_user_to_group_wl <user_id>
      /remove_user_from_group_wl <user_id>
    """
    if str(user_id) != str(ADMIN_CHAT_ID):
        return False
    if not isinstance(text, str):
        return False
    parts = text.strip().split()
    if not parts:
        return False
    cmd = parts[0].lower()
    if cmd == '/group_status':
        gcid = get_group_chat_id()
        wl = sorted(get_group_user_whitelist())
        trig = get_group_trigger()
        body = (
            f'Status do grupo {ASSISTANT_NAME}:\n'
            f'- chat_id: {gcid if gcid is not None else "(nao capturado ainda)"}\n'
            f'- trigger: "{trig}"\n'
            f'- user_ids whitelisted ({len(wl)}): {", ".join(wl) if wl else "(vazio)"}'
        )
        (OUTBOX / f'group_status_{msg_id}.json').write_text(
            json.dumps({'chat_id': chat_id, 'text': body, 'reply_to_message_id': msg_id}, ensure_ascii=False)
        )
        return True
    if cmd == '/confirmgroup':
        if len(parts) < 2:
            (OUTBOX / f'group_confirm_err_{msg_id}.json').write_text(
                json.dumps({'chat_id': chat_id, 'text': 'Uso: /confirmgroup <chat_id>',
                            'reply_to_message_id': msg_id}, ensure_ascii=False)
            )
            return True
        try:
            new_id = int(parts[1])
        except ValueError:
            (OUTBOX / f'group_confirm_err_{msg_id}.json').write_text(
                json.dumps({'chat_id': chat_id, 'text': f'chat_id invalido: {parts[1]}',
                            'reply_to_message_id': msg_id}, ensure_ascii=False)
            )
            return True
        ok = set_group_chat_id(new_id)
        global _group_capture_alerted
        with _group_capture_lock:
            _group_capture_alerted = True
        body = (f'Grupo confirmado: chat_id={new_id}.' if ok
                else f'Falha ao salvar chat_id={new_id} no .env.')
        (OUTBOX / f'group_confirm_{msg_id}.json').write_text(
            json.dumps({'chat_id': chat_id, 'text': body, 'reply_to_message_id': msg_id}, ensure_ascii=False)
        )
        return True
    if cmd == '/add_user_to_group_wl':
        if len(parts) < 2:
            (OUTBOX / f'group_add_err_{msg_id}.json').write_text(
                json.dumps({'chat_id': chat_id, 'text': 'Uso: /add_user_to_group_wl <user_id>',
                            'reply_to_message_id': msg_id}, ensure_ascii=False)
            )
            return True
        target = parts[1].strip()
        if not target.isdigit():
            (OUTBOX / f'group_add_err_{msg_id}.json').write_text(
                json.dumps({'chat_id': chat_id, 'text': f'user_id deve ser numerico: {target}',
                            'reply_to_message_id': msg_id}, ensure_ascii=False)
            )
            return True
        ok = add_user_to_group_whitelist(target)
        wl = sorted(get_group_user_whitelist())
        body = (f'User {target} adicionado a whitelist. Total: {len(wl)}.' if ok
                else f'Falha ao adicionar {target}.')
        (OUTBOX / f'group_add_{msg_id}.json').write_text(
            json.dumps({'chat_id': chat_id, 'text': body, 'reply_to_message_id': msg_id}, ensure_ascii=False)
        )
        return True
    if cmd == '/remove_user_from_group_wl':
        if len(parts) < 2:
            (OUTBOX / f'group_rm_err_{msg_id}.json').write_text(
                json.dumps({'chat_id': chat_id, 'text': 'Uso: /remove_user_from_group_wl <user_id>',
                            'reply_to_message_id': msg_id}, ensure_ascii=False)
            )
            return True
        target = parts[1].strip()
        if target == str(ADMIN_CHAT_ID):
            (OUTBOX / f'group_rm_err_{msg_id}.json').write_text(
                json.dumps({'chat_id': chat_id, 'text': 'Nao posso remover o Chefe da whitelist.',
                            'reply_to_message_id': msg_id}, ensure_ascii=False)
            )
            return True
        ok = remove_user_from_group_whitelist(target)
        wl = sorted(get_group_user_whitelist())
        body = (f'User {target} removido. Total atual: {len(wl)}.' if ok
                else f'Falha ao remover {target}.')
        (OUTBOX / f'group_rm_{msg_id}.json').write_text(
            json.dumps({'chat_id': chat_id, 'text': body, 'reply_to_message_id': msg_id}, ensure_ascii=False)
        )
        return True
    return False


def handle_group_message(msg, msg_id, chat_id, user_id, text):
    """Trata mensagem vinda de grupo/supergrupo.
    Aplica checks (chat_id whitelist, trigger no texto, user_id whitelist).
    Retorna True se a mensagem foi processada (chamador para fluxo). False se ignorada.

    Auto-captura: se MAIA_GROUP_CHAT_ID estiver vazio na primeira msg de grupo,
    captura e alerta o Chefe via DM pedindo /confirmgroup.
    Auto-deteccao de user novo: se chat_id casa mas user_id NAO esta na whitelist,
    alerta o Chefe pedindo /add_user_to_group_wl.

    Modo open-membership (MAIA_GROUP_OPEN_MEMBERSHIP=true): pula o check de user_id
    e permite qualquer participante do grupo confirmado interagir. Mantém o alerta
    DM informativo (throttled 1h) na primeira interação de cada user fora da whitelist.
    """
    global _group_capture_alerted
    configured_chat = get_group_chat_id()
    trigger = get_group_trigger()
    whitelist = get_group_user_whitelist()
    open_membership = get_group_open_membership()
    user_info = msg.get('from', {}) or {}
    first_name = user_info.get('first_name', '')
    last_name = user_info.get('last_name', '')
    username = user_info.get('username', '')
    user_full = f'{first_name} {last_name}'.strip() or username or user_id
    text_lower = (text or '').lower()

    # CHECK 1: chat_id confere com o grupo configurado?
    if configured_chat is None:
        # Primeira mensagem de grupo ja vista. Captura chat_id e alerta Chefe.
        with _group_capture_lock:
            if not _group_capture_alerted:
                _group_capture_alerted = True
                _send_admin_outbox_text(
                    'Grupo capturado: chat_id=' + str(chat_id) + '\n'
                    f'Nome do grupo: {msg.get("chat", {}).get("title", "(sem titulo)")}\n'
                    f'Tipo: {msg.get("chat", {}).get("type", "?")}\n\n'
                    f'Pra confirmar e ativar o grupo, manda no DM:\n'
                    f'/confirmgroup {chat_id}\n\n'
                    'Enquanto nao confirmar, mensagens do grupo continuam ignoradas.',
                    prefix='group_captured'
                )
        _group_logger.info(
            f'msg ignorada (chat nao confirmado) chat_id={chat_id} '
            f'user_id={user_id} user="{user_full}" text="{text[:80]}"'
        )
        return False

    if chat_id != configured_chat:
        # Grupo diferente do whitelisted — ignora silenciosamente
        _group_logger.info(
            f'msg ignorada (chat fora da whitelist) chat_id={chat_id} '
            f'configured={configured_chat} user_id={user_id} text="{text[:80]}"'
        )
        return False

    # CHECK 2: user_id esta na whitelist?
    # Em modo open_membership, qualquer user do grupo confirmado pode interagir,
    # mas mantemos o alerta DM informativo na 1a interação de cada user fora da whitelist.
    if str(user_id) not in whitelist:
        # User novo — alerta Chefe (throttled por 1h)
        now = time.time()
        with _unknown_user_lock:
            last = _unknown_user_alerted.get(str(user_id), 0)
            if now - last >= UNKNOWN_USER_ALERT_THROTTLE:
                _unknown_user_alerted[str(user_id)] = now
                send_alert = True
            else:
                send_alert = False
        if send_alert:
            if open_membership:
                alert_body = (
                    f'Novo participante interagindo no grupo {ASSISTANT_NAME} (open_membership=true):\n'
                    f'- nome: {first_name} {last_name}'.rstrip() + '\n'
                    f'- username: @{username if username else "(sem username)"}\n'
                    f'- user_id: {user_id}\n\n'
                    f'A interação NÃO foi bloqueada (modo aberto).\n'
                    f'Pra remover esse user de futuras interações:\n'
                    f'/remove_user_from_group_wl {user_id}\n'
                    f'(e setar MAIA_GROUP_OPEN_MEMBERSHIP=false no .env pra voltar ao modo whitelist)'
                )
                alert_prefix = 'group_open_member'
            else:
                alert_body = (
                    f'User novo no grupo {ASSISTANT_NAME}:\n'
                    f'- nome: {first_name} {last_name}'.rstrip() + '\n'
                    f'- username: @{username if username else "(sem username)"}\n'
                    f'- user_id: {user_id}\n\n'
                    f'Pra autorizar:\n'
                    f'/add_user_to_group_wl {user_id}'
                )
                alert_prefix = 'group_unknown_user'
            _send_admin_outbox_text(alert_body, prefix=alert_prefix)
        if not open_membership:
            _group_logger.info(
                f'msg ignorada (user fora da whitelist) chat_id={chat_id} '
                f'user_id={user_id} user="{user_full}" text="{text[:80]}"'
            )
            return False
        # open_membership=true: nao bloqueia, segue pro check de trigger
        _group_logger.info(
            f'user fora da whitelist mas open_membership=true — segue chat_id={chat_id} '
            f'user_id={user_id} user="{user_full}" text="{text[:80]}"'
        )

    # CHECK 3: sessao OU trigger no texto?
    # Modo SESSAO (2026-05-21): primeira msg com @maia abre sessao de N min.
    # Durante a sessao, TODAS as msgs do grupo sao processadas sem precisar @maia.
    # /sair encerra a sessao imediato.

    text_stripped = (text or '').strip()
    first_token = text_stripped.split()[0].lower() if text_stripped else ''

    # 3a) /sair ou /encerrar — fecha sessao imediato (em qualquer estado)
    if first_token in ('/sair', '/encerrar'):
        had_session = close_group_session(chat_id)
        # Limpa flag de dedupe de notificacao por timeout
        with _session_timeout_lock:
            _session_timeout_notified.discard(str(chat_id))
        if had_session:
            _send_group_text(
                chat_id,
                f'Sessao encerrada. Pra falar comigo de novo, manda {ASSISTANT_HANDLE}.'
            )
            _group_logger.info(
                f'sessao encerrada via /sair chat_id={chat_id} user_id={user_id}'
            )
        else:
            _group_logger.info(
                f'/sair ignorado (sem sessao ativa) chat_id={chat_id} user_id={user_id}'
            )
        # Em qualquer caso, NAO processa a msg normalmente
        return False

    # 3a-bis) MODO SEMPRE-ABERTO (MAIA_GROUP_ALWAYS_ON=true):
    # grupo dedicado — toda msg de user autorizado e processada sem trigger/sessao.
    # Ja passou pelos checks de chat_id (CHECK 1) e user/open_membership (CHECK 2),
    # entao aqui basta aceitar. /sair acima continua sendo interceptado.
    if get_group_always_on():
        _group_logger.info(
            f'msg ACEITA (always_on) chat_id={chat_id} user_id={user_id} '
            f'user="{user_full}" text="{text[:80]}"'
        )
        return True

    has_trigger = bool(trigger) and trigger in text_lower
    session_active = is_group_session_active(chat_id)

    # 3b) trigger presente — abre/renova sessao e processa
    if has_trigger:
        is_new = open_group_session(chat_id, user_id)
        # Limpa flag de dedupe de notificacao por timeout (reabertura)
        with _session_timeout_lock:
            _session_timeout_notified.discard(str(chat_id))
        if is_new:
            timeout_min = get_group_session_timeout_min()
            _send_group_text(
                chat_id,
                f'Sessao aberta — escuto ate /sair ou {timeout_min}min sem msg.'
            )
            _group_logger.info(
                f'sessao ABERTA chat_id={chat_id} user_id={user_id} '
                f'timeout_min={timeout_min}'
            )
        else:
            _group_logger.info(
                f'sessao RENOVADA via trigger chat_id={chat_id} user_id={user_id}'
            )
        _group_logger.info(
            f'msg ACEITA (trigger) chat_id={chat_id} user_id={user_id} user="{user_full}" '
            f'text="{text[:80]}"'
        )
        return True

    # 3c) sem trigger mas sessao ativa — processa e renova timer
    if session_active:
        touch_group_session(chat_id)
        _group_logger.info(
            f'msg ACEITA (sessao ativa) chat_id={chat_id} user_id={user_id} '
            f'user="{user_full}" text="{text[:80]}"'
        )
        return True

    # 3d) sem trigger e sem sessao — ignora (zero custo Claude)
    _group_logger.info(
        f'msg ignorada (sem trigger e sem sessao) chat_id={chat_id} '
        f'user_id={user_id} user="{user_full}" text="{text[:80]}"'
    )
    return False


def handle_update(update):
    # Callback queries (cliques em botoes inline) — usado pra responder
    # prompts interativos do Claude Code (Padrao A/B).
    if 'callback_query' in update:
        cq = update['callback_query']
        cq_user = str(cq.get('from', {}).get('id', ''))
        # Callback so e processado se o user esta na ALLOWED_USERS (DM) OU na
        # whitelist do grupo (botoes que podem aparecer em grupo no futuro)
        if cq_user not in ALLOWED_USERS and cq_user not in get_group_user_whitelist():
            log.info(f'drop callback de user nao autorizado: {cq_user}')
            return
        data = cq.get('data', '')
        if data.startswith('claude_prompt:'):
            handle_claude_prompt_callback(cq)
        else:
            log.info(f'callback_query desconhecido: {data!r}')
        return
    msg = update.get('message') or update.get('edited_message')
    if not msg:
        return
    user_id = str(msg.get('from', {}).get('id', ''))
    msg_id = msg.get('message_id')
    chat_obj = msg.get('chat', {}) or {}
    chat_id = chat_obj.get('id')
    chat_type = chat_obj.get('type', 'private')  # private | group | supergroup | channel
    text = msg.get('text') or msg.get('caption') or ''

    is_group = chat_type in ('group', 'supergroup')

    # ---------- DM mode (compat antigo) ----------
    if not is_group:
        if user_id not in ALLOWED_USERS:
            log.info(f'drop user nao autorizado (DM): {user_id}')
            return
        # fluxo segue abaixo (audio, STOP, inbox, enqueue)
    # ---------- Group mode ----------
    else:
        # Comandos admin so via DM. Audio agora SUPORTADO no grupo (2026-05-19).
        # Foto/PDF/documento agora SUPORTADO no grupo (2026-05-25).
        # Se nao tem texto, tenta detectar midia visual (foto/doc) ou audio.
        # Mensagens de sistema (someone joined etc) seguem ignoradas.
        group_audio_info = None
        group_visual_info = None
        # Foto/doc detectado PRIMEIRO — funciona COM ou SEM legenda (fix caption
        # 2026-05-29). extract_visual_media_from_msg retorna kind=None rapido se
        # nao houver photo/document, entao chamar sempre nao baixa nada em texto puro.
        group_visual_info = extract_visual_media_from_msg(msg, msg_id)
        if (not text) or group_visual_info['kind']:
            # 1) Foto / documento (PDF, docx, etc) — caso de uso de envio de midia.
            if group_visual_info['kind']:
                # Validacao de grupo PRIMEIRO (chat_id + sessao + user) pra nao gastar
                # download em msg que iria ser ignorada. Como o download ja foi feito
                # acima, removemos o arquivo se a msg for rejeitada.
                if not handle_group_message(msg, msg_id, chat_id, user_id, ''):
                    # Limpa arquivo baixado pra nao deixar lixo
                    try:
                        if group_visual_info['file_path'] and group_visual_info['file_path'].exists():
                            group_visual_info['file_path'].unlink()
                    except Exception:
                        pass
                    _group_logger.info(
                        f'midia visual SKIPPED (grupo rejeitou) chat_id={chat_id} '
                        f'user_id={user_id} msg_id={msg_id} '
                        f'kind={group_visual_info["kind"]}'
                    )
                    return
                if group_visual_info['too_large']:
                    _send_group_text(
                        chat_id,
                        f'Arquivo muito grande (>50MB) pra eu baixar. Manda menor ou um link.',
                        reply_to=msg_id,
                    )
                    _group_logger.info(
                        f'midia visual SKIPPED (>50MB) chat_id={chat_id} '
                        f'user_id={user_id} msg_id={msg_id} '
                        f'kind={group_visual_info["kind"]} size={group_visual_info["file_size"]}'
                    )
                    return
                if group_visual_info['error']:
                    _send_admin_outbox_text(
                        f'Falha ao baixar midia visual do grupo.\n'
                        f'chat_id={chat_id}\n'
                        f'user_id={user_id}\n'
                        f'msg_id={msg_id}\n'
                        f'kind={group_visual_info["kind"]}\n'
                        f'erro: {group_visual_info["error"]}',
                        prefix='group_visual_fail',
                    )
                    _group_logger.error(
                        f'midia visual SKIPPED (download falhou) chat_id={chat_id} '
                        f'user_id={user_id} msg_id={msg_id} '
                        f'kind={group_visual_info["kind"]} erro={group_visual_info["error"]}'
                    )
                    return
                # Sucesso — monta texto sintetico pra Maia ver que chegou midia.
                # Preserva a legenda do usuario junto do marcador (fix caption 2026-05-29).
                user_name = (msg.get('from', {}) or {}).get('first_name', user_id)
                _caption = text  # 'text' aqui ainda eh a legenda original (ou vazio)
                if group_visual_info['kind'] == 'photo':
                    _marker = f'[foto recebida — arquivo em {group_visual_info["file_path"]}]'
                else:
                    _marker = (
                        f'[documento recebido: {group_visual_info["file_name"]} '
                        f'({group_visual_info["mime_type"] or "?"}) — '
                        f'arquivo em {group_visual_info["file_path"]}]'
                    )
                text = f'{_caption}\n{_marker}' if _caption else _marker
                _group_logger.info(
                    f'midia visual PROCESSED chat_id={chat_id} user_id={user_id} '
                    f'msg_id={msg_id} kind={group_visual_info["kind"]} '
                    f'size={group_visual_info["file_size"]} '
                    f'name="{group_visual_info["file_name"]}"'
                )
                # Preenche inbox e enqueue (pulando handle_group_message novamente).
                inbox_data = {
                    'msg_id': msg_id, 'chat_id': chat_id, 'user_id': user_id,
                    'user_name': user_name, 'text': text, 'chat_type': chat_type,
                    'group_origin': True,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'media_kind': group_visual_info['kind'],
                    'media_path': str(group_visual_info['file_path']),
                    'media_name': group_visual_info['file_name'],
                    'media_mime': group_visual_info['mime_type'],
                    'media_size_bytes': group_visual_info['file_size'],
                    'raw': msg,
                }
                inbox_file = INBOX / f'{msg_id}.json'
                inbox_file.write_text(json.dumps(inbox_data, indent=2, ensure_ascii=False))
                log.info(
                    f'msg recebida (group/midia) msg_id={msg_id} chat_id={chat_id} '
                    f'from={user_name} kind={group_visual_info["kind"]} '
                    f'name="{group_visual_info["file_name"]}"'
                )
                react(chat_id, msg_id, '👀')
                start_typing(chat_id, duration=600)
                prefixed = f'[grupo chat_id={chat_id} from={user_name}({user_id})] {text}'
                enqueue_message(msg_id, prefixed, user_name, chat_id)
                return
            # 2) Sem foto/doc — tenta audio (fluxo original).
            group_audio_info = transcribe_telegram_audio(msg, context_label='group')
            if group_audio_info['kind']:
                # Tinha audio/video no grupo. Decide o que fazer.
                if group_audio_info['too_large']:
                    _send_group_text(
                        chat_id,
                        'Audio muito longo (>25MB), nao consigo transcrever.',
                        reply_to=msg_id,
                    )
                    _group_logger.info(
                        f'msg ignorada (audio >25MB) chat_id={chat_id} '
                        f'user_id={user_id} msg_id={msg_id}'
                    )
                    return
                # Video sem audio (mute) — ignora silenciosamente
                if group_audio_info['error'] == 'video_silent':
                    _group_logger.info(
                        f'video mudo ignorado chat_id={chat_id} '
                        f'user_id={user_id} msg_id={msg_id}'
                    )
                    return
                if group_audio_info['transcript']:
                    text = group_audio_info['transcript']
                else:
                    # Transcricao falhou — alerta Chefe via DM e ignora msg no grupo
                    _send_admin_outbox_text(
                        f'Transcricao de audio/video do grupo falhou.\n'
                        f'chat_id={chat_id}\n'
                        f'user_id={user_id}\n'
                        f'msg_id={msg_id}\n'
                        f'kind={group_audio_info["kind"]}\n'
                        f'duration={group_audio_info["duration"]}s\n'
                        f'erro: {group_audio_info["error"]}',
                        prefix='group_audio_fail',
                    )
                    _group_logger.error(
                        f'msg ignorada (transcricao falhou) chat_id={chat_id} '
                        f'user_id={user_id} msg_id={msg_id} '
                        f'kind={group_audio_info["kind"]} '
                        f'erro="{group_audio_info["error"]}"'
                    )
                    return
            else:
                _group_logger.info(
                    f'msg ignorada (sem texto) chat_id={chat_id} user_id={user_id} '
                    f'type={chat_type}'
                )
                return
        if not handle_group_message(msg, msg_id, chat_id, user_id, text):
            return
        # Passou os 3 checks. Preenche user_name e segue pro inbox/enqueue.
        user_name = (msg.get('from', {}) or {}).get('first_name', user_id)
        inbox_data = {
            'msg_id': msg_id, 'chat_id': chat_id, 'user_id': user_id,
            'user_name': user_name, 'text': text, 'chat_type': chat_type,
            'group_origin': True,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'raw': msg,
        }
        if group_audio_info and group_audio_info['transcript']:
            inbox_data['audio_file'] = str(group_audio_info['audio_path']) if group_audio_info['audio_path'] else None
            inbox_data['audio_kind'] = group_audio_info['kind']
            inbox_data['audio_duration'] = group_audio_info['duration']
            inbox_data['transcript'] = group_audio_info['transcript']
            inbox_data['audio_cost_brl'] = round(group_audio_info['cost_brl'], 4)
            # Se foi video MP4, salva metadata do video original (pra edicao posterior)
            if group_audio_info['kind'] == 'video' and group_audio_info.get('video_path'):
                inbox_data['video_file'] = str(group_audio_info['video_path'])
                inbox_data['video_kind'] = 'video'
                inbox_data['video_duration'] = group_audio_info['duration']
                inbox_data['video_size_mb'] = group_audio_info.get('video_size_mb', 0.0)
        inbox_file = INBOX / f'{msg_id}.json'
        inbox_file.write_text(json.dumps(inbox_data, indent=2, ensure_ascii=False))
        log.info(f'msg recebida (group) msg_id={msg_id} chat_id={chat_id} from={user_name}: {text[:80]}')
        react(chat_id, msg_id, '👀')
        start_typing(chat_id, duration=600)
        # Prefixo no texto pra Maia saber que a resposta vai pro grupo
        # e tambem o user que falou. Mantemos o chat_id por baixo da injecao.
        prefixed = f'[grupo chat_id={chat_id} from={user_name}({user_id})] {text}'
        enqueue_message(msg_id, prefixed, user_name, chat_id)
        return

    # ---------- DM continua fluxo original ----------
    # Audio/video handling (refatorado em 2026-05-19 — pipeline compartilhado com grupo)
    # Extendido 2026-05-19 pra MP4 normal (kind='video'): extrai audio via ffmpeg.
    audio_info = transcribe_telegram_audio(msg, context_label='dm')
    audio_file_id = audio_info['file_id']
    audio_kind = audio_info['kind']
    audio_path = audio_info['audio_path']
    transcript = audio_info['transcript']
    if audio_file_id:
        # Video mudo (mute): ignora silenciosamente igual no grupo
        if audio_info.get('error') == 'video_silent':
            log.info(f'DM video mudo ignorado msg_id={msg_id}')
            return
        if transcript:
            text = f'[{audio_kind}] {transcript}'
        elif not text:
            text = f'({audio_kind} - transcricao falhou)'

    # Foto/documento em DM (adicionado 2026-05-25). Sem audio mas com photo/doc:
    # baixa e injeta referencia no inbox pra Maia conseguir analisar.
    dm_visual_info = None
    if not audio_file_id:
        dm_visual_info = extract_visual_media_from_msg(msg, msg_id)
        if dm_visual_info['kind']:
            if dm_visual_info['too_large']:
                log.info(
                    f'DM midia visual SKIPPED (>50MB) msg_id={msg_id} '
                    f'kind={dm_visual_info["kind"]} size={dm_visual_info["file_size"]}'
                )
                # Notifica usuario via outbox direto pra ele saber
                try:
                    out = OUTBOX / f'{msg_id}_too_large.json'
                    out.write_text(json.dumps({
                        'chat_id': chat_id,
                        'text': 'Arquivo muito grande (>50MB) pra eu baixar via bot. Manda menor ou um link.',
                        'reply_to_message_id': msg_id,
                    }, ensure_ascii=False))
                except Exception:
                    pass
                return
            if dm_visual_info['error']:
                log.error(
                    f'DM midia visual download FALHOU msg_id={msg_id} '
                    f'kind={dm_visual_info["kind"]} erro={dm_visual_info["error"]}'
                )
                # Segue fluxo com text=(non-text) — Maia avisa o user
            else:
                _caption = text  # legenda original (ou vazio) antes de sobrescrever
                if dm_visual_info['kind'] == 'photo':
                    _marker = f'[foto recebida — arquivo em {dm_visual_info["file_path"]}]'
                else:
                    _marker = (
                        f'[documento recebido: {dm_visual_info["file_name"]} '
                        f'({dm_visual_info["mime_type"] or "?"}) — '
                        f'arquivo em {dm_visual_info["file_path"]}]'
                    )
                text = f'{_caption}\n{_marker}' if _caption else _marker
                log.info(
                    f'DM midia visual PROCESSED msg_id={msg_id} '
                    f'kind={dm_visual_info["kind"]} '
                    f'size={dm_visual_info["file_size"]} '
                    f'name="{dm_visual_info["file_name"]}"'
                )

    if not text:
        text = '(non-text)'
    user_name = msg.get('from', {}).get('first_name', user_id)

    # STOP: comando admin para preservar pastas avisadas pela limpeza_minio
    if isinstance(text, str) and text.strip().upper() == 'STOP' and str(user_id) == str(ADMIN_CHAT_ID):
        if handle_stop_command(chat_id, msg_id, user_id):
            log.info(f'STOP processado para msg_id={msg_id}')
            return

    # Comandos admin de grupo (DM do Chefe)
    if handle_admin_group_command(chat_id, msg_id, user_id, text):
        log.info(f'comando admin de grupo processado para msg_id={msg_id}')
        return

    inbox_file = INBOX / f'{msg_id}.json'
    inbox_data = {
        'msg_id': msg_id, 'chat_id': chat_id, 'user_id': user_id,
        'user_name': user_name, 'text': text,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'raw': msg
    }
    if audio_path:
        inbox_data['audio_file'] = str(audio_path)
        inbox_data['audio_kind'] = audio_kind
        inbox_data['transcript'] = transcript
        inbox_data['audio_cost_brl'] = round(audio_info.get('cost_brl', 0.0), 4)
        # Se foi video MP4, salva metadata do video original (pra edicao posterior)
        if audio_kind == 'video' and audio_info.get('video_path'):
            inbox_data['video_file'] = str(audio_info['video_path'])
            inbox_data['video_kind'] = 'video'
            inbox_data['video_duration'] = audio_info.get('duration', 0)
            inbox_data['video_size_mb'] = audio_info.get('video_size_mb', 0.0)
    # Foto/documento em DM (adicionado 2026-05-25)
    if dm_visual_info and dm_visual_info.get('kind') and dm_visual_info.get('file_path'):
        inbox_data['media_kind'] = dm_visual_info['kind']
        inbox_data['media_path'] = str(dm_visual_info['file_path'])
        inbox_data['media_name'] = dm_visual_info['file_name']
        inbox_data['media_mime'] = dm_visual_info['mime_type']
        inbox_data['media_size_bytes'] = dm_visual_info['file_size']
    inbox_file.write_text(json.dumps(inbox_data, indent=2, ensure_ascii=False))
    log.info(f'msg recebida msg_id={msg_id} from={user_name}: {text[:80]}')

    react(chat_id, msg_id, '👀')
    start_typing(chat_id, duration=600)  # mantem digitando ate resposta
    enqueue_message(msg_id, text, user_name, chat_id)

def poll_loop():
    log.info('polling loop iniciado')
    backoff = 1
    while running:
        try:
            offset = get_offset()
            r = requests.get(f'{API}/getUpdates',
                params={
                    'offset': offset, 'timeout': 30, 'limit': 100,
                    # allowed_updates precisa ser JSON; default exclui callback_query
                    'allowed_updates': json.dumps([
                        'message', 'edited_message', 'callback_query'
                    ]),
                },
                timeout=35)
            if r.status_code != 200:
                log.warning(f'http {r.status_code}: {r.text[:200]}')
                time.sleep(backoff); backoff = min(backoff * 2, 60); continue
            data = r.json()
            if not data.get('ok'):
                log.warning(f'!ok: {data}')
                time.sleep(backoff); backoff = min(backoff * 2, 60); continue
            backoff = 1
            for update in data.get('result', []):
                handle_update(update)
                save_offset(update['update_id'])
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            log.error(f'poll error: {e}')
            time.sleep(backoff); backoff = min(backoff * 2, 60)

def outbox_loop():
    log.info('outbox watcher iniciado')
    in_flight = set()  # nomes de arquivos sendo processados em thread
    while running:
        try:
            for f in sorted(OUTBOX.glob('*.json')):
                if f.name in in_flight:
                    continue
                try:
                    data = json.loads(f.read_text())
                    chat_id = data.get('chat_id', ADMIN_CHAT_ID)
                    text = data.get('text', '')
                    reply_to = data.get('reply_to_message_id')

                    # Branch de publicacao Instagram: campo 'instagram_publish' presente
                    if data.get('instagram_publish'):
                        in_flight.add(f.name)
                        def _runner_ig(fp=f, dt=data, fname=f.name):
                            try:
                                process_instagram_publish_outbox(fp, dt)
                            finally:
                                in_flight.discard(fname)
                        threading.Thread(target=_runner_ig, daemon=True).start()
                        continue

                    # Branch de documento: campo 'document' presente
                    if data.get('document'):
                        in_flight.add(f.name)
                        def _runner_doc(fp=f, dt=data, fname=f.name):
                            try:
                                process_document_outbox(fp, dt)
                            finally:
                                in_flight.discard(fname)
                        threading.Thread(target=_runner_doc, daemon=True).start()
                        continue

                    # Branch de imagem: campo 'image' presente
                    if data.get('image'):
                        in_flight.add(f.name)
                        def _runner(fp=f, dt=data, fname=f.name):
                            try:
                                process_image_outbox(fp, dt)
                            finally:
                                in_flight.discard(fname)
                        threading.Thread(target=_runner, daemon=True).start()
                        continue

                    if not text:
                        log.warning(f'outbox {f.name} sem text, skip')
                        f.rename(f.with_suffix('.empty'))
                        continue
                    use_voice = data.get('voice') is True or data.get('audio') is True
                    if use_voice:
                        # Gera audio (Gemini TTS voz Kore por default, fallback ElevenLabs) e envia como voice
                        ogg = synthesize_voice(text, f.stem)
                        if ogg:
                            with open(ogg, 'rb') as af:
                                files = {'voice': (ogg.name, af, 'audio/ogg')}
                                form = {'chat_id': chat_id}
                                if reply_to:
                                    form['reply_parameters'] = json.dumps({'message_id': int(reply_to)})
                                r = requests.post(f'{API}/sendVoice', data=form, files=files, timeout=30)
                        else:
                            log.warning(f'voice synthesis falhou, fallback texto: {f.name}')
                            payload = {'chat_id': chat_id, 'text': text}
                            if reply_to:
                                payload['reply_parameters'] = {'message_id': int(reply_to)}
                            r = requests.post(f'{API}/sendMessage', json=payload, timeout=10)
                    else:
                        payload = {'chat_id': chat_id, 'text': text}
                        if reply_to:
                            payload['reply_parameters'] = {'message_id': int(reply_to)}
                        r = requests.post(f'{API}/sendMessage', json=payload, timeout=10)
                    if r.status_code == 200 and r.json().get('ok'):
                        stop_typing(chat_id)
                        sent_file = SENT / f.name
                        sent_file.write_text(json.dumps({
                            **data,
                            'sent_at': datetime.now(timezone.utc).isoformat(),
                            'response': r.json().get('result', {})
                        }, indent=2, ensure_ascii=False))
                        f.unlink()
                        log.info(f'sent {f.name}')
                        mark_maia_responded()
                    else:
                        log.warning(f'send fail {f.name}: {r.text[:200]}')
                        f.rename(f.with_suffix('.failed'))
                except Exception as e:
                    log.error(f'outbox error {f.name}: {e}')
                    try: f.rename(f.with_suffix('.failed'))
                    except: pass
        except Exception as e:
            log.error(f'outbox loop error: {e}')
        time.sleep(2)

if __name__ == '__main__':
    log.info('=== Maia Telegram Bot iniciando ===')
    try:
        r = requests.get(f'{API}/getMe', timeout=10)
        info = r.json().get('result', {})
        log.info(f'bot: @{info.get("username")} ({info.get("first_name")})')
    except Exception as e:
        log.error(f'getMe falhou: {e}')
        sys.exit(1)
    
    threading.Thread(target=outbox_loop, daemon=True).start()
    threading.Thread(target=typing_loop, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=claude_prompt_watcher_loop, daemon=True).start()
    threading.Thread(target=_session_cleanup_loop, daemon=True).start()
    
    try:
        poll_loop()
    except KeyboardInterrupt:
        pass
    log.info('=== bot encerrado ===')
