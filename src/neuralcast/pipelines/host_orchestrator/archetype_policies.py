"""Validated, inheritable archetype policies for host broadcast channels."""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from dataclasses import dataclass, replace
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from neuralcast.config import ASSETS_ROOT

from .models import Archetype


ARCHETYPE_POLICY_CONFIG_PATH = (
    ASSETS_ROOT / "stories" / "archetype_profiles.json"
)


@dataclass(frozen=True)
class NewsTopicDefinition:
    topic_id: str
    labels: Mapping[str, str]

    def label_for(self, locale_tag: str) -> str:
        return (
            self.labels.get(locale_tag)
            or self.labels.get(locale_tag.split("-", 1)[0])
            or self.labels.get("en")
            or self.topic_id
        )


@dataclass(frozen=True)
class ConcertCountryDefinition:
    country_code: str
    labels: Mapping[str, str]
    aliases: tuple[str, ...]

    def label_for(self, locale_tag: str) -> str:
        return (
            self.labels.get(locale_tag)
            or self.labels.get(locale_tag.split("-", 1)[0])
            or self.labels.get("en")
            or self.country_code
        )


@dataclass(frozen=True)
class NewsPolicy:
    topic_ids: tuple[str, ...]
    max_age_hours: int
    preferred_max_age_hours: int


@dataclass(frozen=True)
class ConcertCheckPolicy:
    country_codes: tuple[str, ...]


@dataclass(frozen=True)
class ArchetypePolicy:
    enabled: bool
    automatic: bool
    weight: float
    cooldown_seconds: int
    lead_time_seconds: int
    temperature_range: tuple[float, float]
    top_p_range: tuple[float, float]
    hook_free_probability: float
    search_enabled: bool
    news: NewsPolicy | None = None
    concert_check: ConcertCheckPolicy | None = None


@dataclass(frozen=True)
class ResolvedArchetypeProfile:
    name: str
    archetypes: Mapping[Archetype, ArchetypePolicy]
    news_topics: Mapping[str, NewsTopicDefinition]
    concert_countries: Mapping[str, ConcertCountryDefinition]

    def for_archetype(self, archetype: Archetype) -> ArchetypePolicy:
        return self.archetypes[archetype]

    @property
    def disabled_archetypes(self) -> frozenset[Archetype]:
        return frozenset(
            archetype
            for archetype, policy in self.archetypes.items()
            if not policy.enabled
        )

    @property
    def automatic_archetypes(self) -> tuple[Archetype, ...]:
        return tuple(
            archetype
            for archetype, policy in self.archetypes.items()
            if policy.enabled and policy.automatic
        )

    def news_topic_label(self, topic_id: str, locale_tag: str) -> str:
        return self.news_topics[topic_id].label_for(locale_tag)

    def concert_country_label(self, country_code: str, locale_tag: str) -> str:
        return self.concert_countries[country_code].label_for(locale_tag)


@dataclass(frozen=True)
class ArchetypePolicyRegistry:
    profiles: Mapping[str, ResolvedArchetypeProfile]
    news_topics: Mapping[str, NewsTopicDefinition]
    concert_countries: Mapping[str, ConcertCountryDefinition]

    def resolve(
        self,
        profile_name: str,
        channel_overrides: Mapping[str, Any] | None = None,
        *,
        resolved_name: str | None = None,
    ) -> ResolvedArchetypeProfile:
        try:
            profile = self.profiles[profile_name]
        except KeyError as exc:
            available = ", ".join(sorted(self.profiles))
            raise ValueError(
                f"Unknown archetype profile '{profile_name}'. Available: {available}."
            ) from exc
        if not channel_overrides:
            return profile
        archetypes = _apply_archetype_overrides(
            profile.archetypes,
            channel_overrides,
            self.news_topics,
            self.concert_countries,
            context=f"channel policy '{resolved_name or profile_name}'",
        )
        return ResolvedArchetypeProfile(
            name=resolved_name or profile.name,
            archetypes=MappingProxyType(archetypes),
            news_topics=self.news_topics,
            concert_countries=self.concert_countries,
        )


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object.")
    return value


def _require_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean.")
    return value


def _require_number(value: Any, context: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a number.")
    parsed = float(value)
    if parsed < minimum:
        raise ValueError(f"{context} must be at least {minimum}.")
    return parsed


def _require_int(value: Any, context: str, *, minimum: int = 0) -> int:
    parsed = _require_number(value, context, minimum=float(minimum))
    if not parsed.is_integer():
        raise ValueError(f"{context} must be an integer.")
    return int(parsed)


def _require_range(value: Any, context: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ValueError(f"{context} must contain exactly two numbers.")
    lower = _require_number(value[0], f"{context}[0]")
    upper = _require_number(value[1], f"{context}[1]")
    if lower > upper:
        raise ValueError(f"{context} lower bound cannot exceed its upper bound.")
    return lower, upper


def _require_string_list(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{context} must be an array of strings.")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{context} must be an array of strings.")
    items = tuple(item.strip() for item in value)
    if not items or any(not item for item in items):
        raise ValueError(f"{context} must contain non-empty strings.")
    if len(set(items)) != len(items):
        raise ValueError(f"{context} cannot contain duplicates.")
    return items


def _normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_labels(value: Any, context: str) -> Mapping[str, str]:
    raw = _require_mapping(value, context)
    labels = {
        str(locale).strip(): str(label).strip()
        for locale, label in raw.items()
        if str(locale).strip() and str(label).strip()
    }
    if "en" not in labels:
        raise ValueError(f"{context} requires an English fallback label.")
    return MappingProxyType(labels)


def _parse_news_topics(value: Any) -> Mapping[str, NewsTopicDefinition]:
    raw = _require_mapping(value, "news_topics")
    topics: dict[str, NewsTopicDefinition] = {}
    for topic_id_raw, payload_raw in raw.items():
        topic_id = str(topic_id_raw).strip()
        if not topic_id:
            raise ValueError("News topic IDs cannot be empty.")
        payload = _require_mapping(payload_raw, f"news topic '{topic_id}'")
        topics[topic_id] = NewsTopicDefinition(
            topic_id=topic_id,
            labels=_parse_labels(payload.get("labels"), f"news topic '{topic_id}' labels"),
        )
    if not topics:
        raise ValueError("At least one news topic must be configured.")
    return MappingProxyType(topics)


def _parse_concert_countries(
    value: Any,
) -> Mapping[str, ConcertCountryDefinition]:
    raw = _require_mapping(value, "concert_countries")
    countries: dict[str, ConcertCountryDefinition] = {}
    aliases_seen: dict[str, str] = {}
    for code_raw, payload_raw in raw.items():
        code = str(code_raw).strip().upper()
        if not code:
            raise ValueError("Concert country codes cannot be empty.")
        if code in countries:
            raise ValueError(f"Concert country code '{code}' is duplicated.")
        payload = _require_mapping(payload_raw, f"concert country '{code}'")
        aliases = _require_string_list(
            payload.get("aliases"), f"concert country '{code}' aliases"
        )
        for alias in aliases:
            normalized = _normalize_alias(alias)
            if not normalized:
                raise ValueError(
                    f"Concert country alias '{alias}' for '{code}' is invalid."
                )
            if normalized in aliases_seen:
                raise ValueError(
                    f"Concert country alias '{alias}' is shared by "
                    f"'{aliases_seen[normalized]}' and '{code}'."
                )
            aliases_seen[normalized] = code
        countries[code] = ConcertCountryDefinition(
            country_code=code,
            labels=_parse_labels(
                payload.get("labels"), f"concert country '{code}' labels"
            ),
            aliases=aliases,
        )
    if not countries:
        raise ValueError("At least one concert country must be configured.")
    return MappingProxyType(countries)


def _parse_news_policy(
    value: Any,
    topics: Mapping[str, NewsTopicDefinition],
    context: str,
) -> NewsPolicy:
    raw = _require_mapping(value, context)
    topic_ids = _require_string_list(raw.get("topic_ids"), f"{context}.topic_ids")
    unknown = sorted(set(topic_ids) - set(topics))
    if unknown:
        raise ValueError(f"{context} references unknown news topics: {', '.join(unknown)}.")
    max_age = _require_int(raw.get("max_age_hours"), f"{context}.max_age_hours", minimum=1)
    preferred = _require_int(
        raw.get("preferred_max_age_hours"),
        f"{context}.preferred_max_age_hours",
        minimum=1,
    )
    if preferred > max_age:
        raise ValueError(f"{context} preferred freshness cannot exceed max freshness.")
    return NewsPolicy(topic_ids=topic_ids, max_age_hours=max_age, preferred_max_age_hours=preferred)


def _parse_concert_policy(
    value: Any,
    countries: Mapping[str, ConcertCountryDefinition],
    context: str,
) -> ConcertCheckPolicy:
    raw = _require_mapping(value, context)
    codes = tuple(
        code.upper()
        for code in _require_string_list(
            raw.get("country_codes"), f"{context}.country_codes"
        )
    )
    unknown = sorted(set(codes) - set(countries))
    if unknown:
        raise ValueError(
            f"{context} references unknown concert countries: {', '.join(unknown)}."
        )
    return ConcertCheckPolicy(country_codes=codes)


def _parse_archetype_policy(
    archetype: Archetype,
    value: Any,
    topics: Mapping[str, NewsTopicDefinition],
    countries: Mapping[str, ConcertCountryDefinition],
    context: str,
) -> ArchetypePolicy:
    raw = _require_mapping(value, context)
    allowed_keys = {
        "enabled",
        "automatic",
        "weight",
        "cooldown_seconds",
        "lead_time_seconds",
        "temperature_range",
        "top_p_range",
        "hook_free_probability",
        "search_enabled",
        "news",
        "concert_check",
    }
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"{context} has unknown fields: {', '.join(unknown_keys)}.")
    hook_free = _require_number(
        raw.get("hook_free_probability"), f"{context}.hook_free_probability"
    )
    if hook_free > 1:
        raise ValueError(f"{context}.hook_free_probability cannot exceed 1.")
    top_p_range = _require_range(raw.get("top_p_range"), f"{context}.top_p_range")
    if top_p_range[1] > 1:
        raise ValueError(f"{context}.top_p_range cannot exceed 1.")
    news = (
        _parse_news_policy(raw.get("news"), topics, f"{context}.news")
        if archetype == Archetype.NEWS
        else None
    )
    concert = (
        _parse_concert_policy(
            raw.get("concert_check"), countries, f"{context}.concert_check"
        )
        if archetype == Archetype.CONCERT_CHECK
        else None
    )
    if archetype != Archetype.NEWS and "news" in raw:
        raise ValueError(f"{context}.news is only valid for the news archetype.")
    if archetype != Archetype.CONCERT_CHECK and "concert_check" in raw:
        raise ValueError(
            f"{context}.concert_check is only valid for the concert_check archetype."
        )
    return ArchetypePolicy(
        enabled=_require_bool(raw.get("enabled"), f"{context}.enabled"),
        automatic=_require_bool(raw.get("automatic"), f"{context}.automatic"),
        weight=_require_number(raw.get("weight"), f"{context}.weight"),
        cooldown_seconds=_require_int(
            raw.get("cooldown_seconds"), f"{context}.cooldown_seconds"
        ),
        lead_time_seconds=_require_int(
            raw.get("lead_time_seconds"), f"{context}.lead_time_seconds", minimum=1
        ),
        temperature_range=_require_range(
            raw.get("temperature_range"), f"{context}.temperature_range"
        ),
        top_p_range=top_p_range,
        hook_free_probability=hook_free,
        search_enabled=_require_bool(
            raw.get("search_enabled"), f"{context}.search_enabled"
        ),
        news=news,
        concert_check=concert,
    )


def _apply_list_operation(
    current: tuple[str, ...],
    value: Any,
    valid_values: set[str],
    context: str,
    *,
    normalize=lambda item: item,
) -> tuple[str, ...]:
    raw = _require_mapping(value, context)
    allowed_ops = {"add", "remove", "replace"}
    unknown_ops = sorted(set(raw) - allowed_ops)
    if unknown_ops:
        raise ValueError(f"{context} has unknown operations: {', '.join(unknown_ops)}.")
    if "replace" in raw and ("add" in raw or "remove" in raw):
        raise ValueError(f"{context}.replace cannot be combined with add/remove.")

    def read(operation: str) -> tuple[str, ...]:
        if operation not in raw:
            return ()
        return tuple(
            normalize(item)
            for item in _require_string_list(raw[operation], f"{context}.{operation}")
        )

    replacement = read("replace")
    additions = read("add")
    removals = set(read("remove"))
    referenced = set(replacement) | set(additions) | removals
    unknown = sorted(referenced - valid_values)
    if unknown:
        raise ValueError(f"{context} references unknown values: {', '.join(unknown)}.")
    result = list(replacement if "replace" in raw else current)
    result = [item for item in result if item not in removals]
    for item in additions:
        if item not in result:
            result.append(item)
    if not result:
        raise ValueError(f"{context} cannot resolve to an empty list.")
    return tuple(result)


def _apply_single_archetype_override(
    archetype: Archetype,
    current: ArchetypePolicy,
    value: Any,
    topics: Mapping[str, NewsTopicDefinition],
    countries: Mapping[str, ConcertCountryDefinition],
    context: str,
) -> ArchetypePolicy:
    raw = _require_mapping(value, context)
    allowed_keys = {
        "enabled",
        "automatic",
        "weight",
        "cooldown_seconds",
        "lead_time_seconds",
        "temperature_range",
        "top_p_range",
        "hook_free_probability",
        "search_enabled",
        "news",
        "concert_check",
    }
    unknown = sorted(set(raw) - allowed_keys)
    if unknown:
        raise ValueError(f"{context} has unknown fields: {', '.join(unknown)}.")
    updates: dict[str, Any] = {}
    if "enabled" in raw:
        updates["enabled"] = _require_bool(raw["enabled"], f"{context}.enabled")
    if "automatic" in raw:
        updates["automatic"] = _require_bool(
            raw["automatic"], f"{context}.automatic"
        )
    if "weight" in raw:
        updates["weight"] = _require_number(raw["weight"], f"{context}.weight")
    if "cooldown_seconds" in raw:
        updates["cooldown_seconds"] = _require_int(
            raw["cooldown_seconds"], f"{context}.cooldown_seconds"
        )
    if "lead_time_seconds" in raw:
        updates["lead_time_seconds"] = _require_int(
            raw["lead_time_seconds"], f"{context}.lead_time_seconds", minimum=1
        )
    if "temperature_range" in raw:
        updates["temperature_range"] = _require_range(
            raw["temperature_range"], f"{context}.temperature_range"
        )
    if "top_p_range" in raw:
        top_p_range = _require_range(raw["top_p_range"], f"{context}.top_p_range")
        if top_p_range[1] > 1:
            raise ValueError(f"{context}.top_p_range cannot exceed 1.")
        updates["top_p_range"] = top_p_range
    if "hook_free_probability" in raw:
        probability = _require_number(
            raw["hook_free_probability"], f"{context}.hook_free_probability"
        )
        if probability > 1:
            raise ValueError(f"{context}.hook_free_probability cannot exceed 1.")
        updates["hook_free_probability"] = probability
    if "search_enabled" in raw:
        updates["search_enabled"] = _require_bool(
            raw["search_enabled"], f"{context}.search_enabled"
        )

    if "news" in raw:
        if archetype != Archetype.NEWS or current.news is None:
            raise ValueError(f"{context}.news is only valid for the news archetype.")
        news_raw = _require_mapping(raw["news"], f"{context}.news")
        unknown_news = sorted(
            set(news_raw) - {"topics", "max_age_hours", "preferred_max_age_hours"}
        )
        if unknown_news:
            raise ValueError(
                f"{context}.news has unknown fields: {', '.join(unknown_news)}."
            )
        news = current.news
        topic_ids = (
            _apply_list_operation(
                news.topic_ids,
                news_raw["topics"],
                set(topics),
                f"{context}.news.topics",
            )
            if "topics" in news_raw
            else news.topic_ids
        )
        max_age = (
            _require_int(
                news_raw["max_age_hours"],
                f"{context}.news.max_age_hours",
                minimum=1,
            )
            if "max_age_hours" in news_raw
            else news.max_age_hours
        )
        preferred = (
            _require_int(
                news_raw["preferred_max_age_hours"],
                f"{context}.news.preferred_max_age_hours",
                minimum=1,
            )
            if "preferred_max_age_hours" in news_raw
            else news.preferred_max_age_hours
        )
        if preferred > max_age:
            raise ValueError(
                f"{context}.news preferred freshness cannot exceed max freshness."
            )
        updates["news"] = NewsPolicy(topic_ids, max_age, preferred)

    if "concert_check" in raw:
        if archetype != Archetype.CONCERT_CHECK or current.concert_check is None:
            raise ValueError(
                f"{context}.concert_check is only valid for concert_check."
            )
        concert_raw = _require_mapping(
            raw["concert_check"], f"{context}.concert_check"
        )
        unknown_concert = sorted(set(concert_raw) - {"countries"})
        if unknown_concert:
            raise ValueError(
                f"{context}.concert_check has unknown fields: "
                f"{', '.join(unknown_concert)}."
            )
        country_codes = (
            _apply_list_operation(
                current.concert_check.country_codes,
                concert_raw["countries"],
                set(countries),
                f"{context}.concert_check.countries",
                normalize=lambda item: item.upper(),
            )
            if "countries" in concert_raw
            else current.concert_check.country_codes
        )
        updates["concert_check"] = ConcertCheckPolicy(country_codes)

    return replace(current, **updates)


def _apply_archetype_overrides(
    current: Mapping[Archetype, ArchetypePolicy],
    value: Any,
    topics: Mapping[str, NewsTopicDefinition],
    countries: Mapping[str, ConcertCountryDefinition],
    context: str,
) -> dict[Archetype, ArchetypePolicy]:
    raw = _require_mapping(value, context)
    result = dict(current)
    for archetype_raw, override in raw.items():
        try:
            archetype = Archetype(str(archetype_raw))
        except ValueError as exc:
            raise ValueError(
                f"{context} references unknown archetype '{archetype_raw}'."
            ) from exc
        if archetype not in result:
            raise ValueError(f"{context} cannot override undefined archetype '{archetype.value}'.")
        result[archetype] = _apply_single_archetype_override(
            archetype,
            result[archetype],
            override,
            topics,
            countries,
            f"{context}.{archetype.value}",
        )
    return result


def load_archetype_policy_registry(
    path: pathlib.Path = ARCHETYPE_POLICY_CONFIG_PATH,
) -> ArchetypePolicyRegistry:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing archetype policy configuration: {path}") from None
    root = _require_mapping(payload, "archetype policy configuration")
    if root.get("version") != 1:
        raise ValueError("Archetype policy configuration version must be 1.")
    topics = _parse_news_topics(root.get("news_topics"))
    countries = _parse_concert_countries(root.get("concert_countries"))
    profiles_raw = _require_mapping(root.get("profiles"), "profiles")
    resolved: dict[str, ResolvedArchetypeProfile] = {}
    resolving: set[str] = set()

    def resolve_profile(name: str) -> ResolvedArchetypeProfile:
        if name in resolved:
            return resolved[name]
        if name in resolving:
            raise ValueError(f"Archetype profile inheritance cycle includes '{name}'.")
        try:
            raw = _require_mapping(profiles_raw[name], f"profile '{name}'")
        except KeyError as exc:
            raise ValueError(f"Unknown parent archetype profile '{name}'.") from exc
        unknown_profile_keys = sorted(
            set(raw) - {"extends", "archetypes", "archetype_overrides"}
        )
        if unknown_profile_keys:
            raise ValueError(
                f"profile '{name}' has unknown fields: {', '.join(unknown_profile_keys)}."
            )
        resolving.add(name)
        parent_name = str(raw.get("extends") or "").strip()
        if parent_name:
            parent = resolve_profile(parent_name)
            archetypes = dict(parent.archetypes)
        else:
            archetypes_raw = _require_mapping(
                raw.get("archetypes"), f"profile '{name}'.archetypes"
            )
            archetypes = {}
            for archetype_raw, policy_raw in archetypes_raw.items():
                try:
                    archetype = Archetype(str(archetype_raw))
                except ValueError as exc:
                    raise ValueError(
                        f"profile '{name}' references unknown archetype '{archetype_raw}'."
                    ) from exc
                archetypes[archetype] = _parse_archetype_policy(
                    archetype,
                    policy_raw,
                    topics,
                    countries,
                    f"profile '{name}'.archetypes.{archetype.value}",
                )
            missing = sorted(
                archetype.value
                for archetype in Archetype
                if archetype not in archetypes
            )
            if missing:
                raise ValueError(
                    f"root profile '{name}' is missing archetypes: {', '.join(missing)}."
                )
        if "archetypes" in raw and parent_name:
            raise ValueError(
                f"profile '{name}' must use archetype_overrides when it extends another profile."
            )
        if "archetype_overrides" in raw:
            archetypes = _apply_archetype_overrides(
                archetypes,
                raw["archetype_overrides"],
                topics,
                countries,
                context=f"profile '{name}'.archetype_overrides",
            )
        if not any(policy.enabled and policy.automatic for policy in archetypes.values()):
            raise ValueError(f"profile '{name}' has no enabled automatic archetypes.")
        resolving.remove(name)
        profile = ResolvedArchetypeProfile(
            name=name,
            archetypes=MappingProxyType(archetypes),
            news_topics=topics,
            concert_countries=countries,
        )
        resolved[name] = profile
        return profile

    for profile_name in profiles_raw:
        resolve_profile(str(profile_name))

    return ArchetypePolicyRegistry(
        profiles=MappingProxyType(resolved),
        news_topics=topics,
        concert_countries=countries,
    )


@lru_cache(maxsize=1)
def get_archetype_policy_registry() -> ArchetypePolicyRegistry:
    return load_archetype_policy_registry()


__all__ = [
    "ARCHETYPE_POLICY_CONFIG_PATH",
    "ArchetypePolicy",
    "ArchetypePolicyRegistry",
    "ConcertCheckPolicy",
    "ConcertCountryDefinition",
    "NewsPolicy",
    "NewsTopicDefinition",
    "ResolvedArchetypeProfile",
    "get_archetype_policy_registry",
    "load_archetype_policy_registry",
]
