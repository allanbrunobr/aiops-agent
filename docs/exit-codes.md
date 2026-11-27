# Exit codes e estado terminal honesto (B5, AIOPS-76)

Fonte de verdade: `domain/terminal_state.py::classify_terminal` (função pura).
Rótulos em `interfaces/cli/exit_codes.py`.

## Estado terminal

| Estado | success | Quando |
|--------|---------|--------|
| `RESOLVED` | `true` | (a) TNR **medido** (`verification_mode` `real`/`measured`) com `success`; ou (b) ação read-only auto-aprovada pela política (`safety_verdict=APPROVED`: `require_human_approval=false` + LOW + allowlist); ou não há ação proposta |
| `AWAITING_APPROVAL` | `false` | Ação proposta pendente sem verificação medida (NOT_VERIFIED, TNR simulado) ou veredito `NEEDS_REVIEW` |
| `FAILED` | `false` | TNR `regression`, `rollback_complete` ou `error` |

Nunca há `RESOLVED` + `success=true` com TNR não-verificado e ação pendente.
TNR simulado nunca executa infra real (SECURITY.md) e não conta como medição.

## Matriz TNR × exit code (com ação proposta)

| `TNRResult` | modo | veredito de segurança | estado | exit |
|-------------|------|-----------------------|--------|------|
| `success` | medido | não-`NEEDS_REVIEW` | RESOLVED | 0 |
| `success` | medido | `NEEDS_REVIEW` | AWAITING_APPROVAL | 3 |
| `success` | simulado | `APPROVED` (read-only) | RESOLVED | 0 |
| `success` | simulado | `NEEDS_REVIEW`/ausente | AWAITING_APPROVAL | 3 |
| `not_verified` | — | `APPROVED` (read-only) | RESOLVED | 0 |
| `not_verified` | — | demais | AWAITING_APPROVAL | 3 |
| `regression` | — | qualquer | FAILED | 2 |
| `rollback_complete` | — | qualquer | FAILED | 2 |
| `error` | — | qualquer | FAILED | 2 |

Exit `1`: argumentos inválidos ou erro não tratado / pipeline abortado (não TNR).
Precedência: **regressão/rollback/erro (2) > aprovação (3) > resolvido (0)**.

## Skill learning

`tnr_passed` (aprendizado de skill) só é `true` com TNR `success` **e** modo
medido. Sucesso simulado não infla o score como se fosse medido
(`blueprint/adapters/pipeline_nodes.py::learn_skills_step`).
