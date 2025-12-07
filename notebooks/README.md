# Notebooks

- `tests.ipynb`, `test_album_art.ipynb`, `test_get_album.ipynb`, `tts_snippets.ipynb` hold manual experiments for album art, lookup logic, and TTS/story generation.
- Run them from the repo root with `src/` on `PYTHONPATH` (`export PYTHONPATH=$PWD/src`) so imports like `import neuralcast...` resolve.
- Notebooks may read/write under `src/neuralcast/assets/stories/snippets/` for generated audio/text artifacts.
