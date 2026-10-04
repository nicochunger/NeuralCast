"""Optional editorial calls preserve the researched draft and its contracts."""

from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator import editor, generation
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from neuralcast.pipelines.host_orchestrator.models import Archetype
from neuralcast.pipelines.host_orchestrator.spontaneity import ConversationalAllowance

DRAFT = (
    "That was Artist playing Title, with a bass line that leaves plenty of space "
    "for the voice to move around. The arrangement keeps its steady pace while "
    "the instruments gradually drop away. Next comes Another Artist with Another Title."
)
REMARK = " Someone clearly trusted the volume knob."
EDITED = DRAFT.replace("around.", "around." + REMARK)


def edit(writer, draft=DRAFT, **overrides):
    arguments = dict(
        archetype=Archetype.BACK_SELL,
        allowance=ConversationalAllowance("tiny", 12),
        locale=get_channel_registry().locales["en"],
        personality=generation.resolve_station_personality("neuralcast"),
        recent_scripts=["An earlier musical observation."],
        protected_texts=["Artist", "Title", "Another Artist", "Another Title"],
        generate_text=writer,
    )
    arguments.update(overrides)
    return editor.edit_conversational_draft(draft, **arguments)


@pytest.mark.parametrize("locale", ["en", "es-AR", "fr-CH"])
@pytest.mark.parametrize(
    "archetype,expected",
    [
        (Archetype.BACK_SELL, (35, 65, 4)),
        (Archetype.RECENTLY_PLAYED, (35, 65, 4)),
        (Archetype.UP_NEXT_TEASE, (45, 85, 4)),
        (Archetype.SHORT_STORY, (150, 220, None)),
        (Archetype.ALBUM_SPOTLIGHT, (170, 260, None)),
        (Archetype.ERA_SNAPSHOT, (260, 420, None)),
        (Archetype.DEEP_DIVE, (420, 700, 10)),
    ],
)
def test_limits_come_from_localized_wrappers(locale, archetype, expected):
    assert (
        editor.script_limits(archetype, get_channel_registry().locales[locale])
        == expected
    )


def test_accepts_one_insertion_and_logs_exact_remark(caplog):
    writer = Mock(return_value=EDITED)
    with caplog.at_level("INFO", logger="host_orchestrator"):
        assert edit(writer) == EDITED
    writer.assert_called_once()
    args = writer.call_args.kwargs
    assert args["with_search"] is False
    assert "An earlier musical observation." in args["prompt"]
    assert "Next comes Another Artist with Another Title." in args["prompt"]
    assert "affectionate dry wit" in args["system_prompt"]
    assert (
        "Inserted remark added_words=6 text=Someone clearly trusted the volume knob."
        in caplog.text
    )


@pytest.mark.parametrize(
    "output",
    [
        "NO_ASIDE",
        DRAFT,
        DRAFT.replace("steady", "different"),
        DRAFT + REMARK,
        EDITED.replace("away.", "away. Another addition."),
        DRAFT.replace("around.", "around. <unknown> Wow."),
        DRAFT.replace("around.", "around. [laughing] Wow."),
        DRAFT.replace("around.", "around. https://example.com"),
        DRAFT.replace("around.", "around. " + "word " * 13 + "."),
        DRAFT.replace("around.", "around. Yes. Maybe."),
    ],
)
def test_omissions_and_invalid_edits_keep_original_without_retry(output):
    writer = Mock(return_value=output)
    assert edit(writer) == DRAFT
    writer.assert_called_once()


def test_provider_failure_keeps_original_without_retry():
    writer = Mock(side_effect=RuntimeError("Unavailable"))
    assert edit(writer) == DRAFT
    writer.assert_called_once()


@pytest.mark.parametrize(
    "archetype",
    [
        Archetype.BLOCK_INTRO,
        Archetype.ULTRA_MINIMAL,
        Archetype.NEWS,
        Archetype.CONCERT_CHECK,
    ],
)
def test_unsupported_archetypes_do_not_call_editor(archetype):
    writer = Mock()
    assert edit(writer, archetype=archetype) == DRAFT
    writer.assert_not_called()


def test_direct_mode_does_not_call_editor():
    writer = Mock()
    assert edit(writer, allowance=ConversationalAllowance("direct", 0)) == DRAFT
    writer.assert_not_called()


@pytest.mark.parametrize(
    "draft",
    [
        "Very short draft.",
        "word " * 65 + ".",
        "word " * 66 + ".",
        DRAFT.replace("steady pace", "steady. Pace"),
    ],
)
def test_no_room_or_outside_contract_skips_call(draft):
    writer = Mock()
    assert edit(writer, draft) == draft
    writer.assert_not_called()


def test_preserves_inline_tags():
    draft = DRAFT.replace("The arrangement", "<breath> The arrangement")
    writer = Mock(return_value=draft.replace("around.", "around." + REMARK))
    assert edit(writer, draft) == writer.return_value


def test_protected_name_cannot_be_split_at_internal_punctuation():
    draft = DRAFT.replace("Artist playing", "P. Diddy playing")
    writer = Mock(return_value=draft.replace("P.", "P." + REMARK))
    assert edit(writer, draft, protected_texts=["P. Diddy"]) == draft
    writer.assert_called_once()


def test_room_limits_addition_even_when_allowance_is_larger():
    draft = DRAFT.replace(
        "That was", "That was " + "really " * (60 - len(DRAFT.split()))
    )
    writer = Mock(return_value=draft.replace("around.", "around." + REMARK))
    assert edit(writer, draft) == draft
    assert "Add at most 5 spoken words" in writer.call_args.kwargs["prompt"]


def test_trailing_tag_does_not_allow_an_addition_after_handoff():
    draft = DRAFT + " <short pause>"
    writer = Mock(
        return_value=draft.replace(
            "Title. <short pause>", "Title." + REMARK + " <short pause>"
        )
    )
    assert edit(writer, draft) == draft
    writer.assert_called_once()
