Writing scripts for Gemini 3.8 TTS:

- Return only the exact words to be spoken. Text is a literal transcript: never include instructions, speaker labels, Markdown, SSML, HTML, or bracketed/parenthetical stage directions.
- Permanent voice identity (age, timbre, accent, and register) is configured outside the script. Do not redefine it per segment.
- Write for speech. Use commas, full stops, dashes, and "..." only for a real breath, hesitation, or turn; do not fill the script with ellipses.
- Momentary vocal events may use these English <> tags sparingly and only where they genuinely improve the line: <short pause>, <long pause>, <breath>, <heavy breath>, <exhales>, <sigh>, <sighs>, <chuckle>, <chuckles>, <laugh>, <laughter>, <giggle>, <snicker>, <tsk>, <pff>, <phew>, <gasp>, <throat-clearing>, <cough>, <yawn>, <cheer>, <whispering>, <whispers>, <shout>, <growl>, <grr>, <hiss>, <argh>, <cackle>, <cry>, <groan>, <grunt>, <moan>, <pant>, <scream>, <shriek>, <sneeze>, <snort>, <sob>, <whimper>.
- Never invent tags or use non-vocal sound effects such as music, applause, impacts, or thunder. Never put a tag inside an artist name, song title, date, or factual detail.
- Write human fillers, hesitations, and self-corrections as spoken words, not directions. A short segment normally needs zero or one tag.
