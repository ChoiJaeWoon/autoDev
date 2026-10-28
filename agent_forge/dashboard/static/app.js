(() => {
  "use strict";

  const FSM_ORDER = [
    "INIT",
    "PLAN",
    "WAITING_APPROVAL",
    "WRITE_TEST",
    "IMPLEMENT",
    "VERIFY",
    "REFLECT",
    "TERMINAL",
  ];

  let allRuns = [];
  let currentFilter = "ALL";
  let selectedRunId = null;
  let currentRunDetail = null;
  let autoRefreshTimer = null;
  let fastPollTimer = null;

  const el = (id) => document.getElementById(id);

  function createEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function stateBadgeClass(state) {
    if (state === "DONE") return "badge badge-done";
    if (state === "HALT") return "badge badge-halt";
    if (state === "WAITING_APPROVAL" || state === "REFLECT") return "badge badge-warn";
    return "badge badge-active";
  }

  function formatCost(costUsd) {
    const num = Number(costUsd || 0);
    return "$" + num.toFixed(4);
  }

  function shortSha(sha, len = 8) {
    if (!sha) return "-";
    return String(sha).slice(0, len);
  }

  function matchesFilter(run, filter) {
    if (filter === "ALL") return true;
    if (filter === "DONE") return run.state === "DONE";
    if (filter === "HALT") return run.state === "HALT";
    if (filter === "ACTIVE") return run.state !== "DONE" && run.state !== "HALT";
    return true;
  }

  function showFeedback(message, isError = false) {
    const fb = el("mission-feedback");
    fb.classList.remove("hidden");
    fb.textContent = message;
    if (isError) {
      fb.style.backgroundColor = "var(--status-fail-bg)";
      fb.style.color = "var(--status-fail)";
      fb.style.borderColor = "var(--status-fail-border)";
    } else {
      fb.style.backgroundColor = "var(--accent-blue-bg)";
      fb.style.color = "var(--accent-blue)";
      fb.style.borderColor = "var(--accent-blue-border)";
    }
  }

  function ensureFastPolling(shouldPoll) {
    if (shouldPoll && !fastPollTimer) {
      fastPollTimer = setInterval(fetchRuns, 1500);
    } else if (!shouldPoll && fastPollTimer) {
      clearInterval(fastPollTimer);
      fastPollTimer = null;
    }
  }

  function renderRunList() {
    const listContainer = el("run-list");
    listContainer.replaceChildren();

    const filtered = allRuns.filter((r) => matchesFilter(r, currentFilter));
    el("run-count-label").textContent = `${allRuns.length} Runs`;

    if (filtered.length === 0) {
      const emptyItem = createEl("div", "run-item");
      emptyItem.appendChild(createEl("div", "run-item-task", "조건에 해당하는 실행 기록이 없습니다."));
      listContainer.appendChild(emptyItem);
      return;
    }

    filtered.forEach((run) => {
      const item = createEl("div", "run-item" + (run.run_id === selectedRunId ? " selected" : ""));
      item.setAttribute("role", "listitem");

      const top = createEl("div", "run-item-top");
      top.appendChild(createEl("span", "run-item-id", run.run_id));
      top.appendChild(createEl("span", stateBadgeClass(run.state), run.state));
      item.appendChild(top);

      item.appendChild(createEl("div", "run-item-task", run.task_description));

      const meta = createEl("div", "run-item-meta");
      meta.appendChild(createEl("span", "", `Attempts: ${run.attempt_count}`));
      meta.appendChild(createEl("span", "", `${run.total_tokens} tok / ${formatCost(run.total_cost_usd)}`));
      item.appendChild(meta);

      item.addEventListener("click", () => {
        selectedRunId = run.run_id;
        renderRunList();
        fetchRunDetail(run.run_id);
      });

      listContainer.appendChild(item);
    });
  }

  function renderFsmPipeline(run) {
    const container = el("fsm-pipeline");
    container.replaceChildren();

    const visited = new Set(["INIT"]);
    if (run.plan) {
      visited.add("PLAN");
    }
    if (run.baseline_commit_sha) {
      visited.add("PLAN");
      visited.add("WRITE_TEST");
    }
    if (Array.isArray(run.attempts) && run.attempts.length > 0) {
      visited.add("PLAN");
      visited.add("WRITE_TEST");
      visited.add("IMPLEMENT");
      visited.add("VERIFY");
    }
    if (Array.isArray(run.reflection_notes) && run.reflection_notes.length > 0) {
      visited.add("REFLECT");
    }
    visited.add(run.state);

    FSM_ORDER.forEach((stepKey, idx) => {
      const isTerminalSlot = stepKey === "TERMINAL";
      const label = isTerminalSlot
        ? (run.state === "HALT" ? "HALT" : "DONE")
        : stepKey;

      let stepClass = "fsm-step";
      if (isTerminalSlot) {
        if (run.state === "DONE") stepClass += " visited current-done";
        else if (run.state === "HALT") stepClass += " visited current-halt";
      } else {
        if (visited.has(stepKey)) stepClass += " visited";
        if (run.state === stepKey) stepClass += " current-active";
      }

      const stepEl = createEl("div", stepClass);
      stepEl.appendChild(createEl("span", "fsm-step-index", `0${idx + 1}`));
      stepEl.appendChild(createEl("span", "fsm-step-name", label));
      container.appendChild(stepEl);
    });
  }

  function renderPlanScope(run) {
    const subtasksList = el("plan-subtasks");
    const filesList = el("plan-files");
    subtasksList.replaceChildren();
    filesList.replaceChildren();

    if (!run.plan) {
      subtasksList.appendChild(createEl("li", "", "계획 단계(PLAN)가 아직 완료되지 않았거나 기록이 없습니다."));
      return;
    }

    (run.plan.subtasks || []).forEach((taskText) => {
      subtasksList.appendChild(createEl("li", "", taskText));
    });

    (run.plan.target_source_files || []).forEach((srcPath) => {
      const li = createEl("li", "file-item");
      li.appendChild(createEl("code", "file-path", srcPath));
      li.appendChild(createEl("span", "perm-tag perm-rw", "READ-WRITE"));
      filesList.appendChild(li);
    });

    (run.plan.target_test_files || []).forEach((testPath) => {
      const li = createEl("li", "file-item");
      li.appendChild(createEl("code", "file-path", testPath));
      li.appendChild(createEl("span", "perm-tag perm-locked", "LOCKED IN IMPLEMENT"));
      filesList.appendChild(li);
    });
  }

  function renderUnifiedDiff(titleText, rawDiff) {
    el("diff-viewer-title").textContent = titleText;
    const viewer = el("diff-viewer");
    viewer.replaceChildren();

    if (!rawDiff || !rawDiff.trim()) {
      viewer.appendChild(createEl("span", "diff-line diff-meta", "선택한 단계에 기록된 Git 커밋 패치가 없습니다."));
      return;
    }

    rawDiff.split("\n").forEach((line) => {
      let cls = "diff-line";
      if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff --git") || line.startsWith("index ")) {
        cls += " diff-meta";
      } else if (line.startsWith("@@")) {
        cls += " diff-hunk";
      } else if (line.startsWith("+")) {
        cls += " diff-add";
      } else if (line.startsWith("-")) {
        cls += " diff-del";
      }
      viewer.appendChild(createEl("span", cls, line));
    });
  }

  function renderVisualRegression(runId, attempt) {
    const panel = el("visual-regression-panel");
    if (!attempt || !attempt.artifacts || !attempt.artifacts.after_screenshot) {
      panel.classList.add("hidden");
      return;
    }

    panel.classList.remove("hidden");
    const domStatus = attempt.artifacts.dom_status || "PASS";
    const domBadge = el("vis-dom-badge");
    domBadge.className = domStatus === "PASS" ? "badge badge-pass" : "badge badge-fail";
    domBadge.textContent = `DOM: ${domStatus}`;

    const ratioRaw = Number(attempt.artifacts.diff_ratio || 0);
    const ratioBadge = el("vis-diff-ratio-badge");
    ratioBadge.textContent = `DIFF: ${(ratioRaw * 100).toFixed(2)}%`;

    const cacheBust = `?t=${Date.now()}`;
    el("vis-img-baseline").src = `/api/runs/${encodeURIComponent(runId)}/artifacts/${attempt.attempt_number}/baseline${cacheBust}`;
    el("vis-img-after").src = `/api/runs/${encodeURIComponent(runId)}/artifacts/${attempt.attempt_number}/after${cacheBust}`;
    el("vis-img-diff").src = `/api/runs/${encodeURIComponent(runId)}/artifacts/${attempt.attempt_number}/diff${cacheBust}`;
  }

  function renderAttemptsAndDiffs(run) {
    const tbody = el("attempts-tbody");
    const btnGroup = el("diff-attempt-buttons");
    tbody.replaceChildren();
    btnGroup.replaceChildren();

    const attempts = run.attempts || [];
    const attemptDiffs = run.attempt_diffs || {};

    const baselineBtn = el("diff-btn-baseline");
    baselineBtn.onclick = () => {
      Array.from(tbody.querySelectorAll(".attempt-row")).forEach((r) => r.classList.remove("selected"));
      renderUnifiedDiff(
        `Commit Patch Inspector — Test Lock (${shortSha(run.baseline_commit_sha)})`,
        run.baseline_diff || ""
      );
    };

    const topBaselineBtn = el("view-baseline-diff-btn");
    if (run.baseline_commit_sha) {
      topBaselineBtn.classList.remove("hidden");
      topBaselineBtn.onclick = baselineBtn.onclick;
    } else {
      topBaselineBtn.classList.add("hidden");
    }

    if (attempts.length === 0) {
      el("visual-regression-panel").classList.add("hidden");
      const tr = createEl("tr");
      const td = createEl("td", "meta-label", "아직 기록된 구현 시도(Attempt)가 없습니다.");
      td.colSpan = 10;
      tr.appendChild(td);
      tbody.appendChild(tr);
      renderUnifiedDiff("Commit Patch Inspector — Test Lock", run.baseline_diff || "");
      return;
    }

    let lastRowEl = null;

    attempts.forEach((att) => {
      const tr = createEl("tr", "attempt-row");
      lastRowEl = tr;

      tr.appendChild(createEl("td", "mono", `#${att.attempt_number}`));
      tr.appendChild(createEl("td", "mono", att.phase));

      const gateTd = createEl("td");
      if (att.test_passed === true) {
        gateTd.appendChild(createEl("span", "badge badge-pass", "PASS"));
      } else if (att.test_passed === false) {
        gateTd.appendChild(createEl("span", "badge badge-fail", "FAIL"));
      } else {
        gateTd.appendChild(createEl("span", "badge", "SKIP"));
      }
      tr.appendChild(gateTd);

      tr.appendChild(createEl("td", "mono", att.exit_code !== null && att.exit_code !== undefined ? att.exit_code : "-"));
      tr.appendChild(createEl("td", "mono", shortSha(att.tree_hash, 8)));
      tr.appendChild(createEl("td", "mono", shortSha(att.commit_sha, 7)));
      tr.appendChild(createEl("td", "mono", att.prompt_tokens + att.completion_tokens));
      tr.appendChild(createEl("td", "mono", formatCost(att.cost_usd)));
      tr.appendChild(createEl("td", "", att.patch_summary || "-"));
      tr.appendChild(createEl("td", "mono", att.error_fingerprint || "-"));

      const selectAttemptDiff = () => {
        Array.from(tbody.querySelectorAll(".attempt-row")).forEach((r) => r.classList.remove("selected"));
        tr.classList.add("selected");
        const diffText = attemptDiffs[String(att.attempt_number)] || "";
        renderUnifiedDiff(
          `Commit Patch Inspector — Attempt #${att.attempt_number} (${shortSha(att.commit_sha, 7)})`,
          diffText
        );
        renderVisualRegression(run.run_id, att);
      };

      tr.addEventListener("click", selectAttemptDiff);
      tbody.appendChild(tr);

      const quickBtn = createEl("button", "btn-micro", `Attempt #${att.attempt_number}`);
      quickBtn.type = "button";
      quickBtn.addEventListener("click", selectAttemptDiff);
      btnGroup.appendChild(quickBtn);
    });

    const lastAtt = attempts[attempts.length - 1];
    if (lastRowEl) {
      lastRowEl.classList.add("selected");
    }
    const lastDiff = attemptDiffs[String(lastAtt.attempt_number)] || "";
    renderUnifiedDiff(
      `Commit Patch Inspector — Attempt #${lastAtt.attempt_number} (${shortSha(lastAtt.commit_sha, 7)})`,
      lastDiff
    );
    renderVisualRegression(run.run_id, lastAtt);
  }

  function renderDiagnostics(run) {
    const refList = el("reflection-list");
    refList.replaceChildren();
    const notes = run.reflection_notes || [];
    if (notes.length === 0) {
      refList.appendChild(createEl("li", "meta-label", "루프 가드 반성 노트(Reflection)가 발생하지 않았습니다."));
    } else {
      notes.forEach((note) => {
        refList.appendChild(createEl("li", "reflection-item", note));
      });
    }

    const errBox = el("error-output");
    errBox.textContent = run.last_error_output && run.last_error_output.trim()
      ? run.last_error_output
      : "오류 출력이 없습니다 (검증 게이트 통과).";
  }

  function renderRunDetail(run) {
    currentRunDetail = run;
    el("inspector-empty").classList.add("hidden");
    el("inspector-content").classList.remove("hidden");

    const approvalBanner = el("approval-banner");
    if (run.state === "WAITING_APPROVAL") {
      approvalBanner.classList.remove("hidden");
    } else {
      approvalBanner.classList.add("hidden");
    }

    el("detail-run-id").textContent = run.run_id;
    const stateBadge = el("detail-state-badge");
    stateBadge.className = stateBadgeClass(run.state);
    stateBadge.textContent = run.state;

    const haltBadge = el("detail-halt-badge");
    if (run.halt_reason && run.halt_reason !== "NONE") {
      haltBadge.classList.remove("hidden");
      haltBadge.textContent = run.halt_reason;
    } else {
      haltBadge.classList.add("hidden");
    }

    el("detail-repo-root").textContent = run.repo_root || "-";
    el("detail-task-desc").textContent = run.task_description || "-";
    el("detail-branch").textContent = run.branch_name || "-";
    el("detail-baseline-sha").textContent = shortSha(run.baseline_commit_sha, 10);
    el("detail-attempts-count").textContent = String((run.attempts || []).length);
    el("detail-tokens").textContent = String(run.total_tokens || 0);
    el("detail-cost").textContent = formatCost(run.total_cost_usd);

    renderFsmPipeline(run);
    renderPlanScope(run);
    renderAttemptsAndDiffs(run);
    renderDiagnostics(run);
  }

  async function fetchRunDetail(runId) {
    try {
      const resp = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
      if (!resp.ok) return;
      const detail = await resp.json();
      renderRunDetail(detail);
    } catch (_err) {
      // Keep existing UI state on transient fetch errors
    }
  }

  async function fetchRuns() {
    try {
      const resp = await fetch("/api/runs");
      if (!resp.ok) return;
      const data = await resp.json();
      allRuns = data.runs || [];
      if (data.db_path) {
        el("db-path-label").textContent = data.db_path;
      }

      const workerPill = el("worker-status-pill");
      const submitBtn = el("mission-submit-btn");
      if (data.worker_busy) {
        workerPill.className = "badge badge-active";
        workerPill.textContent = "RUNNING";
        submitBtn.disabled = true;
      } else {
        workerPill.className = "badge badge-idle";
        workerPill.textContent = "IDLE";
        submitBtn.disabled = false;
      }

      const anyActiveRun = Boolean(
        data.worker_busy ||
        allRuns.some((r) => r.state !== "DONE" && r.state !== "HALT" && r.state !== "WAITING_APPROVAL")
      );
      ensureFastPolling(anyActiveRun);

      if (!selectedRunId && allRuns.length > 0) {
        selectedRunId = allRuns[0].run_id;
      }
      renderRunList();
      if (selectedRunId) {
        await fetchRunDetail(selectedRunId);
      }
    } catch (_err) {
      // Ignore network hiccup
    }
  }

  async function submitNewMission(evt) {
    evt.preventDefault();
    const input = el("mission-input");
    const taskText = input.value.trim();
    if (!taskText) return;

    const repoRoot = el("mission-repo").value;
    const autoApprove = el("mission-approval").value === "auto";
    const submitBtn = el("mission-submit-btn");

    submitBtn.disabled = true;
    showFeedback("미션을 디스패치하는 중입니다...", false);

    try {
      const resp = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task: taskText,
          repo_root: repoRoot,
          auto_approve: autoApprove,
        }),
      });
      const payload = await resp.json();
      if (!resp.ok) {
        showFeedback(payload.error || "미션 실행 요청에 실패했습니다.", true);
        submitBtn.disabled = false;
        return;
      }

      input.value = "";
      selectedRunId = payload.run_id;
      showFeedback(`Run ${payload.run_id} 실행이 시작되었습니다.`, false);
      ensureFastPolling(true);
      await fetchRuns();
    } catch (_err) {
      showFeedback("서버 통신 중 오류가 발생했습니다.", true);
      submitBtn.disabled = false;
    }
  }

  async function sendApprovalDecision(approve) {
    if (!selectedRunId) return;
    try {
      const resp = await fetch(`/api/runs/${encodeURIComponent(selectedRunId)}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve }),
      });
      const payload = await resp.json();
      if (!resp.ok) {
        showFeedback(payload.error || "승인/반려 처리에 실패했습니다.", true);
        return;
      }
      showFeedback(
        approve
          ? `Run ${selectedRunId} 계획을 승인하여 실행을 재개합니다.`
          : `Run ${selectedRunId} 계획을 반려하여 중단했습니다.`,
        !approve
      );
      ensureFastPolling(true);
      await fetchRuns();
    } catch (_err) {
      showFeedback("서버 통신 중 오류가 발생했습니다.", true);
    }
  }

  function initControls() {
    el("mission-form").addEventListener("submit", submitNewMission);
    el("mission-input").addEventListener("keydown", (evt) => {
      if ((evt.metaKey || evt.ctrlKey) && evt.key === "Enter") {
        evt.preventDefault();
        el("mission-form").requestSubmit();
      }
    });

    el("approve-run-btn").addEventListener("click", () => sendApprovalDecision(true));
    el("reject-run-btn").addEventListener("click", () => sendApprovalDecision(false));

    el("refresh-btn").addEventListener("click", () => {
      fetchRuns();
    });

    const autoBtn = el("auto-refresh-btn");
    autoBtn.addEventListener("click", () => {
      if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
        autoBtn.classList.remove("active");
        autoBtn.textContent = "Auto-Refresh: Off";
      } else {
        autoRefreshTimer = setInterval(fetchRuns, 3000);
        autoBtn.classList.add("active");
        autoBtn.textContent = "Auto-Refresh: 3s";
      }
    });

    Array.from(document.querySelectorAll(".filter-btn")).forEach((btn) => {
      btn.addEventListener("click", () => {
        Array.from(document.querySelectorAll(".filter-btn")).forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentFilter = btn.getAttribute("data-filter") || "ALL";
        renderRunList();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initControls();
    fetchRuns();
  });
})();
