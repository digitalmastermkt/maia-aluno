# skill-persona-profunda

Skill da **Digital Master** que constroi o raio-x completo do cliente usando o **Metodo
Raio-X de Persona** — as **30 Camadas da Mente do Cliente** + Buyer Persona + ICP (Ideal
Customer Profile) + Mapa de Empatia + Anti-Persona.

Entrega perfis ultradetalhados com 10 insights por camada (300+ no total) prontos para copy,
lancamentos, anuncios e estrategia de produto.

> Persona boa nao e a que parece bonita no slide. E a que faz o cliente sentir que voce leu a
> mente dele. Movimento gera resultado: persona so vale quando vira copy, anuncio e oferta.

---

## O que ela faz

Quando voce invoca `/skill-persona-profunda`, ela ativa 5 fluxos:

1. **Persona Profunda Completa** — 30 Camadas da Mente (medos, desejos, crencas, identidade, sentido, gatilhos, objecoes, vocabulario, rotina)
2. **Buyer Persona Estrategica** — perfil completo para copy e funil
3. **ICP (Ideal Customer Profile)** — quem e o cliente ideal pra prospectar
4. **Mapa de Empatia Expandido** — o que pensa, sente, ve, ouve, fala e faz
5. **Anti-Persona** — quem NAO e seu cliente (filtro de qualificacao)

## Como usar

Dentro do Claude Code, basta pedir:

- `/skill-persona-profunda` — menu completo
- "cria uma persona profunda pra meu produto X"
- "monta o buyer persona da minha mentoria"
- "quero o ICP do meu servico"
- "faz o mapa de empatia do meu publico"
- "quem NAO e meu cliente?" (anti-persona)

A skill levanta o nucleo do produto (Passo 0), faz perguntas de descoberta e devolve o perfil
estruturado pronto pra usar em copy, anuncios, headlines, e-mail e pagina de vendas.

## Estrutura

```
skill-persona-profunda/
├── SKILL.md                        # arquivo principal da skill
├── references/
│   ├── dimensoes-psicologicas.md   # as 30 camadas detalhadas
│   ├── buyer-persona-framework.md  # framework Buyer Persona
│   ├── icp-framework.md            # framework ICP
│   └── aplicacao-em-copy.md        # como aplicar a persona em copy
└── scripts/
    └── upload_persona_to_minio.py  # entrega dos artefatos via drive (opcional)
```

## Integra com

Outras skills da Digital Master (confirme que existem em `.claude/skills/` antes de acionar):

- `/copywriting` — escreve copy mirado nessa persona
- `/skill-pagina-vendas` — gera pagina de vendas usando o perfil
- `/skill-carrossel-instagram` — carrossel de identificacao a partir das dores
- `/skill-copy-ads-ptbr` — anuncios baseados nos gatilhos mapeados
- `/email-sequence` — nurture que trabalha as objecoes

## Anti-alucinacao

A skill NUNCA inventa dado demografico, psicografico ou estatistica sem base real. Se faltar
informacao critica, ela para e pergunta; se o dado nao existir, marca `[HIPOTESE A VALIDAR]`
no documento. Precisao acima de volume — copy em cima de dado falso queima verba de trafego.

## Licenca

Conteudo e metodo proprietarios da Digital Master (Salatiel Batista). Uso interno e por
clientes da Digital Master. Os conceitos de Buyer Persona, ICP e Mapa de Empatia sao padrao
de mercado; a estruturacao das 30 Camadas e a copy desta skill sao obra original Digital
Master.
