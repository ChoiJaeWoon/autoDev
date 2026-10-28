import sys
import pytest
from pathlib import Path
from agent_forge.verifiers.test_runner import SubprocessTestVerifier


def test_subprocess_verifier_rejects_unwhitelisted_command():
    with pytest.raises(ValueError, match="not in allowed test commands"):
        SubprocessTestVerifier(["rm", "-rf", "/"])


def test_subprocess_verifier_runs_pytest_and_handles_timeout(tmp_path: Path):
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    verifier = SubprocessTestVerifier([sys.executable, "-m", "pytest", "-q"], timeout_seconds=10)
    res = verifier.verify(tmp_path)
    assert res.passed is True
    assert res.exit_code == 0
    assert res.error_fingerprint is None

    # Hang test -> timeout handled gracefully with exit_code 124 and TIMEOUT fingerprint
    (tmp_path / "test_sample.py").write_text(
        "import time\ndef test_hang():\n    time.sleep(5)\n", encoding="utf-8"
    )
    fast_timeout_verifier = SubprocessTestVerifier(
        [sys.executable, "-m", "pytest", "-q"], timeout_seconds=1
    )
    timeout_res = fast_timeout_verifier.verify(tmp_path)
    assert timeout_res.passed is False
    assert timeout_res.exit_code == 124
    assert timeout_res.error_fingerprint == "TIMEOUT"
