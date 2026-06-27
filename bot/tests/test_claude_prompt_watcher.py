#!/usr/bin/env python3
"""
Sandbox de testes do detector de prompts do Claude Code (claude_prompt_watcher).

Cobre:
  1. Deteccao do regex generico para os verbos make, create, proceed, delete
  2. Validacao do gate de menu numerado (false positive)
  3. Auto-dismiss do prompt de rating (NAO regride)
  4. Dedupe dentro do TTL (mesmo prompt 2x -> 1 envio)
  5. Extracao do target/arquivo da pergunta
  6. Construcao da mensagem do Telegram com verbo dinamico

Roda standalone:
    /usr/bin/python3 /opt/MAIA/bot/tests/test_claude_prompt_watcher.py

Saida: relatorio "X de Y passaram" + exit code 0/1.
"""
import sys
import time
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

# Carrega bot.py como modulo sem executar o `if __name__ == '__main__'`.
# Tambem precisa mockar requests.get (getMe na inicializacao roda fora do __main__? Nao,
# fica dentro do __main__, entao seguro carregar como modulo).
BOT_PATH = Path('/opt/MAIA/bot/bot.py')
spec = importlib.util.spec_from_file_location('bot_under_test', BOT_PATH)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fake_pane(verb, target=None, with_menu=True, cursor_pos=1):
    """Gera um snapshot fake de pane do Claude Code com o prompt + menu numerado.
    verb: o verbo da pergunta (make/create/proceed/delete/...)
    target: o arquivo/caminho (opcional, alguns verbos como 'proceed' nao tem)
    with_menu: se False, NAO inclui o menu numerado (pra testar falso positivo)
    cursor_pos: posicao do cursor ❯ (1, 2 ou 3)
    """
    if verb == 'make':
        question = f"Do you want to make this edit to {target or '/tmp/foo.txt'}?"
    elif verb == 'create':
        question = f"Do you want to create {target or '/tmp/new.txt'}?"
    elif verb == 'proceed':
        question = "Do you want to proceed?"
    elif verb == 'delete':
        question = f"Do you want to delete {target or '/tmp/old.txt'}?"
    elif verb == 'run':
        question = f"Do you want to run this command?"
    else:
        question = f"Do you want to {verb} something?"

    header = (
        "  some context line above\n"
        "  more context\n"
        "  \n"
        f"  {question}\n"
        "\n"
    )
    if not with_menu:
        return header + "  (no menu — false positive test)\n"
    cur1 = '❯' if cursor_pos == 1 else ' '
    cur2 = '❯' if cursor_pos == 2 else ' '
    cur3 = '❯' if cursor_pos == 3 else ' '
    menu = (
        f"  {cur1} 1. Yes\n"
        f"  {cur2} 2. Yes, and don't ask again this session (shift+tab)\n"
        f"  {cur3} 3. No, and tell Claude what to do differently (esc)\n"
    )
    return header + menu


def _fake_pane_rating():
    return (
        "  Some output above\n"
        "  How is Claude doing this session?\n"
        "  (1) bad  (2) ok  (3) good\n"
    )


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------
class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def check(self, name, ok, detail=''):
        if ok:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            self.errors.append((name, detail))
            print(f"  FAIL  {name}{('  ->  ' + detail) if detail else ''}")

    def total(self):
        return self.passed + self.failed

    def summary(self):
        return f"{self.passed} de {self.total()} testes passaram"


R = TestResult()


# ---------------------------------------------------------------------------
# TEST 1: deteccao dos 4 verbos chave (make, create, proceed, delete)
# ---------------------------------------------------------------------------
print("\n[1] Deteccao do regex generico — 4 verbos chave")
for verb, target in [
    ('make',    '/opt/MAIA/bot/bot.py'),
    ('create',  '/opt/MAIA/.claude/agents/_teste-trava.md'),
    ('proceed', None),
    ('delete',  '/tmp/old.txt'),
]:
    pane = _fake_pane(verb, target=target, with_menu=True)
    kind, extra = bot.detect_claude_prompt(pane)
    ok_kind = (kind == 'generic')
    ok_verb = (extra and extra.get('verb') == verb)
    R.check(
        f"verbo '{verb}' detectado como kind=generic",
        ok_kind and ok_verb,
        detail=f"kind={kind!r} extra={extra!r}",
    )
    if target:
        # target deve estar capturado (heuristica)
        ok_target = (target in (extra or {}).get('target', ''))
        R.check(
            f"verbo '{verb}' target extraido contem '{target}'",
            ok_target,
            detail=f"target={extra.get('target')!r}",
        )


# ---------------------------------------------------------------------------
# TEST 2: gate do menu numerado (false positive)
# ---------------------------------------------------------------------------
print("\n[2] Gate do menu numerado — false positive deve ser IGNORADO")
pane_no_menu = _fake_pane('create', target='/tmp/x.txt', with_menu=False)
kind, extra = bot.detect_claude_prompt(pane_no_menu)
R.check(
    "sem menu numerado nao dispara prompt",
    kind is None,
    detail=f"kind={kind!r}",
)

# E texto solto contendo "Do you want to X?" em log (tambem deve ignorar)
pane_log_only = (
    "  $ cat /tmp/foo.log\n"
    "  User asked: Do you want to delete this row?\n"
    "  (just a log line — not a real prompt)\n"
    "  $ \n"
)
kind, extra = bot.detect_claude_prompt(pane_log_only)
R.check(
    "log com 'Do you want to' sem menu ignorado",
    kind is None,
    detail=f"kind={kind!r}",
)


# ---------------------------------------------------------------------------
# TEST 3: rating auto-dismiss NAO regride
# ---------------------------------------------------------------------------
print("\n[3] Padrao rating (auto-dismiss) preservado")
pane_rating = _fake_pane_rating()
kind, extra = bot.detect_claude_prompt(pane_rating)
R.check(
    "rating detectado como kind=rating",
    kind == 'rating',
    detail=f"kind={kind!r}",
)

# Auto-dismiss chama _tmux_send_keys 2x (0 + C-m). Mockar e validar.
with patch.object(bot, '_tmux_send_keys') as mock_keys:
    mock_keys.return_value = True
    bot.auto_dismiss_rating(pane_rating)
    calls = [c.args for c in mock_keys.call_args_list]
    R.check(
        "auto_dismiss_rating envia '0' + 'C-m'",
        len(calls) == 2
            and calls[0][0] == '0'
            and calls[1][0] == 'C-m',
        detail=f"calls={calls!r}",
    )


# ---------------------------------------------------------------------------
# TEST 4: send_claude_prompt_buttons com verbo dinamico
# ---------------------------------------------------------------------------
print("\n[4] send_claude_prompt_buttons monta mensagem dinamica por verbo")

def _mock_requests_post(captured):
    """Retorna um mock pra requests.post que armazena o payload e devolve ok=True."""
    def _impl(url, json=None, data=None, files=None, timeout=None, **kw):
        captured.append({'url': url, 'json': json, 'data': data})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'ok': True, 'result': {'message_id': 99999}}
        resp.text = '{"ok":true}'
        resp.content = b'{"ok":true}'
        return resp
    return _impl


for verb, target, expected_label in [
    ('make',    '/tmp/foo.py',                 'EDITAR'),
    ('create',  '/opt/MAIA/agents/x.md','CRIAR'),
    ('proceed', None,                          'CONTINUAR'),
    ('delete',  '/tmp/legacy.txt',             'DELETAR'),
]:
    pane = _fake_pane(verb, target=target, with_menu=True)
    kind, extra = bot.detect_claude_prompt(pane)
    assert kind == 'generic', f'{verb}: deteccao quebrou'
    captured = []
    with patch.object(bot.requests, 'post', side_effect=_mock_requests_post(captured)):
        # Tambem precisamos limpar pending_callbacks pra cada teste
        with bot.pending_callbacks_lock:
            bot.pending_callbacks.clear()
        ok, tg_msg, cb_id = bot.send_claude_prompt_buttons(kind, extra, pane)
    R.check(
        f"verbo '{verb}': envio bem-sucedido",
        ok and tg_msg == 99999 and cb_id,
        detail=f"ok={ok} tg_msg={tg_msg} cb_id={cb_id}",
    )
    # Verifica que a msg do Telegram tem o label esperado
    if captured:
        body = (captured[0]['json'] or {}).get('text', '')
        R.check(
            f"verbo '{verb}': msg do Telegram contem '{expected_label}'",
            expected_label in body,
            detail=f"body[:200]={body[:200]!r}",
        )
        if target:
            R.check(
                f"verbo '{verb}': msg contem o target '{target}'",
                target in body,
                detail=f"body[:200]={body[:200]!r}",
            )


# ---------------------------------------------------------------------------
# TEST 5: dedupe — mesmo prompt 2x dentro do TTL gera 1 envio
# ---------------------------------------------------------------------------
print("\n[5] Dedupe — mesmo prompt 2x no TTL gera APENAS 1 envio")

# Limpa o estado global
with bot.recent_prompts_lock:
    bot.recent_prompts.clear()
with bot.pending_callbacks_lock:
    bot.pending_callbacks.clear()

pane = _fake_pane('create', target='/tmp/dedupe.txt', with_menu=True)

# Simula o que claude_prompt_watcher_loop faz: hash -> verifica recent_prompts
# -> se novo, envia botoes; se ja conhecido, pula.
captured = []
def _send_once():
    kind, extra = bot.detect_claude_prompt(pane)
    if not kind or kind == 'rating':
        return False  # nao aplica aqui
    phash = bot._prompt_hash(pane)
    with bot.recent_prompts_lock:
        if phash in bot.recent_prompts:
            return False  # dedupe: nao envia
        bot.recent_prompts[phash] = {
            'first_seen': time.time(),
            'telegram_msg_id': None,
            'kind': kind,
            'callback_id': None,
        }
    with patch.object(bot.requests, 'post', side_effect=_mock_requests_post(captured)):
        bot.send_claude_prompt_buttons(kind, extra, pane)
    return True

# 1a vez: envia. 2a vez (mesmo pane, dentro do TTL): nao envia.
first  = _send_once()
second = _send_once()

R.check(
    "1a chamada envia, 2a chamada (dedupe) NAO envia",
    first is True and second is False and len(captured) == 1,
    detail=f"first={first} second={second} envios={len(captured)}",
)


# ---------------------------------------------------------------------------
# TEST 6: verbos extras conhecidos (run, apply) tambem casam
# ---------------------------------------------------------------------------
print("\n[6] Verbos secundarios (run, apply) tambem casam")
for verb in ['run', 'apply', 'execute']:
    pane = _fake_pane(verb, target='/tmp/anything.sh', with_menu=True)
    kind, extra = bot.detect_claude_prompt(pane)
    R.check(
        f"verbo '{verb}' casa no regex generico",
        kind == 'generic' and extra.get('verb') == verb,
        detail=f"kind={kind} extra={extra}",
    )


# ---------------------------------------------------------------------------
# TEST 7: verbo nao mapeado tem fallback decente
# ---------------------------------------------------------------------------
print("\n[7] Verbo novo (frobnicate) recebe fallback decente na msg")
# Verbo inventado pra simular um novo prompt que a Anthropic introduza
pane = _fake_pane('frobnicate', target='/tmp/baz', with_menu=True)
kind, extra = bot.detect_claude_prompt(pane)
R.check(
    "verbo inedito 'frobnicate' detectado",
    kind == 'generic' and extra.get('verb') == 'frobnicate',
    detail=f"kind={kind} extra={extra}",
)

captured = []
with patch.object(bot.requests, 'post', side_effect=_mock_requests_post(captured)):
    with bot.pending_callbacks_lock:
        bot.pending_callbacks.clear()
    ok, _, _ = bot.send_claude_prompt_buttons(kind, extra, pane)
R.check(
    "verbo inedito gera msg com fallback (.upper())",
    ok and 'FROBNICATE' in (captured[0]['json'].get('text', '') if captured else ''),
    detail=f"body[:200]={(captured[0]['json'].get('text','')[:200] if captured else '')!r}",
)


# ---------------------------------------------------------------------------
# TEST 8: caso real do teste end-to-end de 12/05 (bug original do dono)
# ---------------------------------------------------------------------------
print("\n[8] Caso real 12/05 — Write em .claude/agents/ (bug original)")
pane_caso_real = (
    "  Editing .claude/agents/_teste-trava.md\n"
    "\n"
    "  Do you want to create .claude/agents/_teste-trava.md?\n"
    "\n"
    "  > 1. Yes\n"
    "    2. Yes, and don't ask again this session (shift+tab)\n"
    "    3. No, and tell Claude what to do differently (esc)\n"
)
kind, extra = bot.detect_claude_prompt(pane_caso_real)
R.check(
    "caso real: kind=generic, verb=create",
    kind == 'generic' and extra.get('verb') == 'create',
    detail=f"kind={kind!r} extra={extra!r}",
)
R.check(
    "caso real: target captura .claude/agents/_teste-trava.md",
    '.claude/agents/_teste-trava.md' in (extra or {}).get('target', ''),
    detail=f"target={extra.get('target')!r}",
)
# Mensagem do Telegram deve mencionar CRIAR + o path
captured = []
with patch.object(bot.requests, 'post', side_effect=_mock_requests_post(captured)):
    with bot.pending_callbacks_lock:
        bot.pending_callbacks.clear()
    ok, _, _ = bot.send_claude_prompt_buttons(kind, extra, pane_caso_real)
body = captured[0]['json'].get('text', '') if captured else ''
R.check(
    "caso real: msg do Telegram contem 'CRIAR' e o path",
    ok and 'CRIAR' in body and '.claude/agents/_teste-trava.md' in body,
    detail=f"body[:300]={body[:300]!r}",
)


# ---------------------------------------------------------------------------
# Final
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print(R.summary())
if R.failed:
    print()
    print("Falhas:")
    for n, d in R.errors:
        print(f"  - {n}: {d}")
print("=" * 70)

sys.exit(0 if R.failed == 0 else 1)
