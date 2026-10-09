# Offline edge deployment — version 0.2

**Offline edge is the default transcription provider. Offline AI (GGUF) is the default summary provider.** No API key is needed. Missing files, failed checksums, engine failures, and inactive GPU backends produce errors; they never trigger a cloud fallback. Cloud providers remain available only when explicitly selected, with upload consent. Transcript correction, exact evidence, JSON portability, review, synchronized playback, and seven export formats remain supported.

## Hardware profiles

| Profile | Speech recognition | Diarization | Optional summary | Resource approach |
|---|---|---|---|---|
| `rtx3080` | whisper.cpp CUDA; multilingual `large-v3-turbo-q5_0` recommended | sherpa-onnx pyannote segmentation + WeSpeaker embedding; CPU/global clustering | llama.cpp CUDA; Qwen2.5-3B-Instruct Q4_K_M or equivalent | 6 CPU threads, one job at a time, sequential GPU phases, 8,192-token summary context |
| `apple-silicon` | whisper.cpp Metal; multilingual `large-v3-turbo-q5_0` recommended | Same CPU pipeline | llama.cpp Metal; Qwen2.5-7B-Instruct Q4_K_M or equivalent | Native arm64, 6 CPU threads, sequential GPU phases; 64 GB unified RAM provides headroom |
| `cpu` | whisper.cpp CPU; multilingual `small` suggested | Same CPU pipeline | llama.cpp CPU; 1.5B instruction GGUF suggested | Explicit fallback/testing profile; 4 CPU threads |

These are engineering starting points, **not measured accuracy, VRAM, or speed guarantees**. The 3B summary recommendation is conservative on a 10 GB GPU. Quantization and turbo models trade model capacity/accuracy for resources; benchmark a full large-v3 quantized model if needed. Register different compatible files to choose a quality/speed tradeoff. Models are never silently downgraded. Close other GPU-heavy applications. Recommend 16 GB host RAM for shorter RTX jobs, preferably 32 GB for multi-hour work, with sufficient scratch space. A 64 GB M1-family Mac is typically an M1 Max configuration; use native Apple Silicon rather than Rosetta.

Diarization clusters the **entire recording once**, before recognition chunks. Returning voices share this global identity space. Recognition defaults to 15-minute chunks with 5-second context overlaps; token/word midpoints assign overlap ownership to one chunk core. Different overlapping decodes can still disagree. If tokens do not reconstruct recognized text, the full segment is retained and word alignment is flagged unavailable. Ambiguous competing voices/insufficient overlap produce an unassigned speaker. Local speaker confidence is uncalibrated and always marked for review. The initial clustering threshold is 0.4: it restores four IDs on the upstream four-speaker sample where 0.5 merged them into two. This is not universal calibration; set a known count or adjust `--cluster-threshold` during registration for representative recordings.

The CPU diarization process exits before recognition. Recognition exits before the LLM loads, so models do not compete for GPU memory. Cancellation terminates owned processes, including blocked local summary inference. Completed global diarization and recognition chunks are saved atomically. Source SHA256, asset hashes, settings, hardware profile, and pipeline version determine cache reuse. Incomplete chunks are recomputed; changed inputs/settings cannot reuse stale results. Batch jobs run sequentially. A diarization interval index avoids rescanning all past turns for each word during long-recording alignment. Native diagnostics must demonstrate CUDA/Metal activation for a selected GPU profile.

## Connected preparation, disconnected deployment

Preparation happens on a connected machine of the target OS/architecture. Transfer the app, all engines/runtime libraries, models, and licenses before disconnecting. The app has no runtime model downloader/package installer. An app-only ZIP is **not** a ready-to-transcribe offline installation.

1. Install Python 3.12, Git and the native C++ toolchain, then `uv sync --frozen --extra dev`. Windows CUDA compilation needs Visual Studio C++ tools and a supported CUDA Toolkit; runtime needs the NVIDIA driver. macOS needs Xcode command-line tools and arm64 Python. Build the pinned engines:

   ```sh
   uv run --frozen --extra dev python scripts/build_engines.py --profile rtx3080 --directory edge-bundle/engines --jobs 4
   # On the Mac instead:
   uv run --frozen --extra dev python scripts/build_engines.py --profile apple-silicon --directory edge-bundle/engines --jobs 4
   ```

   RTX compilation targets compute capability 8.6; Metal shaders are embedded. The helper checks for modifications in its own checkouts, uses static engine libraries, and disables host-specific CPU instruction selection. It does not modify the app checkout. Windows generators may put binaries under `build/bin/Release`. Retain adjacent DLLs/dylibs; driver/framework/libc requirements remain platform-specific.

2. Prepare the checksum-pinned recommended models on a connected host:

   ```sh
   python scripts/prepare_models.py --profile rtx3080 --directory edge-bundle/models
   # Or --profile apple-silicon / --profile cpu; --transcript-only omits LLM weights.
   ```

   The source catalog records official Hugging Face LFS SHA256 metadata and pinned official ONNX release bytes. Already verified files are reused; failed downloads never replace a destination. This command is separate from the app and must run before offline deployment. It writes model paths/shard hashes to models.receipt.json. Obtain a **native FFmpeg executable with its runtime dependencies** too. Model sources:
   - [Official whisper.cpp models](https://github.com/ggml-org/whisper.cpp/tree/master/models): `ggml-large-v3-turbo-q5_0.bin`, multilingual rather than `.en`.
   - [sherpa-onnx segmentation release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models): extract `sherpa-onnx-pyannote-segmentation-3-0.tar.bz2`; retain `model.onnx` and licensing information.
   - [Speaker embedding release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models): `wespeaker_en_voxceleb_resnet34.onnx`. The upstream tag really is spelled `recongition`.
   - An instruction-tuned GGUF with a compatible chat template, sized for your chosen profile. Review publisher/license/checksums. Plain base models are not appropriate substitutes for an instruction summary model.

   Suggested folders: `edge-bundle/engines`, `models`, `ffmpeg`, `licenses`. Models are not committed or included in the app-only build. Respect model, training-data, and codec licenses.

3. Verify publisher checksums through trusted sources. The separate preparation tool supports a supplied SHA256:

   ```sh
   python scripts/edge_bundle.py download --url HTTPS_MODEL_URL --sha256 PUBLISHER_SHA256 --output edge-bundle/models/model.bin
   ```

   TLS/checksums remain enabled. Only a verified download replaces the destination. Registration detects subsequent corruption/tampering; computing a local hash does not authenticate an unknown model's original source.

4. Register trusted native files, substituting actual paths:

   ```sh
   python scripts/edge_bundle.py register --profile rtx3080 --output edge-bundle/edge-config.json \
     --asr-executable edge-bundle/engines/whisper.cpp/build/bin/whisper-cli \
     --asr-model edge-bundle/models/ggml-large-v3-turbo-q5_0.bin \
     --segmentation-model edge-bundle/models/segmentation.onnx \
     --embedding-model edge-bundle/models/wespeaker_en_voxceleb_resnet34.onnx \
     --ffmpeg-executable edge-bundle/ffmpeg/ffmpeg \
     --summary-executable edge-bundle/engines/llama.cpp/build/bin/llama-server \
     --summary-model edge-bundle/models/instruct.gguf
   python scripts/edge_bundle.py verify edge-bundle/edge-config.json --summaries
   ```

   Windows uses `.exe`; macOS uses `--profile apple-silicon`. The official Qwen 7B Q4 model is split into two GGUF files: use its `00001-of-00002.gguf` as `--summary-model` and supply `--summary-shard PATH_TO_00002-of-00002.gguf`. Keep both original filenames together; both are verified before loading. The manifest uses relative paths and SHA256 values; transfer the entire tree. For transcription-only installations omit summary assets and retain Full Transcript Only. Choose Local source excerpts explicitly for non-AI notes. Do not register binaries from another OS or a CPU binary under a GPU profile.

5. In Settings select Offline edge, the manifest, and Offline AI (GGUF), or set the non-secret `PSS_EDGE_CONFIG` path. Full Transcript Only remains the default processing mode and never loads an LLM.

6. Package on the native host, optionally embedding a complete bundle:

   ```sh
   uv run --frozen --extra dev python scripts/package.py --edge-bundle edge-bundle
   ```

   All seven registered assets must be inside the embedded directory. The app discovers the packaged manifest automatically; external manifests can also be selected. Include dependencies/licenses. Native signing changes binary hashes: sign nested engines first, register their final bytes, then finish signing the outer app. An offline bundle can be several GB. Do not embed credentials, recordings, or transcripts. The manifest hashes the registered assets, not every ancillary runtime library automatically.

## Offline summaries and citations

The app owns a llama.cpp server on `127.0.0.1`, with an ephemeral key, `--offline`, and no web UI. It never uses a LAN listener or a pre-existing remote service. Local HTTP requests bypass environment proxies; no internet connection is needed for loopback. Loading/requests run off the GUI thread. Completion, cancellation, and startup failure stop the process and remove temporary keys/logs.

Actual tokenization of the rendered template checks context limits; oversized source chunks recursively subdivide without dropping text. Output grammar limits references to source IDs in that chunk, and the citation validator checks them again. Truncated generations are rejected. Generated quotations absent from the cited text are marked unsupported; unassigned/overlapping-source claims are ambiguous regardless of the model's own flag. Quotation matching normalizes whitespace/case and preserves wording/punctuation; abbreviated quotations may need manual review. Exact evidence and display timestamps come from project data. Ordered chunk summaries may repeat points and are not a second global synthesis. Smaller local models can misinterpret evidence; references do not prove arbitrary paraphrase semantics or independent factual truth. Real testing caught a 3B model citing the wrong passage for a quotation; see validation.md. Review significant claims.

## Storage and limits

Original audio stays read-only. Separate PCM16 16 kHz normalization costs about 115 MB/hour on disk. The diarization worker maps float32 samples (about 230 MB/hour), plus native model/intermediate memory that can be much larger. Global clustering may dominate RAM for long conversations. Recognition uses one chunk per child process; Python keeps transcript/turn data. The hard limit is 24 hours/file, **not** a tested 24-hour capacity claim. Benchmark actual multi-hour workloads.

Local caches, diagnostics, autosaves, JSON, and exports are unencrypted and can contain sensitive speech/text. Cloud tests use `PSS_DATA_DIR/edge-cache`. Completed caches remain for recovery; remove the appropriate job cache manually after completion if needed, while retaining saved audio/projects. Offline mode never contacts legacy cloud job IDs.

## Acceptance on the real targets

Disconnect internet access or enforce an outbound firewall:

```sh
python scripts/benchmark_edge.py --config edge-bundle/edge-config.json --audio REAL_PODCAST.wav \
  --output acceptance.pss.json --speakers 4 --summaries
```

Use `--resume` deliberately to reuse a saved job. Reports contain elapsed time, real-time factor, hashes, speaker/segment counts, and sampled total NVIDIA memory where available. GPU readings include other applications. Apple unified/GPU memory needs platform tools. These timings do not measure accuracy without ground truth.

Repeat the original acceptance workflow on Windows 11/RTX 3080 and macOS 26/Apple Silicon: multi-hour speech, accents, noise, overlap, returning voices, interruption/recovery, all import formats, navigation/citations, all export scopes, clean-machine installation, signing/notarization. Start with shorter representative speech and watch RAM/VRAM before increasing duration. See validation.md for executed versus pending checks.
