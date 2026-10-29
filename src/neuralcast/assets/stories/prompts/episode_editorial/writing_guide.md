# Shared episode writing principles

These instructions apply to the whole manuscript at every editorial stage.
The approved concept, candidates, owner instructions, and initial research are
in context.json. Follow the configured host language, locale, and personality
provided in writing_references; do not default to English or imitate an invented
presenter. Read all supplied writing references before drafting or judging.

## One episode, one developing story

Identify a central question or tension that makes the theme worth following.
Organize people, events, and songs around its development. Build a causal story:
decisions have consequences, relationships change, and earlier details acquire
meaning later. A succession of individually interesting artist facts is not
enough. Establish relationships before relying on them for an emotional ending.
Do not force a feud, a villain, or a tidy resolution that the evidence cannot support.
These principles also apply to episodes about musical ideas rather than drama.

Develop the strongest scenes with supported circumstances, people, stakes,
actions, and consequences. A bus journey, audition, studio disagreement, or
encounter becomes interesting through what happened and what it changed.
Never fabricate dialogue, interior thoughts, motives, sensory details, dates,
or a composite scene to create atmosphere. Distinguish recollection from fact;
attribute conflicting versions naturally to the people involved. When facts are
unclear, qualify, omit, or flag them instead of filling gaps plausibly.

## Continuity across music

Read the script as the listener hears it, with complete songs between passages.
Carry forward a person, decision, question, or consequence. After a long song,
give just enough orientation to resume the story without repeating its setup.
Use callbacks with a purpose and resolve promises made to the listener. Avoid
mechanical cliffhangers, repeated welcomes, and a formulaic transition at every break.

Every selected song needs a specific narrative purpose at its position. Explain
why it matters without turning every introduction into a track encyclopedia.
Do not claim to have listened to audio. Do not invent musical observations;
ground specific arrangement, credit, or recording claims in reliable evidence.
Only approved candidate identities may be selected; a subset and reordering are
allowed. Retain candidate IDs, playlist names, artists, and titles exactly.
Use fewer songs if that serves the approved duration and storytelling depth.

## A person telling a story aloud

Write a single host speaking naturally to an interested listener. Use concrete
language, varied sentence length, and a rhythm suited to being heard once.
Contractions, idiom, and direct address should fit the configured locale.
Avoid essay phrasing, dense lists of names and dates, generic dramatic claims,
forced jokes, repetitive rhetorical questions, and explanatory filler.
Energetic storytelling need not sound breathless; loss deserves restraint.

Imagine explaining this story to one curious person sitting with you. They may
know the music but not the events. Let them follow what someone tried, what got
in the way, and what happened next. Do not continually step outside the story
to explain your editorial argument. A clear, grammatical paragraph can still
sound like a presentation; shorter sentences and voseo alone do not make it oral.

Use these examples to calibrate phrasing, not as evidence or reusable script:

- Written: "La colaboración empezaba a producir material."
  Spoken: "Se pusieron a trabajar juntos y empezaron a salir canciones."
- Written: "La familiaridad de la voz no resolvía por sí sola ninguna de las dos
  cosas." Spoken: "Que la voz les resultara familiar no alcanzaba. Todavía tenían
  que ver cómo se llevaban y qué podían escribir juntos."
- Written: "Los relatos difieren sobre cómo se tomó la decisión."
  Spoken: "Paul lo contaba de otra manera." Then explain his account, provided
  the preceding passage has established the other perspective.

Adapt only when the supplied evidence supports the same meaning. Preserve
necessary attribution and uncertainty; a conversational voice never permits
invented dialogue, reactions, thoughts, or personal experiences.

Watch for repeated framing such as "podemos pensar", "esto nos permite entender",
"guardá esa idea", "la pregunta pasa a ser", and "nos queda escuchar cómo".
These can work occasionally, but a series of them sounds like a guided analysis.
Let actions and consequences carry the connection when it is already clear.
Avoid giving each paragraph the same setup, explanation, and concluding lesson.
Use a brief reflection where it adds something; do not explain every anecdote
again immediately after telling it. Credits and chronology should enter when
they matter, rather than arrive as a recital before each song.

Before submitting, mentally speak several consecutive paragraphs, including
their song handoffs. Check whether you could say them comfortably to that person
without sounding as if you were reading an article. Rewrite the remaining
presentation-like lines. Do not manufacture spontaneity with filler quotas,
forced slang, fake hesitations, or a question at every turn. Do not claim an
actual read-aloud or listening test unless one was performed.

Open as a standalone podcast: welcome newcomers to the configured brand's
podcast, introduce the theme and appeal, and briefly preview the journey.
Do not invent a host name, episode number, release date, or schedule. Develop
bridges without restarting the show. Close by resolving the episode's question
as far as the evidence permits and thanking the listener.

In Spanish, begin with "Bienvenidos", not "Bienvenidas y bienvenidos" or a
similar doubled greeting. For NeuralForge, spell the brand "NeuralForsch" in
spoken scripts as a TTS pronunciation aid. Keep the canonical "NeuralForge"
spelling in titles, companion summaries, metadata, and other display text.
NeuralCast keeps its usual spelling. Review the opening and closing for this
distinction rather than copying the display brand into the spoken script.

Let each band stand on its own merits in the opening preview. Do not frame its
place in the episode as owing something to a more famous band. A documented
influence may be explored later in that band's song explanation if relevant;
it is not, by itself, a reason for inclusion.

Research stays outside spoken narration. Do not read source URLs, publication
names, interview/book citations, or a bibliography aloud. A participant's
perspective can be made clear without naming the publication carrying it.
Attach supporting source URLs and precise claims to each commentary's sources.
Put qualifications and unresolved research in research_notes/open_questions,
not concealed behind confident narration. Companion summaries add no new facts.

## Performance and duration

Finish the prose before polishing performance. Use only allowed_inline_tags from
context.json, enclosed in angle brackets. Use a varied selection when the text
calls for it: a breath, thoughtful pause, quiet aside, sigh, or small chuckle.
Do not use square-bracket directions. Tags must support a specific beat, not
meet a quota; do not pepper serious passages with laughter or theatrical effects.
Restraint does not mean reducing every cue to a pause. Consider a breath,
exhale, sigh, phew, or quiet aside where the specific beat earns it; do not
automatically remove useful expressive cues because the subject is serious.
Tags cannot make explanatory prose conversational. Rewrite the prose first.
The validator permits at most eight tags per commentary and fewer in short text.

Write complete scripts and companion summaries for every commentary, each at
most 300 spoken words (and within its max_words). Split a long story into adjacent
commentaries if needed; the episode may have at most 40 segments. Begin with
opening commentary, end with closing commentary, and include at least one song.
Respect timing_policy, measured song durations, the narration budget, and the
approved target. Estimates are provisional until synthesis. Prioritize stories
and reduce the song selection rather than squeezing shallow summaries between
too many tracks. Do not invent exact chapter timestamps before rendering.
