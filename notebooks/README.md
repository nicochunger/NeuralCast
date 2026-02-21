# Notebooks

- `tests.ipynb`, `test_album_art.ipynb`, `test_get_album.ipynb`, `tts_snippets.ipynb` hold manual experiments for album art, lookup logic, and TTS/story generation.
- `host_orchestrator_playground.ipynb` is a minimal local-only orchestrator sandbox: set mock current/next songs, print the generated host script using real orchestrator helpers, and optionally create a local TTS MP3 (no AzuraCast interaction).
- Run them from the repo root with `src/` on `PYTHONPATH` (`export PYTHONPATH=$PWD/src`) so imports like `import neuralcast...` resolve.
- Notebooks may read/write under `src/neuralcast/assets/stories/snippets/` for generated audio/text artifacts.
