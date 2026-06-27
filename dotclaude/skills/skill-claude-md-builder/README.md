# CLAUDE.md Builder

Skill interativa pro Claude Code que monta o **cerebro da sua IA** atraves de uma entrevista estrategica sobre o seu negocio. Voce responde 8 blocos de perguntas, uma de cada vez, e o Claude grava sozinho o arquivo `CLAUDE.md` — dali em diante a IA sempre sabe quem voce e, pra quem vende, como fala e o que te diferencia.

Criada pela **Digital Master** (Salatiel Batista) pra empresarios que querem configurar o Claude Code direito desde o primeiro dia. Movimento gera resultado.

---

## Pre-requisitos

Ter o Claude Code instalado:

```bash
npm install -g @anthropic-ai/claude-code
```

Confirma rodando `claude --version`.

---

## Instalacao

Copie a pasta `skill-claude-md-builder` para dentro de `~/.claude/skills/`:

```bash
cp -r skill-claude-md-builder ~/.claude/skills/skill-claude-md-builder
```

Pronto. A skill ja ta disponivel.

---

## Como usar

1. Vai pra pasta do seu projeto (ou cria uma nova):

```bash
mkdir ~/meu-negocio && cd ~/meu-negocio
```

2. Abre o Claude Code:

```bash
claude
```

3. Roda a skill:

```
/skill-claude-md-builder
```

4. Responde os 8 blocos. No final o `CLAUDE.md` e gerado automaticamente na pasta.

---

## O que a skill cobre

8 blocos de perguntas estrategicas, uma por vez, com exemplos:

1. **Identidade** — nome, negocio, o que voce faz, desde quando
2. **Publico-alvo** — perfil, dor, desejo, onde te encontram, poder aquisitivo
3. **Produtos** — principal, entrada, premium
4. **Tom de voz** — estilo, referencia, palavras banidas, palavras-marca
5. **Diferencial** — o que te torna unico, metodologia, resultado concreto
6. **Prova social** — depoimentos e numeros
7. **Canais** — Instagram, WhatsApp, site
8. **Regras pra IA** — foco do conteudo, preferencias adicionais

---

## Teste rapido depois de criar o CLAUDE.md

Dentro do Claude Code na pasta do projeto:

```
Crie 3 ideias de post pro meu Instagram
```

Se ele responder ja sabendo seu nicho, tom de voz e publico — funcionou.

---

## Licenca

Conteudo proprio da Digital Master. Use, adapte e compartilhe a vontade.

---

Feito pela **Digital Master** — Salatiel Batista
