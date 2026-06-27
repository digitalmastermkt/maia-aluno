# INSTALL-CLAUDE.md — MAIA Aluno (Claude) + como aplicar upgrades

Digital Master / Salatiel Batista — "Movimento gera resultado."

**MAIA Aluno (Claude)** e a edicao de entrada da sua MAIA: enxuta, ja usavel e
pronta pra receber upgrades. Este guia cobre (1) instalar a **edicao de entrada** e
(2) **plugar upgrades** depois, sem nunca reinstalar nem mutar o nucleo. O aluno
personaliza a propria marca no onboarding (a MAIA continua white-label).

---

## 1. Instalar a edicao de entrada

Pre-requisito: VPS Ubuntu/Debian limpa, acesso root. Copie esta pasta pra VPS:

```bash
sudo bash install.sh
```

O `install.sh` (idempotente) faz: timezone, pacotes (Node 22, Claude Code CLI,
Python 3.12, ffmpeg, tmux, sqlite3, jq), swap 2GB, usuario `maia` + `/opt/MAIA`,
copia a base, materializa `dotclaude/` -> `.claude/`, cria o venv do bot e instala
a unit systemd `maia-telegram-bot` **desabilitada**.

> A base **NAO** instala memoria semantica (porta 3007), time de subagentes nem
> Whisper local. Tudo isso e upgrade (secao 3).

Depois do install:

1. `nano /opt/MAIA/bot/.env` — preencher `TELEGRAM_BOT_TOKEN`, `ALLOWED_USERS`,
   `ADMIN_CHAT_ID`, `GEMINI_API_KEY` (lista completa comentada em `bot/.env.TEMPLATE`).
2. Login OAuth da conta Claude do cliente (a sessao tmux NAO sobrevive a reboot):
   ```bash
   sudo -u maia tmux new -s maia-master
   cd /opt/MAIA && claude --model claude-opus-4-8 --dangerously-skip-permissions
   # /login pela URL OAuth, depois Ctrl-b d
   ```
3. Subir o bot (so depois do .env preenchido):
   ```bash
   systemctl enable --now maia-telegram-bot
   ```
4. Mande **"oi"** no bot. A MAIA conduz o onboarding e grava `brand/brand.json`.

---

## 2. O que ja vem na base

- **bot/** — bot Telegram (daemon systemd), reusado do template (nao reescrito).
- **CLAUDE.md** — persona/cerebro da MAIA orquestradora (agnostico ao LLM).
- **brand_loader.py + brand/brand.json** — onboarding de marca white-label.
- **dotclaude/skills/** — 3 skills leves: `skill-claude-md-builder`,
  `skill-persona-profunda`, `skill-conteudo-viral-vendas`.
- **upgrades-engine/** — o **motor de plugin** (abaixo), pronto pra receber upgrade.
- **install.sh + requirements.txt + lib/** — instalador e deps minimas.

---

## 3. Como aplicar um UPGRADE (a "porta" da base)

Um upgrade e um pacote (`.zip`) com 1+ skills e/ou subagentes. Ele **PLUGA** na
base — nunca toca no nucleo. O motor esta em `upgrades-engine/`:

| Script | Papel |
|---|---|
| `install-skill.sh` | Instala UMA skill idempotente em `~/.claude/skills/<nome>/` (resolve HOME sob sudo, pula se `diff -rq` igual, faz chown). |
| `update.sh` | Orquestra 5 passos guiado por `SKILLS=(...)`: detect -> backup -> install -> restart -> validate, com `rollback.sh`. |
| `detect-version.sh` / `backup-config.sh` / `validate.sh` / `rollback.sh` | Versao, snapshot, smoke test e reversao. |
| `manifest.example.json` | Formato do manifesto de um pacote de upgrade. |
| `CONVENCAO.md` | A convencao tecnica completa (ler antes de empacotar). |

### Instalar uma skill avulsa (caminho simples)

```bash
# baixe/descompacte o pacote do upgrade em /opt/maia-upgrades/<id>/
sudo bash /opt/MAIA/upgrades-engine/install-skill.sh /opt/maia-upgrades/<id>/skills/<nome-da-skill>
# o Claude Code descobre a skill automaticamente na proxima sessao (sem restart)
```

### Aplicar um pacote (caminho orquestrado)

```bash
sudo bash /opt/MAIA/upgrades-engine/update.sh /opt/maia-upgrades/<id>/manifest.json
```

`update.sh` faz backup -> instala todas as skills do manifesto -> reinicia o bot
-> roda `validate.sh`. Se algo falhar, `rollback.sh` restaura o snapshot anterior.

### Upgrade de Memoria Semantica (destacavel)

A base roda sem ele. O upgrade aplica um `schema.sql` (sqlite-vec local OU
PostgreSQL+pgvector), liga o servico na porta 3007 e instala os crons. So depois
disso o PASSO 2 do CLAUDE.md (busca por significado) fica ativo.

### Upgrade Time de Agentes (7 subagentes)

Materializa `/opt/MAIA/.claude/agents/` com Lis, Theo, Leo, Nina, Eva, Ravi, Caio.
A MAIA passa a delegar ao time nomeado em vez de subagentes Task genericos.

---

## 4. Regra de ouro

**Um upgrade nunca muta o nucleo.** Ele so adiciona pastas em `.claude/skills/`,
`.claude/agents/` e (quando aplicavel) aplica `schema.sql` no banco opcional. A
base segue identica e atualizavel por conta propria.

> A MESMA base de skills/persona serve os dois cerebros da esteira (Claude CLI e
> Codex/OpenClaw) — so muda o arquivo de config do cerebro. Esta pasta e a versao
> **Claude CLI** (cerebro Anthropic via login OAuth).
