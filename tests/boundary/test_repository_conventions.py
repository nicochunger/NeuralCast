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


def test_root_command_shims_are_absent_and_package_entrypoints_exist() -> None:
    obsolete_shims = {
        "main.py",
        "update_new_releases.py",
        "inject_host_segment.py",
        "schedule_generator.py",
    }

    targets = _project_script_targets()

    assert not any((PROJECT_ROOT / filename).exists() for filename in obsolete_shims)
    assert len(targets) == 5
    for target in targets:
        module_name, attribute_name = target.split(":", maxsplit=1)
        attribute = getattr(importlib.import_module(module_name), attribute_name)
        assert callable(attribute)


def test_pipeline_packages_use_responsibility_module_names() -> None:
    pipelines_dir = PROJECT_ROOT / "src" / "neuralcast" / "pipelines"
    pipeline_packages = (
        "host_orchestrator",
        "new_releases",
        "schedule_generator",
        "station_sync",
    )

    assert not (pipelines_dir / "station_sync.py").exists()
    for package_name in pipeline_packages:
        package_dir = pipelines_dir / package_name
        assert (package_dir / "__init__.py").is_file()
        assert not any(
            module.stem.startswith(f"{package_name}_")
            for module in package_dir.glob("*.py")
        )


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


def test_generated_artifact_patterns_are_ignored() -> None:
    patterns = set(
        (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    )

    assert {
        "*.bak",
        "*.tmp",
        "*.catalog-backup",
        "*/metadata/ai_host_orchestrator.lock",
    } <= patterns


def test_deployment_docs_use_install_for_system_files() -> None:
    for relative_path in ("deployment/INSTRUCTIONS.md", "docs/admin_api.md"):
        contents = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "sudo cp " not in contents
        assert "sudo chmod 644" not in contents
        assert "/.venv/bin/pip " not in contents


def test_makefile_exposes_standard_development_targets() -> None:
    contents = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PYTHON ?= .venv/bin/python" in contents
    for target in (
        "test",
        "test-unit",
        "test-boundary",
        "test-coverage",
        "test-integration",
        "test-live",
        "test-collect",
        "clean",
    ):
        assert f"{target}:" in contents
