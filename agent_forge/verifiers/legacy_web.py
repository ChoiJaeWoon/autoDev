"""Stage 2 Legacy Web (Java/JSP/HTML/CSS) compiler, preview renderer, and Playwright visual verifier."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from PIL import Image

from agent_forge.harness.loop_guard import extract_error_fingerprint
from agent_forge.verifiers.base import VerificationResult

JSP_DIRECTIVE_RE = re.compile(r"<%@.*?%>", re.DOTALL)
JSP_COMMENT_RE = re.compile(r"<%--.*?--%>", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.IGNORECASE | re.DOTALL)
TBODY_RE = re.compile(r"(<tbody[^>]*>)(.*?)(</tbody>)", re.IGNORECASE | re.DOTALL)
EL_EXPR_RE = re.compile(r"\$\{([^}]+)\}")

JUNIT_TEST_ANNOTATION_SHIM = """package org.junit.jupiter.api;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface Test {}
"""

JUNIT_ASSERTIONS_SHIM = """package org.junit.jupiter.api;
import java.util.Objects;

public class Assertions {
    @FunctionalInterface
    public interface Executable {
        void execute() throws Throwable;
    }

    public static void assertEquals(Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError("Expected: " + expected + " but was: " + actual);
        }
    }

    public static void assertEquals(int expected, int actual) {
        if (expected != actual) {
            throw new AssertionError("Expected: " + expected + " but was: " + actual);
        }
    }

    public static void assertEquals(long expected, long actual) {
        if (expected != actual) {
            throw new AssertionError("Expected: " + expected + " but was: " + actual);
        }
    }

    public static void assertEquals(double expected, double actual, double delta) {
        if (Math.abs(expected - actual) > delta) {
            throw new AssertionError("Expected: " + expected + " but was: " + actual);
        }
    }

    public static void assertTrue(boolean condition) {
        if (!condition) {
            throw new AssertionError("Expected condition to be true, but was false");
        }
    }

    public static void assertFalse(boolean condition) {
        if (condition) {
            throw new AssertionError("Expected condition to be false, but was true");
        }
    }

    public static void assertNotNull(Object actual) {
        if (actual == null) {
            throw new AssertionError("Expected non-null value, but was null");
        }
    }

    public static <T extends Throwable> T assertThrows(Class<T> expectedType, Executable executable) {
        try {
            executable.execute();
        } catch (Throwable actual) {
            if (expectedType.isInstance(actual)) {
                return expectedType.cast(actual);
            }
            throw new AssertionError("Expected exception " + expectedType.getName() + " but threw " + actual);
        }
        throw new AssertionError("Expected exception " + expectedType.getName() + " but nothing was thrown");
    }
}
"""

JUNIT_RUNNER_SHIM = """import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import org.junit.jupiter.api.Test;

public class ForgeJUnitRunner {
    public static void main(String[] args) throws Exception {
        int passed = 0;
        int failed = 0;
        for (String className : args) {
            Class<?> cls = Class.forName(className);
            for (Method m : cls.getDeclaredMethods()) {
                if (m.isAnnotationPresent(Test.class)) {
                    m.setAccessible(true);
                    Object instance = cls.getDeclaredConstructor().newInstance();
                    try {
                        m.invoke(instance);
                        passed++;
                    } catch (InvocationTargetException ite) {
                        failed++;
                        Throwable cause = ite.getCause() != null ? ite.getCause() : ite;
                        System.err.println("FAIL " + className + "." + m.getName() + ": " + cause);
                        cause.printStackTrace(System.err);
                    }
                }
            }
        }
        System.out.println("JUnit Summary: " + passed + " passed, " + failed + " failed.");
        if (failed > 0) {
            System.exit(1);
        }
    }
}
"""


def run_java_tests(repo_root: Path, timeout_seconds: int = 30) -> VerificationResult:
    """Compile and run Java unit tests using mvn (if installed) or standalone javac/java."""
    root = Path(repo_root).resolve()
    main_java_dir = root / "src" / "main" / "java"
    test_java_dir = root / "src" / "test" / "java"
    if not main_java_dir.exists() and not test_java_dir.exists():
        return VerificationResult(
            passed=True,
            exit_code=0,
            summary="No Java source directories found",
            raw_output="",
        )

    if shutil.which("mvn") and (root / "pom.xml").is_file():
        res = subprocess.run(
            ["mvn", "test", "-q"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        combined = f"{res.stdout}\n{res.stderr}".strip()
        passed = res.returncode == 0
        return VerificationResult(
            passed=passed,
            exit_code=res.returncode,
            summary="Maven test passed" if passed else "Maven test failed",
            raw_output=combined,
            error_fingerprint=None if passed else extract_error_fingerprint(combined),
        )

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        shim_pkg = tmp_dir / "shim" / "org" / "junit" / "jupiter" / "api"
        shim_pkg.mkdir(parents=True, exist_ok=True)
        test_ann_file = shim_pkg / "Test.java"
        assertions_file = shim_pkg / "Assertions.java"
        runner_file = tmp_dir / "shim" / "ForgeJUnitRunner.java"

        test_ann_file.write_text(JUNIT_TEST_ANNOTATION_SHIM, encoding="utf-8")
        assertions_file.write_text(JUNIT_ASSERTIONS_SHIM, encoding="utf-8")
        runner_file.write_text(JUNIT_RUNNER_SHIM, encoding="utf-8")

        classes_dir = tmp_dir / "classes"
        classes_dir.mkdir(parents=True, exist_ok=True)

        java_files: list[str] = [
            str(test_ann_file),
            str(assertions_file),
            str(runner_file),
        ]
        if main_java_dir.exists():
            java_files.extend(str(p) for p in sorted(main_java_dir.rglob("*.java")))

        test_class_names: list[str] = []
        if test_java_dir.exists():
            for p in sorted(test_java_dir.rglob("*.java")):
                java_files.append(str(p))
                rel = p.relative_to(test_java_dir).with_suffix("")
                test_class_names.append(".".join(rel.parts))

        compile_res = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", str(classes_dir), *java_files],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if compile_res.returncode != 0:
            err_out = f"{compile_res.stdout}\n{compile_res.stderr}".strip()
            return VerificationResult(
                passed=False,
                exit_code=compile_res.returncode,
                summary="Java compilation failed",
                raw_output=err_out,
                error_fingerprint=extract_error_fingerprint(err_out),
            )

        if not test_class_names:
            return VerificationResult(
                passed=True,
                exit_code=0,
                summary="Java compiled cleanly (no test classes)",
                raw_output="",
            )

        run_res = subprocess.run(
            ["java", "-cp", str(classes_dir), "ForgeJUnitRunner", *test_class_names],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        combined = f"{run_res.stdout}\n{run_res.stderr}".strip()
        passed = run_res.returncode == 0
        return VerificationResult(
            passed=passed,
            exit_code=run_res.returncode,
            summary="Java tests passed" if passed else "Java tests failed",
            raw_output=combined,
            error_fingerprint=None if passed else extract_error_fingerprint(combined),
        )


def _sample_cell_for_header(header_text: str, row_idx: int) -> str:
    h = header_text.strip().lower()
    samples_by_row = [
        {
            "번호": "1",
            "id": "1",
            "제목": "사내 보안 점검 안내",
            "title": "사내 보안 점검 안내",
            "작성자": "관리자",
            "author": "관리자",
            "조회수": "142",
            "views": "142",
            "viewcount": "142",
            "날짜": "2026-10-28",
            "작성일": "2026-10-28",
            "상태": "공지",
            "카테고리": "보안",
        },
        {
            "번호": "2",
            "id": "2",
            "제목": "하반기 시스템 정기 패치 공지",
            "title": "하반기 시스템 정기 패치 공지",
            "작성자": "인프라팀",
            "author": "인프라팀",
            "조회수": "89",
            "views": "89",
            "viewcount": "89",
            "날짜": "2026-10-29",
            "작성일": "2026-10-29",
            "상태": "일반",
            "카테고리": "시스템",
        },
    ]
    row_map = samples_by_row[row_idx % len(samples_by_row)]
    for key, val in row_map.items():
        if key in h:
            return val
    return f"샘플-{row_idx + 1}"


def render_jsp_preview_html(jsp_path: Path) -> str:
    """Convert a JSP template into standalone previewable HTML with populated sample data."""
    raw = jsp_path.read_text(encoding="utf-8", errors="ignore")
    cleaned = JSP_DIRECTIVE_RE.sub("", raw)
    cleaned = JSP_COMMENT_RE.sub("", cleaned)
    # Strip basic JSTL tags like <c:forEach ...> and </c:forEach>
    cleaned = re.sub(r"</?c:[^>]+>", "", cleaned)

    headers = [
        re.sub(r"<[^>]+>", "", m.group(1)).strip() for m in TH_RE.finditer(cleaned)
    ]

    def _replace_tbody(match: re.Match) -> str:
        open_tag, inner, close_tag = match.groups()
        inner_without_comments = HTML_COMMENT_RE.sub("", inner).strip()
        if "<tr" not in inner_without_comments.lower() and headers:
            rows_html: list[str] = []
            for r_idx in range(2):
                tds = "".join(
                    f"<td>{_sample_cell_for_header(h, r_idx)}</td>" for h in headers
                )
                rows_html.append(f"        <tr>{tds}</tr>")
            return f"{open_tag}\n" + "\n".join(rows_html) + f"\n        {close_tag}"
        return match.group(0)

    cleaned = TBODY_RE.sub(_replace_tbody, cleaned)

    def _replace_el(match: re.Match) -> str:
        expr = match.group(1).strip().lower()
        if "id" in expr:
            return "1"
        if "title" in expr:
            return "사내 보안 점검 안내"
        if "author" in expr:
            return "관리자"
        if "view" in expr or "count" in expr:
            return "142"
        return "샘플값"

    cleaned = EL_EXPR_RE.sub(_replace_el, cleaned)
    return cleaned.strip()


def compute_visual_diff(
    baseline_path: Path, after_path: Path, diff_path: Path
) -> float:
    """Compare baseline and after PNGs pixel-by-pixel and write a highlighted diff PNG."""
    img_base = Image.open(baseline_path).convert("RGB")
    img_after = Image.open(after_path).convert("RGB")

    width = max(img_base.width, img_after.width)
    height = max(img_base.height, img_after.height)

    if img_base.size != (width, height):
        padded = Image.new("RGB", (width, height), (255, 255, 255))
        padded.paste(img_base, (0, 0))
        img_base = padded

    if img_after.size != (width, height):
        padded = Image.new("RGB", (width, height), (255, 255, 255))
        padded.paste(img_after, (0, 0))
        img_after = padded

    base_pixels = img_base.load()
    after_pixels = img_after.load()
    diff_img = Image.new("RGB", (width, height))
    diff_pixels = diff_img.load()

    changed_count = 0
    total_pixels = width * height

    for y in range(height):
        for x in range(width):
            r1, g1, b1 = base_pixels[x, y]
            r2, g2, b2 = after_pixels[x, y]
            delta = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
            if delta > 24:
                changed_count += 1
                # Highlight changed pixels in high-contrast coral red
                diff_pixels[x, y] = (239, 68, 68)
            else:
                # Softened background copy of after image
                avg = int((r2 + g2 + b2) / 3)
                diff_pixels[x, y] = (
                    int((r2 + avg + 255) / 3),
                    int((g2 + avg + 255) / 3),
                    int((b2 + avg + 255) / 3),
                )

    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_img.save(diff_path, format="PNG")
    return round(changed_count / max(total_pixels, 1), 4)


class PlaywrightVisualVerifier:
    """Stage 2 Verifier combining Java compilation/tests with Playwright DOM & visual regression checks."""

    def __init__(
        self,
        artifacts_dir: Path = Path(".forge/artifacts"),
        viewport_width: int = 1280,
        viewport_height: int = 800,
    ):
        self.artifacts_dir = Path(artifacts_dir).resolve()
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self._active_run_id: str = "default"
        self._attempt_counter: int = 0

    def _find_target_jsp_or_html(self, repo_root: Path) -> Path | None:
        root = Path(repo_root).resolve()
        for pattern in ("*.jsp", "*.html"):
            matches = [
                p
                for p in sorted(root.rglob(pattern))
                if not any(part.startswith(".") or part == "target" for part in p.parts)
            ]
            if matches:
                return matches[0]
        return None

    def _render_screenshot_and_check_dom(
        self, html_content: str, screenshot_path: Path
    ) -> tuple[bool, list[str]]:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        dom_errors: list[str] = []

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    viewport={
                        "width": self.viewport_width,
                        "height": self.viewport_height,
                    }
                )
                page_errors: list[str] = []
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                page.set_content(html_content, wait_until="load")
                page.screenshot(path=str(screenshot_path), full_page=True)

                if page_errors:
                    dom_errors.append(
                        f"Uncaught page errors: {'; '.join(page_errors)}"
                    )

                layout_diag = page.evaluate(
                    """() => {
                        const doc = document.documentElement;
                        const overflowX = doc.scrollWidth > doc.clientWidth;
                        const thCount = document.querySelectorAll('table thead th').length;
                        const rows = Array.from(document.querySelectorAll('table tbody tr'));
                        let colMismatch = false;
                        for (const tr of rows) {
                            const tdCount = tr.querySelectorAll('td').length;
                            if (thCount > 0 && tdCount > 0 && tdCount !== thCount) {
                                colMismatch = true;
                            }
                        }
                        return {
                            scrollWidth: doc.scrollWidth,
                            clientWidth: doc.clientWidth,
                            overflowX,
                            thCount,
                            colMismatch,
                        };
                    }"""
                )
                if layout_diag.get("overflowX"):
                    dom_errors.append(
                        f"Horizontal layout overflow detected: scrollWidth ({layout_diag['scrollWidth']}px) "
                        f"exceeds viewport clientWidth ({layout_diag['clientWidth']}px)"
                    )
                if layout_diag.get("colMismatch"):
                    dom_errors.append(
                        "Table structure mismatch: <tbody> row <td> count does not match <thead> <th> count"
                    )
            finally:
                browser.close()

        return len(dom_errors) == 0, dom_errors

    def capture_baseline(self, repo_root: Path, run_id: str) -> Path | None:
        """Capture pre-implementation baseline screenshot for visual comparison."""
        self._active_run_id = run_id
        self._attempt_counter = 0
        target_tpl = self._find_target_jsp_or_html(repo_root)
        if target_tpl is None:
            return None

        html_content = render_jsp_preview_html(target_tpl)
        baseline_path = self.artifacts_dir / run_id / "baseline.png"
        self._render_screenshot_and_check_dom(html_content, baseline_path)
        return baseline_path

    def verify(self, repo_root: Path) -> VerificationResult:
        """Run Java tests (if present) + Playwright browser rendering, DOM checks, and visual diff."""
        root = Path(repo_root).resolve()
        self._attempt_counter += 1
        run_dir = self.artifacts_dir / self._active_run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # 1. Run Java compilation and unit tests first
        java_res = run_java_tests(root)
        if not java_res.passed:
            return java_res

        # 2. Find JSP/HTML template to render in browser
        target_tpl = self._find_target_jsp_or_html(root)
        if target_tpl is None:
            return java_res

        html_content = render_jsp_preview_html(target_tpl)
        baseline_path = run_dir / "baseline.png"
        after_path = run_dir / f"attempt-{self._attempt_counter}-after.png"
        diff_path = run_dir / f"attempt-{self._attempt_counter}-diff.png"

        if not baseline_path.is_file():
            self._render_screenshot_and_check_dom(html_content, baseline_path)

        dom_ok, dom_errors = self._render_screenshot_and_check_dom(
            html_content, after_path
        )
        diff_ratio = compute_visual_diff(baseline_path, after_path, diff_path)

        artifacts = {
            "template_path": target_tpl.relative_to(root).as_posix(),
            "baseline_screenshot": str(baseline_path),
            "after_screenshot": str(after_path),
            "diff_screenshot": str(diff_path),
            "diff_ratio": f"{diff_ratio:.4f}",
            "dom_status": "PASS" if dom_ok else "FAIL",
        }

        if not dom_ok:
            err_msg = "Browser DOM/Layout verification failed:\n" + "\n".join(
                f"- {e}" for e in dom_errors
            )
            return VerificationResult(
                passed=False,
                exit_code=1,
                summary="Playwright DOM/Layout check failed",
                raw_output=err_msg,
                error_fingerprint=extract_error_fingerprint(err_msg),
                artifacts=artifacts,
            )

        summary = (
            f"Java tests & Playwright visual verification passed "
            f"(visual change ratio: {diff_ratio * 100:.2f}%)"
        )
        raw_out = "\n".join(
            filter(
                None,
                [
                    java_res.raw_output,
                    summary,
                ],
            )
        )
        return VerificationResult(
            passed=True,
            exit_code=0,
            summary=summary,
            raw_output=raw_out,
            error_fingerprint=None,
            artifacts=artifacts,
        )
