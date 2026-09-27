import os
import subprocess

import pytest
from deploy_harness import ROOT

pytestmark = pytest.mark.unit

SCRIPT = ROOT / "deploy/vm/bootstrap.sh"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


def test_help_explains_the_options():
    result = run("--help")

    assert result.returncode == 0
    assert "--ref" in result.stderr and "--enable-timers" in result.stderr


def test_unknown_arguments_are_rejected_before_anything_runs():
    result = run("--nope")

    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_ref_without_a_value_is_rejected():
    result = run("--ref")

    assert result.returncode == 2
    assert "usage:" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="must run as a normal user")
def test_refuses_to_run_without_root():
    result = run("--ref", "main")

    assert result.returncode == 1
    assert "run as root" in result.stderr
