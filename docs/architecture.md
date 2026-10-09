# Architecture and project format

`podcast_studio/model.py` defines version-1 projects, atomic JSON persistence, speaker identities, segments/words, editing, review, search, bookmarks, job recovery, and summary staleness. A sibling temporary file is flushed/fsynced and atomically replaced. Speaker names and roles are separate from stable IDs. Speaking time is the union of a speaker's intervals, not a simple sum that double-counts overlaps.

`media.py` imports files and invokes optional ffprobe without shell interpolation. Original media is only read, never rewritten. `app.py` owns Qt playback, transcript selection, worker scheduling, explicit upload consent, session credentials, and presentation. Provider processing and probing occur on QThreads. A deep project snapshot prevents concurrent GUI edits from racing with a worker's checkpoint. Transcript editing is disabled during work; switching/reading projects remains available.

`providers.py` uses authenticated HTTPS streaming upload, non-retried job submission, persisted remote IDs, cancellable polling, and bounded retries for GET/connectivity failures. Request timeouts are 15 seconds connect/60 seconds read; retries back off. API keys, authorization headers, signed upload URLs, and raw provider errors are never persisted. AssemblyAI's current official SDK README specifies `speech_models=["universal-3-5-pro", "universal-2"]` with language detection and speaker labels; this adapter follows that contract (checked 2026-10-09). Word and utterance milliseconds are converted to seconds once. Provider speaker confidence is frequently absent and is presented as unavailable rather than inferred from transcription confidence. Crossing segment intervals flag overlap; a provider returning a single dominant voice during cross-talk cannot reveal all overlaps.

`summaries.py` maps all ordered transcript chunks into structured claims, validates scope and ID references, and resolves evidence against the project. No timestamps or quoted evidence from model output are trusted. Local source excerpts are a separate, clearly labelled non-AI alternative. `exports.py` resolves the same evidence for every report, supports export scopes, rejects summary SRT, and neutralizes formula-like CSV text fields.

## JSON version 1

A complete transcript JSON export is a reopenable project. Other JSON export scopes are labelled export views (`export_schema_version`) and are not complete projects. Project fields:

| Field | Meaning |
|---|---|
| `schema_version` | `1`; unknown versions rejected |
| `id`, `title` | Stable project ID, display title |
| `audio_path` | Local media path; absolute when saved, relative accepted when loading |
| `duration`, `file_size`, `media` | Seconds, bytes, non-secret media properties |
| `speakers` | Map keyed by stable ID; each has `id`, editable `name`, `color`, user-confirmed `role` |
| `segments` | Chronological entries with unique `id`, `speaker_id`, `start`, `end`, `text`, `original_text`, `words`, confidences, review/overlap flags, optional `readable_text` |
| `original_segments` | Original provider segment snapshot before editing/splitting; no credentials or upload URL |
| `summaries` | Stable ID, mode, title, provider/model label, `stale`, claims |
| `claims` | `text`, `segment_ids`, `status`, `note`; invalid/missing references labelled unsupported |
| `bookmarks` | Seconds and user-supplied labels |
| `job` | Local ID, status, remote transcript ID, sanitized error, non-secret submitted options |

All times are nonnegative finite seconds. Segments must have `end >= start`, nondecreasing start times, valid speaker IDs, unique identities, and word intervals inside the parent interval. Concurrent speakers may overlap. Timestamp formatting supports recordings beyond 24 hours; no modulo-day wrap occurs. Names are never automatically inferred.

Editing text retains recognized original text and word boundaries. Word alignment continues to describe **the original recognized text**, not the replacement words: users must not assume edited spelling has been force-aligned. Splits must fall between words; when no words exist, both halves retain the original passage with an explicit alignment warning, while the full original snapshot remains available in JSON. New IDs invalidate old citations. A full retranscription explicitly replaces current edits; keep a saved copy before that operation.

There is no arbitrary provider endpoint setting: this prevents accidentally transmitting credentials/audio to an unreviewed host. A new provider should implement the worker-compatible interface and explicitly describe upload destinations. Session credentials are environment-derived or entered with a password field; a native OS keychain integration would be a future improvement.

## Version 0.2 offline engines

`edge.py` validates explicit local engine/model hashes, orchestrates read-only normalization, fingerprints input/settings/assets, persists completed stages, chunks recognition with overlap ownership, merges Whisper BPE timestamps, maps words to global speaker turns, withholds ambiguous assignments, and checks selected GPU activation. `edge_worker.py` isolates CPU ONNX global diarization in a cancellable child process and maps float32 audio through a temporary file. Original provider data and edit/citation rules retain project schema version 1; `Job.submitted_options.provider` differentiates local jobs from cloud jobs.

`local_llm.py` manages a private authenticated loopback llama.cpp process with `--offline`, fixed local model files, proxy bypass, context token checking, citation-constrained grammar, bounded output, and cleanup/cancellation. `summaries.py` shares validation/chunk mapping between optional cloud and local completions. Actual token limits can trigger recursive source subdivision; no source characters are silently discarded. No cloud request or model download exists in the offline transcription path.

`scripts/build_engines.py` prepares pinned upstream native code on the intended OS; `edge_bundle.py` registers/verifies trusted local assets or downloads an independently checksummed HTTPS asset during connected preparation. `benchmark_edge.py` measures real target timings/resource samples and preserves an editable result. PyInstaller includes sherpa-onnx shared libraries and can embed an explicit complete local bundle. Application defaults are offline; missing assets cannot silently activate cloud providers.
