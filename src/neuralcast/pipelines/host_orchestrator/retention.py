"""Host asset retention independent of playback, listeners, and enabled channels."""

from .assets import cleanup_local_stories, cleanup_remote_stories
from .channels import HostChannelRegistry, get_channel_registry
from .transport import AzuraCastClient


def cleanup_host_assets(
    client: AzuraCastClient,
    *,
    keep_local_days: int = 3,
    keep_remote_days: int = 7,
    dry_run: bool = False,
    registry: HostChannelRegistry | None = None,
) -> None:
    """Retain each channel's assets through its active media-owner station API.

    Include paused channels so their snippets can expire without running their
    host. Scope remains each configured prefix; podcast uploads are excluded.
    """
    registry = registry or get_channel_registry()
    for channel in registry.channels.values():
        cleanup_local_stories(channel.key, keep_local_days, dry_run=dry_run)
        cleanup_remote_stories(
            client,
            channel.media_owner_station,
            keep_remote_days,
            channel.remote_prefix,
            media_owner_station=channel.media_owner_station,
            dry_run=dry_run,
        )
