"""Validated host brand, locale, and broadcast-channel configuration."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from neuralcast.config import ASSETS_ROOT, station_dir_from_slug

from .archetype_policies import (
    ResolvedArchetypeProfile,
    get_archetype_policy_registry,
)


CHANNEL_CONFIG_PATH = ASSETS_ROOT / "stories" / "host_channels.json"
REQUIRED_PRESENTATION_KEYS = frozenset(
    {
        "back_sell",
        "up_next_tease",
        "short_story",
        "album_spotlight",
        "era_snapshot",
        "deep_dive",
        "news",
        "concert_check",
        "block_intro",
        "ultra_minimal",
        "default",
        "current",
        "next",
        "previous_track",
        "next_track",
        "unknown_track",
        "current_affairs",
        "concerts",
        "new_section",
        "music",
        "and",
        "fallback_current_next",
        "fallback_next",
        "fallback_music",
    }
)
REQUIRED_SCHEDULE_KEYS = frozenset(
    {
        "block_terms",
        "current_markers",
        "open_markers",
        "open_label",
        "mid_open_short",
        "mid_open_long",
        "start_open_short",
        "start_open_long",
        "mid_section_short",
        "mid_section_long",
        "start_section_short",
        "start_section_long",
        "genre_default",
    }
)


@dataclass(frozen=True)
class HostBrand:
    key: str
    content_station: str
    personality_station: str
    cadence_station: str
    cover_station: str
    script_style: str
    tts_style: str


@dataclass(frozen=True)
class HostLocale:
    tag: str
    output_language: str
    script_guidance: str
    prompt_directory: pathlib.Path
    tts_instructions_path: pathlib.Path
    tts_voice: str
    presentation: Mapping[str, Any]
    schedule: Mapping[str, Any]


@dataclass(frozen=True)
class HostChannel:
    key: str
    azuracast_station: str
    azuracast_station_id: int | None
    brand: HostBrand
    locale: HostLocale
    media_owner_station: str
    liquidsoap_media_root: str
    remote_prefix: str
    cadence_profile: str
    archetype_profile: str
    archetype_policy: ResolvedArchetypeProfile
    script_style_override: str | None = None
    tts_instructions_override_path: pathlib.Path | None = None
    legacy_station: str | None = None

    @property
    def content_station(self) -> str:
        return self.brand.content_station

    @property
    def content_station_dir(self) -> pathlib.Path:
        return station_dir_from_slug(self.content_station)


@dataclass(frozen=True)
class HostChannelRegistry:
    brands: Mapping[str, HostBrand]
    locales: Mapping[str, HostLocale]
    channels: Mapping[str, HostChannel]
    legacy_station_channels: Mapping[str, str]

    def resolve(
        self,
        *,
        channel_key: str | None = None,
        station_slug: str | None = None,
    ) -> HostChannel:
        if channel_key:
            try:
                return self.channels[channel_key]
            except KeyError as exc:
                available = ", ".join(sorted(self.channels))
                raise ValueError(
                    f"Unsupported host channel '{channel_key}'. Available: {available}."
                ) from exc

        station = str(station_slug or "").strip().lower()
        try:
            resolved_key = self.legacy_station_channels[station]
            return self.channels[resolved_key]
        except KeyError as exc:
            available = ", ".join(sorted(self.legacy_station_channels))
            raise ValueError(
                f"Unsupported station '{station}'. Allowed values: {available}."
            ) from exc


def _required_string(payload: Mapping[str, Any], key: str, context: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{context} requires non-empty '{key}'.")
    return value


def _mapping(payload: Mapping[str, Any], key: str, context: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} requires object '{key}'.")
    return value


def _require_keys(
    value: Mapping[str, Any], keys: frozenset[str], context: str
) -> None:
    missing = sorted(key for key in keys if key not in value)
    if missing:
        raise ValueError(f"{context} is missing required keys: {', '.join(missing)}.")


def load_channel_registry(
    path: pathlib.Path = CHANNEL_CONFIG_PATH,
) -> HostChannelRegistry:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Missing host-channel configuration: {path}"
        ) from None
    if not isinstance(payload, Mapping):
        raise ValueError("Host-channel configuration root must be an object.")

    raw_brands = _mapping(payload, "brands", "host-channel configuration")
    brands: dict[str, HostBrand] = {}
    for key, raw in raw_brands.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"Brand '{key}' must be an object.")
        brand_key = str(key).strip()
        content_station = _required_string(raw, "content_station", f"brand '{key}'")
        # Validate repository-backed content stations immediately.
        station_dir_from_slug(content_station)
        personality_station = str(raw.get("personality_station") or content_station)
        cadence_station = str(raw.get("cadence_station") or content_station)
        cover_station = str(raw.get("cover_station") or content_station)
        for station_value in (
            personality_station,
            cadence_station,
            cover_station,
        ):
            station_dir_from_slug(station_value)
        brands[brand_key] = HostBrand(
            key=brand_key,
            content_station=content_station,
            personality_station=personality_station,
            cadence_station=cadence_station,
            cover_station=cover_station,
            script_style=_required_string(raw, "script_style", f"brand '{key}'"),
            tts_style=_required_string(raw, "tts_style", f"brand '{key}'"),
        )

    raw_locales = _mapping(payload, "locales", "host-channel configuration")
    locales: dict[str, HostLocale] = {}
    for key, raw in raw_locales.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"Locale '{key}' must be an object.")
        locale_key = str(key).strip()
        instructions_rel = _required_string(
            raw, "tts_instructions", f"locale '{key}'"
        )
        instructions_path = ASSETS_ROOT / "stories" / instructions_rel
        if not instructions_path.is_file():
            raise FileNotFoundError(
                f"Locale '{key}' TTS instructions not found: {instructions_path}"
            )
        prompt_directory_rel = str(raw.get("prompt_directory") or "prompts").strip()
        prompt_directory = ASSETS_ROOT / "stories" / prompt_directory_rel
        if not prompt_directory.is_dir():
            raise FileNotFoundError(
                f"Locale '{key}' prompt directory not found: {prompt_directory}"
            )
        presentation = _mapping(raw, "presentation", f"locale '{key}'")
        schedule = _mapping(raw, "schedule", f"locale '{key}'")
        _require_keys(
            presentation, REQUIRED_PRESENTATION_KEYS, f"locale '{key}' presentation"
        )
        _require_keys(schedule, REQUIRED_SCHEDULE_KEYS, f"locale '{key}' schedule")
        locales[locale_key] = HostLocale(
            tag=locale_key,
            output_language=_required_string(
                raw, "output_language", f"locale '{key}'"
            ),
            script_guidance=_required_string(
                raw, "script_guidance", f"locale '{key}'"
            ),
            prompt_directory=prompt_directory,
            tts_instructions_path=instructions_path,
            tts_voice=str(raw.get("tts_voice") or "Enceladus").strip(),
            presentation=presentation,
            schedule=schedule,
        )

    raw_channels = _mapping(payload, "channels", "host-channel configuration")
    policy_registry = get_archetype_policy_registry()
    channels: dict[str, HostChannel] = {}
    remote_scopes: set[tuple[str, str]] = set()
    for key, raw in raw_channels.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"Channel '{key}' must be an object.")
        channel_key = str(key).strip()
        brand_key = _required_string(raw, "brand", f"channel '{key}'")
        locale_key = _required_string(raw, "locale", f"channel '{key}'")
        if brand_key not in brands:
            raise ValueError(f"Channel '{key}' references unknown brand '{brand_key}'.")
        if locale_key not in locales:
            raise ValueError(
                f"Channel '{key}' references unknown locale '{locale_key}'."
            )
        media_owner = _required_string(
            raw, "media_owner_station", f"channel '{key}'"
        )
        remote_prefix = _required_string(raw, "remote_prefix", f"channel '{key}'")
        if remote_prefix.startswith("/") or ".." in pathlib.PurePosixPath(
            remote_prefix
        ).parts:
            raise ValueError(f"Channel '{key}' has unsafe remote_prefix.")
        remote_scope = (media_owner, remote_prefix.rstrip("/"))
        if remote_scope in remote_scopes:
            raise ValueError(
                f"Channel '{key}' reuses remote media scope {remote_scope!r}."
            )
        remote_scopes.add(remote_scope)
        media_root = str(
            raw.get("liquidsoap_media_root")
            or f"/var/azuracast/stations/{media_owner}/media"
        ).rstrip("/")
        if not media_root.startswith("/var/azuracast/stations/"):
            raise ValueError(f"Channel '{key}' has unsafe liquidsoap_media_root.")
        station_id_raw = raw.get("azuracast_station_id")
        station_id = int(station_id_raw) if station_id_raw is not None else None
        if station_id is not None and station_id <= 0:
            raise ValueError(f"Channel '{key}' has invalid azuracast_station_id.")
        cadence_profile = str(
            raw.get("cadence_profile") or brands[brand_key].cadence_station
        )
        archetype_profile = str(
            raw.get("archetype_profile") or brands[brand_key].content_station
        )
        station_dir_from_slug(cadence_profile)
        archetype_policy = policy_registry.resolve(
            archetype_profile,
            raw.get("archetype_overrides"),
            resolved_name=channel_key,
        )
        tts_instructions_override_path: pathlib.Path | None = None
        tts_instructions_override = str(
            raw.get("tts_instructions_override") or ""
        ).strip()
        if tts_instructions_override:
            tts_instructions_override_path = (
                ASSETS_ROOT / "stories" / tts_instructions_override
            )
            if not tts_instructions_override_path.is_file():
                raise FileNotFoundError(
                    f"Channel '{key}' TTS instructions override not found: "
                    f"{tts_instructions_override_path}"
                )
        channels[channel_key] = HostChannel(
            key=channel_key,
            azuracast_station=_required_string(
                raw, "azuracast_station", f"channel '{key}'"
            ),
            azuracast_station_id=station_id,
            brand=brands[brand_key],
            locale=locales[locale_key],
            media_owner_station=media_owner,
            liquidsoap_media_root=media_root,
            remote_prefix=remote_prefix.rstrip("/"),
            cadence_profile=cadence_profile,
            archetype_profile=archetype_profile,
            archetype_policy=archetype_policy,
            script_style_override=(
                str(raw.get("script_style_override") or "").strip() or None
            ),
            tts_instructions_override_path=tts_instructions_override_path,
            legacy_station=(str(raw.get("legacy_station") or "").strip() or None),
        )

    legacy_raw = _mapping(
        payload, "legacy_station_channels", "host-channel configuration"
    )
    legacy = {str(key): str(value) for key, value in legacy_raw.items()}
    for station, channel_key in legacy.items():
        if channel_key not in channels:
            raise ValueError(
                "Legacy station "
                f"'{station}' references unknown channel '{channel_key}'."
            )

    return HostChannelRegistry(
        brands=brands,
        locales=locales,
        channels=channels,
        legacy_station_channels=legacy,
    )


@lru_cache(maxsize=1)
def get_channel_registry() -> HostChannelRegistry:
    return load_channel_registry()


def resolve_host_channel(
    *, channel_key: str | None = None, station_slug: str | None = None
) -> HostChannel:
    return get_channel_registry().resolve(
        channel_key=channel_key,
        station_slug=station_slug,
    )


def host_channel_keys() -> tuple[str, ...]:
    return tuple(get_channel_registry().channels)


__all__ = [
    "CHANNEL_CONFIG_PATH",
    "HostBrand",
    "HostChannel",
    "HostChannelRegistry",
    "HostLocale",
    "get_channel_registry",
    "host_channel_keys",
    "load_channel_registry",
    "resolve_host_channel",
]
