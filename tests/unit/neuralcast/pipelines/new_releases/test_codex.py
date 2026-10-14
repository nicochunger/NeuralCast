"""Headless Codex command, authentication boundary, and failure handling."""

import json
from pathlib import Path
import subprocess

import pytest

from neuralcast.pipelines.new_releases import codex


def test_headless_command_pins_model_and_effort_without_station_secrets(monkeypatch):
    captured = {}
    monkeypatch.setenv("AZURACAST_API_KEY", "must-not-inherit")

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)
            self.root = Path(command[command.index("--cd") + 1])

        def communicate(self, prompt, timeout):
            captured.update(prompt=prompt, timeout=timeout)
            assert json.loads((self.root / "context.json").read_text()) == {
                "max_discards": 1
            }
            (self.root / "decisions.json").write_text('{"decisions": []}')

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    assert codex.review_with_codex({"max_discards": 1}) == {"decisions": []}
    assert (
        captured["command"][captured["command"].index("--model") + 1] == "gpt-6.1-sol"
    )
    assert 'model_reasoning_effort="high"' in captured["command"]
    assert 'web_search="live"' in captured["command"]
    assert "read-only" in captured["command"]
    assert "AZURACAST_API_KEY" not in captured["env"]
    assert captured["timeout"] == 1800
    assert not Path(captured["command"][captured["command"].index("--cd") + 1]).exists()


def test_timeout_terminates_process_group(monkeypatch):
    killed = []

    class Process:
        pid = 12345

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, *args, **kwargs):
            if kwargs:
                raise subprocess.TimeoutExpired("codex", 1800)

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    monkeypatch.setattr(codex.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(RuntimeError, match="timed out"):
        codex.review_with_codex({})
    assert killed == [(12345, codex.signal.SIGKILL)]


def test_nonzero_exit_does_not_expose_process_logs(monkeypatch):
    class Process:
        returncode = 1

        def __init__(self, *args, **kwargs):
            kwargs["stderr"].write("sensitive auth diagnostic")

        def communicate(self, *args, **kwargs):
            pass

    monkeypatch.setattr(codex.subprocess, "Popen", Process)
    with pytest.raises(RuntimeError, match="exited with 1") as failure:
        codex.review_with_codex({})
    assert "sensitive" not in str(failure.value)
