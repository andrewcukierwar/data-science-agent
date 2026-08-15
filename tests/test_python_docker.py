"""Docker integration tests for real isolated Python execution."""

from pathlib import Path

from orchestration.ledger import AnalysisLedger
from sandbox.executor import DockerSandboxExecutor
from tools.python import PythonExecutionService
from tools.workspace import WorkspaceManager


def test_real_execution_keeps_inputs_and_docs_read_only_and_blocks_network(
    tmp_path: Path,
    docker_image: str,
) -> None:
    input_source = tmp_path / "input-source"
    docs_source = tmp_path / "docs-source"
    input_source.mkdir()
    docs_source.mkdir()
    (input_source / "sentinel.txt").write_text("immutable", encoding="utf-8")
    (docs_source / "definitions.md").write_text("definitions", encoding="utf-8")
    host_secret = tmp_path / "host-secret.txt"
    host_secret.write_text("host-only", encoding="utf-8")

    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-python-docker",
        inputs_source=input_source,
        docs_source=docs_source,
    )
    ledger = AnalysisLedger(workspace, objective="Test isolated Python execution.")
    executor = DockerSandboxExecutor(
        workspace,
        image=docker_image,
        timeout_seconds=20,
    )
    service = PythonExecutionService(workspace, ledger, executor=executor)
    source = f"""
from pathlib import Path
import socket


def write_probe(path):
    try:
        path.write_text("mutated", encoding="utf-8")
    except OSError as exc:
        return type(exc).__name__
    return "writable"


input_path = Path("/workspace/inputs/sentinel.txt")
docs_path = Path("/workspace/docs/definitions.md")
input_status = write_probe(input_path)
docs_status = write_probe(docs_path)
try:
    socket.create_connection(("1.1.1.1", 80), timeout=2)
except OSError:
    network_status = "blocked"
else:
    network_status = "available"

host_access = Path({str(host_secret)!r}).exists()
Path("/workspace/working/generated.txt").write_text("working", encoding="utf-8")
Path("/workspace/outputs/result.txt").write_text(
    f"{{input_status}}|{{docs_status}}|{{network_status}}|{{host_access}}",
    encoding="utf-8",
)
if input_status == "writable" or docs_status == "writable":
    raise SystemExit("read-only mount was writable")
if network_status != "blocked":
    raise SystemExit("network was available")
if host_access:
    raise SystemExit("host secret was visible")
"""

    result = service.run_python(source, script_id="P-INTEGRATION")

    assert result.success is True, result.error or result.stderr
    assert result.exit_code == 0
    assert result.timed_out is False
    assert (workspace.inputs / "sentinel.txt").read_text(encoding="utf-8") == (
        "immutable"
    )
    assert (workspace.docs / "definitions.md").read_text(encoding="utf-8") == (
        "definitions"
    )
    assert (workspace.working / "generated.txt").read_text(encoding="utf-8") == (
        "working"
    )
    probe_result = (workspace.outputs / "result.txt").read_text(encoding="utf-8")
    input_status, docs_status, network_status, host_access = probe_result.split("|")
    assert input_status in {"PermissionError", "OSError"}
    assert docs_status in {"PermissionError", "OSError"}
    assert network_status == "blocked"
    assert host_access == "False"
    assert ledger.budget.python_executions == 1
    assert ledger.tool_events[0].status.value == "succeeded"


def test_real_execution_captures_python_failure(
    tmp_path: Path,
    docker_image: str,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-python-failure"
    )
    ledger = AnalysisLedger(workspace, objective="Test Python failure capture.")
    service = PythonExecutionService(
        workspace,
        ledger,
        executor=DockerSandboxExecutor(workspace, image=docker_image),
    )

    result = service.run_python(
        "raise RuntimeError('expected failure')\n",
        script_id="P-FAILURE",
    )

    assert result.success is False
    assert result.exit_code != 0
    assert result.error is not None
    assert "expected failure" in result.error
    assert ledger.tool_events[0].status.value == "failed"
    assert ledger.budget.python_executions == 1
