# NeuralCast Domain Context

## Station playlist catalog

The station playlist catalog is the station-scoped collection of playlist CSV
definitions and their companion metadata. It owns playlist row parsing and
round-tripping, song identity, deletion markers, validation-state persistence,
and consistency between `New Releases.csv` and
`metadata/New Releases.metadata.json`.

The station playlist catalog does not own track discovery, track validation,
audio downloading or tagging, remote media synchronization, or the policy for
choosing which releases belong in a playlist.
