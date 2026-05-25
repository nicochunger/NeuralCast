# Integration tests

Default `pytest` excludes `integration` and `live` markers.

Use this tree for tests that need configured services or installed system tools,
such as AzuraCast, Gemini, yt-dlp, ffmpeg, mp3gain, or rsync.

Integration tests must skip cleanly when their required environment variables or
executables are missing.
