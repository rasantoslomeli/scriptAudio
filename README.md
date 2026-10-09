# Podcast Speaker Studio — offline edge edition

A functional Python/Qt desktop studio for local podcast transcription, speaker correction, synchronized audio navigation, source-linked summaries, and TXT/Markdown/PDF/DOCX/JSON/CSV/SRT exports. Version 0.2 makes **offline processing the default** and adds RTX 3080 10 GB/CUDA and Apple Silicon 64 GB/Metal deployment profiles. No API key is required for the local workflow.

The original lime/cream/olive interface and light/dark modes remain. Projects preserve recognized originals, separate readability edits, word/segment timestamps, stable speaker/segment IDs, optional review flags, bookmarks, persistent jobs, and exact cited evidence. Rename speakers globally, reassign/merge/split passages, search/filter, navigate/play sources, save/reopen JSON, and export scoped reports.

## Install and run

Python 3.12 and uv 0.12.19 are tested. Install dependencies during connected preparation:

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev podcast-speaker-studio
# Fictional example, with tone cues (NOT real speech or ASR output):
uv run --frozen --extra dev podcast-speaker-studio samples/roundtable.pss.json
```

**Installing the app alone does not install speech/LLM model weights.** Run `scripts/prepare_models.py` on a connected preparation host, prepare native engines/FFmpeg, then choose its `edge-config.json` in Settings. See [the complete edge deployment guide](docs/edge.md) for model sources, native CUDA/Metal builds, checksums, offline transfer, memory/storage budgets, packaging, and target-hardware benchmarks.

| Target | Recognition | Global diarization | Optional local summary |
|---|---|---|---|
| RTX 3080, 10 GB | whisper.cpp CUDA, large-v3-turbo Q5 suggested | sherpa-onnx ONNX on CPU | llama.cpp CUDA, 3B instruction GGUF Q4 suggested |
| Apple Silicon MBP, 64 GB | whisper.cpp Metal, large-v3-turbo Q5 suggested | Same CPU pipeline | llama.cpp Metal, 7B instruction GGUF Q4 suggested |
| Explicit CPU fallback | whisper.cpp CPU | Same CPU pipeline | Smaller instruction GGUF |

Recognition and summaries run sequentially so GPU models are not resident together. Diarization uses one global identity space across the recording. Long recognition jobs use overlapped, resumable chunks. Models/files are verified before processing; inactive GPU backends or missing assets fail visibly. Nothing downloads and no cloud fallback occurs at runtime. These profiles are recommended starting points, not measured speed/VRAM/accuracy guarantees.

## Import → transcript → correction → export

1. Import/drop one or multiple local MP3/WAV/M4A/FLAC/AAC/MP4 or other supported recordings. Original files are never rewritten. Native FFmpeg normalizes a separate local cache for recognition; optional ffprobe detects metadata.
2. In Settings choose Offline edge and the prepared manifest. Leave Full Transcript Only selected to skip summaries entirely. Set expected speaker count only when known; automatic global clustering is otherwise used.
3. Transcribe / resume processes the current recording; Process batch queues untranscribed recordings sequentially. Cancellation stops owned local engines and retains completed chunks/stages. Resume uses matching source/model/settings fingerprints.
4. Click a passage to seek; double-click or Play timestamp to play. Correct text and attribution beneath the transcript. Inspect original provider text/word alignment; Edit readability copy keeps its changes separate. Timestamp boundaries do not change during text edits.
5. Rename/assign confirmed roles in the speaker sidebar, add a speaker, merge duplicate identities, or split a missed change between word boundaries. Ambiguous/overlapping voices are flagged or left unassigned rather than confidently invented.
6. Search/filter/review, inspect adjacent context, add bookmarks, and follow active passages. Ctrl+F searches; Ctrl+Space toggles playback; Alt+Left/Right skips 15 seconds. Playback speed and scrubbing are available.
7. Save project preserves editable JSON. Export selects transcript, speaker transcript, all/speaker summaries, combined report, or source-linked notes. SRT supports transcript scopes; timestamped human-readable evidence is retained in reports. Copy supports timestamps on/off.

## Optional local AI summaries

Settings defaults to Offline AI (GGUF). The Summaries & evidence tab supports entire conversation, individual participants, topic/question, and chronological modes. A managed llama.cpp process listens only on authenticated loopback, runs with `--offline`, bypasses HTTP proxies, and shuts down after work/cancellation. The model tokenizer checks context limits; sources are subdivided without dropping text. JSON grammar and citation validation restrict references to actual source segment IDs.

Evidence quotes, speaker labels, and display timestamps always resolve from stored data. Generated quotations missing from their cited evidence are marked unsupported; claims citing unassigned/overlapping voices are marked ambiguous. Claims remain speaker assertions, not independently verified facts; a valid reference cannot prove semantic support. Review important claims against every passage. Corrections mark summaries stale; split segments receive new IDs, so old citations cannot silently move. Chunked summaries may repeat points and do not perform a second global synthesis. An explicitly selected Local source excerpts mode provides exact, labelled non-AI notes; local topic excerpts use keyword matching.

## Privacy, recovery and limitations

Originals remain read-only. App data/autosaves, local normalized audio, diagnostic logs, jobs/caches, and exports are unencrypted and can contain sensitive speech/text. Protect storage and clean completed caches deliberately when appropriate. Audio is separate from project JSON; transfer it alongside projects and relink if moved. Keys are never saved in project files.

Legacy AssemblyAI/OpenAI providers are opt-in Settings choices with explicit upload consent, session/environment keys, provider costs/retention, and no automatic activation. Local mode never contacts saved cloud job IDs. Cloud cancellation cannot undo already submitted provider work. Credentials from the earlier environment configuration are not required for offline use; remove obsolete cloud requirements in environment settings if unused.

This release is not production-certified. Linux unit/integration, native CPU engines, real Whisper/ONNX/GGUF inference, and the rebuilt Linux desktop package are validated. This host has neither RTX/CUDA nor Apple Silicon/Metal. Target-platform performance, acoustic accuracy, native signing/notarization, clean-machine compatibility, and complex-script PDF fonts still require validation. Refer to [executed checks and limitations](docs/validation.md).

## Development, packaging, and CI

```sh
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev python scripts/package.py
# A complete prepared bundle can be embedded on its native host:
uv run --frozen --extra dev python scripts/package.py --edge-bundle edge-bundle
```

[Packaging/signing instructions](docs/packaging.md) distinguish app-only archives from fully prepared offline bundles. PyInstaller cannot cross-compile Windows/macOS Qt apps on Linux. GitHub Actions runs native tests/builds on pushes to main, PRs, tags, and manual dispatch. It compiles Windows CPU and macOS Metal engine artifacts; RTX CUDA requires its own CUDA-equipped build host. CI archives do not contain large model weights and are not signed/notarized.

## Cloud development workspace

Use the existing isolated `/workspace/scriptAudio` checkout; do not create a worktree unless requested. The headless cloud host has no physical audio output/display:

```sh
export UV_CACHE_DIR=/workspace/.cache/uv
export XDG_CACHE_HOME=/workspace/.cache
export XDG_CONFIG_HOME=/workspace/.config
export PSS_DATA_DIR=/workspace/.studio
export QT_QPA_PLATFORM=offscreen
export QT_AUDIO_BACKEND=pulseaudio
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev python -m podcast_studio --smoke-test
```

The PulseAudio override avoids this container's unconfigured PipeWire crash. It is not a Windows/macOS requirement. The smoke test checks GUI/project/export behavior, local excerpts, and real WAV decoding/seeking without claiming audible output, learned ASR, or LLM inference. For an actual desktop session omit the offscreen/audio overrides.
