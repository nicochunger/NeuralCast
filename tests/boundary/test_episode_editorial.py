"""Offline editorial handoffs, recovery, concept boundaries, and render approval."""

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.episode_producer import editorial, service
from neuralcast.pipelines.episode_producer.editorial_models import EditorialBrief
from neuralcast.pipelines.episode_producer.manuscript import (
    approve_manuscript,
    manuscript_hash,
)
from neuralcast.pipelines.episode_producer.models import Plan, Proposal
from neuralcast.pipelines.episode_producer.storage import (
    Project,
    digest,
    read_json,
    write_json,
)


@pytest.fixture
def episode(tmp_path, monkeypatch):
    station = tmp_path / "station"
    (station / "playlists").mkdir(parents=True)
    (station / "songs" / "Classic Metal").mkdir(parents=True)
    (station / "songs" / "Classic Metal" / "Artist - Song.mp3").write_bytes(
        b"library recording"
    )
    (station / "playlists" / "Classic Metal.csv").write_text(
        "Artist,Title,Album,Year,Validated\nArtist,Song,Album,1984,True\n"
    )
    (station / "playlist_descriptions.md").write_text("Classic metal and its stories.")
    project = Project(
        "neuralforge",
        "editorial-test",
        station_root=station,
        runtime_root=tmp_path / "runtime",
    )
    proposal = Proposal(
        episode_id=project.id,
        channel="neuralforge-es",
        title="Connected stories",
        premise="How musicians and songs connect",
        outline=["A decision and its consequences"],
        target_seconds=600,
        commentary_budget_seconds=100,
    )
    write_json(project.documents / "proposal.json", proposal.model_dump())
    candidate = {
        "kind": "music",
        "id": "song",
        "playlist": "Classic Metal",
        "artist": "Artist",
        "title": "Song",
        "rationale": "Makes the consequence audible",
    }
    brief = EditorialBrief(
        proposal_hash=digest(proposal.model_dump()),
        instructions="Develop the relationships. No spoken citations.",
        candidate_songs=[candidate],
        initial_research=[
            {"url": "https://example.com/history", "claim": "A member left."}
        ],
    )
    write_json(project.documents / "brief.json", brief.model_dump())
    project.approve_proposal(project.concept_hash())
    parts = []
    for id_, role, script in [
        (
            "intro",
            "opening",
            "Bienvenidos al podcast de NeuralForge. Hoy seguimos una historia compartida.",
        ),
        (
            "decision",
            "bridge",
            "Una decisión cambió el grupo. <breath> Esa consecuencia conecta con la canción.",
        ),
    ]:
        parts.append(
            {
                "kind": "commentary",
                "id": id_,
                "role": role,
                "brief": "Tell the story",
                "script": script,
                "companion": {"title": id_, "summary": "A relationship changes."},
                "sources": [
                    {"url": "https://example.com/history", "claim": "A member left."}
                ],
            }
        )
    parts.append(candidate)
    parts.append(
        {
            "kind": "commentary",
            "id": "outro",
            "role": "closing",
            "brief": "Resolve the story",
            "script": "Las canciones guardan esas conexiones. Gracias por escuchar.",
            "companion": {"title": "Cierre", "summary": "The story resolves."},
        }
    )
    plan = Plan(proposal_hash=brief.proposal_hash, segments=parts).model_dump()
    draft = {
        "plan": plan,
        "research_notes": "Verified history; sources attached to each factual passage.",
        "open_questions": [],
    }
    review = {
        "strengths": ["The opening establishes the relationships."],
        "issues": [
            {
                "id": "detail",
                "segment_ids": ["decision"],
                "severity": "major",
                "category": "depth",
                "problem": "The consequence is vague.",
                "correction": "Explain the consequence with evidence.",
                "acceptance": "The listener understands what changed.",
            }
        ],
        "additional_sources": [
            {"url": "https://example.com/consequence", "claim": "The group changed."}
        ],
    }
    revision = deepcopy(draft)
    revision["plan"]["segments"][1][
        "script"
    ] += " Después tuvieron que elegir un reemplazo."
    revision.update(
        changes=["Explained the consequence."],
        resolutions=[
            {
                "issue_id": "detail",
                "status": "resolved",
                "explanation": "The replacement decision is now explicit.",
            }
        ],
    )
    outputs = [draft, review, revision]
    monkeypatch.setattr(editorial, "audio_duration", lambda path: 500)
    return project, outputs


def runner_for(outputs, calls):
    def runner(prompt, context, schema, attempt):
        stage = len(calls)
        calls.append({"prompt": prompt, "context": deepcopy(context), "schema": schema})
        return deepcopy(outputs[stage])

    return runner


def test_three_calls_have_full_handoffs_and_stop_before_audio(episode, monkeypatch):
    project, outputs = episode
    tts = Mock(side_effect=AssertionError("No audio during editing"))
    monkeypatch.setattr(service, "prepare_narration", tts)
    calls = []
    result = editorial.edit_episode(project, runner=runner_for(outputs, calls))
    assert len(calls) == 3
    assert "draft" not in calls[0]["context"]
    assert calls[1]["context"]["draft"]["plan"] == outputs[0]["plan"]
    assert calls[2]["context"]["review"]["issues"][0]["id"] == "detail"
    for call in calls:
        context = call["context"]
        assert context["host"]["locale"] == "es-AR"
        assert "writing_guide.md" in context["writing_references"]
        assert "host_tts_instructions" in context["writing_references"]
        assert "duration_seconds" in context["recordings"]["song"]
        assert "path" not in context["recordings"]["song"]
        assert call["schema"]["additionalProperties"] is False
    assert result["status"] == "awaiting_manuscript_approval"
    markdown = (project.documents / "manuscript.md").read_text()
    assert "Artist | Song" in markdown and "\\<breath\\>" in markdown
    assert "https://" not in markdown
    assert not (project.runtime / "manuscript_approval.json").exists()
    assert not list(project.runtime.glob("**/*.mp3"))
    tts.assert_not_called()
    never = Mock(side_effect=AssertionError("Completed stages must be reused"))
    assert editorial.edit_episode(project, runner=never) == result
    never.assert_not_called()
    client = Mock()
    with pytest.raises(ValueError, match="not been approved"):
        service.prepare(project, client)
    client.upload_media.assert_not_called()
    approve_manuscript(project, result["manuscript_hash"])
    changed = read_json(project.documents / "plan.json")
    changed["segments"][0]["script"] += " Otra frase."
    write_json(project.documents / "plan.json", changed)
    with pytest.raises(ValueError, match="not been approved"):
        service.prepare(project, client)


def embed_brief(project):
    brief = read_json(project.documents / "brief.json")
    proposal = read_json(project.documents / "proposal.json")
    write_json(
        project.documents / "proposal.json", {**proposal, "editorial_brief": brief}
    )
    (project.documents / "brief.json").unlink()


def test_embedding_brief_preserves_approvals_and_cached_editorial_run(episode):
    project, outputs = episode
    result = editorial.edit_episode(project, runner=runner_for(outputs, []))
    approve_manuscript(project, result["manuscript_hash"])
    concept = project.concept_hash()
    revision = service.validated_inputs(project)[-1]
    approval = read_json(project.runtime / "manuscript_approval.json")
    embed_brief(project)
    assert project.has_editorial_brief
    assert project.concept_hash() == concept
    project.approved_proposal()
    assert service.validated_inputs(project)[-1] == revision
    assert read_json(project.runtime / "manuscript_approval.json") == approval
    never = Mock(side_effect=AssertionError("Reuse the existing editorial stages"))
    assert editorial.edit_episode(project, runner=never) == result
    never.assert_not_called()
    proposal = read_json(project.documents / "proposal.json")
    proposal["editorial_brief"]["instructions"] += " Add more detail."
    write_json(project.documents / "proposal.json", proposal)
    with pytest.raises(ValueError, match="not been approved"):
        project.approved_proposal()
    project.approve_proposal(project.concept_hash())
    with pytest.raises(ValueError, match="not been approved"):
        service.prepare(project, Mock())


def test_embedded_brief_still_rejects_unapproved_recordings(episode):
    project, outputs = episode
    editorial.edit_episode(project, runner=runner_for(outputs, []))
    embed_brief(project)
    plan = read_json(project.documents / "plan.json")
    next(s for s in plan["segments"] if s["kind"] == "music")["title"] = "Other Song"
    write_json(project.documents / "plan.json", plan)
    with pytest.raises(ValueError, match="Unapproved song identity"):
        service.validated_inputs(project)


def test_conflicting_embedded_and_legacy_briefs_are_rejected(episode):
    project, _ = episode
    proposal = read_json(project.documents / "proposal.json")
    brief = read_json(project.documents / "brief.json")
    brief["instructions"] += " Different instructions."
    write_json(
        project.documents / "proposal.json", {**proposal, "editorial_brief": brief}
    )
    with pytest.raises(ValueError, match="briefs disagree"):
        project.concept_hash()


@pytest.mark.parametrize("import_legacy", [False, True])
def test_init_embeds_editorial_brief_without_creating_an_extra_file(
    episode, tmp_path, monkeypatch, capsys, import_legacy
):
    from neuralcast.cli import episode_producer as cli

    original, _ = episode
    project = Project(
        original.station,
        original.id,
        station_root=tmp_path / "new-station",
        runtime_root=tmp_path / "new-runtime",
    )
    proposal = read_json(original.documents / "proposal.json")
    brief = read_json(original.documents / "brief.json")
    if not import_legacy:
        proposal["editorial_brief"] = brief
    source = tmp_path / "source-proposal.json"
    write_json(source, proposal)
    monkeypatch.setattr(cli, "Project", lambda station, episode: project)
    args = [
        "init",
        "-s",
        project.station,
        "--episode",
        project.id,
        "--proposal",
        str(source),
    ]
    if import_legacy:
        args += ["--brief", str(original.documents / "brief.json")]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["concept_hash"] == original.concept_hash()
    assert project.editorial_brief().model_dump() == brief
    assert not (project.documents / "brief.json").exists()
    assert read_json(project.documents / "proposal.json")["editorial_brief"] == brief


def test_candidate_or_concept_edits_invalidate_initial_approval(episode):
    project, _ = episode
    brief = read_json(project.documents / "brief.json")
    brief["candidate_songs"][0]["title"] = "Different song"
    write_json(project.documents / "brief.json", brief)
    with pytest.raises(ValueError, match="not been approved"):
        project.approved_proposal()


def test_reapproving_changed_concept_still_requires_new_manuscript_approval(episode):
    project, outputs = episode
    result = editorial.edit_episode(project, runner=runner_for(outputs, []))
    approve_manuscript(project, result["manuscript_hash"])
    brief = read_json(project.documents / "brief.json")
    brief["instructions"] += " Add context for newcomers."
    write_json(project.documents / "brief.json", brief)
    project.approve_proposal(project.concept_hash())
    with pytest.raises(ValueError, match="not been approved"):
        service.prepare(project, Mock())


def test_new_blocker_report_prevents_using_existing_approval(episode):
    project, outputs = episode
    result = editorial.edit_episode(project, runner=runner_for(outputs, []))
    approve_manuscript(project, result["manuscript_hash"])
    result["blockers"] = ["Newly discovered factual uncertainty"]
    write_json(project.runtime / "editorial_latest.json", result)
    with pytest.raises(ValueError, match="unresolved editorial blockers"):
        service.prepare(project, Mock())


def test_timing_policy_is_enforced_before_installing_the_manuscript(
    episode, monkeypatch
):
    project, outputs = episode
    monkeypatch.setattr(editorial, "audio_duration", lambda path: 900)
    with pytest.raises(ValueError, match="duration exceeds"):
        editorial.edit_episode(project, runner=runner_for(outputs, []))
    assert not (project.documents / "plan.json").exists()


def test_cli_editorial_runs_without_audio_credentials(episode, monkeypatch, capsys):
    from neuralcast.cli import episode_producer as cli

    project, outputs = episode
    monkeypatch.setattr(cli, "Project", lambda *args: project)
    monkeypatch.setattr(
        cli,
        "edit_episode",
        lambda project, **kwargs: editorial.edit_episode(
            project, runner=runner_for(outputs, []), **kwargs
        ),
    )
    monkeypatch.setattr(
        cli,
        "load_azuracast_settings",
        Mock(side_effect=AssertionError("No credentials needed")),
    )
    assert cli.main(["editorial", "--episode", project.id]) == 0
    assert (
        json.loads(capsys.readouterr().out)["status"] == "awaiting_manuscript_approval"
    )


def test_failure_resumes_completed_stages_and_preserves_attempts(episode):
    project, outputs = episode
    count = 0

    def fail_review(prompt, context, schema, attempt):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("Simulated timeout")
        return deepcopy(outputs[0])

    with pytest.raises(RuntimeError, match="timeout"):
        editorial.edit_episode(project, runner=fail_review)
    assert not (project.documents / "plan.json").exists()
    calls = []
    result = editorial.edit_episode(project, runner=runner_for(outputs[1:], calls))
    assert len(calls) == 2 and "Stage 2" in calls[0]["prompt"]
    assert list(project.runtime.glob("editorial/*/review/attempt-001/failure.json"))
    assert result["status"] == "awaiting_manuscript_approval"


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda outputs: outputs[0]["plan"]["segments"][2].update(
                title="Unapproved"
            ),
            "Unapproved song",
        ),
        (
            lambda outputs: outputs[0]["plan"]["segments"][0].update(
                script=None, companion=None
            ),
            "Complete manuscript",
        ),
        (
            lambda outputs: outputs[0]["plan"]["segments"][1].update(
                script="Bad <invented> tag"
            ),
            "Unsupported",
        ),
        (
            lambda outputs: outputs[0]["plan"]["segments"][1].update(sources=[]),
            "evidence required",
        ),
        (
            lambda outputs: outputs[1]["issues"][0].update(segment_ids=["missing"]),
            "unknown manuscript",
        ),
        (lambda outputs: outputs[2].update(resolutions=[]), "every review issue"),
    ],
)
def test_invalid_stage_output_never_installs_a_plan(episode, mutation, match):
    project, outputs = episode
    mutation(outputs)
    with pytest.raises(ValueError, match=match):
        editorial.edit_episode(project, runner=runner_for(outputs, []))
    assert not (project.documents / "plan.json").exists()


def test_unresolved_issues_are_exported_and_block_approval(episode):
    project, outputs = episode
    outputs[2]["resolutions"][0]["status"] = "unresolved"
    result = editorial.edit_episode(project, runner=runner_for(outputs, []))
    assert result["status"] == "blocked" and result["blockers"]
    assert "detail:" in (project.documents / "manuscript.md").read_text()
    with pytest.raises(ValueError, match="unresolved editorial blockers"):
        approve_manuscript(project, result["manuscript_hash"])


def test_concurrent_manual_edits_are_preserved(episode):
    project, outputs = episode
    normal = runner_for(outputs, [])

    def runner(prompt, context, schema, attempt):
        result = normal(prompt, context, schema, attempt)
        if "Stage 3" in prompt:
            write_json(project.documents / "plan.json", {"manual": "Do not overwrite"})
        return result

    with pytest.raises(ValueError, match="edits were preserved"):
        editorial.edit_episode(project, runner=runner)
    assert read_json(project.documents / "plan.json") == {"manual": "Do not overwrite"}


def test_stage_cache_tampering_is_rejected(episode):
    project, outputs = episode
    editorial.edit_episode(project, runner=runner_for(outputs, []))
    path = next(project.runtime.glob("editorial/*/write.json"))
    saved = read_json(path)
    saved["output"]["research_notes"] = "Changed after validation"
    write_json(path, saved)
    with pytest.raises(ValueError, match="stage changed"):
        editorial.edit_episode(project, runner=Mock())


def test_inspect_and_approval_cli_use_exact_concept_and_manuscript_hashes(
    episode, monkeypatch, capsys
):
    from neuralcast.cli import episode_producer as cli

    project, outputs = episode
    monkeypatch.setattr(cli, "Project", lambda *args: project)
    args = ["--episode", project.id]
    assert cli.main(["inspect", *args]) == 0
    assert json.loads(capsys.readouterr().out)["concept_hash"] == project.concept_hash()
    result = editorial.edit_episode(project, runner=runner_for(outputs, []))
    with pytest.raises(SystemExit):
        cli.main(["approve-manuscript", *args, "--hash", "a" * 64])
    assert (
        cli.main(["approve-manuscript", *args, "--hash", result["manuscript_hash"]])
        == 0
    )
    stored = read_json(project.runtime / "manuscript_approval.json")
    assert stored["manuscript_hash"] == result["manuscript_hash"]
