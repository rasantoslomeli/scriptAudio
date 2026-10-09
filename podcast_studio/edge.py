"""Offline-only orchestration: explicit files, bounded ASR chunks, global diarization.

No model loaders/downloaders or cloud clients are used here. Large engines run in
child processes so cancellation releases memory and CUDA/Metal allocations.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Event
import hashlib
from bisect import bisect_left, bisect_right
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from .model import Job, Project, uid
from .providers import Cancelled, ProviderError

PROFILES = {
    "rtx3080": {"name": "RTX 3080 · 10 GB · CUDA", "asr": "large-v3-turbo-q5_0", "summary": "3B Instruct Q4_K_M", "threads": 6},
    "apple-silicon": {"name": "Apple Silicon · 64 GB · Metal", "asr": "large-v3-turbo-q5_0", "summary": "7B Instruct Q4_K_M", "threads": 6},
    "cpu": {"name": "CPU fallback (explicit)", "asr": "small", "summary": "1.5B Instruct Q4_K_M", "threads": 4},
}


def digest(path, cancel=None):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        while data := source.read(1024*1024):
            if cancel and cancel.is_set():
                raise Cancelled()
            value.update(data)
    return value.hexdigest()


def write_json(path, data):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".edge-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class EdgeConfig:
    profile: str
    asr_executable: str
    asr_model: str
    segmentation_model: str
    embedding_model: str
    ffmpeg_executable: str
    summary_executable: str = ""
    summary_model: str = ""
    hashes: dict[str, str] = field(default_factory=dict)
    threads: int = 4
    chunk_seconds: int = 900
    overlap_seconds: int = 5
    cluster_threshold: float = .4
    context_size: int = 8192
    max_summary_tokens: int = 1536
    gpu_layers: int = 99
    schema_version: int = 1
    summary_shards: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        if not path or not Path(path).is_file():
            raise ValueError("Offline engine bundle is missing. Choose an edge-config.json in Settings; see docs/edge.md.")
        path = Path(path).resolve()
        config = cls(**json.loads(path.read_text(encoding="utf-8")))
        for name in config.asset_names():
            value = getattr(config, name)
            if value:
                setattr(config, name, str((path.parent / value).resolve()))
        return config

    @staticmethod
    def asset_names():
        return ["asr_executable", "asr_model", "segmentation_model", "embedding_model", "ffmpeg_executable",
                "summary_executable", "summary_model"]

    def verify(self, summary=False, cancel=None):
        if self.schema_version != 1 or self.profile not in PROFILES:
            raise ValueError("Unsupported edge bundle/profile")
        if not 1 <= self.threads <= 32 or not 30 <= self.chunk_seconds <= 3600 or not 0 <= self.overlap_seconds <= 15:
            raise ValueError("Invalid edge thread/chunk configuration")
        if not 0 < self.cluster_threshold < 2 or not 2048 <= self.context_size <= 32768 or not 128 <= self.max_summary_tokens <= self.context_size//2:
            raise ValueError("Invalid clustering/context configuration")
        if not 0 <= self.gpu_layers <= 999:
            raise ValueError("Invalid GPU layer configuration")
        assets = self.asset_names() if summary else self.asset_names()[:5]
        for name in assets:
            path, expected = getattr(self, name), self.hashes.get(name, "")
            if not path or not Path(path).is_file():
                raise ValueError(f"Offline asset missing: {name}. No internet or cloud fallback will be used.")
            if len(expected) != 64 or digest(path, cancel) != expected.lower():
                raise ValueError(f"Offline asset checksum mismatch/unregistered: {name}")
            if name.endswith("executable") and not os.access(path, os.X_OK):
                raise ValueError(f"Engine is not executable: {name}")
        if summary:
            split = re.fullmatch(r"(.+)-(\d{5})-of-(\d{5})\.gguf",Path(self.summary_model).name)
            if split:
                prefix, part, count = split.group(1), int(split.group(2)), int(split.group(3))
                if part != 1 or not 1 <= count <= 64:
                    raise ValueError("Select the first GGUF shard of a supported split model")
                required = {f"{prefix}-{i:05}-of-{count:05}.gguf" for i in range(2,count+1)}
                if not required.issubset(self.summary_shards):
                    raise ValueError("Register every GGUF shard; the primary file alone cannot validate the complete model")
            for filename, expected in self.summary_shards.items():
                if Path(filename).name != filename or not filename.endswith('.gguf'):
                    raise ValueError("Invalid additional GGUF shard filename")
                shard = Path(self.summary_model).parent / filename
                if not shard.is_file() or len(expected) != 64 or digest(shard,cancel) != expected.lower():
                    raise ValueError(f"Additional GGUF shard is missing or has a checksum mismatch: {filename}")
        return self

    def save(self, path):
        write_json(path, asdict(self))


def run_process(command, cancel, progress, label, log_path, progress_file=None):
    """No shell; cancellation terminates the owned engine, not a user service."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with open(log_path, "wb") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.wait(.25):
                    raise Cancelled("Local processing cancelled; completed chunks are retained")
                detail = ""
                if progress_file and Path(progress_file).is_file():
                    try:
                        state = json.loads(Path(progress_file).read_text())
                        detail = f" · {state['done']}/{state['total']} windows"
                    except (ValueError, KeyError, OSError):
                        pass
                progress(f"{label}{detail} · {int(time.monotonic()-started)} s")
            if cancel.is_set():
                raise Cancelled()
            if process.returncode:
                raise ProviderError(f"{label} failed (exit {process.returncode}). Check the engine, models and available memory. Diagnostic: {log_path}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def check_acceleration(profile, log_path):
    if profile == "cpu":
        return
    text = Path(log_path).read_text(encoding="utf-8",errors="replace").lower()
    backend = "cuda" if profile == "rtx3080" else "metal"
    if f"using {backend}" not in text:
        raise ProviderError(f"The selected {backend.upper()} profile did not activate its GPU backend. Install the matching native engine/driver or explicitly choose a CPU bundle.")


def speaker_letter(number):
    result = ""
    while True:
        number, rem = divmod(number, 26)
        result = chr(65+rem) + result
        if number == 0:
            return result
        number -= 1


class TurnIndex:
    """Bound interval queries so long transcripts do not rescan all past turns per word."""
    def __init__(self,turns):
        self.turns=sorted(turns,key=lambda t:t["start"])
        self.starts=[t["start"] for t in self.turns]
        self.prefix_ends=[]
        edge=0.0
        for turn in self.turns:
            edge=max(edge,turn["end"])
            self.prefix_ends.append(edge)

    def overlapping(self,start,end):
        lower=bisect_right(self.prefix_ends,start)
        upper=bisect_left(self.starts,end)
        return [turn for turn in self.turns[lower:upper] if turn["end"]>start]


def assign_speaker(start, end, turns):
    """Overlap scores are alignment heuristics, not calibrated voice confidence."""
    if isinstance(turns,TurnIndex):
        turns=turns.overlapping(start,end)
    scores = {}
    active = []
    for turn in turns:
        if turn["start"] >= end:
            break
        overlap = max(0, min(end, turn["end"]) - max(start, turn["start"]))
        if overlap:
            who = turn["speaker"]
            scores[who] = scores.get(who, 0) + overlap
            active.append(turn)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))
    simultaneous = any(a["speaker"] != b["speaker"] and max(start,a["start"],b["start"]) < min(end,a["end"],b["end"])
                       for i,a in enumerate(active) for b in active[i+1:])
    if not ranked or ranked[0][1] < .5 * max(.01,end-start):
        return None, simultaneous, ["No reliable diarization alignment; reassign manually"]
    if len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * .6:
        return None, simultaneous, ["Ambiguous/overlapping voices; attribution withheld"]
    return ranked[0][0], simultaneous, []


def whisper_words(item):
    """Merge BPE pieces into space-delimited words; keep exact recognized text.

CJK may remain phrase-sized tokens. Alignment is approximate and is never
presented as a forced alignment of subsequent user edits.
"""
    bounds = item["offsets"]
    result, pending = [], None
    for token in item.get("tokens", []):
        text = token.get("text", "")
        if not text or text.startswith("[_") or text.startswith("<|"):
            continue
        offsets = token.get("offsets")
        if not offsets:
            return []  # Preserve full segment text rather than dropping untimed speech.
        start = max(bounds["from"], min(bounds["to"], offsets["from"]))
        end = max(start, min(bounds["to"], offsets["to"]))
        if text[:1].isspace() and pending:
            result.append(pending)
            pending = None
        if pending is None:
            pending = {"text": text, "start": start, "end": end, "confidence": token.get("p")}
        else:
            pending["text"] += text
            pending["end"] = max(pending["end"],end)
            if pending["confidence"] is not None and token.get("p") is not None:
                pending["confidence"] = min(pending["confidence"], token["p"])
    if pending:
        result.append(pending)
    if "".join(w["text"] for w in result).strip() != item["text"].strip():
        return []
    return result


def align_transcript(chunks, turns, duration):
    turns = sorted(turns, key=lambda t: t["start"])
    identity_order = list(dict.fromkeys(t["speaker"] for t in turns))
    identities = {speaker: speaker_letter(i) for i,speaker in enumerate(identity_order)}
    turn_index=TurnIndex(turns)
    utterances = []
    for chunk in chunks:
        offset = chunk["offset"]*1000
        core_start, core_end = chunk["core_start"]*1000, chunk["core_end"]*1000
        for item in chunk["data"].get("transcription", []):
            words = whisper_words(item)
            units = words or [{"text": item["text"], "start": item["offsets"]["from"], "end": item["offsets"]["to"], "confidence": None}]
            current = None
            for word in units:
                start, end = max(0,word["start"]+offset), min(duration*1000,word["end"]+offset)
                if not math.isfinite(start+end) or end < start:
                    raise ProviderError("Whisper returned invalid timestamps")
                if not core_start <= (start+end)/2 < core_end:
                    continue  # Ownership within overlapped chunks: never transcribe just the first hour.
                who, overlap, flags = assign_speaker(start/1000,end/1000,turn_index)
                if not words:
                    flags.append("Word alignment unavailable; inspect speaker boundaries")
                flags.append("Local diarization confidence is uncalibrated; review voice assignment")
                identity = identities.get(who)
                if current is None or current["speaker"] != identity or start-current["end"] > 1500 or end-current["start"] > 30000:
                    current = {"speaker": identity, "start": start, "end": end, "text": "", "words": [],
                               "overlap": overlap, "review_flags": flags.copy()}
                    utterances.append(current)
                current["text"] += word["text"]
                current["end"] = max(current["end"],end)
                current["overlap"] |= overlap
                current["review_flags"] = list(dict.fromkeys(current["review_flags"] + flags))
                if words:
                    current["words"].append({**word,"start": start,"end": end})
            if current:
                current["text"] = current["text"].strip()
    utterances.sort(key=lambda u:u["start"])
    return {"utterances": utterances, "audio_duration": duration}


class OfflineTranscriber:
    def __init__(self, config_path, cache_root, cancel):
        self.config_path = str(Path(config_path).resolve()) if config_path else ""
        self.config = EdgeConfig.load(config_path).verify(cancel=cancel)
        self.cache_root, self.cancel = Path(cache_root), cancel

    def transcribe(self, project: Project, options, checkpoint, progress):
        config = self.config
        progress("Offline: checking recording fingerprint")
        signature = hashlib.sha256(json.dumps({"audio":digest(project.audio_path,self.cancel), "config":asdict(config),
                                                "language": options.get("language_code", "auto"),
                                                "speakers": options.get("speakers_expected", -1), "pipeline_version":1},sort_keys=True).encode()).hexdigest()
        cache = self.cache_root / project.id / signature
        cache.mkdir(parents=True,exist_ok=True)
        if not project.job or project.job.submitted_options.get("provider") != "offline" or project.job.submitted_options.get("fingerprint") != signature:
            project.job = Job(uid("local_job"),submitted_options={"provider":"offline","fingerprint":signature,"profile":config.profile})
        project.job.status = "normalizing"
        project.job.error = None
        checkpoint()
        normalized = cache/"audio.wav"
        if not normalized.exists():
            temporary = cache/"audio.partial.wav"
            run_process([config.ffmpeg_executable,"-nostdin","-hide_banner","-loglevel","error","-y","-i",project.audio_path,
                         "-map","0:a:0","-ac","1","-ar","16000","-c:a","pcm_s16le",str(temporary)],
                        self.cancel,progress,"Offline audio normalization",cache/"ffmpeg.log")
            os.replace(temporary,normalized)
        with wave.open(str(normalized)) as source:
            total_frames,rate = source.getnframes(),source.getframerate()
            if source.getnchannels()!=1 or source.getsampwidth()!=2 or rate!=16000:
                raise ProviderError("Invalid normalized audio")
        duration = total_frames/rate
        if duration > 24*3600:
            raise ProviderError("Offline jobs currently support at most 24 hours per file; split longer recordings losslessly.")
        turns_path = cache/"diarization.json"
        if not turns_path.exists():
            project.job.status = "diarizing"
            checkpoint()
            worker = [sys.executable,"--diarize-worker"] if getattr(sys,"frozen",False) else [sys.executable,"-m","podcast_studio.edge_worker"]
            run_process(worker+[self.config_path,str(normalized),str(turns_path),str(options.get("speakers_expected",-1))],
                        self.cancel,progress,"Offline global speaker diarization",cache/"diarization.log",cache/"diarization.progress.json")
        turns = json.loads(turns_path.read_text())
        chunks = []
        count = math.ceil(duration/config.chunk_seconds)
        with wave.open(str(normalized)) as source:
            for index in range(count):
                if self.cancel.is_set():
                    raise Cancelled()
                core_start = index*config.chunk_seconds
                core_end = min(duration,(index+1)*config.chunk_seconds)
                start = max(0,core_start-config.overlap_seconds)
                end = min(duration,core_end+config.overlap_seconds)
                result_path = cache/f"asr-{index:05}.json"
                if not result_path.exists():
                    project.job.status = f"transcribing {index+1}/{count}"
                    checkpoint()
                    chunk_audio = cache/"chunk.wav"
                    source.setpos(round(start*rate))
                    with wave.open(str(chunk_audio),"wb") as output:
                        output.setparams((1,2,rate,0,"NONE","not compressed"))
                        remaining = round((end-start)*rate)
                        while remaining:
                            size = min(remaining,rate*30)
                            output.writeframes(source.readframes(size))
                            remaining -= size
                    prefix = cache/"asr-partial"
                    command = [config.asr_executable,"-m",config.asr_model,"-f",str(chunk_audio),"-ojf","-of",str(prefix),
                               "-t",str(config.threads),"-l",options.get("language_code","auto"),"-pp"]
                    if config.profile == "cpu":
                        command += ["-ng"]
                    run_process(command,self.cancel,progress,f"Offline speech recognition {index+1}/{count}",cache/f"asr-{index:05}.log")
                    check_acceleration(config.profile,cache/f"asr-{index:05}.log")
                    raw = json.loads(Path(str(prefix)+".json").read_text())
                    if not isinstance(raw.get("transcription"),list):
                        raise ProviderError("Whisper returned no transcript structure")
                    write_json(result_path,raw)
                    chunk_audio.unlink(missing_ok=True)
                chunks.append({"offset":start,"core_start":core_start,"core_end":core_end,"data":json.loads(result_path.read_text())})
        project.media["processing"] = "Offline whisper.cpp + sherpa-onnx"
        project.media["hardware_profile"] = config.profile
        project.job.status = "aligning"
        checkpoint()
        return align_transcript(chunks,turns,duration)
