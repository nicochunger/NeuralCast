"""Owner approvals, frozen revisions, private audio and episode admin jobs."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from neuralcast.admin_api.app import create_app
from neuralcast.admin_api.jobs import JobManager, build_job_command
from neuralcast.pipelines.episode_producer import (
    editorial,
    revision,
    workspace,
    publication,
)
from neuralcast.pipelines.episode_producer.workspace import Workspace, ConflictError
from neuralcast.pipelines.episode_producer.workspace_models import Feedback
from neuralcast.pipelines.episode_producer.editorial_models import EditorialBrief
from neuralcast.pipelines.episode_producer.models import Proposal, Plan, Music
from neuralcast.pipelines.episode_producer.storage import (
    read_json,
    write_json,
    digest,
    file_hash,
)
from neuralcast.pipelines.episode_producer.manuscript import manuscript_hash


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "catalogue_root", lambda: tmp_path / "catalogue")
    station = tmp_path / "station"
    (station / "playlists").mkdir(parents=True)
    (station / "songs" / "Classic Metal").mkdir(parents=True)
    (station / "playlist_descriptions.md").write_text(
        "Classic Metal: established metal artists."
    )
    (station / "playlists" / "Classic Metal.csv").write_text(
        "Artist,Title,Album,Year,Validated\nArtist,Song,Album,2000,True\n"
    )
    (station / "songs" / "Classic Metal" / "Artist - Song.mp3").write_bytes(
        b"library recording"
    )
    w = Workspace(
        "neuralforge", station_root=station, runtime_root=tmp_path / "episodes"
    )
    p = w.project("workspace-test")
    proposal = Proposal(
        episode_id=p.id,
        channel="neuralforge-es",
        title="A story",
        premise="A change in a band",
        target_seconds=600,
        commentary_budget_seconds=197,
        outline=["People", "Choices"],
    )
    song = Music(
        kind="music",
        id="song",
        playlist="Classic Metal",
        artist="Artist",
        title="Song",
        rationale="Part of the story",
    )
    brief = EditorialBrief(
        proposal_hash=digest(proposal.model_dump()),
        instructions="Use the full recording.",
        candidate_songs=[song],
    )
    write_json(
        p.documents / "proposal.json",
        {**proposal.model_dump(), "editorial_brief": brief.model_dump()},
    )
    parts = [
        dict(
            kind="commentary",
            id="opening",
            role="opening",
            brief="Begin",
            max_words=400,
            script="Una historia sigue con personas y decisiones. " * 20,
            companion={"title": "El comienzo", "summary": "Una historia."},
        ),
        song.model_dump(),
        dict(
            kind="commentary",
            id="closing",
            role="closing",
            brief="Close",
            max_words=400,
            script="La historia termina con decisiones y personas. " * 20,
            companion={"title": "El final", "summary": "El cierre."},
        ),
    ]
    plan = Plan(proposal_hash=brief.proposal_hash, segments=parts)
    monkeypatch.setattr(workspace, "audio_duration", lambda path: 400)
    monkeypatch.setattr(editorial, "audio_duration", lambda path: 400)
    monkeypatch.setattr(
        workspace,
        "episode_directory",
        lambda station, episode: tmp_path / "catalogue" / station / episode,
    )
    monkeypatch.setattr(
        editorial,
        "episode_directory",
        lambda station, episode: tmp_path / "catalogue" / station / episode,
    )
    return w, p, plan


def written(rig):
    w, p, plan = rig
    p.approve_proposal(p.concept_hash())
    write_json(p.documents / "plan.json", plan.model_dump())
    return w, p, plan


def test_queue_requires_exact_brief_and_retries_idempotently(rig):
    w, p, _ = rig
    request = w.submit("queue", episode_id=p.id, revision=w.revision(p))
    assert not (p.runtime / "proposal_approval.json").exists()
    assert w.execute(request) == {"queued": True}
    assert w.detail(p)["status"] == "queued"
    assert w.execute(request) == {"queued": True}


def test_changed_concept_cannot_be_approved_by_a_saved_request(rig):
    w, p, _ = rig
    request = w.submit("queue", episode_id=p.id, revision=w.revision(p))
    raw = read_json(p.documents / "proposal.json")
    raw["title"] = "A changed title"
    # Keep the brief valid but make this a different unapproved concept.
    changed = Proposal.model_validate(raw)
    raw["editorial_brief"]["proposal_hash"] = digest(changed.model_dump())
    write_json(p.documents / "proposal.json", raw)
    with pytest.raises(ConflictError, match="concept changed"):
        w.execute(request)
    assert not (p.runtime / "proposal_approval.json").exists()


def test_feedback_is_saved_with_optimistic_revision_checks(rig):
    w, p, _ = written(rig)
    original = w.revision(p)
    result = w.save_feedback(
        p.id,
        Feedback(
            revision=original,
            overall="Keep the story.",
            sections={"opening": "Start broader."},
        ),
    )
    assert result["revision"] != original
    assert result["feedback"]["sections"]["opening"] == "Start broader."
    with pytest.raises(ConflictError):
        w.save_feedback(p.id, Feedback(revision=original, overall="Stale feedback"))
    with pytest.raises(ValueError, match="unknown passage"):
        w.save_feedback(
            p.id, Feedback(revision=result["revision"], sections={"missing": "A note"})
        )


@pytest.mark.parametrize("research", [False, True])
def test_targeted_revision_reuses_the_current_script_and_preserves_music(
    rig, monkeypatch, research
):
    w, p, plan = written(rig)
    data = Feedback(
        revision=w.revision(p),
        overall="Make the beginning more conversational.",
        research=research,
    )
    d = w.save_feedback(p.id, data)
    request = w.submit("revise", episode_id=p.id, revision=d["revision"])
    calls = []

    def edit(prompt, context, schema, attempt, **kwargs):
        calls.append(context)
        return {
            "passages": [
                {
                    "segment_id": s.id,
                    "script": (
                        s.script + " Bueno, seguimos."
                        if s.id == "opening"
                        else s.script
                    ),
                    "explanation": "Owner feedback applied.",
                    "companion": None,
                    "sources": None,
                }
                for s in plan.segments
                if s.kind == "commentary"
            ],
            "resolutions": [],
            "open_questions": [],
            "production_notes": [],
            "listening_notes": [],
        }

    fact = Mock(
        return_value={"strengths": [], "findings": [], "additional_sources": []}
    )
    monkeypatch.setattr(revision.gemini, "run_stage", edit)
    monkeypatch.setattr(revision.codex, "run_stage", fact)
    result = w.execute(request)
    assert len(calls) == 1 and fact.call_count == int(research)
    assert calls[0]["source_plan"] == plan.model_dump()
    after = Plan.model_validate(read_json(p.documents / "plan.json"))
    assert after.segments[1] == plan.segments[1]
    assert after.segments[0].script.endswith("Bueno, seguimos.")
    assert after.segments[2].script == plan.segments[2].script
    assert result["status"] == "awaiting_manuscript_approval"
    # Simulate a lost final job acknowledgement; accepted editing is reused.
    (w.root / "requests" / request / "result.json").unlink()
    w.execute(request)
    assert len(calls) == 1 and fact.call_count == int(research)


def test_published_episode_is_readable_but_cannot_be_changed(rig):
    w, p, _ = written(rig)
    write_json(
        workspace.episode_directory(w.station, p.id) / "manifest.json",
        {"published": True},
    )
    assert w.detail(p)["status"] == "published"
    for action in ["queue", "write", "revise", "render", "publish"]:
        with pytest.raises(ValueError, match="immutable"):
            w.submit(action, episode_id=p.id, revision=w.revision(p))
    with pytest.raises(ValueError, match="immutable"):
        w.save_feedback(p.id, Feedback(revision=w.revision(p), overall="Change it"))


def test_render_request_cannot_approve_later_manuscript_edits(rig):
    w, p, plan = written(rig)
    request = w.submit("render", episode_id=p.id, revision=w.revision(p))
    plan.segments[0].script += " Changed."
    write_json(p.documents / "plan.json", plan.model_dump())
    with pytest.raises(ConflictError, match="manuscript changed"):
        w.execute(request)
    assert not (p.runtime / "manuscript_approval.json").exists()


def test_publication_requires_exact_current_render(rig):
    w, p, _ = written(rig)
    with pytest.raises(ValueError, match="current reviewed audio"):
        w.submit(
            "publish", episode_id=p.id, revision=w.revision(p), render_hash="a" * 64
        )


def test_weekly_waits_for_review_and_does_not_render(rig, monkeypatch):
    w, p, _ = written(rig)
    state = w.state()
    state.update(weekly_enabled=True, managed_episodes=[p.id])
    write_json(w.root / "workspace.json", state)
    never = Mock(
        side_effect=AssertionError("No generation while owner review is pending")
    )
    monkeypatch.setattr(w, "execute", never)
    assert "review" in w.weekly()["skipped"]
    never.assert_not_called()


def test_weekly_only_writes_approved_queued_briefs_once(rig, monkeypatch):
    w, p, _ = rig
    p.approve_proposal(p.concept_hash())
    state = w.state()
    state.update(
        weekly_enabled=True,
        managed_episodes=[p.id],
        ideas_week=datetime.now(timezone.utc).strftime("%G-W%V"),
    )
    write_json(w.root / "workspace.json", state)
    execute = Mock(return_value={"status": "awaiting_manuscript_approval"})
    monkeypatch.setattr(w, "execute", execute)
    assert w.weekly()["episode_id"] == p.id
    assert w.weekly() == {"ideas_ready": True}
    assert execute.call_count == 1
    saved = read_json(w.root / "requests" / execute.call_args.args[0] / "request.json")
    assert saved["action"] == "write" and saved["concept_hash"] == p.concept_hash()


def client(rig, tmp_path, monkeypatch):
    w, _, _ = rig
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_TOKEN", "test-token")
    manager = JobManager(
        base_dir=tmp_path / "admin",
        runner_launcher=lambda p: 1234,
        process_checker=lambda pid: True,
    )
    app = create_app(
        job_manager=manager,
        episodes_factory=lambda station: (
            w
            if station == "neuralforge"
            else Workspace(
                station,
                station_root=tmp_path / "other",
                runtime_root=tmp_path / "episodes",
            )
        ),
    )
    return TestClient(app), manager


def test_api_auth_saved_feedback_and_safe_job_command(rig, tmp_path, monkeypatch):
    w, p, _ = written(rig)
    http, manager = client(rig, tmp_path, monkeypatch)
    url = "/admin/episodes/neuralforge"
    assert http.get(url).status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    view = http.get(url, params={"episode_id": p.id}, headers=headers)
    assert (
        view.status_code == 200
        and view.json()["episode"]["status"] == "manuscript_review"
    )
    saved = http.put(
        url + "/" + p.id + "/feedback",
        json={"revision": w.revision(p), "overall": "More conversational."},
        headers=headers,
    )
    assert saved.status_code == 200
    result = http.post(
        url + "/actions",
        json={
            "action": "revise",
            "episode_id": p.id,
            "revision": saved.json()["revision"],
        },
        headers=headers,
    )
    assert result.status_code == 202
    job = manager.get_job(result.json()["job_id"])
    argv, env, _ = build_job_command(job, project_root=Path("/project"))
    assert env["PYTHONUNBUFFERED"] == "1"
    assert argv[-2:] == ["--request-id", result.json()["request_id"]]
    assert "neuralcast.cli.episode_workspace" in argv
    active = http.get(url, headers=headers).json()["job"]
    assert active["job_id"] == job.job_id
    assert active["progress"]["action"] == "revise"
    assert active["progress"]["episode_id"] == p.id
    assert (
        http.post(
            url + "/actions",
            json={"action": "render", "episode_id": "../escape", "revision": "a" * 64},
            headers=headers,
        ).status_code
        == 422
    )


def test_preview_audio_is_authenticated_seekable_and_bound_to_receipt(
    rig, tmp_path, monkeypatch
):
    w, p, _ = written(rig)
    revision_id = "b" * 64
    audio = p.runtime / revision_id / "episode.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"0123456789")
    write_json(
        audio.parent / "production.json",
        {
            "station": w.station,
            "episode_id": p.id,
            "revision": revision_id,
            "render_hash": file_hash(audio),
        },
    )
    http, _ = client(rig, tmp_path, monkeypatch)
    url = f"/admin/episodes/{w.station}/{p.id}/audio/{revision_id}"
    assert http.get(url).status_code == 401
    response = http.get(
        url, headers={"Authorization": "Bearer test-token", "Range": "bytes=2-5"}
    )
    assert response.status_code == 206 and response.content == b"2345"
    assert response.headers["cache-control"] == "private, no-store"
    audio.write_bytes(b"changed")
    assert (
        http.get(url, headers={"Authorization": "Bearer test-token"}).status_code == 422
    )


def test_topics_concept_and_full_editorial_generation_share_saved_approvals(
    rig, monkeypatch
):
    w, original, template = rig
    calls = []

    def topics(prompt, context, schema, attempt, **kwargs):
        calls.append("ideas")
        return {
            "ideas": [
                {
                    "title": "A voice changes",
                    "premise": "How a band adapts",
                    "angle": "People and their first songs",
                    "artists": ["Artist"],
                }
            ]
        }

    def concept(prompt, context, schema, attempt, **kwargs):
        calls.append("concept")
        assert context["catalogue"][0]["artist"] == "Artist"
        return {
            "title": "A voice changes",
            "premise": "How a band adapts",
            "outline": ["Before the change", "Working together"],
            "instructions": "Use the complete recording in order.",
            "candidate_songs": [template.segments[1].model_dump()],
            "initial_research": [],
        }

    monkeypatch.setattr(workspace.gemini, "run_stage", topics)
    monkeypatch.setattr(workspace.codex, "run_stage", concept)
    request = w.submit("ideas", revision=digest(w.state()), target_minutes=10)
    assert w.execute(request)["ideas"][0]["title"] == "A voice changes"
    assert w.execute(request)["ideas"][0]["title"] == "A voice changes"
    request = w.submit(
        "develop",
        revision=digest(w.state()),
        topic="A voice changes",
        target_minutes=10,
    )
    result = w.execute(request)
    p = w.project(result["episode_id"])
    assert w.detail(p)["status"] == "brief_review"
    assert not (p.runtime / "proposal_approval.json").exists()
    assert calls == ["ideas", "concept"]

    def stage(prompt, context, schema, attempt, **kwargs):
        name = attempt.parent.name
        calls.append(name)
        current = template.model_copy(
            update={"proposal_hash": p.editorial_brief().proposal_hash}
        )
        if name == "write":
            return {
                "plan": current.model_dump(),
                "research_notes": "Fixture research.",
                "open_questions": [],
                "production_notes": [],
            }
        if name == "edit":
            return {
                "passages": [
                    {
                        "segment_id": s.id,
                        "script": s.script,
                        "explanation": "Retained the story.",
                        "companion": None,
                        "sources": None,
                    }
                    for s in current.segments
                    if s.kind == "commentary"
                ],
                "resolutions": [],
                "open_questions": [],
                "production_notes": [],
                "listening_notes": [],
            }
        return {"strengths": [], "findings": [], "additional_sources": []}

    real_editorial = editorial.edit_episode
    monkeypatch.setattr(
        editorial,
        "edit_episode",
        lambda project, **kwargs: real_editorial(
            project, runner=stage, text_runner=stage, **kwargs
        ),
    )
    request = w.submit("write", episode_id=p.id, revision=w.revision(p))
    response = w.execute(request)
    assert response["status"] == "awaiting_manuscript_approval"
    assert w.detail(p)["status"] == "manuscript_review"
    assert w.detail(p)["changes"]
    assert p.id in w.state()["managed_episodes"]
    assert len(calls) == 7  # ideas, concept and the five existing editorial jobs.
    assert not (p.runtime / "manuscript_approval.json").exists()


def test_render_and_publish_use_owner_reviewed_hashes(rig, monkeypatch):
    w, p, plan = written(rig)

    def render(project, client, *, progress):
        assert read_json(project.runtime / "manuscript_approval.json")[
            "manuscript_hash"
        ] == manuscript_hash(project, plan)
        revision_id = "d" * 64
        out = project.runtime / revision_id / "episode.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"private audio")
        from neuralcast.pipelines.host_orchestrator.speech import (
            validate_speech_transcript,
        )

        chapters = [
            {
                "id": s.id,
                "kind": s.kind,
                "title": s.companion.title if s.kind == "commentary" else s.title,
                "transcript": (
                    validate_speech_transcript(s.script).plain_text
                    if s.kind == "commentary"
                    else ""
                ),
                "start": i * 100,
                "end": (i + 1) * 100,
            }
            for i, s in enumerate(plan.segments)
        ]
        receipt = {
            "station": w.station,
            "episode_id": p.id,
            "revision": revision_id,
            "render_hash": file_hash(out),
            "duration": 300,
            "chapters": chapters,
            "uploaded": {"media_id": "1"},
        }
        write_json(out.parent / "production.json", receipt)
        write_json(
            project.runtime / "latest.json",
            {"revision": revision_id, "render_hash": receipt["render_hash"]},
        )
        return receipt

    monkeypatch.setattr(workspace, "prepare", render)
    monkeypatch.setenv("AZURACAST_BASE_URL", "https://example.test")
    monkeypatch.setenv("AZURACAST_API_KEY", "offline-test-key")
    request = w.submit("render", episode_id=p.id, revision=w.revision(p))
    w.execute(request)
    d = w.detail(p)
    assert d["status"] == "audio_review" and d["audio"]["current"]
    with pytest.raises(ValueError, match="current reviewed audio"):
        w.submit(
            "publish", episode_id=p.id, revision=d["revision"], render_hash="f" * 64
        )
    publisher = Mock(return_value={"id": p.id, "status": "published"})
    monkeypatch.setattr(workspace, "publish", publisher)
    request = w.submit(
        "publish",
        episode_id=p.id,
        revision=d["revision"],
        render_hash=d["audio"]["render_hash"],
        description="The reviewed description.",
    )
    w.execute(request)
    assert publisher.call_args.args[0].id == p.id
    assert publisher.call_args.args[1:] == (
        d["audio"]["render_hash"],
        "The reviewed description.",
    )
    assert p.episode_number == 1


def test_saved_manuscript_history_supports_timestamped_snapshots(
    rig, tmp_path, monkeypatch
):
    w, p, plan = written(rig)
    write_json(
        p.runtime / "revisions" / "20261102T123542Z" / "final_plan.json",
        plan.model_dump(),
    )
    http, _ = client(rig, tmp_path, monkeypatch)
    response = http.get(
        f"/admin/episodes/{w.station}/{p.id}/history/revisions/20261102T123542Z",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["segments"] == plan.model_dump()["segments"]


def test_live_activity_reads_buffered_stage_files_without_mutating_the_job(rig):
    from neuralcast.admin_api.episode_activity import episode_activity

    w, p, _ = rig
    request = w.submit("write", episode_id=p.id, revision=w.revision(p))
    request_path = w.root / "requests" / request / "request.json"
    original = request_path.read_bytes()
    directory = p.runtime / "editorial" / "active-run"
    write_json(
        directory / "inputs.json", {"context": {"concept_hash": p.concept_hash()}}
    )
    write_json(directory / "write.json", {"output": {}})
    write_json(directory / "narrative_continuity.json", {"output": {}})
    write_json(directory / "factual_support" / "attempt-001" / "context.json", {})
    write_json(directory / "conversational_voice" / "attempt-001" / "context.json", {})
    job = {
        "status": "running",
        "episode_options": {"request_id": request},
        "log_tail": "Episode workspace: write",
    }
    progress = episode_activity(w, job)
    assert progress["action"] == "write" and progress["episode_id"] == p.id
    steps = {s["id"]: s["status"] for s in progress["steps"]}
    assert steps == {
        "write": "succeeded",
        "factual_support": "running",
        "narrative_continuity": "succeeded",
        "conversational_voice": "running",
        "edit": "pending",
    }
    assert request_path.read_bytes() == original and job["status"] == "running"
    job.update(
        status="failed",
        log_tail="Episode workspace failed: Review factual_support failed; completed jobs were retained.",
    )
    progress = episode_activity(w, job)
    assert "Fact review" in progress["current"] and "retained" in progress["error"]
    assert (
        next(s for s in progress["steps"] if s["id"] == "write")["status"]
        == "succeeded"
    )


@pytest.mark.parametrize(
    "action",
    ["ideas", "develop", "queue", "write", "revise", "render", "publish", "weekly"],
)
def test_activity_describes_each_background_action(rig, action):
    from neuralcast.admin_api.episode_activity import episode_activity

    w, p, _ = rig
    request = "fixture-operation"
    directory = w.root / "requests" / request
    write_json(
        directory / "request.json",
        {
            "action": action,
            "episode_id": p.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "concept_hash": p.concept_hash(),
            "feedback": {"research": True},
        },
    )
    job = {
        "status": "running",
        "episode_options": {"request_id": request},
        "log_tail": (
            "Episode production: narration\nEpisode production: mix"
            if action == "render"
            else ""
        ),
    }
    progress = episode_activity(w, job)
    assert progress["steps"] and progress["episode_id"] == p.id
    if action == "develop":
        assert progress["action"] == "refine_brief"
    if action == "render":
        assert [s["status"] for s in progress["steps"]] == [
            "succeeded",
            "running",
            "pending",
        ]
    job["status"] = "succeeded"
    assert all(s["status"] == "succeeded" for s in episode_activity(w, job)["steps"])
