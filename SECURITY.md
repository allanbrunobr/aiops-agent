# Security

This repository is a local incident-response prototype. It does not ship production credentials. Lab values that are in git on purpose:

- Grafana admin password `admin` / `aiops123` in `infra/docker-compose.yaml`
- Webhook token `test-grafana-token-local` in `.env.example` and the Grafana contact point

Treat both as public. Do not reuse them anywhere else.

## What the process does

- The default CLI does not open a network port and does not execute the mitigation command.
- The demo server, the webhook server and the HTTP app (`python -m interfaces.http`, port 47893: health, A2A, OAuth server when a signing key is set, MCP over SSE only when `mcp.enabled`) bind to `127.0.0.1` unless you override the host.
- The lab Compose file publishes Prometheus, Grafana, the exporter and the Slack mock on `127.0.0.1` only.
- `POST /a2a/tasks` requires a Bearer JWT signed with `AIOPS_MCP__OAUTH__JWT_SECRET` (at least 32 bytes). Without that key the route rejects every task. The token's `iss` is validated against `oauth.issuer` (default `aiops-agent`); a token minted for a different issuer is rejected even with a valid signature.
- OAuth authorization (`POST /auth/authorize` then `POST /auth/token`) checks the client allow-list and PKCE S256 (`code_verifier` must match `code_challenge`). Confidential clients — those with a configured `secret_sha256` — must also send `client_secret` (`client_secret_post`), checked in constant time against the SHA-256 hash; a wrong secret gets no token. Hashes come from a JSON file outside the repository (`mcp.oauth.client_secrets_file`, env `AIOPS_MCP__OAUTH__CLIENT_SECRETS_FILE`, `{client_id: sha256 hex}`); a missing, malformed or group/world-readable file yields no hashes. A client without a configured hash is a public client (PKCE only). An empty allow-list allows nobody. There is no built-in HMAC secret.
- **Known limitation — public-client impersonation.** `POST /auth/authorize` does not authenticate public clients: it accepts any registered `client_id` plus a caller-chosen `code_challenge`. PKCE protects against interception of the authorization code, **not** against impersonation — anyone who knows a registered public `client_id` can complete their own PKCE flow and receive a JWT carrying that client's scopes. Confidential clients (with `secret_sha256`) are authenticated by `client_secret`. To bind every token to a verified client identity, configure a secret hash for each client or front the flow with an upstream identity provider; do not rely on `client_id` alone as an identity proof.
- MCP header auth accepts only client ids listed in `mcp.oauth.allowed_clients`. An empty scope list does not skip the scope check.
- The pre-execution gate blocks commands with shell metacharacters (`;`, `&`, `|`, `<`, `>`, newlines), so a read-only prefix cannot carry a chained command. A command is auto-approved only when its start matches the allow-list (read-only commands and reversible rollouts) and its risk score is low; anything else waits for human approval. Service names are interpolated into a mitigation command only when they are valid Kubernetes names. The program still does not run the command itself.

## Reporting

Open a private security report to the repository owner. Do not file a public issue for a vulnerability that is not yet fixed.
