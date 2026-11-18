# Security

This repository is a local incident-response prototype. It does not ship production credentials. Lab values that are in git on purpose:

- Grafana admin password `admin` / `aiops123` in `infra/docker-compose.yaml`
- Webhook token `test-grafana-token-local` in `.env.example` and the Grafana contact point

Treat both as public. Do not reuse them anywhere else.

## What the process does

- The default CLI does not open a network port and does not execute the mitigation command.
- The demo server and the webhook server bind to `127.0.0.1` unless you override the host.
- The lab Compose file publishes Prometheus, Grafana, the exporter and the Slack mock on `127.0.0.1` only.
- `POST /a2a/tasks` requires a Bearer JWT signed with `AIOPS_MCP__OAUTH__JWT_SECRET` (at least 32 bytes). Without that key the route rejects every task. The token's `iss` is validated against `oauth.issuer` (default `aiops-agent`); a token minted for a different issuer is rejected even with a valid signature.
- OAuth registration checks the client allow-list and PKCE S256 (`code_verifier` must match `code_challenge`). An empty allow-list allows nobody. There is no built-in HMAC secret.
- **Known limitation — public-client impersonation.** `POST /auth/authorize` does not authenticate the client: it accepts any registered `client_id` plus a caller-chosen `code_challenge`. PKCE protects against interception of the authorization code, **not** against impersonation — anyone who knows a registered `client_id` can complete their own PKCE flow and receive a JWT carrying that client's scopes. The built-in authorization server advertises `token_endpoint_auth_methods_supported: ["none"]` (public clients). To bind a token to a verified client identity, front the flow with a confidential-client authenticator or an upstream identity provider; do not rely on `client_id` alone as an identity proof.
- MCP header auth accepts only client ids listed in `mcp.oauth.allowed_clients`. An empty scope list does not skip the scope check.
- The pre-execution gate denies commands that are not on its read-only list. `curl ... | sh` is blocked. The program still does not run the command itself.

## Reporting

Open a private security report to the repository owner. Do not file a public issue for a vulnerability that is not yet fixed.
