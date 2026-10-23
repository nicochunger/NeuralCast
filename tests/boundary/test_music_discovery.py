"""Offline owner-review workflow, retry safety, and private API boundaries."""

from pathlib import Path
import csv
import json

from fastapi.testclient import TestClient
import pytest

from neuralcast.admin_api.app import create_app
from neuralcast.admin_api.jobs import JobManager, build_job_command
from neuralcast.pipelines.music_discovery import media
from neuralcast.pipelines.music_discovery.codex import run_agent
from neuralcast.pipelines.music_discovery.models import (
    FeedbackDraft,
    FeedbackResult,
    Suggestions,
)
from neuralcast.pipelines.music_discovery.service import DiscoveryService
from neuralcast.pipelines.music_discovery.storage import (
    ConflictError,
    atomic_json,
    file_hash,
)
from neuralcast.services import editorial_agent


def suggestions():
    return {
        "candidates": [
            dict(
                id=f"song-{i}",
                artist=f"New Artist {i}",
                title=f"Song {i}",
                album=f"Album {i}",
                year="2026",
                playlist="Folk Metal",
                rationale="Folk melody and communal choruses.",
                uncertainty="Owner listening determines fit.",
                source_url=f"https://youtu.be/abcdefghij{i}",
                research_urls=[f"https://example.com/artist-{i}"],
            )
            for i in range(5)
        ]
    }


@pytest.fixture
def rig(tmp_path):
    station = tmp_path / "NeuralForge"
    (station / "playlists").mkdir(parents=True)
    (station / "songs").mkdir()
    (station / "playlist_descriptions.md").write_text(
        "Folk Metal: melody, communal choruses; harsh vocals welcome."
    )
    (station / "playlists" / "Folk Metal.csv").write_text(
        "Artist,Title,Album,Year,Validated,OwnerNote\nKnown Artist,Existing,Old Album,2000,True,Preserve me\n"
    )
    (station / "playlists" / "New Releases.csv").write_text(
        "Artist,Title,Album,Year,Validated\n"
    )
    calls = []
    downloads = []

    def agent(stage, context, attempt):
        calls.append((stage, context))
        if stage == "discovery":
            return suggestions()
        return {
            "placements": [
                dict(candidate_id=key, playlist="Folk Metal")
                for key, value in context["feedback"].items()
                if value["accepted"]
            ],
            "preference_updates": [
                dict(
                    candidate_ids=["song-1"],
                    scope="station",
                    text="Enjoys engaging folk melodies with harsh vocals.",
                )
            ],
        }

    def downloader(candidate, path):
        downloads.append(candidate.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"Audition recording {candidate.id}".encode())

    service = DiscoveryService(
        "neuralforge",
        root=tmp_path / "auditions",
        station_dir=station,
        maintenance_lock=tmp_path / "maintenance.lock",
        agent=agent,
        downloader=downloader,
    )
    return service, calls, downloads


def review(service, accepted=(1, 3)):
    batch = service.store.current()
    return service.save_feedback(
        FeedbackDraft(
            batch_id=batch.id,
            revision=batch.revision,
            feedback={
                f"song-{i}": dict(
                    accepted=i in accepted,
                    reasoning="Engaging melody, even with harsh vocals.",
                )
                for i in range(5)
            },
        )
    )


def test_generate_downloads_five_and_never_touches_catalog_or_live_media(rig):
    service, calls, downloads = rig
    before = (service.station_dir / "playlists" / "Folk Metal.csv").read_bytes()
    result = service.generate(scheduled=True)
    assert result["skipped"] is False
    batch = service.store.current()
    assert batch.status == "review" and len(batch.auditions) == 5
    assert len(downloads) == 5 and [stage for stage, _ in calls] == ["discovery"]
    assert not list((service.station_dir / "songs").rglob("*.mp3"))
    assert (service.station_dir / "playlists" / "Folk Metal.csv").read_bytes() == before
    assert service.generate()["skipped"] is True
    assert service.generate(scheduled=True)["skipped"] is True
    assert len(calls) == 1 and len(downloads) == 5


def test_feedback_only_promotes_explicit_yes_reuses_audio_and_learns_once(rig):
    service, calls, downloads = rig
    service.generate()
    batch = review(service)
    expected = {
        c.id: service.store.verified_audio(batch, c.id).read_bytes()
        for c in batch.candidates
    }
    service.submit(batch.id, batch.revision)
    assert service.generate()["skipped"] is True
    service.process(batch.id)
    assert [stage for stage, _ in calls] == ["discovery", "feedback"]
    assert len(downloads) == 5  # No wider discovery or downloads during feedback.
    path = service.station_dir / "playlists" / "Folk Metal.csv"
    rows = list(csv.DictReader(path.open()))
    assert [row["Artist"] for row in rows] == [
        "Known Artist",
        "New Artist 1",
        "New Artist 3",
    ]
    assert rows[0]["OwnerNote"] == "Preserve me"
    assert all(row["Validated"] == "False" for row in rows[1:])
    files = list((service.station_dir / "songs").rglob("*.mp3"))
    assert len(files) == 2
    assert all(path.stat().st_mode & 0o444 == 0o444 for path in files)
    assert {path.read_bytes() for path in files} == {
        expected["song-1"],
        expected["song-3"],
    }
    assert not list(service.store.root.rglob("*.mp3"))
    assert len(service.store.state().preferences) == 1
    assert service.process(batch.id)["already_processed"] is True
    assert len(service.store.state().preferences) == 1
    assert len(calls) == 2 and len(downloads) == 5
    assert service.generate(scheduled=True)["skipped"] is True


def test_draft_revision_and_partial_review_are_guarded(rig):
    service, _, _ = rig
    service.generate()
    batch = service.store.current()
    with pytest.raises(ValueError, match="all five"):
        service.submit(batch.id, batch.revision)
    draft = FeedbackDraft(
        batch_id=batch.id,
        revision=0,
        feedback={"song-0": dict(accepted=False, reasoning="Too repetitive")},
    )
    service.save_feedback(draft)
    with pytest.raises(ConflictError, match="changed"):
        service.save_feedback(draft)
    batch = review(service)
    service.submit(batch.id, batch.revision)
    with pytest.raises(ConflictError):
        service.save_feedback(
            FeedbackDraft(batch_id=batch.id, revision=batch.revision, feedback={})
        )


def test_failed_download_resumes_same_batch_without_research_or_redownload(rig):
    service, calls, downloads = rig
    original = service.downloader

    def failing(candidate, path):
        if candidate.id == "song-2":
            raise RuntimeError("Provider unavailable")
        original(candidate, path)

    service.downloader = failing
    with pytest.raises(RuntimeError):
        service.generate()
    batch_id = service.store.current().id
    assert service.store.current().status == "preparing"
    service.downloader = original
    service.generate()
    assert service.store.current().id == batch_id
    assert len(calls) == 1
    assert downloads == [f"song-{i}" for i in range(5)]


def test_partial_catalog_failure_resumes_without_extra_agent_or_duplicate_rows(
    rig, monkeypatch
):
    service, calls, downloads = rig
    service.generate()
    batch = review(service)
    service.submit(batch.id, batch.revision)
    original = media.promote
    count = 0

    def flaky(source, target, expected_hash):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("Disk temporarily unavailable")
        original(source, target, expected_hash)

    monkeypatch.setattr(media, "promote", flaky)
    with pytest.raises(OSError):
        service.process(batch.id)
    assert service.store.current().promoted == ["song-1"]
    monkeypatch.setattr(media, "promote", original)
    service.process(batch.id)
    rows = list(
        csv.DictReader((service.station_dir / "playlists" / "Folk Metal.csv").open())
    )
    assert len(rows) == 3
    assert len(calls) == 2 and len(downloads) == 5


def test_all_rejected_changes_no_catalog_or_station_media(rig):
    service, _, _ = rig
    before = (service.station_dir / "playlists" / "Folk Metal.csv").read_bytes()
    service.generate()
    batch = review(service, accepted=())
    service.submit(batch.id, batch.revision)
    service.process(batch.id)
    assert (service.station_dir / "playlists" / "Folk Metal.csv").read_bytes() == before
    assert not list((service.station_dir / "songs").rglob("*.mp3"))


@pytest.mark.parametrize(
    "change",
    ["extra", "rejected", "missing", "duplicate", "playlist", "broad_without_reason"],
)
def test_agent_cannot_expand_or_override_owner_choices(rig, change):
    service, _, _ = rig
    service.generate()
    batch = review(service)
    if change == "broad_without_reason":
        batch.feedback["song-1"].reasoning = ""
        service.store.save_batch(batch)
    service.submit(batch.id, batch.revision)
    original = service.agent

    def invalid(stage, context, attempt):
        result = original(stage, context, attempt)
        if change == "extra":
            result["placements"].append(
                dict(candidate_id="new-song", playlist="Folk Metal")
            )
        elif change == "rejected":
            result["placements"][0]["candidate_id"] = "song-0"
        elif change == "missing":
            result["placements"].pop()
        elif change == "duplicate":
            result["placements"].append(result["placements"][0])
        elif change == "playlist":
            result["placements"][0]["playlist"] = "New Releases"
        return result

    service.agent = invalid
    with pytest.raises(ValueError):
        service.process(batch.id)
    assert not list((service.station_dir / "songs").rglob("*.mp3"))
    assert service.store.state().preferences == []


def test_conflicting_live_file_is_not_overwritten_or_deleted(rig):
    service, _, _ = rig
    service.generate()
    batch = review(service)
    service.submit(batch.id, batch.revision)
    path = service.station_dir / "songs" / "Folk Metal" / "New Artist 3 - Song 3.mp3"
    path.parent.mkdir()
    path.write_bytes(b"existing authoritative recording")
    with pytest.raises(ConflictError):
        service.process(batch.id)
    assert path.read_bytes() == b"existing authoritative recording"
    assert len(list(path.parent.glob("*.mp3"))) == 1  # Full preflight before additions.


def test_changed_audition_and_frozen_feedback_fail_closed(rig):
    service, _, _ = rig
    service.generate()
    batch = review(service)
    path = service.store.audio_path(batch, "song-1")
    original = path.read_bytes()
    path.write_bytes(b"Changed preview")
    with pytest.raises(ValueError, match="changed"):
        service.submit(batch.id, batch.revision)
    path.write_bytes(original)
    batch = service.submit(batch.id, batch.revision)
    batch.feedback["song-1"].accepted = False
    service.store.save_batch(batch)
    with pytest.raises(ConflictError, match="altered"):
        service.process(batch.id)


@pytest.mark.parametrize(
    "kind", ["existing", "repeated", "unsafe_url", "unknown_playlist"]
)
def test_invalid_discovery_never_downloads(rig, kind):
    service, _, downloads = rig
    payload = suggestions()
    if kind == "existing":
        payload["candidates"][0].update(artist="Known Artist", title="Existing")
    elif kind == "repeated":
        payload["candidates"][1] = payload["candidates"][0]
    elif kind == "unsafe_url":
        payload["candidates"][0]["source_url"] = "http://127.0.0.1/secrets"
    else:
        payload["candidates"][0]["playlist"] = "../elsewhere"
    service.agent = lambda *_: payload
    with pytest.raises(ValueError):
        service.generate()
    assert downloads == []


def test_agent_stages_use_separate_search_modes_and_strict_schemas(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        editorial_agent,
        "run_stage",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {},
    )
    run_agent("discovery", {}, tmp_path / "research")
    run_agent("feedback", {}, tmp_path / "feedback")
    assert calls[0][1]["search"] is True
    assert calls[1][1]["search"] is False
    assert (
        calls[0][1]["reasoning_effort"] == calls[1][1]["reasoning_effort"] == "medium"
    )
    assert "Do not search" in calls[1][0][0]
    assert calls[0][0][2]["additionalProperties"] is False


def test_api_requires_auth_and_streams_ranges_without_exposing_other_files(
    rig, tmp_path, monkeypatch
):
    service, _, _ = rig
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_TOKEN", "test-token")
    manager = JobManager(
        base_dir=tmp_path / "jobs",
        runner_launcher=lambda _: 123,
        process_checker=lambda _: True,
    )
    client = TestClient(
        create_app(
            job_manager=manager,
            discovery_factory=lambda station: (
                service
                if station == "neuralforge"
                else DiscoveryService(station, root=tmp_path / "other")
            ),
        )
    )
    assert client.get("/admin/discovery/neuralforge").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.get("/admin/discovery/unknown", headers=headers).status_code == 404
    service.generate()
    view = client.get("/admin/discovery/neuralforge", headers=headers)
    assert view.status_code == 200 and view.headers["cache-control"] == "no-store"
    batch = service.store.current()
    audio = f"/admin/discovery/neuralforge/batches/{batch.id}/audio/song-0"
    assert client.get(audio).status_code == 401
    response = client.get(audio, headers={**headers, "Range": "bytes=0-7"})
    assert response.status_code == 206 and response.content == b"Audition"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.head(audio, headers=headers).status_code == 200
    assert client.get(audio + "-unknown", headers=headers).status_code == 422
    assert (
        client.post(
            "/admin/discovery/neuralforge/generate", headers=headers
        ).status_code
        == 409
    )
    batch = review(service)
    response = client.post(
        "/admin/discovery/neuralforge/process",
        headers=headers,
        json={"batch_id": batch.id, "revision": batch.revision},
    )
    assert response.status_code == 202
    job = manager.get_job(response.json()["job_id"])
    command, _, _ = build_job_command(job)
    assert "process" in command and "generate" not in command
    assert command[-1] == batch.id
    assert service.store.current().status == "submitted"
    assert (
        client.put(
            "/admin/discovery/neuralforge/feedback",
            headers=headers,
            json={"batch_id": batch.id, "revision": batch.revision, "feedback": {}},
        ).status_code
        == 409
    )
    assert (
        client.get("/public/episodes/neuralforge/" + batch.id + "/audio").status_code
        == 404
    )


def test_station_lock_prevents_concurrent_generation(rig):
    service, calls, _ = rig
    with service.store.lock():
        with pytest.raises(ConflictError):
            service.generate()
    assert calls == []


def test_artist_context_is_optional_for_old_batches_and_round_trips(rig):
    service, _, _ = rig
    payload = suggestions()
    payload["candidates"][0].update(
        artist_background="A melodic metal band from Oslo, Norway, formed in 2012.",
        listening_note="The source may be a live recording.",
    )
    service.agent = lambda *_: payload
    service.generate()
    batch = service.store.current()
    assert batch.candidates[0].artist_background.startswith("A melodic metal band")
    assert batch.candidates[0].listening_note == "The source may be a live recording."
    assert batch.candidates[1].artist_background == ""
    assert batch.candidates[1].listening_note == ""


def test_private_artwork_survives_audio_cleanup_and_rejects_unsafe_content(
    rig, tmp_path, monkeypatch
):
    from mutagen.id3 import APIC, ID3

    service, _, _ = rig
    # ID3-only files suffice at this mocked download boundary.
    image = b"\x89PNG\r\n\x1a\ncover-data"

    def download_with_cover(candidate, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tags = ID3()
        tags.add(APIC(mime="image/png", type=3, data=image))
        tags.save(path)

    service.downloader = download_with_cover
    service.generate()
    batch = service.store.current()
    original_hash = batch.auditions["song-0"].sha256
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_TOKEN", "test-token")
    client = TestClient(
        create_app(
            job_manager=JobManager(base_dir=tmp_path / "jobs"),
            discovery_factory=lambda station: service,
        )
    )
    headers = {"Authorization": "Bearer test-token"}
    url = f"/admin/discovery/neuralforge/batches/{batch.id}/artwork/song-0"
    assert client.get(url).status_code == 401
    response = client.get(url, headers=headers)
    assert response.status_code == 200 and response.content == image
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    head = client.head(url, headers=headers)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(image))
    assert client.get(url + "-unknown", headers=headers).status_code == 422
    assert file_hash(service.store.verified_audio(batch, "song-0")) == original_hash
    batch = review(service)
    service.submit(batch.id, batch.revision)
    service.process(batch.id)
    assert not service.store.audio_path(batch, "song-0").exists()
    assert client.get(url, headers=headers).content == image
    path = service.store.artwork_path(batch, "song-0")
    path.write_bytes(b"<svg onload='alert(1)' />")
    assert client.get(url, headers=headers).status_code == 404
    path.unlink()
    path.symlink_to(tmp_path / "private-file")
    assert client.get(url, headers=headers).status_code == 404


def test_missing_or_unsupported_embedded_art_is_optional(rig):
    from mutagen.id3 import APIC, ID3

    service, _, _ = rig

    def download_without_usable_cover(candidate, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tags = ID3()
        tags.add(APIC(mime="image/svg+xml", type=3, data=b"<svg />"))
        tags.save(path)

    service.downloader = download_without_usable_cover
    service.generate()
    batch = service.store.current()
    assert batch.status == "review"
    assert not service.store.artwork_path(batch, "song-0").exists()


def test_processing_waits_for_catalog_maintenance_without_losing_frozen_plan(rig):
    from neuralcast.pipelines.music_discovery.storage import exclusive_lock

    service, calls, _ = rig
    service.generate()
    batch = review(service)
    service.submit(batch.id, batch.revision)
    with exclusive_lock(service.maintenance_lock):
        with pytest.raises(ConflictError):
            service.process(batch.id)
    assert service.store.current().editorial is not None
    assert not list((service.station_dir / "songs").rglob("*.mp3"))
    service.process(batch.id)
    assert len(calls) == 2


def test_station_reviews_and_audio_are_isolated(rig):
    service, _, _ = rig
    service.generate()
    other = DiscoveryService("neuralcast", root=service.store.root.parent)
    assert other.store.current() is None
    with pytest.raises(FileNotFoundError):
        other.store.load_batch(service.store.current().id)


def test_launch_failure_does_not_leave_an_unretryable_admin_job(tmp_path):
    def failure(_):
        raise OSError("Cannot start worker")

    manager = JobManager(base_dir=tmp_path, runner_launcher=failure)
    with pytest.raises(OSError):
        manager.enqueue_discovery(station="neuralforge", action="generate")
    assert manager.latest_discovery_job("neuralforge")["status"] == "failed"
    manager._runner_launcher = lambda _: 123
    assert (
        manager.enqueue_discovery(station="neuralforge", action="generate").status
        == "running"
    )
