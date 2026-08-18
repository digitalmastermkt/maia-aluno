# Google Calendar OAuth — Maia Master

Integracao OAuth 2.0 dedicada (alem do MCP) para a Maia ter controle TOTAL sobre
a agenda do Chefe: criar, deletar, renomear calendarios, mudar timezone, etc.

## Arquivos

| Arquivo | Funcao |
|---|---|
| `/opt/MAIA/bot/calendar_auth.py` | Fluxo OAuth inicial (rodar 1x) |
| `/opt/MAIA/bot/calendar_api.py`  | Wrapper de alto nivel (Maia usa) |
| `/opt/MAIA/bot/.calendar_token.json` | refresh_token (chmod 600) |
| `/opt/MAIA/bot/.env` | client_id + client_secret |
| `/opt/MAIA/bot/logs/calendar.log` | Logs de cada operacao |

## Setup inicial (uma vez)

### 1. Criar projeto no Google Cloud Console

Acessar https://console.cloud.google.com (logado como
`seu-email@gmail.com`):

1. Topo da pagina, clicar no seletor de projetos -> `New Project`
2. Nome: `maia-master-calendar` (ou qualquer outro)
3. Aguardar ~30s ate criar e selecionar o projeto

### 2. Habilitar Google Calendar API

1. Menu lateral -> `APIs & Services` -> `Library`
2. Buscar "Google Calendar API" -> clicar -> `Enable`

### 3. OAuth consent screen

1. Menu lateral -> `APIs & Services` -> `OAuth consent screen`
2. User Type: `External` -> Create
3. App name: `Maia Master Calendar`
4. User support email: seu-email@gmail.com
5. Developer contact: seu-email@gmail.com
6. Avancar; em `Scopes` adicionar manualmente:
   `https://www.googleapis.com/auth/calendar`
7. Em `Test users` adicionar `seu-email@gmail.com`
8. Salvar (modo Testing OK por enquanto)

### 4. Criar credenciais OAuth (Desktop app)

1. Menu lateral -> `APIs & Services` -> `Credentials`
2. `+ Create credentials` -> `OAuth client ID`
3. Application type: **Desktop app**
4. Name: `maia-cli`
5. Create -> Google mostra Client ID e Client Secret -> copiar AMBOS

### 5. Salvar credenciais no servidor

Adicionar ao `/opt/MAIA/bot/.env`:

```
GOOGLE_CALENDAR_CLIENT_ID=SEU_CLIENT_ID.apps.googleusercontent.com
GOOGLE_CALENDAR_CLIENT_SECRET=SEU_CLIENT_SECRET
```

### 6. Rodar autorizacao inicial

```bash
/opt/MAIA/bot/venv/bin/python /opt/MAIA/bot/calendar_auth.py
```

O script vai imprimir uma URL longa. Abrir essa URL em qualquer navegador,
logar como `seu-email@gmail.com`, aceitar os escopos solicitados.
Google entrega um codigo de autorizacao -> colar no terminal -> ENTER.

Saida esperada:

```
OK — refresh token salvo em /opt/MAIA/bot/.calendar_token.json
OK — list_calendars() retornou N calendario(s):
  - ... (id=primary, role=owner)
  - CLINICA DE AGENDAMENTO (id=...)
  - Family (id=...)
  - Feriados no Brasil (id=...)
```

## Como a Maia usa (exemplos)

### Via Python (preferido, dentro de scripts)

```python
import sys
sys.path.insert(0, "/opt/MAIA/bot")
import calendar_api as cal

# listar
for c in cal.list_calendars():
    print(c["id"], c["summary"], c["accessRole"])

# deletar (irreversivel; exige confirm=True)
cal.delete_calendar("abcd123@group.calendar.google.com", confirm=True)

# remover da lista (sem deletar fisicamente — usar para feriados etc)
cal.unsubscribe_calendar("pt.brazilian#holiday@group.v.calendar.google.com")

# criar
cal.create_calendar("Meu Calendario", timezone="America/Sao_Paulo")

# renomear / mudar timezone
cal.update_calendar_settings("abcd123@...", summary="Novo Nome", timezone="America/Sao_Paulo")
```

### Via CLI (uteis para debug)

```bash
PY=/opt/MAIA/bot/venv/bin/python
API=/opt/MAIA/bot/calendar_api.py

$PY $API list
$PY $API get primary
$PY $API create "Novo Calendar" --tz America/Sao_Paulo
$PY $API update CAL_ID --summary "Renomeado"
$PY $API unsubscribe CAL_ID
$PY $API delete CAL_ID --confirm
```

## Rotacao / problemas

- **refresh_token NAO expira por tempo** se o app permanecer em modo Testing
  com o usuario como test_user, mas pode ser invalidado manualmente em
  https://myaccount.google.com/permissions (procurar "Maia Master Calendar")
- **access_token** expira em 1h e e renovado automaticamente pelo wrapper
  (a cada chamada, se necessario)
- **Se o token for invalidado**: rodar de novo `calendar_auth.py` e refazer o
  fluxo de autorizacao
- **Mudar de modo Testing -> Production**: nao precisa enquanto so o Chefe
  usar; em modo Testing vale 7 dias de inatividade — chamadas frequentes da
  Maia nao deixam expirar

## Escopo

`https://www.googleapis.com/auth/calendar` da acesso TOTAL: ler/criar/editar/
deletar calendarios e eventos, gerenciar ACL, mudar timezone. E o necessario
para deletar calendars (que o MCP atual nao permite).
