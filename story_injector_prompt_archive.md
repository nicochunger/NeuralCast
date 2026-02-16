# Story Injector Prompt Archive (Pre-Orchestrator)

This archive captures the **exact prompts** and prompt-construction flow from the stories-only injector system that existed before commit `8a5be84` (`New ai host orchestrator`).

## Snapshot A: Simpler Variations System

- Commit: `53a54b5` (2025-10-29)
- Files at that point:
  - `inject_story_snippet.py`
  - `stories/story_prompt.md`
  - `stories/tts_story_instructions.md`
  - `story_variation.py`

### A1) Story Prompt Template (Global Base Prompt)

```md
Write a short story about the song “[TITLE]” by [ARTIST], told in the voice of an Argentine radio announcer as if speaking live on [STATION].

Requirements:

* The story **must be written in Spanish (Rioplatense)**.
* Tone: natural, calm, spontaneous — like someone speaking live from a radio studio (think Aspen-style warmth), not reading a rehearsed script.
* Voice: serene, mature, slightly nostalgic, authentic. Don’t dramatize or overact.
* Because this goes out immediately after the song ends, acknowledge it — e.g. “recién escuchamos…”, “eso fue…”, “acabamos de escuchar…”. etc
* Conclude naturally by previewing what's coming up next: "[NEXT_TITLE]" by [NEXT_ARTIST] (say it like a warm radio segue, not robotic).
* Use natural filler words and small hesitations to sound human, e.g. “bueno…”, “viste…”, “no sé…”, “lo loco es que…”, “en realidad…”, short pauses, etc.
* Avoid grandiloquent or poetic lines — it should sound like a simple, conversational recollection or anecdote about the song.
* Length: brief — aim for roughly **150–250 words** so it fits into ~45–90 seconds on air.
* Keep it spontaneous, with natural rhythm and small colloquial touches, nothing that sounds obviously scripted.
* Do not include links, web addresses, or numeric reference markers like “[1]”.

Style cues to weave into the narration (mantain the Aspen warmth while applying them):

* {{INTRO_STYLE}}
* {{BODY_STYLE}}
* {{OUTRO_STYLE}}
* {{FILLER_WORDS}}

Fact-checking:

* Before including any factual claims (dates, recording facts, chart positions, anecdotes), research online and verify them.
* Only include facts that can be confirmed; if something isn’t verifiable, omit it rather than guessing.
* If there are, try to include little fun facts beyond just generic things like release date and key people involved.
```

### A2) Narrative Variation Overlay Definitions (Injected into `{{INTRO_STYLE}}`, `{{BODY_STYLE}}`, `{{OUTRO_STYLE}}`, `{{FILLER_WORDS}}`)

```python
NARRATIVE_VARIANTS: Sequence[NarrativeVariant] = (
    NarrativeVariant(
        style_id="warm-anecdote",
        description="Arranque cálido con recuerdo personal y giro anecdótico ligero.",
        intro_instruction="Arrancá como si recordarás una tarde de radio en que descubriste la canción, con un saludo cercano.",
        body_instruction="Sumá un detalle concreto de la historia del tema, contado como anécdota que escuchaste entre colegas o en la producción.",
        outro_instruction="Cerrá destacando por qué combina bien con la próxima canción, enlazando sensaciones.",
        filler_words="meté muletillas suaves como 'mirá', 'sabés que', 'posta' para mantener la charla viva.",
    ),
    NarrativeVariant(
        style_id="curious-fact",
        description="Giro curiosidad, resaltando un dato poco conocido con tono cómplice.",
        intro_instruction="Empezá sorprendiendo con un 'vos sabés que...' como si recién hubieras descubierto un dato escondido.",
        body_instruction="Contá un detalle curioso o poco difundido del proceso creativo o una colaboración inesperada.",
        outro_instruction="Invitá al oyente a quedarse para escuchar el próximo tema porque complementa esa curiosidad.",
        filler_words="usá coletillas como 'te juro', 'de hecho', 'lo loco es que' para reforzar complicidad.",
    ),
    NarrativeVariant(
        style_id="listener-memory",
        description="Se enfoca en recuerdos compartidos y escenas cotidianas con la audiencia.",
        intro_instruction="Abrí evocando una escena cotidiana en la que esta canción suele aparecer, como un viaje en auto o una sobremesa.",
        body_instruction="Conectá la letra o la melodía con una sensación compartida, como mates entre amigas o un paseo por la ciudad.",
        outro_instruction="Presentá la próxima canción como continuidad de ese momento compartido.",
        filler_words="sumá expresiones como 'viste', 'no sé si te pasa', 'me encanta cuando' para generar cercanía.",
    ),
    NarrativeVariant(
        style_id="studio-behind-scenes",
        description="Relato detrás de escena desde la cabina del estudio.",
        intro_instruction="Presentá la canción desde la perspectiva del estudio, como si estuvieras mostrando la consola a alguien.",
        body_instruction="Compartí un detalle técnico o de producción que hayas comentado al aire con el equipo.",
        outro_instruction="Dale paso al próximo tema como si fuera parte de la programación cuidada del estudio.",
        filler_words="incorporá frases como 'acá entre nosotros', 'te cuento', 'en cabina' para pintar el ambiente.",
    ),
    NarrativeVariant(
        style_id="city-walk",
        description="Paseo por Buenos Aires mientras suena la canción.",
        intro_instruction="Invitá a caminar por un rincón de Buenos Aires evocando sonidos de la ciudad que combinan con la canción.",
        body_instruction="Mencioná detalles sensoriales —olor a café, tranvías imaginarios, alguna plaza— enlazados con el tema.",
        outro_instruction="Cerrá sugiriendo que la próxima canción acompaña la misma caminata con otro ánimo.",
        filler_words="sumá frases como 'ponele', 'me pasa que', 'medio así' para mantener ritmo relajado.",
    ),
)

```

### A3) TTS Base Instructions Template

```md
La voz suena natural y cercana, con un acento argentino suave y cotidiano.
El tono es tranquilo, sin dramatizar ni sobreactuar.
Se habla con la cadencia de alguien que está contando algo que recuerda con claridad, no como quien interpreta un texto.

Las frases tienen un ritmo pausado pero espontáneo, con breves silencios donde la respiración se siente real.
No hay intención de emocionar; la emoción aparece sola, en la sinceridad del tono.

La voz transmite madurez y serenidad, sin impostar profundidad ni buscar efectos.
Debe sentirse como una conversación relajada, como si el locutor estuviera en el estudio al final del día, compartiendo un dato o un pensamiento sobre una canción que lo marcó.

La dicción es clara pero no perfecta: se permiten pequeñas inflexiones naturales, como las que aparecen cuando uno habla sin apuro.
Nada suena ensayado.

Ajustes específicos para esta historia:

{{DELIVERY_VARIATION}}
{{PACE_ADJUSTMENT}}
{{DELIVERY_ADDITIONAL}}
```

### A4) TTS Delivery Variation Overlay Definitions (Injected into `{{DELIVERY_VARIATION}}`, `{{PACE_ADJUSTMENT}}`, `{{DELIVERY_ADDITIONAL}}`)

```python
DELIVERY_VARIANTS: Sequence[DeliveryVariant] = (
    DeliveryVariant(
        style_id="calm-late-night",
        description="Entonación calma, respiraciones amplias, clima nocturno.",
        delivery_instruction="Pedile a la voz que suene como al final de la noche en cabina, con un susurro amable al presentar el dato clave.",
        pace_instruction="Indicá que se tome microsilencios antes de cada frase importante, manteniendo tempo lento.",
        additional_prompts="Aclarar que las sonrisas son apenas insinuadas, casi como quien charla con luces bajas.",
    ),
    DeliveryVariant(
        style_id="bright-morning",
        description="Toque matinal con energía contenida y ritmo dinámico.",
        delivery_instruction="Sugerí que deje entrar un poquito más de brillo al mencionar el artista, como café de la mañana.",
        pace_instruction="Pedí un ritmo apenas más ágil, con pausas cortas y marcadas para sostener claridad.",
        additional_prompts="Recordá que no se convierta en euforia: solo un entusiasmo suave y confiable.",
    ),
    DeliveryVariant(
        style_id="storyteller-intimate",
        description="Narración íntima, como confidencia uno a uno.",
        delivery_instruction="Explicá que debe sonar como quien comparte un secreto con un oyente en particular.",
        pace_instruction="Invitá a dejar un silencio notable entre el clímax de la anécdota y el anticipo del siguiente tema.",
        additional_prompts="Reforzá que las respiraciones se oigan naturales y que cierre con un suspiro apenas perceptible.",
    ),
    DeliveryVariant(
        style_id="rhythmic-groove",
        description="Cadencia con leve swing, acompañando el beat de la canción anterior.",
        delivery_instruction="Solicitá que marque un pulso suave con la voz, como si siguiera golpeando el pie al ritmo del tema.",
        pace_instruction="Pedí pausas sincronizadas con compases de cuatro tiempos, dando sensación de groove.",
        additional_prompts="Mencioná que resalte palabras clave con una micro-subida de energía y vuelva al tono cálido inmediatamente.",
    ),
    DeliveryVariant(
        style_id="sunset-reflection",
        description="Reflexivo, evocando atardecer y cierre de jornada.",
        delivery_instruction="Indicá que se escuche como quien mira el atardecer desde la ventana del estudio.",
        pace_instruction="Solicitá un tempo medio con caídas suaves al final de cada frase.",
        additional_prompts="Sumá que al presentar la canción siguiente deje una sonrisa audible y un 'quedate ahí' muy sutil.",
    ),
)


```

### A5) Exact Prompt Assembly Code (Story Text)

```python
def generate_story_text(
    artist: str,
    title: str,
    station: str,
    next_artist: str,
    next_title: str,
    narrative_variant: NarrativeVariant,
) -> str:
    template = STORY_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template
    replacements = {
        "ARTIST": artist,
        "TITLE": title,
        "STATION": station,
        "NEXT_ARTIST": next_artist,
        "NEXT_TITLE": next_title,
    }
    for key, value in replacements.items():
        prompt = prompt.replace(f"[{key}]", value)

    variant_replacements = {
        "INTRO_STYLE": narrative_variant.intro_instruction,
        "BODY_STYLE": narrative_variant.body_instruction,
        "OUTRO_STYLE": narrative_variant.outro_instruction,
        "FILLER_WORDS": narrative_variant.filler_words,
    }
    for key, value in variant_replacements.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)

    story = openai_text_completion(prompt=prompt, model="gpt-5-search-api")
    return cleanup_story_text(story)


```

### A6) Exact Prompt Assembly Code (TTS Instructions)

```python
def synthesize_story_audio(
    story_text: str,
    outfile: pathlib.Path,
    delivery_variant: DeliveryVariant,
) -> None:
    tts_template = TTS_INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    instructions = tts_template
    delivery_replacements = {
        "DELIVERY_VARIATION": delivery_variant.delivery_instruction,
        "PACE_ADJUSTMENT": delivery_variant.pace_instruction,
        "DELIVERY_ADDITIONAL": delivery_variant.additional_prompts,
    }
    for key, value in delivery_replacements.items():
        instructions = instructions.replace(f"{{{{{key}}}}}", value)

    openai_speech(
        text=story_text,
        outfile=str(outfile),
        model="gpt-4o-mini-tts",
        voice="ash",
        instructions=instructions,
    )


```

### A7) Variant Selection Code (How style overlays were selected)

```python
def compute_story_seed(
    station: str,
    artist: str,
    title: str,
    next_artist: str,
    next_title: str,
) -> str:
    """Build a stable seed string from story context values."""

    parts = [
        station.strip().lower(),
        artist.strip().lower(),
        title.strip().lower(),
        next_artist.strip().lower(),
        next_title.strip().lower(),
    ]
    combined = "|".join(parts)
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return digest


def deterministic_variant_choice(
    seed: str,
    variants: Sequence[T],
    recent_ids: Sequence[str],
    avoid_window: int,
    id_getter: Optional[Callable[[T], str]] = None,
) -> Tuple[int, T]:
    """Choose a variant deterministically while avoiding the most recent IDs.

    Example:
        >>> seed = compute_story_seed("neuralcast", "Artista", "Tema", "Proximo", "Cancion")
        >>> idx, variant = deterministic_variant_choice(seed, NARRATIVE_VARIANTS, [], 2)
        >>> variant.style_id in {item.style_id for item in NARRATIVE_VARIANTS}
        True
    """

    if not variants:
        raise ValueError("variants must not be empty")

    if id_getter is None:
        id_getter = lambda item: getattr(item, "style_id", "")

    rng = random.Random(_hash_to_int(seed))
    order = list(range(len(variants)))
    rng.shuffle(order)

    recent_list = list(recent_ids)
    avoided = set(recent_list[:avoid_window]) if avoid_window > 0 else set()
    for index in order:
        candidate = variants[index]
        candidate_id = id_getter(candidate)
        if candidate_id not in avoided:
            return index, candidate

    # Fallback: deterministic first element from shuffled order
    fallback_index = order[0]
    return fallback_index, variants[fallback_index]


```

### A8) Runtime Selection + Prompt Build Callsite

```python
    story_seed = compute_story_seed(
        station=args.station,
        artist=selected_track.artist,
        title=selected_track.title,
        next_artist=following_track.artist,
        next_title=following_track.title,
    )
    history = load_style_history(STYLE_HISTORY_PATH)
    narrative_recent = list(iter_recent_ids(history, args.station, "narrative_id"))
    delivery_recent = list(iter_recent_ids(history, args.station, "delivery_id"))
    _, narrative_variant = deterministic_variant_choice(
        seed=f"{story_seed}|narrative",
        variants=NARRATIVE_VARIANTS,
        recent_ids=narrative_recent,
        avoid_window=NARRATIVE_AVOID_WINDOW,
    )
    _, delivery_variant = deterministic_variant_choice(
        seed=f"{story_seed}|delivery",
        variants=DELIVERY_VARIANTS,
        recent_ids=delivery_recent,
        avoid_window=DELIVERY_AVOID_WINDOW,
    )
    print(
        f"Narrative style selected: {narrative_variant.style_id} — {narrative_variant.description}"
    )
    print(f"Delivery style selected: {delivery_variant.style_id} — {delivery_variant.description}")

    story_text = generate_story_text(
        selected_track.artist,
        selected_track.title,
        station_display_name,
        following_track.artist,
        following_track.title,
        narrative_variant,
    )
```

---

## Snapshot B: Last Stories-Only Version Before Orchestrator

- Commit: `58a92b4` (2026-01-19)
- Files at that point:
  - `src/neuralcast/pipelines/story_injector.py`
  - `src/neuralcast/assets/stories/story_prompt.md`
  - `src/neuralcast/assets/stories/tts_story_instructions.md`
  - `src/neuralcast/stories/variation.py`
  - `src/neuralcast/services/openai_client.py`

### B1) Story Prompt Template (Global Base Prompt)

```md
Write a short story about the song “[TITLE]” by [ARTIST], told in the voice of an Argentine radio announcer as if speaking live on [STATION].

Requirements:

* The story **must be written in Spanish (Rioplatense)**.
* Tone: natural, calm, spontaneous — like someone speaking live from a radio studio (think Aspen-style warmth), not reading a rehearsed script.
* Voice: serene, mature, slightly nostalgic, authentic. Don’t dramatize or overact.
* Because this goes out immediately after the song ends, acknowledge it — e.g. “recién escuchamos…”, “eso fue…”, “acabamos de escuchar…”. etc
* Conclude naturally by previewing what's coming up next: "[NEXT_TITLE]" by [NEXT_ARTIST] (say it like a warm radio segue, not robotic).
* Use natural filler words and small hesitations to sound human, but keep them subtle; they are optional, and if they do not fit, omit them. Example mix: “bueno…”, “viste…”, “no sé…”, “che…”, “mirá…”, “en realidad…”, “la verdad…”, “bah…”, “qué sé yo…”, “ponele…”, “como que…”, “te juro…”, “nada…”, short pauses, etc.
* Avoid grandiloquent or poetic lines — it should sound like a simple, conversational recollection or anecdote about the song.
* Length: brief — aim for roughly **150–250 words** so it fits into ~45–90 seconds on air.
* Keep it spontaneous, with natural rhythm and small colloquial touches, nothing that sounds obviously scripted.
* Do not include links, web addresses, or numeric reference markers like “[1]”.

Style cues to weave into the narration (mantain the Aspen warmth while applying them):

* {{INTRO_STYLE}}
* {{BODY_STYLE}}
* {{OUTRO_STYLE}}
* {{FILLER_WORDS}}

Fact-checking:

* Before including any factual claims (dates, recording facts, chart positions, anecdotes), research online and verify them.
* Only include facts that can be confirmed; if something isn’t verifiable, omit it rather than guessing.
* If there are, try to include little fun facts beyond just generic things like release date and key people involved.
```

### B2) Narrative Variation Overlay Definitions

```python
NARRATIVE_VARIANTS: Sequence[NarrativeVariant] = (
    NarrativeVariant(
        style_id="warm-anecdote",
        description="Arranque cálido con recuerdo personal y giro anecdótico ligero.",
        intro_instruction="Arrancá como si recordarás una tarde de radio en que descubriste la canción, con un saludo cercano.",
        body_instruction="Sumá un detalle concreto de la historia del tema, contado como anécdota que escuchaste entre colegas o en la producción.",
        outro_instruction="Cerrá destacando por qué combina bien con la próxima canción, enlazando sensaciones.",
        filler_words="meté muletillas suaves como 'mirá', 'sabés que', 'posta' para mantener la charla viva.",
    ),
    NarrativeVariant(
        style_id="curious-fact",
        description="Giro curiosidad, resaltando un dato poco conocido con tono cómplice.",
        intro_instruction="Empezá sorprendiendo con un 'vos sabés que...' como si recién hubieras descubierto un dato escondido.",
        body_instruction="Contá un detalle curioso o poco difundido del proceso creativo o una colaboración inesperada.",
        outro_instruction="Invitá al oyente a quedarse para escuchar el próximo tema porque complementa esa curiosidad.",
        filler_words="usá coletillas como 'te juro', 'de hecho', 'lo loco es que' para reforzar complicidad.",
    ),
    NarrativeVariant(
        style_id="listener-memory",
        description="Se enfoca en recuerdos compartidos y escenas cotidianas con la audiencia.",
        intro_instruction="Abrí evocando una escena cotidiana en la que esta canción suele aparecer, como un viaje en auto o una sobremesa.",
        body_instruction="Conectá la letra o la melodía con una sensación compartida, como mates entre amigas o un paseo por la ciudad.",
        outro_instruction="Presentá la próxima canción como continuidad de ese momento compartido.",
        filler_words="sumá expresiones como 'viste', 'no sé si te pasa', 'me encanta cuando' para generar cercanía.",
    ),
    NarrativeVariant(
        style_id="studio-behind-scenes",
        description="Relato detrás de escena desde la cabina del estudio.",
        intro_instruction="Presentá la canción desde la perspectiva del estudio, como si estuvieras mostrando la consola a alguien.",
        body_instruction="Compartí un detalle técnico o de producción que hayas comentado al aire con el equipo.",
        outro_instruction="Dale paso al próximo tema como si fuera parte de la programación cuidada del estudio.",
        filler_words="incorporá frases como 'acá entre nosotros', 'te cuento', 'en cabina' para pintar el ambiente.",
    ),
    NarrativeVariant(
        style_id="sonic-postcard",
        description="Postal sonora breve desde la cabina o la sala, con detalles acústicos concretos.",
        intro_instruction="Abrí como si apoyarás el micrófono al ambiente: mencioná una luz tenue, el zumbido de la consola o un rebote de reverb.",
        body_instruction="Enlazá la canción con un detalle audible específico (una cola de reverb, un delay corto, un paneo raro) y cómo te hace sentir en cabina.",
        outro_instruction="Cerrá dejando flotando un sonido imaginado que anticipa la próxima pista, como si la mezcla siguiera viva.",
        filler_words="usá onomatopeyas suaves tipo 'shh', 'clac', 'mmm' y guiños como 'escuchá esto' para mantener textura.",
    ),
    NarrativeVariant(
        style_id="time-shift-echo",
        description="Salto temporal a un año concreto, conectando clima cultural con la canción y hoy.",
        intro_instruction="Arrancá anclando la canción en un año preciso con un detalle palpable (un objeto, slang, programa de TV o gadget).",
        body_instruction="Contrastá cómo sonaba o se vivía entonces frente a ahora, sin nostalgia melosa: marcá diferencias táctiles.",
        outro_instruction="Presentá el próximo tema como eco actualizado de aquel año, invitando a escuchar el diálogo entre épocas.",
        filler_words="sumá marcas temporales tipo 'en esa época', 'cuando recién', 'mirá lo que cambió' sin caer en 'qué tiempos aquellos'.",
    ),
    NarrativeVariant(
        style_id="micro-instrument-focus",
        description="Zoom a un gesto mínimo de un instrumento y la historia que abre.",
        intro_instruction="Introducí la canción nombrando un detalle microscópico: un hi-hat respirando, un slide de bajo, un patch de sintetizador.",
        body_instruction="Contá una mini-historia o contexto alrededor de ese gesto (quién lo decidió, cómo se grabó, qué inspiró).",
        outro_instruction="Dale paso al siguiente tema destacando otro instrumento que continuará la conversación sonora.",
        filler_words="meté frases como 'fijate en', 'se escucha apenas', 'ese matiz' para dirigir la escucha sin sobre-explicar.",
    ),
    NarrativeVariant(
        style_id="chain-reaction",
        description="Traza un enlace inesperado entre esta canción y otra influencia que empuja a la siguiente.",
        intro_instruction="Arrancá revelando una conexión rara: un sample escondido, un músico de sesión, un remix olvidado.",
        body_instruction="Explicá cómo ese enlace modificó el carácter de la canción o disparó algo en otra escena o género.",
        outro_instruction="Cerrá adelantando que la próxima canción sigue esa reacción en cadena y qué parte de la vibra hereda.",
        filler_words="usá guiños como 'seguí el hilo', 'esto no es casual', 'mirá la cadena' para mantener tensión narrativa.",
    ),
)

```

### B3) TTS Base Instructions Template

```md
La voz suena natural y cercana, con un acento argentino suave y cotidiano.
El tono es tranquilo, sin dramatizar ni sobreactuar.
Se habla con la cadencia de alguien que está contando algo que recuerda con claridad, no como quien interpreta un texto.

Las frases tienen un ritmo pausado pero espontáneo, con breves silencios donde la respiración se siente real.
No hay intención de emocionar; la emoción aparece sola, en la sinceridad del tono.

La voz transmite madurez y serenidad, sin impostar profundidad ni buscar efectos.
Debe sentirse como una conversación relajada, como si el locutor estuviera en el estudio al final del día, compartiendo un dato o un pensamiento sobre una canción que lo marcó.

La dicción es clara pero no perfecta: se permiten pequeñas inflexiones naturales, como las que aparecen cuando uno habla sin apuro.
Nada suena ensayado.

Ajustes específicos para esta historia:

{{DELIVERY_VARIATION}}
{{PACE_ADJUSTMENT}}
{{DELIVERY_ADDITIONAL}}
```

### B4) TTS Delivery Variation Overlay Definitions

```python
DELIVERY_VARIANTS: Sequence[DeliveryVariant] = (
    DeliveryVariant(
        style_id="calm-late-night",
        description="Entonación calma, respiraciones amplias, clima nocturno.",
        delivery_instruction="Pedile a la voz que suene como al final de la noche en cabina, con un susurro amable al presentar el dato clave.",
        pace_instruction="Indicá que se tome microsilencios antes de cada frase importante, manteniendo tempo lento.",
        additional_prompts="Aclarar que las sonrisas son apenas insinuadas, casi como quien charla con luces bajas.",
    ),
    DeliveryVariant(
        style_id="bright-morning",
        description="Toque matinal con energía contenida y ritmo dinámico.",
        delivery_instruction="Sugerí que deje entrar un poquito más de brillo al mencionar el artista, como café de la mañana.",
        pace_instruction="Pedí un ritmo apenas más ágil, con pausas cortas y marcadas para sostener claridad.",
        additional_prompts="Recordá que no se convierta en euforia: solo un entusiasmo suave y confiable.",
    ),
    DeliveryVariant(
        style_id="storyteller-intimate",
        description="Narración íntima, como confidencia uno a uno.",
        delivery_instruction="Explicá que debe sonar como quien comparte un secreto con un oyente en particular.",
        pace_instruction="Invitá a dejar un silencio notable entre el clímax de la anécdota y el anticipo del siguiente tema.",
        additional_prompts="Reforzá que las respiraciones se oigan naturales y que cierre con un suspiro apenas perceptible.",
    ),
    DeliveryVariant(
        style_id="rhythmic-groove",
        description="Cadencia con leve swing, acompañando el beat de la canción anterior.",
        delivery_instruction="Solicitá que marque un pulso suave con la voz, como si siguiera golpeando el pie al ritmo del tema.",
        pace_instruction="Pedí pausas sincronizadas con compases de cuatro tiempos, dando sensación de groove.",
        additional_prompts="Mencioná que resalte palabras clave con una micro-subida de energía y vuelva al tono cálido inmediatamente.",
    ),
    DeliveryVariant(
        style_id="sunset-reflection",
        description="Reflexivo, evocando atardecer y cierre de jornada.",
        delivery_instruction="Indicá que se escuche como quien mira el atardecer desde la ventana del estudio.",
        pace_instruction="Solicitá un tempo medio con caídas suaves al final de cada frase.",
        additional_prompts="Sumá que al presentar la canción siguiente deje una sonrisa audible y un 'quedate ahí' muy sutil.",
    ),
    DeliveryVariant(
        style_id="off-air-whisper",
        description="Inicio como si el mic estuviera apagado, acercamiento íntimo antes de abrir señal.",
        delivery_instruction="Pedí que arranque con voz al ras del mic, casi privada, y que suba apenas la presencia al dar el dato clave.",
        pace_instruction="Tempo corto con respiraciones audibles y una micro-pausa justo al 'salir al aire'.",
        additional_prompts="Aclarar que las 's' y 'f' se suavicen como si cuidara no activar el limitador; cerrar con un 'seguí ahí' mínimo.",
    ),
    DeliveryVariant(
        style_id="tape-saturated",
        description="Color de cinta ligeramente warble, consonantes redondeadas.",
        delivery_instruction="Indicá que imagine la voz pasando por una cassette con borde cálido: ataque lento, graves redondeados.",
        pace_instruction="Pedir un tempo medio-lento con caídas suaves, como si el motor de la cinta aflojara levemente.",
        additional_prompts="Sumá que arrastre un poco las vocales largas y deje un pequeño 'wow/flutter' imaginario al final de frases.",
    ),
    DeliveryVariant(
        style_id="metronome-led",
        description="Frases acompasadas a un click interior entre 92-96 BPM sin sonar robótico.",
        delivery_instruction="Solicitá que marque leves acentos en cada cuatro tiempos, como quien cabecea al beat anterior.",
        pace_instruction="Pedir que pause micro-segundos en el 2 y 4, manteniendo flujo natural.",
        additional_prompts="Recordá que no rapee: solo deja que el pulso se note en respiraciones y cierres de palabra.",
    ),
    DeliveryVariant(
        style_id="fade-in-handoff",
        description="Entrada como si viniera de otro locutor o una charla interna, ajustando foco al aire.",
        delivery_instruction="Pedí que inicie mid-thought, luego afine dicción en la segunda frase y aterrice en tono nítido.",
        pace_instruction="Tempo medio con primer frase más difusa y las siguientes claras; cierre con un fader imaginario bajando.",
        additional_prompts="Cerrá con un guiño tipo 'seguí ahí' o 'queda lo mejor' mientras baja la intensidad de voz.",
    ),
)

```

### B5) Curated Pairings Layer (Added on top of simple deterministic selection)

```python
PREFERRED_PAIRINGS: Sequence[VariantPairing] = (
    VariantPairing("warm-anecdote", "calm-late-night", weight=2),
    VariantPairing("warm-anecdote", "storyteller-intimate", weight=1),
    VariantPairing("curious-fact", "bright-morning", weight=2),
    VariantPairing("curious-fact", "metronome-led", weight=1),
    VariantPairing("listener-memory", "sunset-reflection", weight=2),
    VariantPairing("listener-memory", "calm-late-night", weight=1),
    VariantPairing("studio-behind-scenes", "fade-in-handoff", weight=1),
    VariantPairing("studio-behind-scenes", "tape-saturated", weight=1),
    VariantPairing("sonic-postcard", "tape-saturated", weight=2),
    VariantPairing("sonic-postcard", "off-air-whisper", weight=1),
    VariantPairing("time-shift-echo", "storyteller-intimate", weight=2),
    VariantPairing("micro-instrument-focus", "metronome-led", weight=2),
    VariantPairing("micro-instrument-focus", "tape-saturated", weight=1),
    VariantPairing("chain-reaction", "rhythmic-groove", weight=2),
    VariantPairing("chain-reaction", "fade-in-handoff", weight=1),
)


```

### B6) Exact Prompt Assembly Code (Story Text)

```python
def generate_story_text(
    artist: str,
    title: str,
    station: str,
    next_artist: str,
    next_title: str,
    narrative_variant: NarrativeVariant,
) -> str:
    template = STORY_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template
    replacements = {
        "ARTIST": artist,
        "TITLE": title,
        "STATION": station,
        "NEXT_ARTIST": next_artist,
        "NEXT_TITLE": next_title,
    }
    for key, value in replacements.items():
        prompt = prompt.replace(f"[{key}]", value)

    variant_replacements = {
        "INTRO_STYLE": narrative_variant.intro_instruction,
        "BODY_STYLE": narrative_variant.body_instruction,
        "OUTRO_STYLE": narrative_variant.outro_instruction,
        "FILLER_WORDS": narrative_variant.filler_words,
    }
    for key, value in variant_replacements.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)

    story = gemini_text_completion(prompt=prompt)
    return cleanup_story_text(story)


```

### B7) Exact Prompt Assembly Code (TTS Instructions)

```python
def synthesize_story_audio(
    story_text: str,
    outfile: pathlib.Path,
    delivery_variant: DeliveryVariant,
) -> None:
    tts_template = TTS_INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    instructions = tts_template
    delivery_replacements = {
        "DELIVERY_VARIATION": delivery_variant.delivery_instruction,
        "PACE_ADJUSTMENT": delivery_variant.pace_instruction,
        "DELIVERY_ADDITIONAL": delivery_variant.additional_prompts,
    }
    for key, value in delivery_replacements.items():
        instructions = instructions.replace(f"{{{{{key}}}}}", value)

    synthesize_speech(
        text=story_text,
        outfile=str(outfile),
        instructions=instructions,
    )


```

### B8) Story LLM Call Behavior (No separate system prompt; full prompt sent as `contents=prompt`)

```python
def gemini_text_completion(prompt: str, model: Optional[str] = None) -> str:
    client = get_gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Gemini client is not installed. Install with: pip install google-genai"
        ) from exc

    grounding_tool = types.Tool(google_search=types.GoogleSearch())
    config = types.GenerateContentConfig(tools=[grounding_tool])
    response = client.models.generate_content(
        model=(model or _DEFAULT_GEMINI_TEXT_MODEL),
        contents=prompt,
        config=config,
    )
    if not response or not response.text:
        raise RuntimeError("Gemini did not return any text for the story prompt.")
    return response.text


```

### B9) Pairing-Aware Variant Selection Code

```python
def compute_story_seed(
    station: str,
    artist: str,
    title: str,
    next_artist: str,
    next_title: str,
) -> str:
    """Build a stable seed string from story context values."""

    parts = [
        station.strip().lower(),
        artist.strip().lower(),
        title.strip().lower(),
        next_artist.strip().lower(),
        next_title.strip().lower(),
    ]
    combined = "|".join(parts)
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return digest


def deterministic_variant_choice(
    seed: str,
    variants: Sequence[T],
    recent_ids: Sequence[str],
    avoid_window: int,
    id_getter: Optional[Callable[[T], str]] = None,
) -> Tuple[int, T]:
    """Choose a variant deterministically while avoiding the most recent IDs.

    Example:
        >>> seed = compute_story_seed("neuralcast", "Artista", "Tema", "Proximo", "Cancion")
        >>> idx, variant = deterministic_variant_choice(seed, NARRATIVE_VARIANTS, [], 2)
        >>> variant.style_id in {item.style_id for item in NARRATIVE_VARIANTS}
        True
    """

    if not variants:
        raise ValueError("variants must not be empty")

    if id_getter is None:
        id_getter = lambda item: getattr(item, "style_id", "")

    rng = random.Random(_hash_to_int(seed))
    order = list(range(len(variants)))
    rng.shuffle(order)

    recent_list = list(recent_ids)
    avoided = set(recent_list[:avoid_window]) if avoid_window > 0 else set()
    for index in order:
        candidate = variants[index]
        candidate_id = id_getter(candidate)
        if candidate_id not in avoided:
            return index, candidate

    # Fallback: deterministic first element from shuffled order
    fallback_index = order[0]
    return fallback_index, variants[fallback_index]


def _weighted_deterministic_choice(seed: str, pairings: Sequence[VariantPairing]) -> VariantPairing:
    """Pick a pairing using a deterministic RNG and simple weights."""

    rng = random.Random(_hash_to_int(f"{seed}|pairing-weight"))
    total_weight = sum(max(pair.weight, 0) for pair in pairings)
    if total_weight <= 0:
        return pairings[0]

    threshold = rng.uniform(0, total_weight)
    cumulative = 0.0
    for pair in pairings:
        cumulative += max(pair.weight, 0)
        if threshold <= cumulative:
            return pair

    return pairings[-1]


def _filter_pairings_for_recency(
    pairings: Sequence[VariantPairing],
    narrative_avoid: Sequence[str],
    delivery_avoid: Sequence[str],
    narrative_lookup: Dict[str, NarrativeVariant],
    delivery_lookup: Dict[str, DeliveryVariant],
) -> List[VariantPairing]:
    """Return pairings that respect the avoid windows and exist in current inventories."""

    narrative_blocked = set(narrative_avoid)
    delivery_blocked = set(delivery_avoid)
    filtered: List[VariantPairing] = []
    for pair in pairings:
        if pair.weight <= 0:
            continue
        if pair.narrative_id not in narrative_lookup:
            continue
        if pair.delivery_id not in delivery_lookup:
            continue
        if pair.narrative_id in narrative_blocked:
            continue
        if pair.delivery_id in delivery_blocked:
            continue
        filtered.append(pair)
    return filtered


def deterministic_pairing_choice(
    seed: str,
    pairings: Sequence[VariantPairing],
    narrative_variants: Sequence[NarrativeVariant],
    delivery_variants: Sequence[DeliveryVariant],
    narrative_recent: Sequence[str],
    delivery_recent: Sequence[str],
    narrative_avoid_window: int,
    delivery_avoid_window: int,
) -> Optional[VariantPairing]:
    """Select a curated pairing, relaxing recency windows if needed."""

    narrative_lookup = {item.style_id: item for item in narrative_variants}
    delivery_lookup = {item.style_id: item for item in delivery_variants}

    max_shrink = max(narrative_avoid_window, delivery_avoid_window)
    for shrink in range(max_shrink + 1):
        narrative_window = max(narrative_avoid_window - shrink, 0)
        delivery_window = max(delivery_avoid_window - shrink, 0)
        narrative_avoid = list(narrative_recent)[:narrative_window]
        delivery_avoid = list(delivery_recent)[:delivery_window]
        candidates = _filter_pairings_for_recency(
            pairings=pairings,
            narrative_avoid=narrative_avoid,
            delivery_avoid=delivery_avoid,
            narrative_lookup=narrative_lookup,
            delivery_lookup=delivery_lookup,
        )
        if candidates:
            return _weighted_deterministic_choice(seed=seed, pairings=candidates)

    return None


def select_variants_with_pairing(
    seed: str,
    narrative_variants: Sequence[NarrativeVariant],
    delivery_variants: Sequence[DeliveryVariant],
    pairings: Sequence[VariantPairing],
    narrative_recent: Sequence[str],
    delivery_recent: Sequence[str],
    narrative_avoid_window: int,
    delivery_avoid_window: int,
) -> Tuple[NarrativeVariant, DeliveryVariant, Optional[VariantPairing]]:
    """Choose narrative/delivery variants, preferring curated pairings."""

    narrative_lookup = {item.style_id: item for item in narrative_variants}
    delivery_lookup = {item.style_id: item for item in delivery_variants}

    chosen_pair = deterministic_pairing_choice(
        seed=seed,
        pairings=pairings,
        narrative_variants=narrative_variants,
        delivery_variants=delivery_variants,
        narrative_recent=narrative_recent,
        delivery_recent=delivery_recent,
        narrative_avoid_window=narrative_avoid_window,
        delivery_avoid_window=delivery_avoid_window,
    )
    if chosen_pair:
        narrative_variant = narrative_lookup.get(chosen_pair.narrative_id)
        delivery_variant = delivery_lookup.get(chosen_pair.delivery_id)
        if narrative_variant and delivery_variant:
            return narrative_variant, delivery_variant, chosen_pair

    _, narrative_variant = deterministic_variant_choice(
        seed=f"{seed}|narrative",
        variants=narrative_variants,
        recent_ids=narrative_recent,
        avoid_window=narrative_avoid_window,
    )
    _, delivery_variant = deterministic_variant_choice(
        seed=f"{seed}|delivery",
        variants=delivery_variants,
        recent_ids=delivery_recent,
        avoid_window=delivery_avoid_window,
    )
    return narrative_variant, delivery_variant, None


```

### B10) Runtime Selection + Prompt Build Callsite

```python
    story_seed = compute_story_seed(
        station=args.station,
        artist=selected_track.artist,
        title=selected_track.title,
        next_artist=following_track.artist,
        next_title=following_track.title,
    )
    history = load_style_history(STYLE_HISTORY_PATH)
    narrative_recent = list(iter_recent_ids(history, args.station, "narrative_id"))
    delivery_recent = list(iter_recent_ids(history, args.station, "delivery_id"))
    (
        narrative_variant,
        delivery_variant,
        chosen_pair,
    ) = select_variants_with_pairing(
        seed=story_seed,
        narrative_variants=NARRATIVE_VARIANTS,
        delivery_variants=DELIVERY_VARIANTS,
        pairings=PREFERRED_PAIRINGS,
        narrative_recent=narrative_recent,
        delivery_recent=delivery_recent,
        narrative_avoid_window=NARRATIVE_AVOID_WINDOW,
        delivery_avoid_window=DELIVERY_AVOID_WINDOW,
    )
    if chosen_pair:
        print(
            "Paired styles selected: "
            f"{narrative_variant.style_id} (narrative) + {delivery_variant.style_id} (delivery)"
        )
        print(f"Narrative description: {narrative_variant.description}")
        print(f"Delivery description: {delivery_variant.description}")
    else:
        print(
            f"Narrative style selected: {narrative_variant.style_id} — {narrative_variant.description}"
        )
        print(
            f"Delivery style selected: {delivery_variant.style_id} — {delivery_variant.description}"
        )

    story_text = generate_story_text(
        selected_track.artist,
        selected_track.title,
        station_display_name,
        following_track.artist,
        following_track.title,
        narrative_variant,
    )
```

## Notes

- Snapshot A (`53a54b5`) is the earlier, simpler variations model.
- Snapshot B (`58a92b4`) is the final stories-only model immediately before the orchestrator rewrite (`8a5be84`).
- The text blocks above are verbatim from git history at those commits.
