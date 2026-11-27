# AIOPS-99 lote 1 — Higiene mypy: auth (APENAS auth/)

Worker Claude: repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `claude-progress.txt`, `pyproject.toml` ([tool.mypy]), `scripts/ci-local.sh:79`, `.github/workflows/ci.yml:47`, `git log --oneline -5`.

## ESCOPO FECHADO: somente `auth/`. NÃO toque interfaces/, validation/, app/, nem qualquer outro pacote.
O closure completo (interfaces/auth/validation) é dividido em lotes; este é o lote 1 = auth. Over-scoping para outros pacotes = FALHA.

## Estado atual
Gate mypy = `domain application core pipeline safety data agents tools rag` (commit c92bdd0). O override `module="*" ignore_errors=true` suprime os transitivos não limpos. `auth/` tem **16 erros strict** em 2 arquivos. Verifique o estado REAL com:
```
# /tmp/strict-mypy.ini  (já existe; se não, crie)
[mypy]
python_version = 3.12
ignore_missing_imports = True
explicit_package_bases = True
ignore_errors = False
```
`.venv/bin/mypy --config-file /tmp/strict-mypy.ini auth`

## Os 16 erros + correção (SEM mudar comportamento)
1. **`auth/keys.py:63`** (9 erros union-attr) + **`:75/:76`** (6 erros union-attr `.n`/`.e`): em `public_key_to_jwk`, `key = load_pem_public_key(...)` é uma union de muitos tipos de chave; só `RSAPublicKey` tem `public_numbers()` retornando `RSAPublicNumbers` (com `.n`/`.e`). A função é RSA-específica (kty RSA, alg RS256). **Correção**: estreite com guard `isinstance`:
   ```python
   from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
   key = load_pem_public_key(public_key_pem.encode())
   if not isinstance(key, RSAPublicKey):
       raise ValueError("public_key_to_jwk espera chave pública RSA")
   pub_numbers = key.public_numbers()   # RSAPublicNumbers — .n/.e válidos
   ```
   **Remova** o `# type: ignore[attr-defined]` existente na linha do `public_numbers()`. O guard é behavior-preserving para PEM RSA válido (entrada sempre RSA neste fluxo); para não-RSA levanta ValueError em vez de AttributeError — ambos falham, sem regressão de caminho feliz.
2. **`auth/oidc_validator.py:121`** (1 erro arg-type): `pyjwt.decode(token, public_key, ...)` — `public_key` vem de `self._find_key(jwks, kid)` cujo retorno está tipado `object`. **Correção**: tipe o retorno de `_find_key` com o tipo aceito por pyjwt (ex.: `RSAPublicKey | EllipticCurvePublicKey | Ed25519PublicKey | Ed448PublicKey | str | bytes`, ou o alias que pyjwt exporta), OU faça `cast(...)` no ponto de uso. Leia `_find_key` e escolha a opção que NÃO muda comportamento. NÃO use `Any`/`type: ignore` genérico.

## Estender o gate (só depois de auth strict-clean)
- `pyproject.toml`: adicione `"auth", "auth.*"` à lista `ignore_errors = false`. **MANTENHA** `module="*" ignore_errors=true` (interfaces/validation/app ainda suprimidos).
- `scripts/ci-local.sh:79` e `.github/workflows/ci.yml:47`: comando → `mypy domain application core pipeline safety data agents tools rag auth`.
- Verifique `.venv/bin/mypy domain application core pipeline safety data agents tools rag auth` → **Success**.

## Regras INVIOLÁVEIS
- ZERO mudança de comportamento. Suíte unit deve continuar **2169 passed**, 0 failed.
- Sem `# type: ignore` genérico nem `Any` indiscriminado; `isinstance`/`cast`/Protocol ok.
- NÃO regen snapshots (se algum mudar, PARE e reporte).
- Sem deps novas. Nada gravar em `output/` (tmp_path). Confirme file:line antes de editar.
- SOMENTE auth/. interfaces/validation/app = lotes futuros.

## Verificação LOCAL M5 (antes do commit)
- `.venv/bin/mypy --config-file /tmp/strict-mypy.ini auth` → Success.
- `.venv/bin/mypy domain application core pipeline safety data agents tools rag auth` → Success.
- `.venv/bin/python -m pytest tests -m unit -q` → 2169 passed, 0 failed.
- `.venv/bin/python -m pytest tests -m snapshot -q` → 17 passed, snapshots INALTERADOS.
- `.venv/bin/ruff check .` → All checks passed.
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/auth-smoke; echo exit=$?` → 3.
- NÃO use `scripts/ci-local.sh unit` nem M1.

Ao terminar: commit incluindo `AIOPS-99` (ex: `chore(higiene): mypy limpo em auth + gate estendido (AIOPS-99 lote 1)`). Setar `feature_list.json[0].passes=true` SOMENTE com auth strict-clean + gate estendido verde completo. Reporte: erros corrigidos, override final, comando do gate, números.