# Validation and known limitations

Validation was performed on Linux x86_64 with Python 3.12.14, uv 0.12.19, PySide6 6.12.0, and the committed dependency lock. This report distinguishes actual behavior from protocol fixtures and checks still needed.

## Executed

- 48 automated tests passed: identity/global renaming, storage, original-vs-edited/readability text, word and segment timestamp preservation, merge/split, invalid timestamps/IDs, JSON atomic replacement/round trips, relative audio relinking, Unicode search, review filters, and four-hour timestamp formatting.
- Provider tests use deterministic HTTP responses to exercise streaming upload setup, job submission options, polling completion, persisted remote ID, resume without repeat upload, pre-network cancellation, denied authentication, failed connectivity, sanitized errors, rate-limit recovery, no duplicate POST retry, returning voices, cross-talk flags, low confidence, long gaps, and refusal to replace a good transcript with malformed provider output.
- Summary tests cover missing/fabricated/out-of-scope IDs, exact evidence resolution, ignoring model timestamps, participant-specific extraction, topic extraction, structured mocked AI responses, complete multi-chunk coverage, and an oversized individual passage with no dropped text.
- All seven export formats were generated. DOCX paragraphs, text/CSV/SRT formatting, PDF signature, editable JSON, speaker-scoped exports, evidence reports, CSV formula neutralization, and refusal of summary SRT are exercised.
- A complete Qt workflow imports a batch, consumes a controlled transcription response, corrects/renames speakers, generates local cited notes, exports all formats, and reopens the JSON. This is an integration test, not live speech recognition.
- Qt desktop tests exercise search-result navigation, adjacent context, corrections, citation selection, active passage highlighting, autosave, themes, asynchronous completion, and automatic reload of a persisted interrupted job.
- Real WAV import/probing preserves the source SHA-256. Qt decodes generated WAV buffers and seeks to 1.5 seconds without a playback error. These are acoustic decoder checks, not recognition accuracy measurements or proof of audible output in the headless machine.
- `python -m podcast_studio --smoke-test` opens the fictional sample, corrects a speaker, creates a local source-linked excerpt summary, generates all seven formats, round-trips the project, and decodes/seeks the included audio. The native Linux bundle is checked with the same smoke mode after packaging.

## Not executed / required before a production release

- Live AssemblyAI transcription and live OpenAI summarization: no provider credentials were supplied. Mocked responses demonstrate client behavior, not compatibility with every paid account, model, region, or future API change. Configure credentials securely and run the manual end-to-end checklist below.
- Windows 11 and macOS Tahoe GUI/codec acceptance, native release archives, signing, notarization, clean-machine installation, and hardware playback. The cloud host is Linux; Windows/macOS build automation is supplied but not executed here.
- Acoustic benchmarks with real accents, background noise, multi-hour speech, laughter, simultaneous voices, and returning speakers. Synthetic data exercises time/identity handling but does not quantify ASR or diarization accuracy.
- Full offline ASR/diarization, semantic citation verification, a second global synthesis over chunk summaries, and OS keychain storage are not implemented. JSON project editing and local excerpts work offline.
- Complex script/CJK PDF font coverage is incomplete: bundled Vera handles common Latin accents, not every language. Use DOCX, JSON, or UTF-8 text for unsupported scripts until a suitable licensed font is configured.
- Qt may not decode every codec/container combination on every OS. Verify MP3, WAV, M4A, FLAC, AAC, MP4, OGG/Opus, AIFF, WMA, and WebM on each target. ffprobe detects media but is not a playback decoder. Unsupported streams fail visibly rather than rewriting the original.
- Summary IDs provide traceability, not proof of truth. Model claims can misinterpret valid evidence; inspect the exact passages. Speaker confidence is frequently unavailable. Silence gaps are review hints and do not establish omitted speech.
- Submitted cloud jobs can keep running after cancellation. An interrupted POST before an ID is saved requires dashboard reconciliation to avoid duplicate charges. Very large uploads may hit provider/account limits; consult their current limits and use valid media. There is no automatic lossy transcoding.
- Large transcripts are stored in memory and the list paints passage text; very long recordings should be benchmarked on intended desktop hardware. Audio itself is streamed/uploaded from disk and is not loaded wholesale into RAM.

## Manual paid-provider acceptance checklist

1. Configure an AssemblyAI key. Record consent and the provider/account/model used. Import a disposable real MP3/WAV and capture its hash.
2. Run Full Transcript Only. Confirm no summary call happens. Compare the full returned conversation against the audio, including fillers, disagreement, corrections, interruptions, and returns by each speaker. Document errors rather than treating IDs as perfect identities.
3. Rename/reassign speakers, merge a duplicate, split a missed change between aligned words, and verify timestamps and original text. Save and reopen.
4. Search a word with accents, filter a speaker, click timestamps, vary playback speed, scrub, bookmark, and inspect overlap/uncertainty flags. Confirm the original media hash is unchanged.
5. Start a longer job, cancel during polling, close/reopen, and resume its saved remote ID without submitting a second job. Also simulate network loss and dashboard reconciliation at submission.
6. Configure an OpenAI key. Generate separate participant summaries and topic/chronological/entire summaries. Inspect every claim's multiple source passages and classification; ensure claims cannot cite unknown IDs. Make an edit and confirm summaries are stale.
7. Export every format and scope; check speakers, exact evidence, timestamps, readable-vs-original distinction, UTF-8 content, and meaningful PDF/DOCX output. JSON complete-transcript exports must reopen.
8. Repeat with multi-hour speech, varied accents/noise, brief voices, interruptions, and simultaneous speakers on Windows 11 and macOS 26. Document word error rate and diarization errors if making quantitative accuracy statements.
