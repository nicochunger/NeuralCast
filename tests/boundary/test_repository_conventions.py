"""Boundary tests for repository-wide naming and entrypoint conventions."""

from __future__ import annotations

import importlib
from pathlib import Path

from neuralcast.metadata.constants import (
    NEW_RELEASES_ARTIST_CACHE_FILENAME,
    NEW_RELEASES_EXCLUSIONS_FILENAME,
    NEW_RELEASES_METADATA_FILENAME,
    NEW_RELEASES_PLAYLIST_FILENAME,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_script_targets() -> list[str]:
    targets = []
    in_scripts_section = False
    for raw_line in (PROJECT_ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if line == "[project.scripts]":
            in_scripts_section = True
            continue
        if in_scripts_section and line.startswith("["):
            break
        if in_scripts_section and "=" in line:
            _name, target = line.split("=", maxsplit=1)
            targets.append(target.strip().strip('"'))
    return targets


def test_root_documentation_and_dependency_sources_are_canonical() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'readme = "README.md"' in pyproject
    assert (PROJECT_ROOT / "README.md").is_file()
    assert (PROJECT_ROOT / "AGENTS.md").is_file()
    assert not (PROJECT_ROOT / "readme.md").exists()
    assert not (PROJECT_ROOT / "GEMINI.md").exists()
    assert not (PROJECT_ROOT / "vps_requirements.txt").exists()


def test_root_compatibility_shims_and_package_entrypoints_exist() -> None:
    expected_shims = {
        "main.py",
        "update_new_releases.py",
        "inject_host_segment.py",
        "schedule_generator.py",
    }

    targets = _project_script_targets()

    assert all((PROJECT_ROOT / filename).is_file() for filename in expected_shims)
    assert len(targets) == 5
    for target in targets:
        module_name, attribute_name = target.split(":", maxsplit=1)
        attribute = getattr(importlib.import_module(module_name), attribute_name)
        assert callable(attribute)


def test_new_releases_filenames_are_defined_in_one_source_module() -> None:
    constants_path = (
        PROJECT_ROOT / "src" / "neuralcast" / "metadata" / "constants.py"
    )
    canonical_filenames = {
        NEW_RELEASES_ARTIST_CACHE_FILENAME,
        NEW_RELEASES_EXCLUSIONS_FILENAME,
        NEW_RELEASES_METADATA_FILENAME,
        NEW_RELEASES_PLAYLIST_FILENAME,
    }

    for source_path in (PROJECT_ROOT / "src" / "neuralcast").rglob("*.py"):
        if source_path == constants_path:
            continue
        source = source_path.read_text(encoding="utf-8")
        assert all(f'"{filename}"' not in source for filename in canonical_filenames)
