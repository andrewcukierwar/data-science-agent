"""Unit tests for the high-level Python analysis execution service."""

from hashlib import sha256
from pathlib import Path

import pytest

from agents.analyst import validate_analyst_result
from orchestration.ledger import AnalysisLedger
from sandbox.executor import SandboxExecutionResult
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from tools.python import PythonExecutionService
from tools.workspace import WorkspaceManager


class RecordingLedger:
    """Minimal ledger double covering tool events and usage accounting."""

    def __init__(self) -> None:
        self.events = []
        self.python_executions = 0

    def append_tool_event(self, event) -> None:  # noqa: ANN001
        self.events.append(event)

    def increment_budget(self, **usage: int) -> None:
        self.python_executions += usage["python_executions"]


class FakeExecutor:
    """Deterministic executor double that inspects the persisted script first."""

    timeout_seconds = 30.0
    memory_limit = "512m"
    cpu_limit = 1.0
    pids_limit = 128

    def __init__(
        self,
        workspace,
        result: SandboxExecutionResult,
        generated_files: dict[str, str | bytes] | None = None,
    ) -> None:
        self.workspace = workspace
        self.result = result
        self.generated_files = generated_files or {}
        self.calls = []

    def execute(self, script_path: str, *, timeout_seconds: float | None = None):
        self.calls.append(
            {
                "path": script_path,
                "source": (self.workspace.root / script_path).read_text(
                    encoding="utf-8"
                ),
                "timeout_seconds": timeout_seconds,
            }
        )
        for relative_path, content in self.generated_files.items():
            path = self.workspace.root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        return self.result


def _workspace(tmp_path: Path):
    return WorkspaceManager(tmp_path / "workspaces").create_workspace("run-python")


def test_run_python_persists_script_before_execution_and_records_success(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    ledger = RecordingLedger()
    executor = FakeExecutor(
        workspace,
        SandboxExecutionResult(
            success=True,
            stdout="42\n",
            exit_code=0,
            duration_seconds=0.25,
        ),
    )
    service = PythonExecutionService(workspace, ledger, executor=executor)

    result = service.run_python(
        "print(42)\n",
        script_id="P001",
        timeout_seconds=5,
    )

    assert result.success is True
    assert result.script_path == "working/scripts/P001.py"
    assert result.stdout == "42\n"
    assert (workspace.root / result.script_path).read_text(encoding="utf-8") == (
        "print(42)\n"
    )
    assert executor.calls == [
        {
            "path": "working/scripts/P001.py",
            "source": "print(42)\n",
            "timeout_seconds": 5,
        }
    ]
    assert len(ledger.events) == 1
    assert ledger.events[0].tool_name == "run_python"
    assert ledger.events[0].status.value == "succeeded"
    assert ledger.events[0].artifact_refs == ["working/scripts/P001.py"]
    assert ledger.python_executions == 1


def test_run_python_records_failure_and_increments_usage(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    ledger = RecordingLedger()
    executor = FakeExecutor(
        workspace,
        SandboxExecutionResult(
            success=False,
            stderr="Traceback: bad analysis",
            exit_code=1,
            duration_seconds=0.5,
            error="Traceback: bad analysis",
        ),
    )
    service = PythonExecutionService(workspace, ledger, executor=executor)

    result = service.execute("raise RuntimeError('bad analysis')", script_id="P002")

    assert result.success is False
    assert result.error == "Traceback: bad analysis"
    assert result.exit_code == 1
    assert ledger.events[0].status.value == "failed"
    assert ledger.events[0].error == result.error
    assert ledger.python_executions == 1


def test_successful_python_run_returns_csv_as_executed_evidence(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    ledger = AnalysisLedger(workspace, objective="Test generated evidence.")
    executor = FakeExecutor(
        workspace,
        SandboxExecutionResult(success=True, exit_code=0, duration_seconds=0.1),
        generated_files={"working/results/summary.csv": "metric,value\ncac,1.2\n"},
    )
    service = PythonExecutionService(workspace, ledger, executor=executor)

    result = service.run_python("pass", script_id="P-CSV")

    assert result.generated_evidence[0].evidence_ref == "working/results/summary.csv"
    assert result.generated_evidence[0].change_type == "created"
    event = ledger.tool_events[0]
    assert "working/results/summary.csv" in event.artifact_refs
    assert event.output is not None
    assert event.output["generated_evidence_refs"] == ["working/results/summary.csv"]
    assert event.output["generated_evidence"][0]["size_bytes"] == len(
        "metric,value\ncac,1.2\n"
    )
    finding = Finding(
        id="F-CSV",
        statement="The generated CSV contains the measured CAC.",
        metric="cac",
        value=1.2,
        evidence_refs=[result.generated_evidence[0].evidence_ref],
        confidence=ConfidenceLevel.MEDIUM,
    )
    assert validate_analyst_result(
        SpecialistResult(objective="Use the generated CSV.", findings=[finding]),
        ledger,
    ).findings == [finding]


def test_successful_python_run_retains_chart_checksum_and_size(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    ledger = AnalysisLedger(workspace, objective="Test generated chart evidence.")
    chart_bytes = b"not-a-rendered-chart-but-a-generated-file"
    executor = FakeExecutor(
        workspace,
        SandboxExecutionResult(success=True, exit_code=0, duration_seconds=0.1),
        generated_files={"outputs/charts/diagnostic.png": chart_bytes},
    )
    service = PythonExecutionService(workspace, ledger, executor=executor)

    result = service.run_python("pass", script_id="P-CHART")

    evidence = result.generated_evidence[0]
    assert evidence.evidence_ref == "outputs/charts/diagnostic.png"
    assert evidence.sha256 == sha256(chart_bytes).hexdigest()
    assert evidence.size_bytes == len(chart_bytes)
    assert ledger.tool_events[0].artifact_refs[-1] == evidence.evidence_ref


def test_untouched_preexisting_file_is_not_executed_evidence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ledger = AnalysisLedger(workspace, objective="Reject unrelated evidence.")
    preexisting = workspace.working / "results" / "unrelated.csv"
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_text("metric,value\nother,99\n", encoding="utf-8")
    service = PythonExecutionService(
        workspace,
        ledger,
        executor=FakeExecutor(
            workspace,
            SandboxExecutionResult(success=True, exit_code=0, duration_seconds=0.1),
        ),
    )

    result = service.run_python("pass", script_id="P-NO-UNRELATED")

    assert result.generated_evidence == []
    assert "working/results/unrelated.csv" not in ledger.tool_events[0].artifact_refs
    unrelated_finding = Finding(
        id="F-UNRELATED",
        statement="The unrelated file is evidence.",
        metric="other",
        value=99,
        evidence_refs=["working/results/unrelated.csv"],
        confidence=ConfidenceLevel.LOW,
    )
    with pytest.raises(ValueError, match="F-UNRELATED"):
        validate_analyst_result(
            SpecialistResult(
                objective="Reject unrelated evidence.",
                findings=[unrelated_finding],
            ),
            ledger,
        )


def test_run_python_persists_events_and_budget_through_analysis_ledger(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    ledger = AnalysisLedger(workspace, objective="Run a Python analysis.")
    executor = FakeExecutor(
        workspace,
        SandboxExecutionResult(success=True, exit_code=0, duration_seconds=0.1),
    )
    service = PythonExecutionService(workspace, ledger, executor=executor)

    service.run_python("print('ok')", script_id="P003")
    reloaded = AnalysisLedger(ledger.state_path)

    assert len(reloaded.tool_events) == 1
    assert reloaded.tool_events[0].artifact_refs == ["working/scripts/P003.py"]
    assert reloaded.budget.python_executions == 1


def test_run_python_rejects_empty_source_unsafe_ids_and_duplicates(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    service = PythonExecutionService(
        workspace,
        executor=FakeExecutor(
            workspace,
            SandboxExecutionResult(success=True, exit_code=0, duration_seconds=0),
        ),
    )

    with pytest.raises(ValueError):
        service.run_python(" ", script_id="P004")
    with pytest.raises(ValueError):
        service.run_python("pass", script_id="../escape")

    service.run_python("pass", script_id="P004")
    with pytest.raises(FileExistsError):
        service.run_python("pass", script_id="P004")
