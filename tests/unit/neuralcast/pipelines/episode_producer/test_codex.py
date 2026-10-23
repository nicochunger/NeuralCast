"""Codex execution contracts without network, credentials, or subprocesses."""

import json
from pathlib import Path
import subprocess

import pytest

from neuralcast.services import editorial_agent as codex
from neuralcast.pipelines.episode_producer.editorial_models import (
    Draft,
    Review,
    Revision,
)


@pytest.mark.parametrize("model", [Draft, Review, Revision])
def test_output_schema_is_strict_and_uses_supported_union_form(model):
    schema = codex.output_schema(model)

    def check(value):
        if isinstance(value, dict):
            assert (
                "default" not in value
                and "oneOf" not in value
                and "discriminator" not in value
            )
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(schema)


def test_run_is_fresh_read_only_research_enabled_and_keeps_only_safe_artifacts(
    tmp_path, monkeypatch
):
    captured = {}
    monkeypatch.setenv("AZURACAST_API_KEY", "secret")
    monkeypatch.setenv("GEMINI_API_KEY", "secret")

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)
            self.root = Path(command[command.index("--cd") + 1])
            kwargs["stdout"].write("Sensitive diagnostic")

        def communicate(self, prompt, timeout):
            captured.update(prompt=prompt, timeout=timeout)
            assert json.loads((self.root / "context.json").read_text()) == {
                "approved": True
            }
            (self.root / "result.json").write_text(
                '{"strengths": ["Works"], "issues": [], "additional_sources": []}'
            )

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    attempt = tmp_path / "attempt-001"
    result = codex.run_stage(
        "Review this snapshot", {"approved": True}, codex.output_schema(Review), attempt
    )
    assert result["strengths"] == ["Works"]
    assert "--ephemeral" in captured["command"]
    assert "--ignore-user-config" in captured["command"]
    assert 'web_search="live"' in captured["command"]
    assert "read-only" in captured["command"]
    assert captured["start_new_session"] is True
    assert "AZURACAST_API_KEY" not in captured["env"]
    assert "GEMINI_API_KEY" not in captured["env"]
    assert (attempt / "context.json").is_file() and (
        attempt / "response.json"
    ).is_file()
    assert not (attempt / "process.log").exists()
    assert not Path(captured["command"][captured["command"].index("--cd") + 1]).exists()


def test_timeout_kills_process_group_and_excludes_logs(tmp_path, monkeypatch):
    killed = []

    class Process:
        pid = 1234

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, *args, **kwargs):
            if kwargs:
                raise subprocess.TimeoutExpired("codex", 1800)

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    monkeypatch.setattr(codex.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(RuntimeError, match="timed out"):
        codex.run_stage("Write", {}, {}, tmp_path / "attempt")
    assert killed == [(1234, codex.signal.SIGKILL)]


def test_feedback_mode_disables_web_research(tmp_path, monkeypatch):
    captured = []

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured.extend(command)
            self.root = Path(command[command.index("--cd") + 1])

        def communicate(self, *args, **kwargs):
            (self.root / "result.json").write_text("{}")

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    codex.run_stage(
        "Interpret frozen feedback", {}, {}, tmp_path / "feedback", search=False
    )
    assert 'web_search="disabled"' in captured
    assert 'web_search="live"' not in captured


def test_explicit_executable_and_medium_effort_do_not_change_model(
    tmp_path, monkeypatch
):
    command = []
    monkeypatch.setenv("NC_CODEX_EXECUTABLE", "/usr/bin/codex")

    class Process:
        returncode = 0

        def __init__(self, argv, **kwargs):
            command.extend(argv)
            self.root = Path(argv[argv.index("--cd") + 1])

        def communicate(self, *args, **kwargs):
            (self.root / "result.json").write_text("{}")

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    codex.run_stage(
        "Synthetic", {}, {}, tmp_path / "attempt", reasoning_effort="medium"
    )
    assert command[0] == "/usr/bin/codex"
    assert command[command.index("--model") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="medium"' in command


def test_rejected_model_keeps_safe_diagnostics_without_raw_log_or_secrets(
    tmp_path, monkeypatch
):
    class Process:
        returncode = 1

        def __init__(self, command, **kwargs):
            self.root = Path(command[command.index("--cd") + 1])
            kwargs["stdout"].write(
                "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.\n"
                "Authorization: Bearer private-access-token\n"
                "provider body: secret-provider-details\n"
            )

        def communicate(self, *args, **kwargs):
            pass

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    attempt = tmp_path / "attempt"
    with pytest.raises(
        codex.EditorialStageError, match="rejected the configured model"
    ) as error:
        codex.run_stage("Synthetic", {}, {}, attempt)
    failure = json.loads((attempt / "failure.json").read_text())
    assert failure["category"] == "model_access" and failure["exit_code"] == 1
    assert "private-access-token" not in str(error.value) + json.dumps(failure)
    assert "secret-provider-details" not in str(error.value) + json.dumps(failure)
    assert not (attempt / "process.log").exists()


@pytest.mark.parametrize(
    "raw, category",
    [
        ("Invalid schema for response_format", "schema"),
        ("Failed to refresh token. Authorization: private", "authentication"),
        ("Context window exceeded", "context_limit"),
        ("error sending request: private URL", "network"),
        ("Unexpected argument --new-flag", "configuration"),
        ("unknown private diagnostic", "unknown"),
    ],
)
def test_failure_categories_never_echo_arbitrary_raw_output(raw, category):
    actual, message = codex.failure_diagnostic(raw)
    assert actual == category
    assert "private" not in message
