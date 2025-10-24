# Initial Explanation Stage

Your task is NOT to implement this yet, but to fully understand and prepare.

Here is exactly what I need implemented:

NeuralCast is a self-hosted online radio station powered by **AzuraCast**, running locally on a **Raspberry Pi**.
It streams 24/7 on the home network, automatically rotating playlists and managing playback through AzuraCast’s AutoDJ engine.

This prototype aims to add an **AI-driven storytelling layer** — a system that can generate and **inject short, spoken stories about songs directly into the live broadcast**.

Create and follow an ExecPlan for this feature.

---

### **Objective**

Create a **new Python script** (e.g., `inject_story_snippet.py`) that can automatically:

1. Detect what song is about to play,
2. Generate a short story about that song,
3. Convert it into speech using TTS, and
4. Inject that MP3 file into the AzuraCast queue so it plays **immediately after** the corresponding song.

---

### **Prototype Scope and Requirements**

1. **Fetch the Upcoming Song**

   * Connect to the AzuraCast API running on the Raspberry Pi (The Pi's IP is `192.168.1.226`).
   * Retrieve the *now playing* and *upcoming* tracks.
   * Select one of the upcoming songs.
     * The way to select the song could be with a short openai call to ask which one of the upcoming songs has an interesting story to tell.

2. **Generate a Short Story**

   * Read the base story prompt from:

     ```
     stories/story_prompt.md
     ```
   * This prompt contains placeholders `[TITLE]` and `[ARTIST]` that must be dynamically replaced with the selected song’s title and artist before sending it to the LLM.
   * Use the OpenAI API for text generation (you can reuse the **OpenAI utility functions** already defined in `openai_utils.py`).
   * If those existing utils are not appropriate for this purpose, create minimal new ones within the script.
   * You may use `context7 MCP` to gather metadata about relevant packages or dependencies if needed.

3. **Generate TTS Audio**

   * Convert the generated story into speech.
   * For TTS, use the instruction text found in:

     ```
     stories/tts_story_instructions.md
     ```

     (This file defines how the voice should sound and behave.)
   * Save the audio file locally as:

     ```
     stories/Story_<Artist>_<Title>.mp3
     ```

4. **Inject the TTS MP3 into AzuraCast**

   * Automatically upload the generated MP3 to the correct **media directory** or inject it into the **upcoming queue** so that it plays immediately after the selected song.

5. **Verify Playback Order**

   * Confirm via the API that the new MP3 has been successfully added to the queue following the intended song.

---

### **Goal of This Prototype**

Validate the **full automation flow** for dynamic voice stories:

> “Detect upcoming song → Generate contextual story → Convert to speech → Inject into broadcast queue.”

Once functional, this will serve as the foundation for NeuralCast’s autonomous DJ system, capable of speaking naturally about the music it plays — just like a real radio host.

---

Your responsibilities:

- Analyze and understand the existing codebase thoroughly.
- Determine exactly how this feature integrates, including dependencies, structure, edge cases (within reason, don't go overboard), and constraints.
- Clearly identify anything unclear or ambiguous in my description or the current implementation.
- List clearly all questions or ambiguities you need clarified.

Remember, your job is not to implement (yet). Just exploring, planning, and then asking me questions to ensure all ambiguities are covered. We will go back and forth until you have no further questions. Do NOT assume any requirements or scope beyond explicitly described details.