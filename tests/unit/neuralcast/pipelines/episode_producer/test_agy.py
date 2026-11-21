"""Headless Antigravity CLI (agy) editorial runner tests."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from neuralcast.services import editorial_agy as agy


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> Mock:
    proc = Mock()
    proc.pid = 4242
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


def test_agy_stage_runs_isolated_headless_and_records_usage(tmp_path, monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "structured_output": {"findings": []},
        "usage": {
            "input_tokens": 200,
            "output_tokens": 40,
            "thinking_tokens": 25,
            "total_tokens": 265,
        },
    }
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        captured["files"] = sorted(p.name for p in kwargs["cwd"].iterdir())
        captured["schema"] = json.loads((kwargs["cwd"] / "schema.json").read_text())
        return _proc(stdout=json.dumps(envelope))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    attempt = tmp_path / "attempt"
    schema = {
        "type": "object",
        "properties": {"findings": {"type": "array", "minItems": 0, "maxItems": 6}},
        "required": ["findings"],
        "additionalProperties": False,
    }
    result = agy.run_stage(
        "Review this manuscript",
        {
            "source_plan": {"segments": []},
            "writing_references": {
                "writing_guide.md": "# Guide\nKeep it natural.",
                "voice_examples": [
                    {
                        "id": "natural-aside",
                        "category": "personality",
                        "status": "owner_approved_example",
                        "before": None,
                        "after": "Que… bueno, tampoco era obvio.",
                        "reason": "Brief reaction.",
                        "check": "Fits tone?",
                    }
                ],
            },
        },
        schema,
        attempt,
        effort="high",
        search=False,
        timeout_seconds=120,
    )
    assert result == {"findings": []}
    assert "--model" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "gemini-4-neon-high"
    assert "--output-format" in captured["cmd"]
    assert "--disable-slash-commands" in captured["cmd"]
    assert "--sandbox" in captured["cmd"]
    assert captured["files"] == [
        "context.json",
        "schema.json",
        "voice_examples.md",
        "writing_guide.md",
    ]
    assert "minItems" not in captured["schema"]["properties"]["findings"]
    assert "maxItems" not in captured["schema"]["properties"]["findings"]
    assert (attempt / "validation_schema.json").exists()
    assert (attempt / "response.json").exists()
    metrics = json.loads((attempt / "metrics.json").read_text())
    assert metrics["provider"] == "agy"
    assert metrics["model"] == "gemini-4-neon-high"
    assert metrics["effort"] == "high"
    assert metrics["conversation_id"] == "conv-123"
    assert metrics["usage"]["thinking_tokens"] == 25


def test_agy_writes_workspace_files_and_strips_them_from_context_json(
    tmp_path, monkeypatch
):
    envelope = {"status": "SUCCESS", "structured_output": {"ok": True}}
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["files"] = sorted(p.name for p in kwargs["cwd"].iterdir())
        captured["context"] = json.loads((kwargs["cwd"] / "context.json").read_text())
        captured["tsv"] = (kwargs["cwd"] / "catalog.tsv").read_text()
        return _proc(stdout=json.dumps(envelope))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    agy.run_stage(
        "Develop",
        {
            "station": "neuralforge",
            "workspace_files": {"catalog.tsv": "playlist\tartist\ttitle\nMetal\tA\tB\n"},
        },
        {"type": "object"},
        tmp_path / "attempt",
    )
    assert captured["files"] == ["catalog.tsv", "context.json", "schema.json"]
    assert "workspace_files" not in captured["context"]
    assert "Metal\tA\tB" in captured["tsv"]


def test_agy_parses_envelope_when_warning_precedes_json(tmp_path, monkeypatch):
    envelope = {
        "status": "SUCCESS",
        "structured_output": {"ok": True},
        "usage": {"total_tokens": 10},
    }
    stdout = f"warning: something harmless\n{json.dumps(envelope)}\n"
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _proc(stdout=stdout))
    result = agy.run_stage("Check", {}, {"type": "object"}, tmp_path / "attempt")
    assert result == {"ok": True}


def test_agy_failure_classifies_auth_error_without_leaking_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: _proc(
            stderr="401 unauthorized: Bearer super-secret-token", returncode=1
        ),
    )
    with pytest.raises(agy.EditorialStageError, match="authentication failed") as exc:
        agy.run_stage("Edit", {}, {}, tmp_path / "attempt")
    saved = "".join(p.read_text() for p in (tmp_path / "attempt").iterdir())
    assert "super-secret-token" not in saved + str(exc.value)
    failure = json.loads((tmp_path / "attempt" / "failure.json").read_text())
    assert failure["category"] == "authentication"
    assert failure["exit_code"] == 1


def test_agy_timeout_exit_code_is_not_misclassified_as_schema_error(
    tmp_path, monkeypatch
):
    envelope = {
        "status": "TIMEOUT",
        "json_schema": {"type": "object"},
        "usage": {"total_tokens": 100},
    }
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: _proc(
            stdout=json.dumps(envelope), stderr="print timeout exceeded", returncode=3
        ),
    )
    with pytest.raises(agy.EditorialStageError, match="timed out"):
        agy.run_stage("Research", {}, {"type": "object"}, tmp_path / "attempt")
    failure = json.loads((tmp_path / "attempt" / "failure.json").read_text())
    assert failure["category"] == "timeout"


def test_agy_missing_executable_reports_configuration_error(tmp_path, monkeypatch):
    monkeypatch.setenv("NC_AGY_EXECUTABLE", "missing-agy-bin")
    monkeypatch.setattr(
        subprocess, "Popen", Mock(side_effect=FileNotFoundError("no such file"))
    )
    with pytest.raises(agy.EditorialStageError, match="not found"):
        agy.run_stage("Edit", {}, {}, tmp_path / "attempt")
    failure = json.loads((tmp_path / "attempt" / "failure.json").read_text())
    assert failure["category"] == "configuration"


def test_agy_timeout_kills_process_group_and_records_failure(tmp_path, monkeypatch):
    proc = Mock()
    proc.pid = 9999
    proc.communicate.side_effect = subprocess.TimeoutExpired(cmd=["agy"], timeout=10)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    killed = []
    monkeypatch.setattr("os.killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(agy.EditorialStageError, match="timed out"):
        agy.run_stage("Edit", {}, {}, tmp_path / "attempt", timeout_seconds=5)
    assert killed and killed[0][0] == 9999
    failure = json.loads((tmp_path / "attempt" / "failure.json").read_text())
    assert failure["category"] == "timeout"
