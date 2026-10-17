"""Read-only, independently prompted Codex executions for episode editing."""

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from pydantic import BaseModel

from .storage import write_json

MODEL = "gpt-6.1-sol"
REASONING_EFFORT = "high"
TIMEOUT_SECONDS = 1800


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


def run_stage(prompt: str, context: dict, schema: dict, attempt: Path) -> dict:
    """Return a document; the controller alone can mutate project files.

    The private temporary workspace receives only snapshots, no media or secrets.
    Prompts/context/results persist in the project attempt directory; CLI logs do
    not, since authentication diagnostics can contain sensitive information.
    """
    attempt.mkdir(parents=True, exist_ok=False)
    (attempt / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(attempt / "context.json", context)
    write_json(attempt / "schema.json", schema)
    with tempfile.TemporaryDirectory(prefix="neuralcast-episode-editorial-") as temp:
        root = Path(temp)
        write_json(root / "context.json", context)
        write_json(root / "schema.json", schema)
        command = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--model",
            MODEL,
            "-c",
            f'model_reasoning_effort="{REASONING_EFFORT}"',
            "-c",
            'web_search="live"',
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
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                text=True,
                env=environment,
                start_new_session=True,
            )
            try:
                process.communicate(prompt, timeout=TIMEOUT_SECONDS)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise RuntimeError(
                    "Codex episode stage interrupted or timed out"
                ) from None
        if process.returncode:
            raise RuntimeError(f"Codex episode stage exited with {process.returncode}")
        raw = (root / "result.json").read_text(encoding="utf-8")
        (attempt / "response.json").write_text(raw, encoding="utf-8")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Codex stage returned a non-object document")
        return result
