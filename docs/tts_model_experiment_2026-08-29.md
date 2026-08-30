# TTS model experiment — 2026-08-29

## Decision

Keep Gemini 3.1 Flash TTS as the NeuralForge speech provider for now.

Gemini was the only tested model that produced an acceptable Argentine
Rioplatense accent. The English samples from the other providers were described
as "okay-ish," but their Spanish output was unacceptable. More explicit accent
instructions did not fix the non-Gemini voices.

This was an operational experiment performed with one-off API calls. No reusable
experiment command, provider adapter, or other application code was added.

## Objective

Compare quality, regional accent, instruction following, speed, and approximate
cost across several hosted TTS models while using real AI host scripts rather
than isolated demo sentences.

The most important requirement for NeuralForge was a natural, moderate
Argentine Rioplatense accent suitable for an adult music-radio host.

## Script generation

The existing host orchestrator generated two real `short_story` segments from
the live AzuraCast queues. Both cycles used `--dry-run`, so they generated local
text and Gemini baseline audio without uploading or queueing a segment.

```bash
.venv/bin/python -m neuralcast.cli.host_orchestrator \
  --channel neuralforge-es \
  --dry-run \
  --min-listeners 0 \
  --force-archetype short_story \
  --force-track-focus next

.venv/bin/python -m neuralcast.cli.host_orchestrator \
  --channel neuralcast-en \
  --dry-run \
  --min-listeners 0 \
  --force-archetype short_story \
  --force-track-focus next
```

The Spanish segment introduced Iron Savior's `The Creature's Tale` after
Kalmah's `Seventh Swamphony`. The English segment introduced Los Piojos'
`Tan Solo` after Iration's `Time Bomb`.

The generated scripts and comparison audio were copied into the ignored runtime
directory:

```text
runtime/tts_experiments/2026-08-29/
```

## Canonical NeuralForge instructions

Gemini used the production NeuralForge instruction file:

```text
src/neuralcast/assets/stories/prompts/locales/es-AR/neuralforge_tts_instructions.md
```

The relevant accent requirement is:

> Acento: Español argentino rioplatense natural y moderado, sin caricaturizarlo.

The full file also specifies an adult Argentine music-radio host, a natural
medium-low register, constant vocal support and energy, relaxed articulation,
conversational pacing, short functional pauses, and no gradual shift into an
airy or whispered voice.

## Round one

Round-one provider outputs used the same scripts, then were converted to mono
192 kbps MP3 and loudness-normalized to approximately -17 LUFS for listening.

| Model | Configuration | Spanish instruction support in this test | Result |
| --- | --- | --- | --- |
| Gemini 3.1 Flash TTS | `Charon`; full canonical NeuralForge instructions | Dedicated prompt/configuration path | Only acceptable Argentine accent |
| Qwen3-TTS | `Aiden`; Spanish; shortened radio-host style | Dedicated `style_instruction`, but the shortened round-one text accidentally omitted the Rioplatense requirement | Spanish accent was unacceptable |
| Inworld Realtime TTS 2 | `Dennis`; Spanish | Bracketed natural-language steering, but round one omitted the Rioplatense requirement | Spanish accent was unacceptable |
| MiniMax Speech 2.8 Turbo | `Deep_Voice_Man`; `language_boost=Spanish`; automatic emotion | Replicate exposes language, voice, emotion, pitch, and speed, but no free-form instruction field | Spanish accent was unacceptable |
| Kokoro 82M | `am_fenrir` | The deployed voice list did not contain a Spanish voice, so it was tested only in English | English was usable as a very cheap baseline |

The English comparison included Gemini, Qwen3-TTS, Inworld, MiniMax, and
Kokoro. Listener feedback characterized the non-Gemini English samples as
acceptable but only "okay-ish."

Approximate Replicate spend for round one, including the short API validation
request, was **$0.22**. At the time of the test, approximate costs for a
one-minute script were:

- Kokoro: $0.0004;
- Qwen3-TTS: $0.02;
- Inworld: $0.03;
- MiniMax: $0.06.

The Gemini baseline was billed outside Replicate.

## Round two: explicit accent direction

Round two repeated the Spanish script with stronger direction.

| Model | How the instructions were supplied | Result |
| --- | --- | --- |
| Qwen3-TTS | Full canonical NeuralForge instruction file passed verbatim through `style_instruction` | Still unacceptable |
| OpenAI GPT-4o Mini TTS | Full canonical file passed through the dedicated `instructions` field; `cedar` voice | Still unacceptable |
| ElevenLabs v3 on Replicate | Condensed Rioplatense direction prepended as a bracketed performance tag; `Roger` voice | Unacceptable and read the instruction tag aloud |
| Inworld Realtime TTS 2 | Faithful condensed version of the canonical instructions in bracketed steering syntax; `Dennis` voice | Still unacceptable |

Inworld's text field had a 2,000-character limit, so its instruction had to be
condensed to leave room for the approximately 1,000-character script. It still
included the explicit natural, moderate Rioplatense-accent requirement plus the
production register, pacing, articulation, and energy guidance.

Replicate's ElevenLabs v3 wrapper did not expose a separate instruction field.
Its bracket syntax reliably supports a limited set of audio tags, but the
custom regional-direction tag was treated as transcript and spoken aloud. The
Replicate endpoint also exposed only a fixed preset-voice list rather than the
native voices and professional voice clones available through ElevenLabs'
direct Voice Library. Retrying generic Replicate presets was therefore not
considered useful.

## Interpretation

The result is primarily a regional voice problem, not a generic Spanish-language
problem. A model can support Spanish while retaining the accent and cadence of
an English-oriented or non-Argentine preset voice.

Gemini succeeded because it followed the detailed performance instructions well
enough to produce the required regional accent with the selected production
voice. Qwen and Inworld still failed after explicit direction, while MiniMax did
not expose equivalent free-form control in the tested endpoint.

Credible future alternatives would require one of the following:

- a native Argentine voice from the direct ElevenLabs Voice Library;
- an authorized Argentine reference recording for voice cloning;
- Azure Speech's native `es-AR-TomasNeural` or `es-AR-ElenaNeural` voices.

Azure Speech and its `es-AR` voices were not available through Replicate when
the catalog was checked, so they were not tested.

## AzuraCast listening copies

Normalized copies were uploaded to NeuralForge station media storage for phone
listening:

```text
TTS Experiments/2026-08-29/
TTS Experiments/2026-08-29/Round 2 - Argentine Instructions/
```

Round-one uploads received AzuraCast media IDs `17087` through `17095`.
Round-two uploads received IDs `17096` through `17099`.

Only the station file-upload API was used. No playlist was created or modified,
no Liquidsoap command was sent, and none of the experiment files was queued for
broadcast.

## Limitations

- This was a practical listening test, not a controlled academic benchmark.
- It used one English script, one Argentine-Spanish script, and one preset voice
  per provider.
- Quality and accent judgments were made by listening, while runtime and cost
  came from provider metrics and pricing available on the experiment date.
- A native or authorized cloned Argentine voice could materially change the
  result for providers that support voice libraries or cloning.
- Runtime artifacts are intentionally ignored by Git and may be removed during
  operational cleanup; this document is the durable experiment record.
