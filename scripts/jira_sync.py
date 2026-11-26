#!/usr/bin/env python3
"""Sincroniza feature_list.json (long-run) -> cards do board Jira AIOPS (board8).

Fonte da verdade continua sendo o repo; este script é um espelho que move os
cards conforme o estado das features. Idempotente: não move card que já está no
status alvo. Falha silenciosa (nunca deve quebrar o loop do long-run).

Uso:
  python3 scripts/jira_sync.py --feature N --status doing|done|planned
  python3 scripts/jira_sync.py --all          # reconcilia passes=true -> done

Mapeamento: cada feature pode ter um campo opcional "jira_key" (ex.: "AIOPS-41").
Sem jira_key, a feature é ignorada (sync é opt-in por feature).

Credenciais: ~/.config/jira-pessoal.env (JIRA_SITE/JIRA_EMAIL/JIRA_API_TOKEN).
O token nunca é impresso.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# status id no workflow Kanban do projeto AIOPS (esquema PF 10002)
STATUS_DONE = "10005"      # Concluído com evidência
STATUS_DOING = "3"         # Em andamento / Em execução
STATUS_PLANNED = "10004"   # Selected for Development / Próximas entregas
_TARGET = {"done": STATUS_DONE, "doing": STATUS_DOING, "planned": STATUS_PLANNED}


def _creds() -> tuple[str, str, str]:
    env = Path.home() / ".config" / "jira-pessoal.env"
    data: dict[str, str] = {}
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        data[key.strip()] = val.strip().strip('"').strip("'")
    site = data.get("JIRA_SITE") or data.get("JIRA_BASE_URL") or ""
    if site and not site.startswith("http"):
        site = "https://" + site
    return site.rstrip("/"), data["JIRA_EMAIL"], data["JIRA_API_TOKEN"]


try:
    _SITE, _EMAIL, _TOKEN = _creds()
    _AUTH = base64.b64encode(f"{_EMAIL}:{_TOKEN}".encode()).decode()
except Exception as exc:  # noqa: BLE001 - credenciais ausentes -> sync desligado
    print(f"[jira_sync] credenciais indisponíveis: {exc}", file=sys.stderr)
    sys.exit(0)


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_SITE + path, data=payload, method=method)
    req.add_header("Authorization", "Basic " + _AUTH)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:200]
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def _transition(key: str, target_status: str) -> bool:
    """Move o card para target_status se ainda não estiver lá. Idempotente."""
    st, issue = _req("GET", f"/rest/api/3/issue/{key}?fields=status")
    if st != 200 or not isinstance(issue, dict):
        return False
    current = str(issue.get("fields", {}).get("status", {}).get("id", ""))
    if current == target_status:
        return True  # já está no alvo
    st, trans = _req("GET", f"/rest/api/3/issue/{key}/transitions")
    if st != 200 or not isinstance(trans, dict):
        return False
    for t in trans.get("transitions", []):
        if str(t.get("to", {}).get("id", "")) == target_status:
            code, _ = _req("POST", f"/rest/api/3/issue/{key}/transitions",
                           {"transition": {"id": t["id"]}})
            return code in (200, 204)
    return False


def _features(cwd: Path) -> list[dict]:
    fl = cwd / "feature_list.json"
    if not fl.exists():
        return []
    try:
        data = json.loads(fl.read_text())
    except (OSError, ValueError):
        return []  # arquivo ilegível/JSON inválido -> sync desligado, nunca quebra o loop
    return data if isinstance(data, list) else data.get("features", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature", type=int, help="número da feature no feature_list.json")
    ap.add_argument("--status", choices=list(_TARGET), help="status alvo")
    ap.add_argument("--all", action="store_true", help="reconcilia passes=true -> done")
    ap.add_argument("--cwd", default=".", help="diretório do projeto")
    args = ap.parse_args()
    cwd = Path(args.cwd).resolve()
    features = _features(cwd)

    if args.all:
        moved = 0
        for f in features:
            key = f.get("jira_key")
            if key and f.get("passes"):
                if _transition(key, STATUS_DONE):
                    moved += 1
        print(f"[jira_sync] --all: {moved} cards reconciliados para done")
        return 0

    if args.feature is None or args.status is None:
        print("[jira_sync] uso: --feature N --status doing|done|planned | --all", file=sys.stderr)
        return 0
    feat = next((f for f in features if f.get("number") == args.feature), None)
    if not feat:
        return 0  # feature não encontrada -> no-op
    key = feat.get("jira_key")
    if not key:
        return 0  # sem mapeamento -> sync opt-in, ignora
    ok = _transition(key, _TARGET[args.status])
    print(f"[jira_sync] feature {args.feature} ({key}) -> {args.status}: {'ok' if ok else 'no-op/falha'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
