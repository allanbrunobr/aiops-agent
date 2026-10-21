# Slack Workspace de Teste — Setup

## 1. Criar Workspace Gratuito

1. Acesse https://slack.com/get-started#/createnew
2. Use seu email pessoal (nao o corporativo)
3. Nome sugerido: `AIOps Lab` ou `aiops-test`
4. Crie o canal `#aiops-alerts`

## 2. Criar Slack App

1. Acesse https://api.slack.com/apps
2. Clique **Create New App** → **From scratch**
3. Nome: `AIOps Agent Bot`
4. Workspace: selecione o workspace criado acima

## 3. Configurar Bot Token

1. No painel da app, va em **OAuth & Permissions**
2. Em **Bot Token Scopes**, adicione:
   - `chat:write` — postar mensagens
   - `chat:write.public` — postar em canais publicos sem ser membro
3. Clique **Install to Workspace** no topo da pagina
4. Autorize
5. Copie o **Bot User OAuth Token** (comeca com `xoxb-`)

## 4. Configurar no AIOps Agent

Edite `.env.local` na raiz do projeto:

```
SLACK_BOT_TOKEN=xoxb-seu-token-aqui
SLACK_CHANNEL=#aiops-alerts
```

## 5. Testar

```bash
# Teste rapido via curl
curl -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer xoxb-seu-token-aqui" \
  -H "Content-Type: application/json" \
  -d '{"channel": "#aiops-alerts", "text": "AIOps Agent conectado!"}'
```

## 6. Fluxo Completo de Teste

```
Metrics Exporter (Docker)
    ↓ scrape cada 15s
Prometheus (Docker)
    ↓ datasource
Grafana (Docker) — detecta anomalia
    ↓ webhook POST (Bearer token)
AIOps Agent (local :47890)
    ↓ normaliza → pipeline → diagnostico
Slack (cloud) — notificacao com Block Kit
```

## Alternativa: Dry-Run (sem Slack real)

Se quiser testar sem Slack:

```python
# O SlackNotifier suporta dry_run=True
# Logs mostram os blocos Block Kit que seriam enviados
notifier = SlackNotifier(channel="#test", creds=creds, dry_run=True)
```

Os blocos sao logados em JSON via structlog — util para validar o formato.
