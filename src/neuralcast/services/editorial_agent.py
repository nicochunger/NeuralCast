"""Isolated, schema-constrained Codex executions for editorial pipelines."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

from pydantic import BaseModel


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


MODEL = "gpt-6.1-sol"
REASONING_EFFORT = "high"
TIMEOUT_SECONDS = 1800


class EditorialStageError(RuntimeError):
    """Safe, classified failure suitable for operator-facing job diagnostics."""


def failure_diagnostic(raw: str) -> tuple[str, str]:
    """Classify CLI failures without returning raw logs or upstream bodies."""
    checks = (
        (
            "model_access",
            r"model[^\n]{0,160}(?:not supported|not found|does not exist)|model_not_found",
            "The Codex CLI/account rejected the configured model. Check NC_CODEX_EXECUTABLE and model access.",
        ),
        (
            "authentication",
            r"invalid_api_key|refresh.token|not logged in|authentication required|unauthorized|401",
            "Codex authentication failed. Check the CLI login for the service user.",
        ),
        (
            "schema",
            r"invalid.json.schema|invalid schema|response_format",
            "Codex rejected the structured output schema.",
        ),
        (
            "context_limit",
            r"context.window|context.length|maximum context|input.too.large",
            "The editorial snapshot exceeded the model context limit.",
        ),
        (
            "rate_limit",
            r"rate.limit|insufficient.quota|usage.limit|quota.exceeded|429",
            "Codex reported a rate or usage limit. Retry after the limit clears.",
        ),
        (
            "configuration",
            r"unexpected argument|error loading config|failed to parse.*config|unknown variant",
            "The selected Codex CLI rejected its arguments or configuration.",
        ),
        (
            "network",
            r"connection.refused|connection.reset|stream.disconnected|error sending request|failed to resolve|network.is.unreachable|tls|certificate",
            "Codex could not reach its provider. Check service networking and retry.",
        ),
        (
            "sandbox",
            r"operation not permitted|sandbox.*(?:failed|denied)|permission denied",
            "Codex could not initialize or use its execution sandbox.",
        ),
    )
    for category, pattern, message in checks:
        if re.search(pattern, raw, re.IGNORECASE):
            return category, message
    return (
        "unknown",
        "Codex failed without a recognized safe diagnostic. Raw logs were discarded to protect credentials.",
    )


def stage_error(
    attempt: Path, executable: str, category: str, message: str, exit_code: int | None
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
    return EditorialStageError(f"Codex editorial stage{status}: {message}")


def output_schema(model: type[BaseModel]) -> dict:
    """Codex structured output requires every object property to be required."""

    def strict(value):
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


def run_stage(
    prompt: str,
    context: dict,
    schema: dict,
    attempt: Path,
    *,
    search: bool = True,
    reasoning_effort: str = REASONING_EFFORT,
) -> dict:
    """Return a document; the controller alone can mutate project files.

    The private temporary workspace receives only snapshots, no media or secrets.
    Prompts/context/results persist in the project attempt directory; CLI logs do
    not, since authentication diagnostics can contain sensitive information.
    """
    attempt.mkdir(parents=True, exist_ok=False)
    if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("Unsupported editorial reasoning effort")
    executable = os.getenv("NC_CODEX_EXECUTABLE", "codex").strip() or "codex"
    (attempt / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(attempt / "context.json", context)
    write_json(attempt / "schema.json", schema)
    with tempfile.TemporaryDirectory(prefix="neuralcast-editorial-") as temp:
        root = Path(temp)
        write_json(root / "context.json", context)
        write_json(root / "schema.json", schema)
        command = [
            executable,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--model",
            MODEL,
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "-c",
            'web_search="live"' if search else 'web_search="disabled"',
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            str(root),
            "--output-schema",
            str(root / "schema.json"),
            "--output-last-message",
            str(root / "result.json"),
            "-",
        ]
        allowed = {
            "PATH",
            "HOME",
            "CODEX_HOME",
            "CODEX_API_KEY",
            "LANG",
            "LC_ALL",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "NODE_EXTRA_CA_CERTS",
        }
        environment = {k: v for k, v in os.environ.items() if k in allowed}
        with (root / "process.log").open("w") as log:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=log,
                    text=True,
                    env=environment,
                    start_new_session=True,
                )
            except OSError:
                raise stage_error(
                    attempt,
                    executable,
                    "executable",
                    "Could not start Codex. Check NC_CODEX_EXECUTABLE and executable permissions.",
                    None,
                ) from None
            try:
                process.communicate(prompt, timeout=TIMEOUT_SECONDS)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise stage_error(
                    attempt,
                    executable,
                    "timeout",
                    "Codex was interrupted or timed out.",
                    None,
                ) from None
        if process.returncode:
            category, message = failure_diagnostic(
                (root / "process.log").read_text(encoding="utf-8", errors="replace")
            )
            raise stage_error(
                attempt, executable, category, message, process.returncode
            )
        raw = (root / "result.json").read_text(encoding="utf-8")
        (attempt / "response.json").write_text(raw, encoding="utf-8")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Codex stage returned a non-object document")
        return result
