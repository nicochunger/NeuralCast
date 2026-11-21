"""Isolated, schema-constrained headless Antigravity CLI (agy) executions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
from time import monotonic
from typing import Any

from pydantic import BaseModel

EFFORT_MODELS: dict[str, str] = {
    "low": "gemini-4-neon-low",
    "medium": "gemini-4-neon-medium",
    "high": "gemini-4-neon-high",
    "xhigh": "gemini-4-neon-xhigh",
}
DEFAULT_EFFORT = "medium"
MODEL = EFFORT_MODELS[DEFAULT_EFFORT]
TIMEOUT_SECONDS = 900


def model_for(effort: str = DEFAULT_EFFORT) -> str:
    key = effort.strip().lower()
    if key not in EFFORT_MODELS:
        raise ValueError(f"Unsupported agy effort level: {effort}")
    return EFFORT_MODELS[key]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class EditorialStageError(RuntimeError):
    """Safe, classified failure suitable for operator-facing job diagnostics."""


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Build a strict JSON schema where every object property is required."""

    def strict(value: Any) -> Any:
        if isinstance(value, dict):
            result = {
                ("anyOf" if key == "oneOf" else key): strict(item)
                for key, item in value.items()
                if key not in {"default", "discriminator"}
            }
            if result.get("type") == "object":
                result["additionalProperties"] = False
                result["required"] = list(result.get("properties", {}))
            return result
        if isinstance(value, list):
            return [strict(item) for item in value]
        return value

    return strict(model.model_json_schema())


def response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep the provider grammar small; Pydantic enforces array counts locally."""

    def convert(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: convert(item)
                for key, item in value.items()
                if key not in {"minItems", "maxItems"}
            }
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    return convert(schema)


def failure_diagnostic(raw: str) -> tuple[str, str]:
    """Classify agy CLI failures without returning raw logs or credentials."""
    checks = (
        (
            "timeout",
            r"timed out|print.timeout|deadline exceeded|\"status\"\s*:\s*\"TIMEOUT\"",
            "Antigravity CLI timed out before finishing the stage.",
        ),
        (
            "model_access",
            r"model[^\n]{0,160}(?:not supported|not found|does not exist|unavailable)|model_not_found",
            "The agy CLI rejected the configured model. Check NC_AGY_EXECUTABLE and model access.",
        ),
        (
            "authentication",
            r"invalid_api_key|refresh.token|not logged in|authentication required|unauthorized|unauthenticated|401",
            "Antigravity CLI authentication failed. Check the agy login for the service user.",
        ),
        (
            "schema",
            r"invalid.json.schema|invalid schema|schema validation failed",
            "Antigravity CLI rejected the structured output schema.",
        ),
        (
            "context_limit",
            r"context.window|context.length|maximum context|input.too.large",
            "The editorial snapshot exceeded the model context limit.",
        ),
        (
            "rate_limit",
            r"rate.limit|insufficient.quota|usage.limit|quota.exceeded|resource_exhausted|429",
            "Antigravity CLI reported a rate or usage limit. Retry after the limit clears.",
        ),
        (
            "configuration",
            r"unknown flag|flag provided but not defined|read-only file system|installation_id|error loading",
            "Antigravity CLI failed due to configuration or environment permissions.",
        ),
        (
            "network",
            r"connection.refused|connection.reset|stream.disconnected|network.is.unreachable|tls|certificate",
            "Antigravity CLI could not reach its backend. Check service networking and retry.",
        ),
        (
            "sandbox",
            r"operation not permitted|sandbox.*(?:failed|denied)|permission denied",
            "Antigravity CLI could not initialize or use its execution sandbox.",
        ),
    )
    for category, pattern, message in checks:
        if re.search(pattern, raw, re.IGNORECASE):
            return category, message
    return (
        "unknown",
        "Antigravity CLI failed without a recognized safe diagnostic. Raw logs were discarded to protect credentials.",
    )


def _stage_error(
    attempt: Path,
    executable: str,
    category: str,
    message: str,
    exit_code: int | None,
) -> EditorialStageError:
    write_json(
        attempt / "failure.json",
        {
            "category": category,
            "message": message,
            "executable": executable,
            "exit_code": exit_code,
        },
    )
    status = f" exited with {exit_code}" if exit_code is not None else " failed"
    return EditorialStageError(f"Antigravity editorial stage{status}: {message}")


def _parse_envelope(stdout: str) -> dict[str, Any] | None:
    """Extract the JSON result object from agy --output-format json stdout."""
    text = stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _extract_usage(envelope: dict[str, Any]) -> dict[str, int] | None:
    raw_usage = envelope.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    usage = {
        key: value for key, value in raw_usage.items() if type(value) is int
    }
    return usage or None


def _render_voice_examples(items: list[dict[str, Any]]) -> str:
    lines = ["# Voice Calibration Examples", ""]
    for item in items:
        lines.append(f"## {item.get('id', 'example')} ({item.get('category', '')})")
        if item.get("status"):
            lines.append(f"Status: `{item['status']}`")
        lines.append("")
        if item.get("before"):
            lines.append("**Before (avoid):**")
            lines.append(f"> {item['before']}")
            lines.append("")
        if item.get("after"):
            lines.append("**After (target):**")
            lines.append(item["after"])
            lines.append("")
        if item.get("reason"):
            lines.append(f"**Why:** {item['reason']}")
        if item.get("check"):
            lines.append(f"**Check:** {item['check']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _build_workspace_files(tmpdir: Path, context: dict[str, Any]) -> list[str]:
    """Write context.json and any Markdown/TSV reference files into the isolated cwd."""
    files = ["context.json"]
    slim_context = dict(context)
    extra_files = slim_context.pop("workspace_files", None)
    if isinstance(extra_files, dict):
        for filename, content in sorted(extra_files.items()):
            if (
                isinstance(filename, str)
                and isinstance(content, str)
                and "/" not in filename
                and "\\" not in filename
                and filename not in {"context.json", "schema.json"}
            ):
                (tmpdir / filename).write_text(content, encoding="utf-8")
                files.append(filename)
    refs = slim_context.get("writing_references")
    if isinstance(refs, dict):
        rest_refs = dict(refs)
        if isinstance(rest_refs.get("writing_guide.md"), str):
            (tmpdir / "writing_guide.md").write_text(
                rest_refs.pop("writing_guide.md"), encoding="utf-8"
            )
            files.append("writing_guide.md")
        if isinstance(rest_refs.get("voice_examples"), list):
            (tmpdir / "voice_examples.md").write_text(
                _render_voice_examples(rest_refs.pop("voice_examples")),
                encoding="utf-8",
            )
            files.append("voice_examples.md")
        if rest_refs:
            slim_context["writing_references"] = rest_refs
        else:
            slim_context.pop("writing_references", None)
    write_json(tmpdir / "context.json", slim_context)
    return files


def _build_prompt(prompt: str, files: list[str], *, search: bool) -> str:
    file_list = ", ".join(files)
    tool_rule = (
        "You may read the workspace files and use web search to verify primary sources."
        if search
        else "Read only the workspace files. Do not use web search, shell commands, or subagents."
    )
    return (
        f"{prompt.strip()}\n\n"
        f"Workspace files in the current directory: {file_list}.\n"
        f"{tool_rule} Never create, edit, or delete files. "
        "Return only the schema-conforming JSON object."
    )


def _resolve_executable() -> str:
    configured = os.getenv("NC_AGY_EXECUTABLE", "").strip()
    if configured:
        return configured
    found = shutil.which("agy")
    if found:
        return found
    local_bin = Path.home() / ".local" / "bin" / "agy"
    if local_bin.is_file():
        return str(local_bin)
    return "agy"


def run_stage(
    prompt: str,
    context: dict[str, Any],
    schema: dict[str, Any],
    attempt: Path,
    *,
    effort: str = DEFAULT_EFFORT,
    model: str | None = None,
    search: bool = False,
    timeout_seconds: int = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one isolated headless agy stage and return its parsed JSON object."""
    attempt.mkdir(parents=True, exist_ok=False)
    (attempt / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(attempt / "context.json", context)
    write_json(attempt / "validation_schema.json", schema)
    provider_schema = response_schema(schema)
    write_json(attempt / "schema.json", provider_schema)

    resolved_model = model or model_for(effort)
    executable = _resolve_executable()
    started = monotonic()
    usage: dict[str, int] | None = None
    conversation_id: str | None = None

    try:
        with tempfile.TemporaryDirectory(prefix="neuralcast-agy-") as tmp:
            tmpdir = Path(tmp)
            files = _build_workspace_files(tmpdir, context)
            schema_path = tmpdir / "schema.json"
            write_json(schema_path, provider_schema)
            full_prompt = _build_prompt(prompt, files, search=search)
            cmd = [
                executable,
                "--model",
                resolved_model,
                "--output-format",
                "json",
                "--json-schema",
                str(schema_path),
                "--disable-slash-commands",
                "--sandbox",
                "--print-timeout",
                f"{timeout_seconds}s",
                f"--print={full_prompt}",
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=tmpdir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except FileNotFoundError:
                raise _stage_error(
                    attempt,
                    executable,
                    "configuration",
                    f"Antigravity executable '{executable}' was not found.",
                    None,
                ) from None

            try:
                stdout, stderr = proc.communicate(timeout=timeout_seconds + 30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                raise _stage_error(
                    attempt,
                    executable,
                    "timeout",
                    f"Antigravity editorial stage timed out after {timeout_seconds}s.",
                    None,
                ) from None

        envelope = _parse_envelope(stdout)
        if envelope is not None:
            usage = _extract_usage(envelope)
            if isinstance(envelope.get("conversation_id"), str):
                conversation_id = envelope["conversation_id"]

        if proc.returncode != 0:
            category, message = failure_diagnostic(f"{stderr}\n{stdout}")
            raise _stage_error(attempt, executable, category, message, proc.returncode)

        if envelope is None:
            raise _stage_error(
                attempt,
                executable,
                "incomplete",
                "Antigravity CLI exited cleanly without returning a JSON envelope.",
                proc.returncode,
            )

        status = str(envelope.get("status") or "")
        if status and status.upper() != "SUCCESS":
            category, message = failure_diagnostic(
                f"{status}\n{envelope.get('error') or ''}\n{stderr}"
            )
            if category == "unknown":
                category = "incomplete"
                message = f"Antigravity CLI returned status {status}."
            raise _stage_error(attempt, executable, category, message, proc.returncode)

        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            raw = json.dumps(structured, ensure_ascii=False)
            (attempt / "response.json").write_text(raw, encoding="utf-8")
            return structured

        raw_response = envelope.get("response")
        if not isinstance(raw_response, str) or not raw_response.strip():
            raise _stage_error(
                attempt,
                executable,
                "incomplete",
                "Antigravity CLI returned an empty response.",
                proc.returncode,
            )
        (attempt / "response.json").write_text(raw_response, encoding="utf-8")
        result = json.loads(raw_response)
        if not isinstance(result, dict):
            raise ValueError("Antigravity CLI returned a non-object document")
        return result
    finally:
        metrics: dict[str, Any] = {
            "provider": "agy",
            "model": resolved_model,
            "effort": effort,
            "search": search,
            "elapsed_seconds": round(monotonic() - started, 3),
            "usage": usage,
        }
        if conversation_id:
            metrics["conversation_id"] = conversation_id
        write_json(attempt / "metrics.json", metrics)
