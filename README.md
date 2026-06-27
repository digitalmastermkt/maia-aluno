# MAIA Aluno

Sua assistente de IA white-label rodando no **Claude Code CLI**, comandada pelo **Telegram**: ela orquestra, automatiza e organiza o seu negócio direto da sua VPS.

> "MAIA Aluno" é o nome da EDIÇÃO. O nome da SUA assistente você define no onboarding — é white-label, a marca é sua.

---

## O que ela faz

- Vive numa VPS sua e responde pelo seu bot do Telegram (texto, áudio e imagem).
- Funciona como **orquestradora**: entende o pedido, delega para subagentes e te traz o resultado.
- Já vem com 3 skills leves prontas pra entregar valor na primeira sessão:
  - **skill-claude-md-builder** — onboarding estruturado do seu negócio (gera/atualiza o `CLAUDE.md`).
  - **skill-persona-profunda** — persona completa (30 dimensões + ICP + mapa de empatia).
  - **skill-conteudo-viral-vendas** — roteiros prontos pra gravar e vender.
- É **modular**: novas skills, time de subagentes e memória semântica plugam depois via `upgrades-engine/`, sem reinstalar nada.

---

## Pré-requisitos

Antes de instalar, tenha em mãos:

| Item | Para que serve |
|---|---|
| **VPS Ubuntu/Debian** (acesso root) | Onde a MAIA vai morar |
| **Conta Claude** (login OAuth) | O cérebro da assistente (Claude Code CLI) |
| **Token de bot do Telegram** (via @BotFather) | Canal de conversa |
| **Seu ID do Telegram** (via @userinfobot) | Autoriza só você a comandar |
| **Chave da API Gemini** | Voz, imagem e recursos de IA do bot |

Opcionais (a base funciona sem): chaves Groq/OpenAI (transcrição de áudio), ElevenLabs (voz premium).

---

## Instalação (resumo)

1. **Clone na sua VPS:**
   ```bash
   git clone https://github.com/digitalmastermkt/maia-aluno.git
   cd maia-aluno
   ```

2. **Rode o instalador** (idempotente — pode rodar de novo sem quebrar nada):
   ```bash
   sudo bash install.sh
   ```
   Ele cuida de timezone, pacotes (Node 22, Claude Code CLI, Python 3.12, ffmpeg, tmux), swap, usuário `maia`, copia tudo pra `/opt/MAIA`, cria o venv do bot e deixa a unit systemd `maia-telegram-bot` instalada e **desabilitada** (sobe só depois que você configurar).

3. **Preencha as credenciais:**
   ```bash
   nano /opt/MAIA/bot/.env
   ```
   Obrigatórios: `TELEGRAM_BOT_TOKEN`, `ALLOWED_USERS`, `ADMIN_CHAT_ID`, `GEMINI_API_KEY` (a lista completa está comentada em `bot/.env.TEMPLATE`).

4. **Faça login na conta Claude** (a sessão tmux não sobrevive a reboot — relance quando reiniciar a VPS):
   ```bash
   sudo -u maia tmux new -s maia-master
   cd /opt/MAIA && claude --dangerously-skip-permissions
   # faça o /login pela URL OAuth, depois saia da tmux com Ctrl-b d
   ```

5. **Suba o bot** (só depois do `.env` preenchido):
   ```bash
   systemctl enable --now maia-telegram-bot
   ```

6. **Mande "oi" no seu bot do Telegram.** A MAIA conduz o **onboarding de marca**: pergunta sobre você, seu negócio, produtos, tom de voz e canais, e grava tudo em `brand/brand.json`.

Passo a passo completo + como aplicar upgrades: veja **`INSTALL-CLAUDE.md`**.

---

## White-label: a assistente é sua

A MAIA não vem com marca pré-definida. Toda a identidade (nome da assistente, nome do seu negócio, @ do Instagram, site, slogan, cores, produtos) é preenchida por VOCÊ no onboarding e gravada em:

```
brand/brand.json
```

Esse arquivo é a única fonte de verdade de marca que as skills leem. Enquanto estiver em branco, rodapés e handles ficam neutros. Depois do onboarding, tudo passa a sair com a SUA marca.

---

## Upgrades

A base é enxuta de propósito. Recursos avançados (mais skills, time de 7 subagentes nomeados, memória semântica por significado, transcrição local, etc.) chegam como pacotes que **plugam** na base sem tocar no núcleo. O motor está em `upgrades-engine/` e o passo a passo em `INSTALL-CLAUDE.md`.

---

Digital Master — "Movimento gera resultado."
