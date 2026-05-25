"""Marker-gated integration test templates.

These tests document the live checks expected by the suite and skip unless the
local environment explicitly provides the required credentials or executables.
"""

from __future__ import annotations

import os
import shutil

import pytest


pytestmark = pytest.mark.integration


def test_azuracast_credentials_are_available_for_live_api_checks() -> None:
    if not (os.getenv("AZURACAST_BASE_URL") and os.getenv("AZURACAST_API_KEY")):
        pytest.skip("AZURACAST_BASE_URL and AZURACAST_API_KEY are required.")


def test_gemini_credentials_are_available_for_live_ai_checks() -> None:
    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY is required.")


def test_media_toolchain_is_available_for_live_download_checks() -> None:
    missing = [
        executable
        for executable in ("yt-dlp", "ffmpeg", "mp3gain")
        if shutil.which(executable) is None
    ]
    if missing:
        pytest.skip(f"Missing media toolchain executables: {', '.join(missing)}")
