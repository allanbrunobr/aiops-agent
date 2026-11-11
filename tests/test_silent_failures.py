"""Falhas não podem mais sumir em `except Exception: pass`.

Regressão de F07/F5-02, ARQ-05/F5-01 e da parte de PII de 4B-03/SEC-08
(docs/analise-20261109.md). Três defeitos funcionais ficaram invisíveis
porque o erro era engolido sem log:

- a memória institucional nunca gravava: float("medium") levantava ValueError;
- o bloco "2c. Predictive Stage" chamava step_predict com assinatura errada;
- a busca MCP devolvia texto sem redação de PII quando o sanitizer falhava.

O primeiro teste é um portão determinístico: varre o código de produção por
AST e falha se aparecer um handler amplo cujo corpo é só `pass`.
"""
from __future__ import annotations

import ast
import inspect
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domain.confidence import confidence_to_score

ROOT = Path(__file__).resolve().parent.parent
# Fallback quando não há git (ex.: tarball): pacotes de produção do projeto.
PRODUCTION_PACKAGES = (
    "adapters", "agents", "app", "application", "auth", "blueprint", "config",
    "core", "data", "domain", "infra", "interfaces", "pipeline", "rag", "safety",
    "scripts", "tools", "validation",
)
NON_PRODUCTION_TOP = {"tests", "docs"}
# Exceções estreitas em que ignorar é o comportamento correto.
ALLOWED_SILENT = {"ImportError", "ModuleNotFoundError", "asyncio.CancelledError",
                  "CancelledError", "WebSocketDisconnect"}


def _production_files() -> list[Path]:
    """Só arquivos do projeto: versionados no git, ou os pacotes conhecidos.

    Não usa rglob na raiz: arquivos locais fora do controle de versão
    (evaluate*.py, _bmad/, venvs com outro nome) não são código do projeto.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
            text=True, timeout=10, check=True,
        ).stdout.split()
        files = [ROOT / f for f in out]
    except (OSError, subprocess.SubprocessError):
        files = [ROOT / "main.py"] + [
            p for pkg in PRODUCTION_PACKAGES for p in (ROOT / pkg).rglob("*.py")
        ]
    return sorted(
        p for p in files
        if p.is_file() and p.relative_to(ROOT).parts[0] not in NON_PRODUCTION_TOP
    )


def _silent_handlers() -> list[str]:
    found = []
    for path in _production_files():
        rel = path.relative_to(ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            only_pass = all(
                isinstance(s, ast.Pass)
                or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                for s in node.body
            )
            caught = ast.unparse(node.type) if node.type else "<bare>"
            if only_pass and caught not in ALLOWED_SILENT:
                found.append(f"{rel}:{node.lineno}: except {caught}: pass")
    return found


@pytest.mark.unit
class TestNoSilentBroadExcept:
    def test_production_code_has_no_silent_broad_except(self):
        offenders = _silent_handlers()
        assert offenders == [], (
            "Handler amplo que engole a exceção sem log:\n  " + "\n  ".join(offenders)
        )


@pytest.mark.unit
class TestConfidenceToScore:
    @pytest.mark.parametrize(("value", "expected"), [
        ("high", 0.85), ("medium", 0.5), ("low", 0.25),
        ("HIGH", 0.85), (" Medium ", 0.5),
        (0.7, 0.7), (1, 1.0), ("0.42", 0.42),
        (1.7, 1.0), (-0.2, 0.0),
        (None, 0.0), ("bogus", 0.0), (True, 0.0), ([], 0.0),
    ])
    def test_conversion(self, value, expected):
        assert confidence_to_score(value) == pytest.approx(expected)

    def test_custom_default(self):
        assert confidence_to_score("bogus", default=0.5) == 0.5


@pytest.mark.unit
class TestIncidentPersistence:
    def _result(self, confidence):
        return {"stages": {
            "diagnosis": {"root_cause": "pool exhausted", "confidence": confidence,
                          "evidence": ["pool=5"]},
            "detection": {"affected_services": ["payment-svc"]},
        }}

    @pytest.mark.parametrize("confidence", ["medium", "low", "high", 0.7])
    def test_textual_confidence_is_persisted(self, tmp_path, confidence):
        from main import step_persist_incident
        from rag.memory import IncidentMemoryDB

        db = IncidentMemoryDB(db_path=str(tmp_path / "mem.db"))
        report = MagicMock(log_templates=[], metric_anomalies=[], trace_anomalies=[])
        assert step_persist_incident(self._result(confidence), report, "s", 1.0, db=db) is True
        rows = db.find_similar_incidents(affected_services=["payment-svc"], tags=[], limit=5)
        assert len(rows) == 1
        assert rows[0].diagnosis_confidence == pytest.approx(confidence_to_score(confidence))

    def test_failure_is_logged_not_swallowed(self, tmp_path):
        from main import step_persist_incident

        db = MagicMock()
        db.save_incident.side_effect = RuntimeError("disk full")
        report = MagicMock(log_templates=[], metric_anomalies=[], trace_anomalies=[])
        with patch("main._persist_log") as log:
            assert step_persist_incident(self._result("medium"), report, "s", 1.0, db=db) is False
        assert log.warning.called


@pytest.mark.unit
class TestSuiteDoesNotTouchRepoMemoryDb:
    """A fixture autouse do conftest isola o default de todos os construtores."""

    def test_default_db_paths_point_outside_the_repo(self):
        from rag.memory import IncidentMemoryDB

        db = IncidentMemoryDB()
        assert "data/incident_memory.db" not in str(db.db_path).replace("\\", "/")
        for mod_name in ("rag.indexer", "rag.retriever_history"):
            mod = __import__(mod_name, fromlist=["_"])
            for obj in vars(mod).values():
                init = vars(obj).get("__init__") if isinstance(obj, type) else None
                defaults = getattr(init, "__defaults__", None) or ()
                assert "data/incident_memory.db" not in defaults, (mod_name, obj)


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_cli")
class TestNoDeadPredictCall:
    def test_step_predict_is_only_called_with_its_real_signature(self):
        import main

        signature = inspect.signature(main.step_predict)
        calls: list[str] = []

        def spy(*args, **kwargs):
            try:
                signature.bind(*args, **kwargs)
                calls.append("ok")
            except TypeError:
                calls.append("bad-signature")

        mock_result = {"success": True, "stages": {
            "detection": {"is_incident": True, "severity": "HIGH", "signals": []},
            "diagnosis": {"root_cause": "x", "evidence": [], "affected_services": ["svc"],
                          "confidence": "high", "timeline": []},
            "mitigation": {"pattern_matched": "x", "action": None},
            "verification": {"result": "success", "health_delta": 1.0, "message": "ok"},
        }, "timeline": []}
        incident = MagicMock()
        incident.fix_time.__sub__ = MagicMock(
            return_value=MagicMock(total_seconds=MagicMock(return_value=1)))
        with (
            patch("sys.argv", ["main.py", "--no-llm", "--no-rag"]),
            patch("main.load_dotenv"), patch("main.print_banner"),
            patch("main.step_generate_data", return_value=(incident, "s")),
            patch("main.step_sanitize", return_value=MagicMock(threat_detected=False, total_entries=1)),
            patch("main.step_pipeline", return_value=MagicMock()),
            patch("main.step_compression", return_value=(MagicMock(), "t", MagicMock(
                compression_ratio=0.5, critical_preserved=1, chunks_kept=1, chunks_removed=0))),
            patch("main.step_agents", return_value=mock_result),
            patch("main.step_predict", side_effect=spy),
            patch("main.step_safety_validation", return_value=None),
            patch("main.step_remediation_validation", return_value=None),
            patch("main.step_postmortem", return_value=(MagicMock(), None)),
            patch("main.step_persist_incident", return_value=True),
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
        ):
            main.main()

        assert calls == ["ok"]


@pytest.mark.unit
class TestMcpSearchRedactsWhenSanitizerFails:
    def test_text_is_redacted_not_returned_raw(self):
        from interfaces.mcp.tools import _sanitize_result

        class BrokenSanitizer:
            def sanitize_text(self, text):
                raise RuntimeError("boom")

        out = _sanitize_result(
            {"summary": "user alice@example.com leaked", "score": 0.9}, BrokenSanitizer())
        assert "alice@example.com" not in out["summary"]
        assert out["score"] == 0.9

    def test_missing_method_is_redacted_too(self):
        from interfaces.mcp.tools import _sanitize_result

        out = _sanitize_result({"diagnosis": "token=abc123"}, object())
        assert "abc123" not in out["diagnosis"]
