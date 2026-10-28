"""SQLite checkpoint store for Agent Forge runs and attempts."""

import json
import sqlite3
from pathlib import Path
from agent_forge.core.state import AttemptRecord, HaltReason, PlanSpec, RunRecord, RunState


class StateStore:
    """Persists RunRecord and AttemptRecord state in SQLite for inspection and resumability."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    task_description TEXT NOT NULL,
                    repo_root TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    halt_reason TEXT NOT NULL,
                    plan_json TEXT,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    total_cost_usd REAL NOT NULL DEFAULT 0.0,
                    baseline_commit_sha TEXT,
                    reflection_notes_json TEXT NOT NULL DEFAULT '[]',
                    last_error_output TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    run_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    tree_hash TEXT NOT NULL,
                    commit_sha TEXT,
                    patch_summary TEXT NOT NULL,
                    test_passed INTEGER,
                    exit_code INTEGER,
                    error_fingerprint TEXT,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    PRIMARY KEY (run_id, attempt_number),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                """
            )

    def save_run(self, run: RunRecord) -> None:
        plan_json = run.plan.model_dump_json() if run.plan is not None else None
        reflection_json = json.dumps(run.reflection_notes, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, task_description, repo_root, branch_name, state,
                    halt_reason, plan_json, total_tokens, total_cost_usd,
                    baseline_commit_sha, reflection_notes_json, last_error_output
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    task_description = excluded.task_description,
                    repo_root = excluded.repo_root,
                    branch_name = excluded.branch_name,
                    state = excluded.state,
                    halt_reason = excluded.halt_reason,
                    plan_json = excluded.plan_json,
                    total_tokens = excluded.total_tokens,
                    total_cost_usd = excluded.total_cost_usd,
                    baseline_commit_sha = excluded.baseline_commit_sha,
                    reflection_notes_json = excluded.reflection_notes_json,
                    last_error_output = excluded.last_error_output
                """,
                (
                    run.run_id,
                    run.task_description,
                    run.repo_root,
                    run.branch_name,
                    run.state.value,
                    run.halt_reason.value,
                    plan_json,
                    run.total_tokens,
                    run.total_cost_usd,
                    run.baseline_commit_sha,
                    reflection_json,
                    run.last_error_output,
                ),
            )
            for attempt in run.attempts:
                self._upsert_attempt(conn, run.run_id, attempt)

    def _upsert_attempt(
        self, conn: sqlite3.Connection, run_id: str, attempt: AttemptRecord
    ) -> None:
        test_passed_int = (
            None if attempt.test_passed is None else (1 if attempt.test_passed else 0)
        )
        conn.execute(
            """
            INSERT INTO attempts (
                run_id, attempt_number, phase, tree_hash, commit_sha,
                patch_summary, test_passed, exit_code, error_fingerprint,
                prompt_tokens, completion_tokens, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, attempt_number) DO UPDATE SET
                phase = excluded.phase,
                tree_hash = excluded.tree_hash,
                commit_sha = excluded.commit_sha,
                patch_summary = excluded.patch_summary,
                test_passed = excluded.test_passed,
                exit_code = excluded.exit_code,
                error_fingerprint = excluded.error_fingerprint,
                prompt_tokens = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                cost_usd = excluded.cost_usd
            """,
            (
                run_id,
                attempt.attempt_number,
                attempt.phase.value,
                attempt.tree_hash,
                attempt.commit_sha,
                attempt.patch_summary,
                test_passed_int,
                attempt.exit_code,
                attempt.error_fingerprint,
                attempt.prompt_tokens,
                attempt.completion_tokens,
                attempt.cost_usd,
            ),
        )

    def add_attempt(self, run_id: str, attempt: AttemptRecord) -> None:
        with self._connect() as conn:
            self._upsert_attempt(conn, run_id, attempt)

    def _row_to_run(self, conn: sqlite3.Connection, row: sqlite3.Row) -> RunRecord:
        attempt_rows = conn.execute(
            "SELECT * FROM attempts WHERE run_id = ? ORDER BY attempt_number ASC",
            (row["run_id"],),
        ).fetchall()
        attempts = [
            AttemptRecord(
                attempt_number=ar["attempt_number"],
                phase=RunState(ar["phase"]),
                tree_hash=ar["tree_hash"],
                commit_sha=ar["commit_sha"],
                patch_summary=ar["patch_summary"],
                test_passed=(
                    None if ar["test_passed"] is None else bool(ar["test_passed"])
                ),
                exit_code=ar["exit_code"],
                error_fingerprint=ar["error_fingerprint"],
                prompt_tokens=ar["prompt_tokens"],
                completion_tokens=ar["completion_tokens"],
                cost_usd=ar["cost_usd"],
            )
            for ar in attempt_rows
        ]
        plan = (
            PlanSpec.model_validate_json(row["plan_json"])
            if row["plan_json"] is not None
            else None
        )
        return RunRecord(
            run_id=row["run_id"],
            task_description=row["task_description"],
            repo_root=row["repo_root"],
            branch_name=row["branch_name"],
            state=RunState(row["state"]),
            halt_reason=HaltReason(row["halt_reason"]),
            plan=plan,
            total_tokens=row["total_tokens"],
            total_cost_usd=row["total_cost_usd"],
            baseline_commit_sha=row["baseline_commit_sha"],
            reflection_notes=json.loads(row["reflection_notes_json"] or "[]"),
            last_error_output=row["last_error_output"] or "",
            attempts=attempts,
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_run(conn, row)

    def list_runs(self) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY rowid ASC").fetchall()
            return [self._row_to_run(conn, r) for r in rows]
