"""Loop detection via error fingerprinting and git tree hash oscillation."""

import hashlib
import re
from enum import Enum
from agent_forge.core.state import AttemptRecord


class LoopAction(str, Enum):
    """Decision returned by the loop guard after comparing current attempt to history."""

    CONTINUE = "CONTINUE"
    REFLECT = "REFLECT"
    HALT_OSCILLATION = "HALT_OSCILLATION"


HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
DURATION_RE = re.compile(r"\b\d+\.\d+s\b")
ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?")
TMP_PATH_RE = re.compile(r"(?:/private)?/tmp/[^\s:]+|/var/folders/[^\s:]+")
ERROR_LINE_KEYWORDS = ("FAILED", "ERROR", "Error", "Exception", "assert", "E   ")


def normalize_error_log(raw_output: str) -> str:
    """Strip volatile tokens (addresses, durations, timestamps, temp paths) from test failure logs."""
    cleaned_lines: list[str] = []
    for line in raw_output.splitlines():
        if not any(kw in line for kw in ERROR_LINE_KEYWORDS):
            continue
        norm = HEX_ADDR_RE.sub("0xADDR", line)
        norm = DURATION_RE.sub("0.00s", norm)
        norm = ISO_TS_RE.sub("TIMESTAMP", norm)
        norm = TMP_PATH_RE.sub("/tmp/NORM", norm)
        norm = " ".join(norm.split())
        if norm:
            cleaned_lines.append(norm)

    if not cleaned_lines:
        fallback = HEX_ADDR_RE.sub("0xADDR", raw_output)
        fallback = DURATION_RE.sub("0.00s", fallback)
        fallback = ISO_TS_RE.sub("TIMESTAMP", fallback)
        fallback = TMP_PATH_RE.sub("/tmp/NORM", fallback)
        cleaned_lines = [" ".join( fallback.split())]

    return "\n".join(cleaned_lines)


def extract_error_fingerprint(raw_output: str) -> str:
    """Compute a deterministic 16-char SHA-256 fingerprint of a failure output."""
    normalized = normalize_error_log(raw_output)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def evaluate_loop_guard(
    current_fingerprint: str | None,
    current_tree_hash: str,
    history: list[AttemptRecord],
    threshold: int = 2,
) -> LoopAction:
    """Check whether the current attempt oscillates to a prior tree hash or repeats the same error."""
    if any(
        item.tree_hash == current_tree_hash and item.tree_hash != ""
        for item in history
    ):
        return LoopAction.HALT_OSCILLATION

    if current_fingerprint is not None and threshold >= 2:
        needed_prior = threshold - 1
        if len(history) >= needed_prior:
            recent = history[-needed_prior:]
            if all(item.error_fingerprint == current_fingerprint for item in recent):
                return LoopAction.REFLECT

    return LoopAction.CONTINUE
