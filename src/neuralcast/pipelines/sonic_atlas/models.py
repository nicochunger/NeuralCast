"""Pydantic models for the Sonic Atlas & Station Vault API payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RotationRole = Literal["themed", "open_only", "reserved_window"]


class AtlasTopArtist(BaseModel):
    name: str
    slug: str
    track_count: int


class AtlasSampleTrack(BaseModel):
    artist: str
    title: str
    album: str = ""
    year: int | None = None


class AtlasPlaylistNode(BaseModel):
    slug: str
    name: str
    description: str = ""
    placement_notes: str = ""
    rotation_role: RotationRole = "themed"
    track_count: int
    unique_artist_count: int
    min_year: int | None = None
    max_year: int | None = None
    decades: dict[str, int] = Field(default_factory=dict)
    top_artists: list[AtlasTopArtist] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)
    bridge_count: int = 0
    x: float
    y: float


class AtlasBridge(BaseModel):
    source: str
    target: str
    shared_count: int
    jaccard: float
    sample_tracks: list[AtlasSampleTrack] = Field(default_factory=list)


class AtlasDecadeTopPlaylist(BaseModel):
    slug: str
    name: str
    count: int


class AtlasDecadeTopArtist(BaseModel):
    name: str
    slug: str
    count: int


class AtlasDecadeBucket(BaseModel):
    decade: int
    label: str
    track_count: int
    unique_track_count: int
    top_playlists: list[AtlasDecadeTopPlaylist] = Field(default_factory=list)
    top_artists: list[AtlasDecadeTopArtist] = Field(default_factory=list)


class AtlasEpisodeRef(BaseModel):
    id: str
    station: str
    title: str
    episode_number: int | None = None
    description: str = ""
    published_at: str | None = None
    duration: float | None = None
    playlist_slugs: list[str] = Field(default_factory=list)
    matched_artists: list[str] = Field(default_factory=list)


class AtlasHostStory(BaseModel):
    archetype: str
    title: str
    summary: str = ""
    created_at: str | None = None
    track_title: str | None = None


class AtlasArtistTrack(BaseModel):
    artist: str
    title: str
    album: str = ""
    year: int | None = None
    playlists: list[str] = Field(default_factory=list)


class AtlasArtistEntry(BaseModel):
    slug: str
    name: str
    track_count: int
    min_year: int | None = None
    max_year: int | None = None
    playlists: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)
    host_stories: list[AtlasHostStory] = Field(default_factory=list)
    tracks: list[AtlasArtistTrack] = Field(default_factory=list)


class AtlasStats(BaseModel):
    playlist_count: int
    total_entries: int
    unique_tracks: int
    unique_artists: int
    bridge_count: int
    min_year: int | None = None
    max_year: int | None = None
    episode_count: int


class StationAtlasPayload(BaseModel):
    station: str
    station_name: str
    accent: str
    generated_at: str
    stats: AtlasStats
    playlists: list[AtlasPlaylistNode] = Field(default_factory=list)
    bridges: list[AtlasBridge] = Field(default_factory=list)
    decades: list[AtlasDecadeBucket] = Field(default_factory=list)
    episodes: list[AtlasEpisodeRef] = Field(default_factory=list)
    artists: list[AtlasArtistEntry] = Field(default_factory=list)


class PlaylistDetailTrack(BaseModel):
    artist: str
    artist_slug: str
    title: str
    album: str = ""
    year: int | None = None
    decade: int | None = None
    other_playlists: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)


class PlaylistDetailBridge(BaseModel):
    playlist_slug: str
    playlist_name: str
    shared_count: int
    jaccard: float
    sample_tracks: list[AtlasSampleTrack] = Field(default_factory=list)


class PlaylistDetailDecade(BaseModel):
    decade: int
    label: str
    track_count: int


class PlaylistDetailMatchedTrack(BaseModel):
    artist: str
    title: str


class PlaylistDetailEpisode(BaseModel):
    id: str
    station: str
    title: str
    episode_number: int | None = None
    description: str = ""
    published_at: str | None = None
    duration: float | None = None
    matched_tracks: list[PlaylistDetailMatchedTrack] = Field(default_factory=list)


class PlaylistDetailPayload(BaseModel):
    station: str
    playlist: AtlasPlaylistNode
    tracks: list[PlaylistDetailTrack] = Field(default_factory=list)
    bridges: list[PlaylistDetailBridge] = Field(default_factory=list)
    decades: list[PlaylistDetailDecade] = Field(default_factory=list)
    episodes: list[PlaylistDetailEpisode] = Field(default_factory=list)
