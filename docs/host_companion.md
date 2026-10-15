# Listener host companion

The radio player in the sister `neuralcast-web` project shows a reading card for
`news`, `concert_check`, `short_story`, `album_spotlight`, `era_snapshot`, and
`deep_dive`. Other ordinary archetypes do not publish cards or replace the current
card. Approved authored episodes add a chapter guide through the same popup; see
[episode_producer.md](episode_producer.md).

News cards reuse validated headlines and source links. Concert cards reuse
validated artists, dates, venues, cities, countries, and event links. The four
music-story archetypes request a title, concise summary, and up to three key
points in the existing script-generation call. There is no additional model call
for companion content. The companion must describe only claims already in the
spoken story, in the host's language. Optional conversational additions do not
change that researched summary; the full transcript includes the final edits.

Music output appends `<COMPANION>{...}</COMPANION>` after the spoken script.
`companion.py` separates it before speech validation or TTS. Missing or invalid
metadata omits the card while retaining usable speech. This optional contract
accepts old generators that return only speech. It does not repair invalid
summaries with another model request.

## Publication and timing

Only successful queue insertion publishes a record. Upload or insertion failures
and dry runs publish nothing. Queue success is treated as broadcast; there is no
playback-confirmation polling. Estimated start uses the existing remaining-track
time; estimated end adds the duration reported by the media upload. If duration
is unavailable, expiry is 20 minutes after estimated start.

A card becomes visible at estimated start. It expires 20 minutes after estimated
end, or is replaced by a newer eligible segment whose estimated start has arrived.
Future queued cards leave the currently visible card in place. Ordinary segments
have no effect. Timing is an estimate; unusual queue delays can shift actual speech.

The player polls every 15 seconds and locally checks expiry. Opening the recap popup
pins that card until it is closed, even if it expires or another card arrives.
Changing station or host language resets the card and transcript. UI labels follow
the app language; editorial content follows the selected host channel language.

## Persistence and API

`host_companion.json` sits next to the channel's orchestrator state: legacy channels
use station `metadata/`, and other channels use `metadata/host_channels/<channel>/`.
The existing host lock serializes writes; records are written atomically with
`fsync` and rename. Writes retain at most 100 records from the previous seven days.
These files are runtime data and Git-ignored. Reads tolerate absent/corrupt data.
Companion preparation or persistence failure never prevents audio publication.

The authenticated VPS endpoint is:

```text
GET /admin/host-channels/{channel}/companion
Authorization: Bearer <admin token>
```

It returns `{ "card": null }` or the latest visible record, with `Cache-Control:
no-store`. Unknown channel IDs are rejected. No provider or AzuraCast request is
needed to read a card.

The web app exposes `/api/host-companion/{stationId}?channel={channelId}`. It checks
that the channel belongs to the requested station and calls the VPS endpoint
server-side with the existing `HOST_ADMIN_BASE_URL` and `HOST_ADMIN_TOKEN`.
Public listeners receive card content only; credentials stay on the server.
Missing backend configuration hides the feature. Temporary read failures hide the
unpinned card without affecting playback. Source links accept HTTP(S) only.

The UI lives in `HostCompanionCard.tsx` and its CSS module. It does not own or
modify the persistent audio element. Artwork lookup is not part of this version.

## Deployment and validation

Restart `neuralcast-admin-api` after deploying the endpoint and verify `/healthz`.
Cron-launched host cycles load the generation change on their next invocation.
Deploy the sister web app through its existing deployment process; no new secrets,
cron entries, or live queue operations are required. Existing snippets do not get
cards retroactively; the feature begins with newly generated eligible segments.

Offline tests cover output separation, optional-summary failure, same-call
metadata, structured facts, eligible archetypes, channel isolation, expiry,
future-card replacement, atomic storage, queue/upload failures, optional storage
failure, and API authentication. Web validation uses TypeScript, the production
build, and the persistent-audio architecture check.
