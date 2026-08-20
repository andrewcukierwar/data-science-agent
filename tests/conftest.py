"""Shared test fixtures and production-shaped deterministic builders."""

import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from schemas.audit import AuditObservation, AuditResult, AuditStatus, TableAudit
from schemas.run_state import ToolEvent, ToolEventStatus

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def docker_image():
    """Build the runtime image, skipping only when Docker is unavailable."""

    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable; skipping Docker integration tests")

    info = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
    )
    if info.returncode != 0:
        reason = info.stderr.strip() or "Docker daemon is unavailable"
        pytest.skip(f"Docker integration unavailable: {reason}")

    image = f"data-science-agent-test:{uuid.uuid4().hex[:12]}"
    build = subprocess.run(
        [
            "docker",
            "build",
            "--tag",
            image,
            "--file",
            str(ROOT / "Dockerfile"),
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        pytest.fail(
            f"Docker runtime image failed to build:\n{build.stdout}\n{build.stderr}"
        )

    yield image
    subprocess.run(
        ["docker", "image", "rm", "--force", image],
        capture_output=True,
        text=True,
        check=False,
    )


_AUDIT_STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def evidence_bearing_audit(
    ledger,  # noqa: ANN001
    *,
    status: AuditStatus = AuditStatus.COMPLETE,
    event_id: str = "tool-audit-inspect",
) -> AuditResult:
    """Build a production-shaped audit and the execution that establishes it.

    An empty ``AuditResult`` satisfies the provenance contract only because it
    claims nothing, so a lifecycle test built on one never exercises the
    audit-to-Lead handoff that the 2026-08-20 canary failed on. This records a
    successful tool event and returns an audit whose every material claim cites
    it, which is what a real Data Auditor produces.
    """

    if not any(event.id == event_id for event in ledger.tool_events):
        ledger.append_tool_event(
            ToolEvent(
                id=event_id,
                tool_name="inspect_relations",
                status=ToolEventStatus.SUCCEEDED,
                started_at=_AUDIT_STAMP,
                completed_at=_AUDIT_STAMP,
                arguments={"include_row_counts": True},
            )
        )
    return AuditResult(
        status=status,
        tables=[
            TableAudit(
                table_name="orders",
                row_count=2,
                evidence_refs=[event_id],
            )
        ],
        limitations=[
            AuditObservation(
                statement="Only the registered input relations were inspected.",
                evidence_refs=[event_id],
            )
        ],
        audited_at=_AUDIT_STAMP,
    )
