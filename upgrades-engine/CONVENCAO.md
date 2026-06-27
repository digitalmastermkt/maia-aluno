# Convencao da Mecanica de Upgrade Modular - MAIA Esteira (Digital Master)

> Padrao tecnico que permite vender a MAIA como **base enxuta (entrada barata)**
> e cobrar os **upgrades a parte** (skills, banco de memoria semantica,
> subagentes), sem nunca tocar no núcleo que ja roda na maquina do cliente.
>
> Marca: Digital Master / Salatiel Batista. "Movimento gera resultado."

---

## 1. O que e uma skill

Uma **skill e uma pasta autodescoberta**. Nada de registro central, nada de
"importar" em codigo. A pasta contem:

```
skills/<nome>/
  SKILL.md          # obrigatorio - frontmatter YAML + instrucoes
  scripts/          # opcional - scripts auxiliares
  templates/        # opcional - modelos, assets, exemplos
```

O `SKILL.md` comeca com frontmatter YAML:

```yaml
---
name: skill-dossie-sdr
description: Quando usar a skill (gatilhos claros pro runtime decidir sozinho).
allowed-tools: Read, Write, Bash
---
```

- **No Claude Code (cerebro Anthropic):** o runtime descobre a skill lendo
  `~/.claude/skills/*/SKILL.md` a cada sessao. Instalar = copiar a pasta pra la.
  **Zero config extra, zero restart.**
- **No OpenClaw (cerebro Codex/GLM):** nao ha autodescoberta. Apos copiar a
  pasta, e preciso **registrar a skill em `openclaw.json`** e **reiniciar** o
  OpenClaw. Mesmo conteudo de skill; muda so o passo de ativacao.

> O **mesmo modulo serve os dois cerebros** porque a skill (persona +
> instrucoes) e agnostica ao LLM. O que muda e a ativacao, nao o conteudo.

---

## 2. Base x Upgrade

- **Base (núcleo):** bot + memoria local + 4 skills essenciais. Roda sozinha.
  E o produto de **entrada barata**. Nunca e reinstalada por um upgrade.
- **Upgrade:** pacote que **PLUGA** skills / subagentes / banco semantico na
  base. Vendido a parte. A comunidade libera ALGUNS; a consultoria premium
  libera TODOS.

Regra de ouro: **um upgrade nunca muta o núcleo**. Ele so adiciona pastas em
`~/.claude/skills/`, `~/.claude/agents/` e (quando aplicavel) aplica um
`schema.sql` no banco semantico opcional.

---

## 3. Repo de SETUP x Repo de UPDATE + CHANGELOG

Dois repositorios separados:

- **Repo de SETUP** (a base white-label): instala a MAIA do zero na VPS.
- **Repo de UPDATE** (os upgrades): cada pacote "pluga" sem tocar no que roda.
  Versionado num `CHANGELOG.md` com o padrao `ANO.MINOR-upgrade-N`
  (ex: `2026.4-upgrade-1`).

Isso permite evoluir os upgrades sem reempacotar a base, e vice-versa.

---

## 4. Scripts desta pasta (mecanismo de referencia)

| Script              | Papel |
|---------------------|-------|
| `install-skill.sh`  | Instala UMA skill de forma idempotente em `~/.claude/skills/<nome>/`. Resolve HOME real sob sudo (`SUDO_USER`), pula se `diff -rq` der igual, faz `chown`. |
| `update.sh`         | Orquestrador de 5 passos guiado pelo array `SKILLS=(...)`: detect -> backup -> loop install -> restart -> validate, com `rollback.sh` em caso de erro. |
| `detect-version.sh` | Le a versao atual (arquivo `VERSION` ou `CHANGELOG.md`). |
| `backup-config.sh`  | Snapshot `.tar.gz` da config antes de atualizar; imprime o caminho na ultima linha. |
| `validate.sh`       | Smoke test pos-install (skills com SKILL.md, bot ativo, banco opcional). |
| `rollback.sh`       | Restaura o backup mais recente e reinicia o bot. |
| `manifest.example.json` | Formato do manifesto de um pacote de upgrade. |

Todos os `.sh` sao idempotentes e seguros para rodar de novo.

---

## 5. Manifesto do pacote (`manifest.example.json`)

Cada pacote de upgrade carrega um manifesto declarando: `id`, `nome`,
`versao`, `cerebro_alvo` (`claude` e/ou `codex`), lista de `skills`,
`subagentes` (com `model_padrao` + `fallbacks`), `arquivos_extra`,
`passos_pos_install` (separados por cerebro) e bloco `comercial`
(preco, entrega `.zip` no pagamento, se entra no bundle anual).

O `update.sh` pode ler o manifesto para popular o array `SKILLS=(...)`
automaticamente, ou o array pode ser mantido na mao para pacotes simples.

---

## 6. Banco semantico destacavel (upgrade opcional)

A base funciona **sem** banco semantico. O upgrade de memoria semantica:

1. aplica um `schema.sql` (PostgreSQL + pgvector, indice HNSW
   `vector_cosine_ops` + `pg_trgm`);
2. liga o plugin de memoria;
3. instala os crons desacoplados (flush a cada 2h, reindex periodico).

Por ser destacavel, vira um **SKU proprio** na esteira.

---

## 7. Dual-brain e CONFIG, nao codigo

No OpenClaw, trocar/empilhar cerebro e **so configuracao**:

- `configure-glm.sh`: provider compativel com OpenAI (GLM da Z.ai) via patch
  no `openclaw config`.
- `configure-gpt-codex.sh`: `openclaw models auth login` (OAuth do ChatGPT Plus).

Cada subagente declara seu `model` + `fallbacks`. Como a persona e as skills
sao agnosticas ao LLM, **o mesmo agente serve os dois cerebros** (Claude e
Codex) sem reescrever conteudo.

> PASSIVO DE ToS (registrar): entregar OAuth (ChatGPT Plus / Claude) headless
> 24/7 para dezenas de alunos pode ferir o ToS da OpenAI/Anthropic e gerar ban
> em massa. Documentar como risco antes de escalar a venda.
