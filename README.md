# Podcast Speaker Studio

A functional Python/Qt desktop studio for source-linked podcast transcripts. The interface uses lime green, cream, olive, and sage with a dark mode. It supports cloud transcription, global speaker renaming, reassignment/merge/split corrections, original recognized text, separate readability edits, synchronized playback, search, review, bookmarks, optional cited summaries, project recovery, and seven export formats.

This is an initial implementation, not a claim of production certification or perfect recognition. The Linux test environment validates functional editing, exports, provider protocol fixtures, and actual WAV decoding/seeking. Live paid provider calls and real-world acoustic accuracy have **not** been validated without credentials and representative recordings. Windows 11 and macOS 26 packages must be built and tested on those platforms; native build automation is supplied but has not run here.

## Install and launch

Requires Python 3.11+ (3.12 is tested). Qt 6.12 is locked. Use a supported 64-bit desktop OS. `uv` 0.12.19 is the tested dependency manager. Install uv through its official instructions or `python -m pip install uv==0.12.19` in an appropriate virtual environment.

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev podcast-speaker-studio
```

Optional FFmpeg/`ffprobe` on PATH provides import-time codec, channels, sample rate, and duration. Qt supplies playback and its own metadata without this external tool. No audio conversion or source modification is performed.

To open the included fictional example:

```sh
uv run --frozen --extra dev podcast-speaker-studio samples/roundtable.pss.json
```

The sample transcript is hand-authored fiction. Its audio consists of distinct tone cues for navigation, **not synthesized speech** or a measured ASR result. The application works with real recordings through AssemblyAI. Sample assets are also bundled under `samples` in native distributions.

## Import → transcript → export

1. Use Import audio, drop local files, or select multiple recordings. The project selector switches between batch items.
2. Open Settings. Enter an AssemblyAI API key (session only), or provide `ASSEMBLYAI_API_KEY` in the process environment. Leave the expected speaker count automatic unless you know it. Full Transcript Only is the default and does not request a summary.
3. Choose Transcribe / resume for one recording or Process batch for all untranscribed imports. Confirm the explicit upload notice. Progress separates upload percentage from provider processing status; provider processing has no reliable completion percentage.
4. Inspect the complete returned utterances. Click any passage to seek to its timestamp; double-click or Play timestamp to play. The editor beneath the list corrects text or attribution while preserving segment timestamps. Original provider speech and word alignment remain inspectable. Readability edits have their own field and do not replace recognized speech.
5. Select a speaker in the sidebar to filter, rename globally, assign a user-confirmed role, or merge duplicate identities. Add a speaker for manual reassignment. Split a missed speaker change at a boundary between words, supplying left/right text and the right speaker.
6. Search, navigate results, inspect adjacent conversation context, mark uncertain passages reviewed, and add bookmarks. Current passages are bold during playback; Follow playback scrolls automatically. Ctrl+F searches, Ctrl+Space toggles playback, Alt+Left/Right skips 15 seconds. macOS may use Control for the literal shortcuts; menu/toolbar controls are available on all platforms.
7. Save project writes editable JSON. Export offers TXT, Markdown, PDF, DOCX, JSON, CSV, and SRT. Select transcript, speaker transcript, summaries, speaker summaries, combined report, or source-linked notes. Speaker-specific exports use the selected sidebar participant. SRT is restricted to transcript scopes. Copy transcript offers timestamps on/off.

## Optional summaries

The Summaries & evidence tab offers entire podcast, individual speakers (one or more selected participants), topic/question, and chronological modes. Supply an OpenAI API key in Settings or through `PSS_SUMMARY_API_KEY` (also reads `OPENAI_API_KEY` on a normal desktop). The default model is `gpt-4o-mini`; Settings permits a compatible JSON-output chat model. Provider URLs are fixed to official HTTPS APIs.

Every model claim includes segment IDs. References must exist **and** belong to the chunk/participant sent to the model. Invalid references are labelled unsupported; ambiguous outputs are labelled ambiguous. Exact evidence, names, and timestamps are resolved from project data. Click each source to inspect and play it. Multiple sources are shown separately. Edits invalidate summaries, and splitting creates new IDs so old references cannot silently move to different evidence.

Reference validation cannot prove semantic entailment or verify facts. A valid ID can still accompany a mistaken model claim. Quotes/evidence are rendered from actual transcript text, never from model-provided quotation fields. Model paraphrases must be manually reviewed against that evidence. All claims are speaker assertions, not externally verified facts.

Long transcripts are processed in ordered chunks without dropping later segments. Chunk summaries are combined in sequence; this does not perform a second global synthesis, so cross-chunk repetition is possible. Individual speaker summaries use only that speaker's segments and can miss conversational context; consult adjacent passages. Without an OpenAI key the button produces labelled **local extractive source excerpts**, not an AI summary. Local topic mode is literal keyword matching, not semantic retrieval.

## Recovery and privacy

Projects autosave to the application data directory, including job status and the AssemblyAI transcript ID. Recover autosave reopens saved work. Transcribe / resume polls a saved remote ID without uploading or creating a second job. Cancellation interrupts polling/upload reads; an in-flight HTTP call can take up to the request timeout to return. Locally cancelling does **not** cancel already submitted provider processing or charges. The application refuses to exit while its worker is stopping.

If interrupted after submission but before receiving/persisting an ID, paste the transcript ID from the AssemblyAI dashboard at retry, or explicitly choose a new potentially chargeable submission. HTTP POST submissions are never automatically retried. Existing transcripts require confirmation before replacement. Audio files are separate from JSON; move/copy them alongside your projects and relink when prompted. JSON can reference a relative path on import.

Keys are held in memory; only non-secret settings are saved. Keys are excluded from project JSON, logs, and exports. Project JSON, autosaves, and exports contain potentially sensitive transcripts and local audio paths in plain text; secure your device and storage. Original provider segments are retained as a separate snapshot to preserve recognized data through edits/splits.

Cloud transcription uploads the original recording to AssemblyAI; AI summaries upload transcript chunks to OpenAI. Provider terms, retention settings, and usage charges apply. Check current [AssemblyAI pricing](https://www.assemblyai.com/pricing), [AssemblyAI privacy](https://www.assemblyai.com/legal/privacy-policy), [OpenAI pricing](https://openai.com/api/pricing/), and [OpenAI API data controls](https://platform.openai.com/docs/guides/your-data). Account plan, recording length, and summary token usage determine costs; no fixed cost estimate is promised. No cloud traffic occurs merely by opening/editing/exporting a project. Fully local/offline ASR and diarization are not implemented; offline project editing, navigation, exports, and extractive notes are supported.

## Development and delivery

```sh
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev python scripts/package.py
```

See [architecture and format](docs/architecture.md), [packaging and signing](docs/packaging.md), and [validation and limitations](docs/validation.md).

The packaging command creates a portable Windows application folder/ZIP or macOS `.app`/ZIP **when run on that operating system**. It also creates a Linux folder/ZIP for this environment. PyInstaller does not cross-compile Qt applications. GitHub Actions provides Windows 2025/Windows 11 compatibility and macOS 26 runners through `.github/workflows/desktop-builds.yml`; run it after the source is in your repository. Public macOS delivery requires Developer ID signing and notarization; Windows signing is recommended. Unsigned development archives are not equivalent to production installers.

## Cloud workspace

This task already has an isolated checkout at `/workspace/scriptAudio`; use it directly, without creating a worktree. The cloud machine has no physical audio output/display. For headless tests:

```sh
export UV_CACHE_DIR=/workspace/.cache/uv
export XDG_CACHE_HOME=/workspace/.cache
export XDG_CONFIG_HOME=/workspace/.config
export PSS_DATA_DIR=/workspace/.studio
export QT_QPA_PLATFORM=offscreen
export QT_AUDIO_BACKEND=pulseaudio
uv run --frozen --extra dev pytest -q
```

The PulseAudio override avoids the installed Qt/PipeWire backend's crash when its configuration is unavailable in this container. It is a cloud-test setting, not a requirement for Windows or macOS. WAV decoding/seek tests capture decoded buffers without claiming audible output. For an actual desktop session, omit `QT_QPA_PLATFORM=offscreen`.
