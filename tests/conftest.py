"""Shared test fixtures for deterministic Docker integration coverage."""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

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
