"""Offline five-job editorial flow, focused retries and manuscript approval boundaries."""

from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.episode_producer import editorial, service
from neuralcast.pipelines.episode_producer.editorial_models import EditorialBrief
from neuralcast.pipelines.episode_producer.manuscript import (
    approve_manuscript,
    require_manuscript_approval,
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
    (station / "songs" / "Classic Metal" / "Artist - Song Two.mp3").write_bytes(
        b"second library recording"
    )
    (station / "playlists" / "Classic Metal.csv").write_text(
        "Artist,Title,Album,Year,Validated\nArtist,Song,Album,1984,True\nArtist,Song Two,Album,1984,True\n"
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
    second_candidate = {**candidate, "id": "song-two", "title": "Song Two"}
    brief = EditorialBrief(
        proposal_hash=digest(proposal.model_dump()),
        instructions="Develop the relationships. No spoken citations.",
        candidate_songs=[candidate, second_candidate],
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
            "Bienvenidos al podcast de NeuralForsch. Hoy seguimos una historia compartida.",
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
    parts.insert(1, candidate)
    parts.append(second_candidate)
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
    finding = {
        "segment_ids": ["decision"],
        "quote": "Una decisión cambió el grupo.",
        "severity": "major",
        "problem": "The consequence is vague.",
        "correction": "Explain what changed.",
    }
    outputs = {
        "research": {
            "central_question": "How did one decision reshape the band?",
            "narrative_arc": ["Opening setup", "Departure and replacement", "Closing"],
            "verified_sources": [
                {"url": "https://example.com/history", "claim": "A member left."}
            ],
            "research_notes": "Verified primary history.",
            "open_questions": [],
            "production_notes": [],
        },
        "write": draft,
        "editorial_review": {
            "strengths": ["Keep the welcome.", "Keep the conversational phrasing."],
            "findings": [finding],
            "additional_sources": [],
        },
        "edit": {
            "passages": [
                {
                    "segment_id": p["id"],
                    "script": p["script"],
                    "explanation": "Keep the supported scene.",
                    "companion": None,
                    "sources": None,
                }
                for p in plan["segments"]
                if p["kind"] == "commentary"
            ],
            "resolutions": [
                {
                    "finding_id": "editorial_review-1",
                    "status": "resolved",
                    "explanation": "Made the replacement decision explicit.",
                }
            ],
            "open_questions": [],
            "production_notes": [],
            "listening_notes": [],
        },
        "fact_check": {"findings": []},
        "fact_check_recheck": {"findings": []},
    }
    outputs["edit"]["passages"][1]["script"] = (
        parts[2]["script"].replace("<breath>", "<sigh>")
        + " Después tuvieron que elegir un reemplazo."
    )
    monkeypatch.setattr(editorial, "audio_duration", lambda path: 250)
    return project, outputs


def runner_for(outputs, calls):
    def runner(prompt, context, schema, attempt, **kwargs):
        name = attempt.parent.name
        calls.append(
            {
                "name": name,
                "prompt": prompt,
                "context": deepcopy(context),
                "schema": schema,
                **kwargs,
            }
        )
        return deepcopy(outputs[name])

    return runner


def run_episode(project, outputs, calls=None, **kwargs):
    runner = runner_for(outputs, calls if calls is not None else [])
    return editorial.edit_episode(project, runner=runner, **kwargs)


def delivery_edit(outputs):
    result = deepcopy(outputs["edit"])
    result["resolutions"] = []
    result["passages"][0]["script"] = result["passages"][0]["script"].replace(
        "Hoy seguimos", "Hoy vamos a seguir"
    )
    return result


def embed_brief(project):
    brief = read_json(project.documents / "brief.json")
    proposal = read_json(project.documents / "proposal.json")
    write_json(
        project.documents / "proposal.json", {**proposal, "editorial_brief": brief}
    )
    (project.documents / "brief.json").unlink()


def test_embedding_brief_preserves_approvals_and_cached_editorial_run(episode):
    project, outputs = episode
    result = run_episode(project, outputs)
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
    run_episode(project, outputs)
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
    result = run_episode(project, outputs)
    approve_manuscript(project, result["manuscript_hash"])
    brief = read_json(project.documents / "brief.json")
    brief["instructions"] += " Add context for newcomers."
    write_json(project.documents / "brief.json", brief)
    project.approve_proposal(project.concept_hash())
    with pytest.raises(ValueError, match="not been approved"):
        service.prepare(project, Mock())


def test_new_blocker_report_prevents_using_existing_approval(episode):
    project, outputs = episode
    result = run_episode(project, outputs)
    approve_manuscript(project, result["manuscript_hash"])
    result["blockers"] = ["Newly discovered factual uncertainty"]
    write_json(project.runtime / "editorial_latest.json", result)
    with pytest.raises(ValueError, match="unresolved editorial blockers"):
        service.prepare(project, Mock())


def test_timing_estimates_do_not_block_manuscript_installation(episode, monkeypatch):
    project, outputs = episode
    monkeypatch.setattr(editorial, "audio_duration", lambda path: 900)
    result = run_episode(project, outputs)
    assert (
        result["timing"]["estimated_total_seconds"] > project.proposal().target_seconds
    )
    assert (project.documents / "plan.json").exists()


def test_inspect_and_approval_cli_use_exact_concept_and_manuscript_hashes(
    episode, monkeypatch, capsys
):
    from neuralcast.cli import episode_producer as cli

    project, outputs = episode
    monkeypatch.setattr(cli, "Project", lambda *args: project)
    args = ["--episode", project.id]
    assert cli.main(["inspect", *args]) == 0
    assert json.loads(capsys.readouterr().out)["concept_hash"] == project.concept_hash()
    result = run_episode(project, outputs)
    with pytest.raises(SystemExit):
        cli.main(["approve-manuscript", *args, "--hash", "a" * 64])
    assert (
        cli.main(["approve-manuscript", *args, "--hash", result["manuscript_hash"]])
        == 0
    )
    stored = read_json(project.runtime / "manuscript_approval.json")
    assert stored["manuscript_hash"] == result["manuscript_hash"]


def test_published_episode_cannot_be_rewritten(episode, monkeypatch, tmp_path):
    project, _ = episode
    catalogue = tmp_path / "catalogue"
    write_json(catalogue / "manifest.json", {"published": True})
    monkeypatch.setattr(editorial, "episode_directory", lambda *args: catalogue)
    never = Mock()
    for operation in (editorial.edit_episode, editorial.polish_delivery):
        with pytest.raises(ValueError, match="Published episodes"):
            operation(project, runner=never)
    never.assert_not_called()


def test_five_jobs_have_direct_handoffs_and_stop_before_audio(episode, monkeypatch):
    project, outputs = episode
    calls = []
    tts = Mock(side_effect=AssertionError("No audio during editing"))
    monkeypatch.setattr(service, "prepare_narration", tts)
    result = run_episode(project, outputs, calls)
    by_name = {c["name"]: c for c in calls}
    assert set(by_name) == {"research", "write", "editorial_review", "edit", "fact_check"}
    assert len(calls) == 5
    assert by_name["research"]["effort"] == "high" and by_name["research"]["search"]
    assert by_name["write"]["effort"] == "medium" and not by_name["write"]["search"]
    assert by_name["editorial_review"]["effort"] == "medium"
    assert by_name["edit"]["effort"] == "medium"
    assert by_name["fact_check"]["effort"] == "low"
    assert all("subagent_count" not in call for call in calls)
    assert by_name["editorial_review"]["context"]["source_plan"] == outputs["write"]["plan"]
    findings = by_name["edit"]["context"]["findings"]
    assert len(findings) == 1 and findings[0]["id"] == "editorial_review-1"
    assert (
        findings[0]["problem"]
        == outputs["editorial_review"]["findings"][0]["problem"]
    )
    assert "writing_references" not in by_name["research"]["context"]
    refs = by_name["write"]["context"]["writing_references"]
    assert set(refs) == {"writing_guide.md", "voice_examples"}
    assert sum(len(str(v)) for v in refs.values()) < 24000
    assert len(refs["voice_examples"]) == 8
    assert any(ex["id"] == "orient-insider-term" for ex in refs["voice_examples"])
    assert any(ex["id"] == "song-handoff-and-reentry" for ex in refs["voice_examples"])
    assert any(ex["id"] == "oral-beat-pacing" for ex in refs["voice_examples"])
    assert not any("runtime/" in str(x) for x in refs["voice_examples"])
    assert "host_system_instructions" not in refs
    assert result["status"] == "awaiting_manuscript_approval"
    assert len(result["jobs"]) == 5
    assert all(metrics["usage"] is None for metrics in result["jobs"].values())
    assert "1.5s of silence" in (project.documents / "manuscript.md").read_text()
    assert "\\<sigh\\>" in (project.documents / "manuscript.md").read_text()
    assert "Before:" in Path(result["report"]).read_text()
    tts.assert_not_called()
    assert not list(project.runtime.glob("**/*.mp3"))
    never = Mock(side_effect=AssertionError("Reuse all completed jobs"))
    assert editorial.edit_episode(project, runner=never) == result
    with pytest.raises(ValueError, match="not been approved"):
        service.prepare(project, Mock())
    approve_manuscript(project, result["manuscript_hash"])
    require_manuscript_approval(
        project, Plan.model_validate(read_json(project.documents / "plan.json"))
    )


def test_only_failed_review_retries_and_completed_stages_survive_resume(episode):
    project, outputs = episode
    calls = []
    ordinary = runner_for(outputs, calls)

    def failing(prompt, context, schema, attempt, **kwargs):
        if attempt.parent.name == "editorial_review":
            calls.append({"name": "failed-review"})
            raise RuntimeError("Temporary review failure")
        return ordinary(prompt, context, schema, attempt, **kwargs)

    with pytest.raises(RuntimeError, match="Temporary review failure"):
        editorial.edit_episode(project, runner=failing)
    assert len([c for c in calls if c["name"] == "failed-review"]) == 2
    assert not (project.documents / "plan.json").exists()
    directory = next((project.runtime / "editorial").iterdir())
    assert (directory / "research.json").exists() and (directory / "write.json").exists()
    retry_calls = []
    run_episode(project, outputs, retry_calls)
    assert [c["name"] for c in retry_calls] == [
        "editorial_review",
        "edit",
        "fact_check",
    ]
    assert len(list((directory / "editorial_review").glob("attempt-*"))) == 3


@pytest.mark.parametrize("factor", [0.1, 0.85, 2, 10])
def test_timing_estimates_are_advisory(episode, factor):
    project, outputs = episode
    proposal, brief, common = editorial._context(project, "")
    plan = Plan.model_validate(outputs["write"]["plan"])
    initial = editorial.validate_plan(plan, proposal, brief, common["recordings"])
    proposal.commentary_budget_seconds = initial["estimated_narration_seconds"] * factor
    proposal.target_seconds = initial["estimated_total_seconds"] * factor
    assert (
        editorial.validate_plan(plan, proposal, brief, common["recordings"]) == initial
    )


def test_edit_can_exceed_word_estimates_without_a_retry(episode):
    project, outputs = episode
    calls = []
    for passage in outputs["edit"]["passages"]:
        passage["script"] += " Una historia conecta canciones." * 70
    run_episode(project, outputs, calls)
    assert len(calls) == 5


def test_validation_repair_uses_only_failed_job_and_exact_error(episode):
    project, outputs = episode
    calls = []
    ordinary = runner_for(outputs, calls)
    failed = False

    def once(prompt, context, schema, attempt, **kwargs):
        nonlocal failed
        if attempt.parent.name == "edit" and not failed:
            failed = True
            result = ordinary(prompt, context, schema, attempt, **kwargs)
            result["resolutions"] = []
            return result
        return ordinary(prompt, context, schema, attempt, **kwargs)

    receipt = editorial.edit_episode(project, runner=once)
    assert len(calls) == 6
    edit_calls = [c for c in calls if c["name"] == "edit"]
    repair = edit_calls[-1]["context"]["retry_feedback"]
    assert "required finding" in repair["validation_error"]
    assert repair["previous_response"]["resolutions"] == []
    assert len(receipt["jobs"]) == 6


@pytest.mark.parametrize(
    "job,mutation,match",
    [
        (
            "write",
            lambda r: r["plan"]["segments"][1].update(title="Other song"),
            "Unapproved song",
        ),
        (
            "write",
            lambda r: r["plan"]["segments"][0].update(script=None, companion=None),
            "Complete manuscript",
        ),
        (
            "write",
            lambda r: r["plan"]["segments"].insert(
                1,
                {**deepcopy(r["plan"]["segments"][0]), "id": "extra", "role": "bridge"},
            ),
            "alternate commentary",
        ),
        (
            "write",
            lambda r: r["plan"]["segments"][2].update(sources=[]),
            "evidence required",
        ),
        (
            "write",
            lambda r: r["plan"]["segments"][2].update(script="Bad <invented> tag"),
            "Unsupported",
        ),
        (
            "editorial_review",
            lambda r: r.update(
                findings=[
                    {
                        "segment_ids": ["missing"],
                        "quote": "Bad",
                        "severity": "major",
                        "problem": "Unsupported",
                        "correction": "Remove",
                    }
                ]
            ),
            "unknown manuscript",
        ),
        (
            "editorial_review",
            lambda r: r.update(
                additional_sources=[
                    {"url": "https://example.com/invented", "claim": "Invented"}
                ]
            ),
            "cannot add research",
        ),
        ("edit", lambda r: r["passages"].pop(), "exactly the existing"),
        (
            "edit",
            lambda r: r["passages"].append(deepcopy(r["passages"][0])),
            "exactly the existing",
        ),
        (
            "edit",
            lambda r: r["passages"][0].update(segment_id="song"),
            "exactly the existing",
        ),
        (
            "edit",
            lambda r: r["passages"][0].update(
                companion={"title": "a" * 41, "summary": "Summary"}
            ),
            "40 characters",
        ),
        (
            "edit",
            lambda r: r["passages"][0].update(
                sources=[{"url": "https://example.com/new", "claim": "New"}]
            ),
            "outside the supplied evidence",
        ),
        (
            "edit",
            lambda r: r["listening_notes"].append(
                {"segment_id": "song", "note": "Not a host passage"}
            ),
            "unknown commentary",
        ),
        ("edit", lambda r: r.update(resolutions=[]), "required finding"),
        (
            "edit",
            lambda r: r["resolutions"].append(deepcopy(r["resolutions"][0])),
            "duplicate or unknown",
        ),
        (
            "edit",
            lambda r: r["resolutions"][0].update(finding_id="missing"),
            "duplicate or unknown",
        ),
        (
            "edit",
            lambda r: r["passages"][0].update(explanation="explanation " * 50),
            "repeated-token",
        ),
    ],
)
def test_invalid_output_cannot_install_a_manuscript(episode, job, mutation, match):
    project, outputs = episode
    mutation(outputs[job])
    with pytest.raises((ValueError, RuntimeError), match=match):
        run_episode(project, outputs)
    assert not (project.documents / "plan.json").exists()
    assert not (project.documents / "manuscript.md").exists()


@pytest.mark.parametrize("status", ["resolved", "dismissed", "unresolved"])
def test_every_finding_has_one_visible_resolution_and_blockers_prevent_approval(
    episode, status
):
    project, outputs = episode
    outputs["edit"]["resolutions"][0]["status"] = status
    receipt = run_episode(project, outputs)
    assert status in Path(receipt["report"]).read_text()
    assert (receipt["status"] == "blocked") == (status == "unresolved")
    if status == "unresolved":
        with pytest.raises(ValueError, match="unresolved editorial blockers"):
            approve_manuscript(project, receipt["manuscript_hash"])
    else:
        approve_manuscript(project, receipt["manuscript_hash"])


def test_writer_questions_join_same_ledger_and_routine_checks_are_saved_once(episode):
    project, outputs = episode
    outputs["write"]["open_questions"] = ["Who left first?"]
    outputs["write"]["production_notes"] = [
        "Owner should listen to the source recording."
    ]
    outputs["edit"]["resolutions"].append(
        {
            "finding_id": "research-question-1",
            "status": "unresolved",
            "explanation": "Evidence conflicts.",
        }
    )
    outputs["edit"]["production_notes"] = [
        "Owner should listen to the source recording."
    ]
    receipt = run_episode(project, outputs)
    assert len(receipt["blockers"]) == 1
    assert receipt["production_notes"].count(
        "Owner should listen to the source recording."
    ) == 1
    assert any(
        "Audition the selected recordings" in note
        for note in receipt["production_notes"]
    )
    ledger = read_json(Path(receipt["artifacts"]) / "findings.json")["findings"]
    assert ledger[-1]["id"] == "research-question-1"


def test_final_editor_can_apply_verified_factual_and_companion_corrections(episode):
    project, outputs = episode
    source = {
        "url": "https://example.com/verified",
        "claim": "The group chose a replacement.",
    }
    outputs["research"]["verified_sources"].append(source)
    outputs["edit"]["passages"][1].update(
        sources=[source],
        companion={"title": "Una decisión", "summary": "The replacement choice."},
    )
    receipt = run_episode(project, outputs)
    plan = read_json(project.documents / "plan.json")
    assert plan["segments"][2]["sources"] == [source]
    assert plan["segments"][2]["companion"]["title"] == "Una decisión"
    assert receipt["status"] == "awaiting_manuscript_approval"


def test_minor_editorial_review_findings_do_not_require_explicit_resolutions(episode):
    project, outputs = episode
    outputs["editorial_review"]["findings"][0]["severity"] = "minor"
    outputs["edit"]["resolutions"] = []
    receipt = run_episode(project, outputs)
    assert receipt["status"] == "awaiting_manuscript_approval"
    assert "noted" in Path(receipt["report"]).read_text()


def test_cache_tampering_and_concurrent_manual_changes_are_preserved(episode):
    project, outputs = episode
    receipt = run_episode(project, outputs)
    before = (project.documents / "plan.json").read_bytes()
    path = Path(receipt["artifacts"]) / "edit.json"
    saved = read_json(path)
    saved["output"]["passages"][0]["script"] += " Tampered."
    write_json(path, saved)
    with pytest.raises(ValueError, match="stage changed"):
        run_episode(project, outputs)
    assert (project.documents / "plan.json").read_bytes() == before


@pytest.mark.parametrize("filename", ["plan.json", "manuscript.md"])
def test_concurrent_manual_edits_are_not_overwritten(episode, filename):
    project, outputs = episode
    initial = run_episode(project, outputs)
    before = (project.documents / filename).read_bytes()
    ordinary = runner_for(outputs, [])

    def concurrent(prompt, context, schema, attempt, **kwargs):
        if attempt.parent.name == "edit":
            if filename == "plan.json":
                p = read_json(project.documents / filename)
                p["segments"][0]["script"] += " Owner edit."
                write_json(project.documents / filename, p)
            else:
                (project.documents / filename).write_text("Owner edit.")
        return ordinary(prompt, context, schema, attempt, **kwargs)

    with pytest.raises(ValueError, match="changed during editorial"):
        editorial.edit_episode(project, feedback="New direction", runner=concurrent)
    assert (project.documents / filename).read_bytes() != before
    assert read_json(project.runtime / "editorial_latest.json") == initial


def test_delivery_is_one_gemini_edit_preserves_metadata_and_reuses_evidence(episode):
    project, outputs = episode
    initial = run_episode(project, outputs)
    approve_manuscript(project, initial["manuscript_hash"])
    old = read_json(project.documents / "plan.json")
    before_markdown = (project.documents / "manuscript.md").read_bytes()
    approval = (project.runtime / "manuscript_approval.json").read_bytes()
    calls = []
    receipt = editorial.polish_delivery(
        project,
        feedback="Looser voice",
        runner=runner_for({"edit": delivery_edit(outputs)}, calls),
    )
    assert len(calls) == 1 and calls[0]["name"] == "edit"
    assert calls[0]["context"]["locked_metadata"] is True
    assert calls[0]["context"]["findings"] == []
    assert calls[0]["context"]["source_plan"] == old
    new = read_json(project.documents / "plan.json")
    for a, b in zip(old["segments"], new["segments"]):
        assert {k: v for k, v in a.items() if k != "script"} == {
            k: v for k, v in b.items() if k != "script"
        }
    directory = Path(receipt["artifacts"])
    assert read_json(directory / "previous_plan.json") == old
    assert (directory / "previous_manuscript.md").read_bytes() == before_markdown
    assert (project.runtime / "manuscript_approval.json").read_bytes() == approval
    assert receipt["manuscript_hash"] != initial["manuscript_hash"]
    never = Mock(side_effect=AssertionError("No extra requests"))
    assert (
        editorial.polish_delivery(project, feedback="Looser voice", runner=never)
        == receipt
    )
    with pytest.raises(ValueError, match="not been approved"):
        require_manuscript_approval(project, Plan.model_validate(new))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["passages"][0].update(
            companion={"title": "Changed", "summary": "Changed"}
        ),
        lambda r: r["passages"][0].update(sources=[]),
        lambda r: r["passages"][0].update(script="Bad <invented> tag"),
        lambda r: r["passages"].pop(),
    ],
)
def test_delivery_failure_preserves_working_documents(episode, mutation):
    project, outputs = episode
    run_episode(project, outputs)
    before = {
        name: (project.documents / name).read_bytes()
        for name in ("plan.json", "manuscript.md")
    }
    edit = delivery_edit(outputs)
    mutation(edit)
    with pytest.raises(ValueError):
        editorial.polish_delivery(project, runner=runner_for({"edit": edit}, []))
    assert all(
        (project.documents / name).read_bytes() == value
        for name, value in before.items()
    )


def test_delivery_cannot_clear_editorial_blockers_or_rewrite_fragmented_plan(episode):
    project, outputs = episode
    outputs["edit"]["open_questions"] = ["Unverified attribution."]
    run_episode(project, outputs)
    never = Mock(side_effect=AssertionError("Do not call provider"))
    with pytest.raises(ValueError, match="Resolve existing editorial blockers"):
        editorial.polish_delivery(project, runner=never)
    latest = read_json(project.runtime / "editorial_latest.json")
    latest["blockers"] = []
    write_json(project.runtime / "editorial_latest.json", latest)
    plan = read_json(project.documents / "plan.json")
    plan["segments"].insert(
        1, {**deepcopy(plan["segments"][0]), "id": "extra", "role": "bridge"}
    )
    write_json(project.documents / "plan.json", plan)
    with pytest.raises(ValueError, match="alternate commentary"):
        editorial.polish_delivery(project, runner=never)
    never.assert_not_called()


def test_cli_editorial_and_delivery_require_no_audio_credentials(
    episode, monkeypatch, capsys
):
    from neuralcast.cli import episode_producer as cli

    project, outputs = episode
    monkeypatch.setattr(cli, "Project", lambda *args: project)
    monkeypatch.setattr(
        cli,
        "load_azuracast_settings",
        Mock(side_effect=AssertionError("No audio credentials")),
    )
    monkeypatch.setattr(
        cli,
        "edit_episode",
        lambda project, **kwargs: run_episode(project, outputs, **kwargs),
    )
    monkeypatch.setattr(
        cli,
        "polish_delivery",
        lambda project, **kwargs: editorial.polish_delivery(
            project, runner=runner_for({"edit": delivery_edit(outputs)}, []), **kwargs
        ),
    )
    assert cli.main(["editorial", "-s", "neuralforge", "--episode", project.id]) == 0
    assert (
        json.loads(capsys.readouterr().out)["status"] == "awaiting_manuscript_approval"
    )
    assert (
        cli.main(
            [
                "delivery",
                "-s",
                "neuralforge",
                "--episode",
                project.id,
                "--feedback",
                "Looser voice",
            ]
        )
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["status"] == "awaiting_manuscript_approval"
    )


def test_added_production_note_does_not_trigger_another_delivery_request(episode):
    project, outputs = episode
    run_episode(project, outputs)
    result = delivery_edit(outputs)
    result["production_notes"] = ["Confirm the source edition before rendering."]
    receipt = editorial.polish_delivery(
        project, feedback="Looser", runner=runner_for({"edit": result}, [])
    )
    never = Mock(side_effect=AssertionError("Do not repeat accepted edit"))
    assert (
        editorial.polish_delivery(project, feedback="Looser", runner=never) == receipt
    )


def test_stored_delivery_identity_is_validated_before_reading_paths(episode):
    project, outputs = episode
    run_episode(project, outputs)
    receipt = editorial.polish_delivery(
        project, runner=runner_for({"edit": delivery_edit(outputs)}, [])
    )
    receipt["run_hash"] = "../outside"
    write_json(project.runtime / "editorial_latest.json", receipt)
    with pytest.raises(ValueError, match="Invalid stored delivery run"):
        editorial.polish_delivery(project, runner=Mock())


def test_agy_pipeline_auto_repairs_fact_drift_and_blocks_if_still_present(episode):
    project, outputs = episode
    outputs["research"]["production_notes"] = ["Audition the first track edition."]
    drift = {
        "findings": [
            {
                "segment_ids": ["decision"],
                "quote": "Después tuvieron que elegir un reemplazo.",
                "severity": "major",
                "problem": "Introduced an unsupported replacement claim.",
                "correction": "Remove the unsupported replacement claim.",
            }
        ]
    }
    outputs["fact_check"] = deepcopy(drift)
    outputs["fact_check_repair"] = {
        **deepcopy(outputs["edit"]),
        "resolutions": [
            {
                "finding_id": "fact_check-1",
                "status": "resolved",
                "explanation": "Removed unsupported replacement claim.",
            }
        ],
    }
    outputs["fact_check_recheck"] = deepcopy(drift)
    calls = []
    receipt = run_episode(project, outputs, calls)
    by_name = {c["name"]: c for c in calls}
    assert set(by_name) == {
        "research",
        "write",
        "editorial_review",
        "edit",
        "fact_check",
        "fact_check_repair",
        "fact_check_recheck",
    }
    assert "writing_references" not in by_name["research"]["context"]
    assert "research_dossier" in by_name["write"]["context"]
    assert "Audition the first track edition." in receipt["production_notes"]
    assert receipt["status"] == "blocked"
    assert any("fact_check-1" in b for b in receipt["blockers"])
    assert "Post-edit factual check" in Path(receipt["report"]).read_text()
    with pytest.raises(ValueError, match="unresolved editorial blockers"):
        approve_manuscript(project, receipt["manuscript_hash"])


def test_execution_retry_does_not_inject_empty_retry_feedback(episode):
    project, outputs = episode
    calls = []
    ordinary = runner_for(outputs, calls)
    failed = False

    def flaky(prompt, context, schema, attempt, **kwargs):
        nonlocal failed
        if attempt.parent.name == "editorial_review" and not failed:
            failed = True
            calls.append({"name": "review-fail", "context": deepcopy(context)})
            raise RuntimeError("Transient failure")
        return ordinary(prompt, context, schema, attempt, **kwargs)

    editorial.edit_episode(project, runner=flaky)
    review_retry = [c for c in calls if c["name"] == "editorial_review"][-1]
    assert "retry_feedback" not in review_retry["context"]


def test_prose_diagnostics_flag_crutches_and_walls_of_text(episode):
    project, outputs = episode
    long_wall = "Ahora bien, lo llamativo es que la banda cambió. " * 20
    outputs["write"]["plan"]["segments"][0]["script"] = long_wall
    calls = []
    run_episode(project, outputs, calls)
    by_name = {c["name"]: c for c in calls}
    review_hints = by_name["editorial_review"]["context"]["prose_diagnostics"]
    assert review_hints and review_hints[0]["segment_id"] == "intro"
    assert "ahora bien" in review_hints[0]["essay_crutches"]
    assert "lo llamativo es" in review_hints[0]["essay_crutches"]
    assert "paragraph breaks" in review_hints[0]["wall_of_text"]


def test_dossier_song_notes_validate_song_ids_and_urls(episode):
    project, outputs = episode
    outputs["research"]["song_notes"] = [
        {
            "song_id": "unknown-song",
            "creation_context": "Recorded live.",
            "verified_listening_cue": "Listen to the intro.",
            "post_song_bridge_hook": "Connect to next act.",
            "source_urls": ["https://example.com/history"],
        }
    ]
    with pytest.raises(ValueError, match="unapproved song IDs"):
        run_episode(project, outputs)


def test_revision_supports_agy_research(episode, monkeypatch):
    from neuralcast.pipelines.episode_producer import revision

    project, outputs = episode
    run_episode(project, outputs)
    seen = []

    def fake_agy(prompt, context, schema, attempt, **kwargs):
        seen.append((attempt.parent.name, kwargs.get("search", False), prompt))
        if attempt.parent.name == "feedback_research":
            return {
                "strengths": [],
                "findings": [],
                "additional_sources": [
                    {"url": "https://example.com/verified", "claim": "Confirmed."}
                ],
            }
        return {
            "passages": [
                {
                    "segment_id": s["id"],
                    "script": s["script"],
                    "explanation": "Kept.",
                    "companion": None,
                    "sources": None,
                }
                for s in context["source_plan"]["segments"]
                if s["kind"] == "commentary"
            ],
            "resolutions": [],
            "open_questions": [],
            "production_notes": [],
            "listening_notes": [],
        }

    revision.revise_manuscript(
        project,
        {"overall": "Verify fact", "sections": {}, "research": True},
        runner=fake_agy,
    )
    assert seen[0][0] == "feedback_research" and seen[0][1] is True
    assert seen[1][0] == "edit" and seen[1][1] is False
