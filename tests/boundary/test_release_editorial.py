"""Offline editorial decisions, persistence, and playlist-local retirement."""

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from neuralcast.pipelines.new_releases import editorial, migration, retention, storage
from neuralcast.pipelines.new_releases.models import ArtistRelease
from neuralcast.pipelines.new_releases.runtime import (
    NewReleasesRequest,
    NewReleasesRuntime,
    release_update_lock,
)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    playlists = tmp_path / "Station" / "playlists"
    playlists.mkdir(parents=True)
    (playlists.parent / "playlist_descriptions.md").write_text(
        "### Folk Metal\nFolk-driven songs.\n### Power Metal\nMelodic metal."
    )
    for name in ("Folk Metal", "Power Metal"):
        (playlists / f"{name}.csv").write_text("Artist,Title,Album,Year,Validated\n")
    releases = [
        ArtistRelease(
            "Band",
            f"Song {index}",
            2025,
            "Album",
            datetime(2025, 1, 1, tzinfo=UTC),
            str(index),
            album_type="album",
        )
        for index in range(5)
    ]
    storage.save_new_releases(playlists, releases, dry_run=False)
    source = playlists.parent / "songs" / "New Releases"
    source.mkdir(parents=True)
    for release in releases:
        (source / f"Band - {release.title}.mp3").write_bytes(b"test audio")
    monkeypatch.setattr(migration, "_promote_release_album", lambda release: False)
    monkeypatch.setattr(migration, "tag_mp3", lambda *args, **kwargs: None)
    return playlists, releases


def output_for(context, discard=1):
    return {
        "decisions": [
            {
                "track_key": item["track_key"],
                "action": "discard" if index < discard else "keep",
                "destination": None if index < discard else "Folk Metal",
                "reason": (
                    "Weakest fit" if index < discard else "Distinctive folk character"
                ),
                "sources": ["https://example.com/album"],
            }
            for index, item in enumerate(context["candidates"])
        ]
    }


def install_reviewer(monkeypatch, reviewer):
    original = editorial.review_releases
    monkeypatch.setattr(
        retention,
        "review_releases",
        lambda *args, **kwargs: original(*args, reviewer=reviewer, **kwargs),
    )


def test_weekly_flow_retires_only_source_and_preserves_permanent_copy(
    catalog, monkeypatch
):
    playlists, releases = catalog
    permanent = playlists.parent / "songs" / "Power Metal"
    permanent.mkdir()
    protected = permanent / "Band - Song 0.mp3"
    protected.write_bytes(b"existing permanent audio")
    (playlists / "Power Metal.csv").write_text(
        "Artist,Title,Album,Year,Validated\nBand,Song 0,Album,2025,True\n"
    )
    install_reviewer(monkeypatch, output_for)
    from neuralcast.pipelines.new_releases import runtime

    monkeypatch.setattr(
        runtime, "default_station_paths", lambda station: (playlists.parent, playlists)
    )
    monkeypatch.setattr(runtime, "build_new_releases", lambda *args, **kwargs: [])
    result = NewReleasesRuntime().run(NewReleasesRequest(station="neuralforge"))
    assert result.final_releases == []
    assert storage.load_existing_new_releases(playlists) == []
    assert protected.read_bytes() == b"existing permanent audio"
    assert "Band,Song 0" in (playlists / "Power Metal.csv").read_text()
    assert not list((playlists.parent / "songs" / "New Releases").iterdir())
    assert len(list((playlists.parent / "songs" / "Folk Metal").glob("*.mp3"))) == 4
    assert "band song 0".replace(" ", "") in storage.load_release_exclusions(playlists)
    assert len(editorial.saved_decisions(editorial.load_journal(playlists))) == 5


def test_decisions_survive_partial_retry_without_another_quota(catalog):
    playlists, releases = catalog
    calls = []

    def reviewer(context):
        calls.append(context)
        return output_for(context)

    first = editorial.review_releases(
        playlists, releases, dry_run=False, reviewer=reviewer
    )
    retried = editorial.review_releases(
        playlists, releases[:2], dry_run=False, reviewer=reviewer
    )
    assert retried == {
        editorial.track_key(r): first[editorial.track_key(r)] for r in releases[:2]
    }
    assert len(calls) == 1
    assert calls[0]["max_discards"] == 1
    assert "playlist_descriptions" in calls[0]
    assert "permanent_catalog" in calls[0]


@pytest.mark.parametrize(
    "mutation", ["quota", "missing", "duplicate", "unknown", "destination", "source"]
)
def test_invalid_agent_output_defers_entire_batch_without_writes(
    catalog, monkeypatch, mutation
):
    playlists, releases = catalog
    before = {
        path: path.read_bytes()
        for path in playlists.parent.rglob("*")
        if path.is_file()
    }

    def reviewer(context):
        output = output_for(context, discard=2 if mutation == "quota" else 1)
        rows = output["decisions"]
        if mutation == "missing":
            rows.pop()
        elif mutation == "duplicate":
            rows[-1] = rows[0]
        elif mutation == "unknown":
            rows[0]["track_key"] = "invented"
        elif mutation == "destination":
            rows[-1]["destination"] = "../../outside"
        elif mutation == "source":
            rows[0]["sources"] = ["not-a-url"]
        return output

    install_reviewer(monkeypatch, reviewer)
    pending = retention.process_outdated_releases(
        releases,
        {},
        playlists.parent / "songs",
        "New Releases",
        playlists_dir=playlists,
        dry_run=False,
    )
    assert pending == releases
    after = {
        path: path.read_bytes()
        for path in playlists.parent.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_dry_run_previews_without_station_changes(catalog, monkeypatch):
    playlists, releases = catalog
    before = {
        path: path.read_bytes()
        for path in playlists.parent.rglob("*")
        if path.is_file()
    }
    install_reviewer(monkeypatch, output_for)
    assert (
        retention.process_outdated_releases(
            releases,
            {},
            playlists.parent / "songs",
            "New Releases",
            playlists_dir=playlists,
            dry_run=True,
        )
        == []
    )
    assert {
        path: path.read_bytes()
        for path in playlists.parent.rglob("*")
        if path.is_file()
    } == before


@pytest.mark.parametrize("research_fails", [False, True])
def test_catalog_edit_during_research_aborts_instead_of_overwriting(
    catalog, monkeypatch, research_fails
):
    playlists, releases = catalog

    def reviewer(context):
        with (playlists / "New Releases.csv").open("a") as handle:
            handle.write("Other,Song,Album,2025,False\n")
        if research_fails:
            raise RuntimeError("research failed after catalog edit")
        return output_for(context)

    install_reviewer(monkeypatch, reviewer)
    with pytest.raises(editorial.EditorialCatalogChanged):
        retention.process_outdated_releases(
            releases,
            {},
            playlists.parent / "songs",
            "New Releases",
            playlists_dir=playlists,
            dry_run=False,
        )
    assert "Other,Song" in (playlists / "New Releases.csv").read_text()
    assert not (playlists.parent / "metadata" / editorial.DECISIONS_FILENAME).exists()


def test_failed_research_preserves_aged_releases_and_saves_fresh_discovery(
    catalog, monkeypatch
):
    playlists, releases = catalog
    from neuralcast.pipelines.new_releases import runtime

    def failed(context):
        raise RuntimeError("Codex unavailable")

    install_reviewer(monkeypatch, failed)
    monkeypatch.setattr(
        runtime, "default_station_paths", lambda station: (playlists.parent, playlists)
    )
    fresh = ArtistRelease("Other", "Fresh", 2026, "New", datetime.now(UTC), "fresh")
    monkeypatch.setattr(runtime, "build_new_releases", lambda *args, **kwargs: [fresh])
    result = NewReleasesRuntime().run(NewReleasesRequest(station="neuralforge"))
    assert len(result.final_releases) == 6
    assert len(storage.load_existing_new_releases(playlists)) == 6
    assert len(list((playlists.parent / "songs" / "New Releases").glob("*.mp3"))) == 5


def test_retirement_refuses_ambiguous_filename_and_symlink(catalog):
    playlists, releases = catalog
    source = playlists.parent / "songs" / "New Releases"
    alternate = source / "band - song 0.mp3"
    alternate.write_bytes(b"alternate")
    with pytest.raises(ValueError, match="Noncanonical"):
        retention.retire_release(releases[0], playlists, source.parent, dry_run=False)
    alternate.unlink()
    target = source / "Band - Song 0.mp3"
    target.unlink()
    target.symlink_to(source / "Band - Song 1.mp3")
    with pytest.raises(ValueError, match="symlink"):
        retention.retire_release(releases[0], playlists, source.parent, dry_run=False)
    assert (source / "Band - Song 1.mp3").exists()


def test_small_batch_cannot_discard_and_empty_batch_skips_agent(catalog):
    playlists, releases = catalog
    with pytest.raises(ValueError, match="limit"):
        editorial.review_releases(
            playlists, releases[:4], dry_run=False, reviewer=output_for
        )
    assert (
        editorial.review_releases(
            playlists, [], dry_run=False, reviewer=lambda _: pytest.fail()
        )
        == {}
    )


def test_updater_lock_rejects_overlap(tmp_path):
    with release_update_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already running"):
            with release_update_lock(tmp_path):
                pytest.fail("Overlapping update entered")


def test_append_failure_keeps_release_pending(catalog, monkeypatch):
    playlists, releases = catalog
    monkeypatch.setattr(
        migration, "_append_release_to_playlist", lambda *args, **kwargs: False
    )
    assert (
        migration.move_outdated_releases(
            releases[:1],
            {"Band": {playlists / "Folk Metal.csv": set()}},
            playlists.parent / "songs",
            "New Releases",
            dry_run=False,
        )
        == releases[:1]
    )
