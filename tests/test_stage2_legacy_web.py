"""Tests for Stage 2 Legacy Web (Java/JSP) compilation, Playwright DOM checks, and visual diffing."""

import shutil
from pathlib import Path

from agent_forge.verifiers.legacy_web import (
    PlaywrightVisualVerifier,
    render_jsp_preview_html,
    run_java_tests,
)


def test_run_java_tests_compiles_and_executes_junit_tests_on_legacy_target(tmp_path: Path):
    src_target = Path(__file__).resolve().parent.parent / "targets" / "legacy-jsp-app"
    repo = tmp_path / "legacy-jsp-app"
    shutil.copytree(src_target, repo)

    # 1. Existing BoardServiceTest should compile and pass via standalone javac/java runner
    res_pass = run_java_tests(repo)
    assert res_pass.passed is True, res_pass.raw_output
    assert res_pass.exit_code == 0

    # 2. Inject a failing assertion into BoardServiceTest and confirm it fails
    test_file = repo / "src/test/java/com/example/legacy/BoardServiceTest.java"
    content = test_file.read_text(encoding="utf-8")
    broken_content = content.replace("assertEquals(1,", "assertEquals(999,")
    test_file.write_text(broken_content, encoding="utf-8")

    res_fail = run_java_tests(repo)
    assert res_fail.passed is False
    assert res_fail.exit_code != 0
    assert "Expected: 999" in res_fail.raw_output or "AssertionError" in res_fail.raw_output


def test_playwright_visual_verifier_captures_baseline_after_and_diff_and_checks_dom(
    tmp_path: Path,
):
    src_target = Path(__file__).resolve().parent.parent / "targets" / "legacy-jsp-app"
    repo = tmp_path / "legacy-jsp-app"
    shutil.copytree(src_target, repo)

    artifacts_root = tmp_path / "artifacts"
    verifier = PlaywrightVisualVerifier(artifacts_dir=artifacts_root)

    # 1. Capture baseline before implementation changes
    baseline_path = verifier.capture_baseline(repo, "run-vis01")
    assert baseline_path is not None
    assert baseline_path.is_file()

    # 2. Modify boardList.jsp: add '조회수' column header cleanly
    jsp_file = repo / "src/main/webapp/WEB-INF/jsp/boardList.jsp"
    orig_jsp = jsp_file.read_text(encoding="utf-8")
    updated_jsp = orig_jsp.replace(
        "<th>작성자</th>",
        "<th>작성자</th>\n            <th>조회수</th>",
    )
    jsp_file.write_text(updated_jsp, encoding="utf-8")

    # Preview HTML should strip JSP directives and populate sample rows matching 4 columns
    preview_html = render_jsp_preview_html(jsp_file)
    assert "<%@" not in preview_html
    assert "<th>조회수</th>" in preview_html

    res_ok = verifier.verify(repo)
    assert res_ok.passed is True, res_ok.raw_output
    assert Path(res_ok.artifacts["baseline_screenshot"]).is_file()
    assert Path(res_ok.artifacts["after_screenshot"]).is_file()
    assert Path(res_ok.artifacts["diff_screenshot"]).is_file()
    assert float(res_ok.artifacts["diff_ratio"]) > 0.0
    assert res_ok.artifacts["dom_status"] == "PASS"

    # 3. Introduce a horizontal overflow layout regression (width: 3200px)
    overflow_jsp = updated_jsp.replace("width: 960px;", "width: 3200px;")
    jsp_file.write_text(overflow_jsp, encoding="utf-8")

    res_overflow = verifier.verify(repo)
    assert res_overflow.passed is False
    assert "overflow" in res_overflow.raw_output.lower()
    assert res_overflow.artifacts["dom_status"] == "FAIL"


def test_legacy_web_verifier_syncs_run_context_and_handles_java_timeout(
    monkeypatch, tmp_path: Path
):
    import subprocess
    import agent_forge.verifiers.legacy_web as lw

    src_target = Path(__file__).resolve().parent.parent / "targets" / "legacy-jsp-app"
    repo = tmp_path / "legacy-jsp-app"
    shutil.copytree(src_target, repo)

    # 1. Verify set_run_context synchronizes run_id and attempt_number (e.g. on resume or after patch failure)
    artifacts_root = tmp_path / "artifacts"
    verifier = PlaywrightVisualVerifier(artifacts_dir=artifacts_root)
    verifier.set_run_context("run-resumed-99", attempt_number=3)
    res = verifier.verify(repo)
    assert res.passed is True
    assert "run-resumed-99/attempt-3-after.png" in res.artifacts["after_screenshot"]

    # 2. Verify subprocess.TimeoutExpired in run_java_tests returns structured TIMEOUT result instead of raising
    def fake_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="java", timeout=1)

    monkeypatch.setattr(lw.subprocess, "run", fake_timeout)
    timeout_res = run_java_tests(repo, timeout_seconds=1)
    assert timeout_res.passed is False
    assert timeout_res.exit_code == 124
    assert timeout_res.error_fingerprint == "TIMEOUT"

