# Exemplo B — Fundo Verde / React (clone reagindo a vídeo baixado)

> **Exemplo ILUSTRATIVO.** A oferta abaixo (nome, data, preço, vagas) é fictícia, só pra demonstrar o formato. Use sempre a oferta REAL do negócio e a marca central em /opt/MAIA/brand/brand.json.

**Produto:** (exemplo) lançamento de um produto/oferta do negócio — online ao vivo, com lote promocional e vagas limitadas.
**Objetivo:** vender inscrição (CTA link na bio) + engajar (react gera comentário).
**Formato:** Fundo Verde (chroma key) mesclado com React — o clone do dono do negócio no cantinho reagindo a um vídeo viral que roda atrás.
**Dor / ângulo:** "gasta em tráfego e não vende".
**Vídeo fonte (atrás):** `[FONTE A DEFINIR]` — vídeo viral de alguém reclamando que "investiu pesado em anúncio e não vendeu nada" (achar no TikTok Studio aba Inspirações ou Viral Jump; ou print de uma manchete tipo "custo de anúncios sobe X% em 2026"). Baixar o original via print + Google Lens.

---

## ROTEIRO PRONTO PRA GRAVAR (vertical 9:16, ~50s)

**[GANCHO 0-3s]** (o vídeo de fundo já é gancho visual; o clone abre com gancho verbal)
Fala: "Olha esse vídeo aqui... esse cara fez EXATAMENTE o que 90% das pessoas fazem — e por isso quebrou."
(vídeo de fundo rodando: a pessoa reclamando do tráfego que não vendeu)
(texto de tela só no comecinho: "POR QUE O TRÁFEGO NÃO VENDE")

**[DESENVOLVIMENTO — reagindo ao vídeo]**
Fala: "Repara: ele colocou dinheiro no anúncio, teve clique, teve gente entrando... e venda zero. Sabe por quê? Porque ele jogou o tráfego num lugar que não foi feito pra vender. Anúncio bom levando pra estrutura ruim é dinheiro no ralo. E é isso que tá acontecendo com a maioria agora que o clique encareceu."
(reagir apontando pra tela nos momentos-chave do vídeo de fundo)

**[VIRADA]**
Fala: "E é exatamente isso que eu vou resolver no dia [DATA], ao vivo: montar do zero a estrutura de vendas com IA que pega esse mesmo tráfego e transforma em venda no automático. O lead chega quente e fecha sem você responder um por um."
(texto de tela: "[NOME DA OFERTA] — [DATA] AO VIVO")

**[CTA ÚNICO]**
Fala: "Lote promocional [PREÇO], só [N] vagas. Comenta a palavra-chave aqui embaixo que eu te explico, ou já garante pelo link na bio."
(texto de tela: "[PREÇO] • [N] VAGAS • LINK NA BIO")

---

## CAPTION
Reagindo ao erro que quebra quem investe em tráfego. No dia [DATA] eu monto a estrutura de vendas com IA ao vivo, do zero. Lote promocional [PREÇO], [N] vagas. Comenta a palavra-chave ou link na bio.
**Hashtags:** #tráfegopago #vendasonline #inteligenciaartificial

## 2-3 VARIAÇÕES DE GANCHO (só a fala/legenda do gancho — pra multiplicar)
1. "Esse vídeo viralizou e ninguém percebeu o erro de R$1.000 que tá nele. Eu percebi."
2. (textual) "Assiste até o fim que no segundo 30 eu mostro onde o dinheiro dele vazou."
3. "Todo mundo tá compartilhando esse vídeo. Eu vou mostrar o que ele REALMENTE ensina sobre vender."

## RECEITA DE PRODUÇÃO ESPECÍFICA (Fundo Verde / React)
1. **Achar o vídeo viral** (TikTok Studio aba "Inspirações" ou Viral Jump) sobre "investiu em tráfego e não vendeu".
2. **Pegar o link → Viral Jump** extrai o roteiro. No chat: *"Faça como se fosse um react, como se eu estivesse reagindo a esse vídeo, e depois conecte com o que eu vendo (o produto/oferta do negócio). Faça uma CTA no final."* Gerar variações de gancho até gostar.
3. **Baixar o vídeo original de fundo:** print do vídeo → **Google Lens / busca por imagem** → baixar o original (ou baixar pelo VJ/TikTok Studio).
4. **O dono do negócio grava o ÁUDIO do roteiro com a própria voz** (celular serve; errou, segue e conserta na edição). Mandar pro PC como documento (grupo "Rascunhos") pra vir o bruto; converter pra MP3 se preciso. NUNCA ElevenLabs.
5. **Gerar o clone com FUNDO VERDE:** HeyGen (avatar do próprio dono, via navegador logado) → carregar o áudio → Avatar 3 → remover fundo → avatar no cantinho → fundo VERDE → exportar 30fps. (Alternativa Viral Jump: subir um avatar já com fundo verde.)
6. **Editar no CapCut (gratuito):** arrastar o clone (fundo verde) → Remover fundo > **Chroma key** → conta-gotas no verde → **intensidade ~70%** → ajustar sombra. Cortar TODOS os silêncios/erros ("meter a faca": ~4min vira ~1min). Reordenar trechos pra virada cair no momento certo. Selecionar tudo → "Criar clipe composto" → clone na camada de cima. Trazer o **vídeo de fundo baixado** pra camada de baixo → **silenciar a faixa** dele → ajustar tamanho. **Acelerar 1.20x** (clone + fundo). Música baixinha que combine com a emoção (suspense). Exportar **Full HD, H.264, 30fps**.
7. **Esteira video-stack-2026-06-13:** aplicar LUT padrão Vibrante 45% no resultado composto (`ffmpeg -vf "lut3d=/opt/MAIA/workspace/video-stack-2026-06-13/luts/default.cube:interp=tetrahedral"`), legendas word-level, b-roll só se sobrar espaço (o vídeo de fundo já é o visual).
8. **Título-gancho** só no comecinho (parte do gancho) ao postar no Instagram.
9. **Aprovação do dono do negócio** antes de publicar (Reel permanece no feed).
