"""Open rotation is introduced once, without recurring format explanations."""

import datetime as dt
from dataclasses import replace

import pytest

from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.prompts import (
    build_prompt,
    resolve_station_personality,
)
from neuralcast.pipelines.host_orchestrator.schedule import resolve_schedule_context

START = dt.datetime(2026, 10, 3, 12, tzinfo=dt.timezone.utc).timestamp()
KEY = "2026-10-03|afternoon"


def context(mode, minutes, mentions=None):
    return resolve_schedule_context(
        {
            "timezone": "UTC",
            "expanded_blocks": [
                {
                    "block_key": KEY,
                    "date_local": "2026-10-03",
                    "start_time_local": "12:00",
                    "end_time_local": "14:00",
                    "section_label": "Afternoon",
                    "mode": mode,
                    "genre_labels": ["mixed"],
                }
            ],
        },
        START + minutes * 60,
        mentions or {},
    )


def prompt_for(schedule_context, locale_tag, archetype):
    track = QueueTrack("song", "1", "Artist", "Title", 240)
    return build_prompt(
        archetype=archetype,
        station_name="NeuralCast",
        personality=resolve_station_personality("neuralcast"),
        current=track,
        next_track=track,
        upcoming_tracks=[track],
        current_meta=TrackMetadata(),
        next_meta=TrackMetadata(),
        angle=None,
        hook="",
        banned_list=[],
        recent_scripts=[],
        schedule_context=schedule_context,
        locale=get_channel_registry().locales[locale_tag],
    )


def test_open_rotation_retains_start_intro_but_never_requests_mid_reminders():
    assert context("open", 0).mention_intent == "start"
    for mentions in (
        {},
        {KEY: {"start": True, "speak_count": 8}},
        {KEY: {"speak_count": 8, "intro_missed": True}},
    ):
        for minutes in (35, 60, 85, 110):
            assert context("open", minutes, mentions).mention_intent is None


def test_themed_blocks_keep_their_mid_mention_cadence():
    assert context("playlist", 60).mention_intent == "mid"
    mentions = {KEY: {"start": True, "speak_count": 8}}
    assert context("playlist", 60, mentions).mention_intent == "mid"


@pytest.mark.parametrize("locale_tag", ["es-AR", "en", "fr-CH"])
@pytest.mark.parametrize(
    "archetype",
    [
        Archetype.BACK_SELL,
        Archetype.UP_NEXT_TEASE,
        Archetype.SHORT_STORY,
        Archetype.NEWS,
    ],
)
@pytest.mark.parametrize("intent", [None, "mid"])
def test_regular_open_segments_omit_format_explanations_even_with_legacy_mid_intent(
    locale_tag, archetype, intent
):
    ctx = replace(context("open", 60), mention_intent=intent)
    prompt = prompt_for(ctx, locale_tag, archetype)
    if locale_tag == "fr-CH":
        assert "Ne pas mentionner ni décrire la rotation libre" in prompt
        assert "il faut donc l'inclure cette fois" not in prompt
        assert (
            "ajoute une courte proposition indiquant que tous les genres" not in prompt
        )
    else:
        assert "No mencionar ni describir el bloque libre" in prompt
        assert "asi que incluirlo esta vez" not in prompt
        assert (
            "sumar una clausula corta aclarando que puede sonar cualquier genero"
            not in prompt
        )


@pytest.mark.parametrize("locale_tag", ["es-AR", "en", "fr-CH"])
def test_open_intro_can_still_orient_the_listener_briefly(locale_tag):
    prompt = prompt_for(context("open", 0), locale_tag, Archetype.BLOCK_INTRO)
    if locale_tag == "fr-CH":
        assert "À cette ouverture uniquement" in prompt
        assert "une seule proposition" in prompt
        assert "Ne pas mentionner ni décrire la rotation libre" not in prompt
    else:
        assert "Solo en esta apertura" in prompt
        assert "una sola clausula" in prompt
        assert "No mencionar ni describir el bloque libre" not in prompt
