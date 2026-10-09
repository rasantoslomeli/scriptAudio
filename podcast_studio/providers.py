"""Cancellable HTTP work. No credentials or provider URLs are stored in projects."""
from __future__ import annotations
import time
from dataclasses import asdict
from threading import Event
from pathlib import Path
import requests
from .model import Job, Project, Segment, Speaker, Word, uid


class Cancelled(Exception):
    pass


class ProviderError(Exception):
    pass


class Transport:
    def __init__(self, key: str, cancel: Event, session=None):
        self.key, self.cancel = key, cancel
        self.session = session or requests.Session()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("Processing cancelled; submitted cloud work may continue")

    def wait(self, seconds):
        if self.cancel.wait(seconds):
            self.check()

    def request(self, method, url, *, headers=None, retry=True, **kwargs):
        for attempt in range(4 if retry else 1):
            self.check()
            try:
                response = self.session.request(method, url, headers=headers, timeout=(15, 60), **kwargs)
                self.check()
                if response.status_code == 429 or response.status_code >= 500:
                    if retry and attempt < 3:
                        self.wait(2 ** attempt)
                        continue
                if response.status_code in (401, 403):
                    raise ProviderError("Provider denied access. Check the API key and account permissions.")
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as error:
                # Do not echo response bodies, headers, keys, or signed audio URLs.
                if isinstance(error, requests.HTTPError) or not retry or attempt == 3:
                    raise ProviderError("Provider request failed. Check connectivity, media, and provider status.") from None
                self.wait(2 ** attempt)
        raise ProviderError("Provider request failed")


class CheckedFile:
    def __init__(self, source, transport, progress):
        self.source, self.transport, self.progress = source, transport, progress
        self.total = Path(source.name).stat().st_size

    def __len__(self):
        return self.total

    def read(self, size=-1):
        self.transport.check()
        result = self.source.read(size)
        self.progress(f"Uploading audio: {self.source.tell() / max(1, self.total):.0%}")
        return result


class AssemblyAI:
    base = "https://api.assemblyai.com/v2"

    def __init__(self, key: str, cancel: Event, session=None):
        if not key.strip():
            raise ValueError("An AssemblyAI key is required for cloud transcription")
        self.transport = Transport(key, cancel, session)
        self.headers = {"authorization": key}

    def transcribe(self, project: Project, options: dict, checkpoint, progress) -> dict:
        t = self.transport
        if not project.job:
            project.job = Job(uid("job"))
        job = project.job
        if not job.remote_id:
            job.status, job.error = "uploading", None
            checkpoint()
            progress("Uploading original recording to AssemblyAI…")
            with open(project.audio_path, "rb") as source:
                upload = t.request("POST", self.base + "/upload", headers=self.headers,
                                   data=CheckedFile(source, t, progress), retry=False)
            job.status = "submitting"
            job.submitted_options = dict(options)
            checkpoint()
            # POST is never blindly retried: that can create duplicate charged jobs.
            result = t.request("POST", self.base + "/transcript", headers=self.headers, retry=False,
                               json={"audio_url": upload["upload_url"], "speaker_labels": True,
                                     "disfluencies": True, "format_text": False, "punctuate": True,
                                     "speech_models": ["universal-3-5-pro", "universal-2"],
                                     **options})
            job.remote_id = result["id"]
            checkpoint()
        started = time.monotonic()
        while True:
            t.check()
            result = t.request("GET", self.base + "/transcript/" + job.remote_id, headers=self.headers)
            job.status = result.get("status", "processing")
            checkpoint()
            progress(f"AssemblyAI: {job.status} · {int((time.monotonic()-started)/60)} min in this session")
            if job.status == "completed":
                return result
            if job.status == "error":
                raise ProviderError("AssemblyAI could not process this recording. Check media and account status.")
            t.wait(4)


def apply_transcript(project: Project, result: dict) -> None:
    utterances = result.get("utterances")
    if not utterances:
        raise ProviderError("No speaker-labeled speech returned. The recording may contain no detectable speech.")
    speakers, segments = {}, []
    colors = ["#56702A", "#7365A0", "#A36A35", "#28787D", "#A24F68", "#4E71AA"]
    for u in utterances:
        identity = str(u.get("speaker") if u.get("speaker") is not None else "unknown")
        speaker_id = "speaker_" + identity
        if speaker_id not in speakers:
            speakers[speaker_id] = Speaker(speaker_id, "Unassigned" if identity == "unknown" else "Speaker " + identity,
                                           colors[len(speakers) % len(colors)])
        start, end = u["start"] / 1000, u["end"] / 1000
        words = [Word(w["text"], w["start"] / 1000, w["end"] / 1000, w.get("confidence"))
                 for w in u.get("words", [])]
        segments.append(Segment(uid("seg"), speaker_id, start, end, u["text"], u["text"], words,
                                u.get("confidence"), u.get("speaker_confidence"),
                                bool(u.get("overlap", False)), flags=["Unassigned voice"] if identity == "unknown" else []))
    segments.sort(key=lambda s: s.start)
    # Provider silence gaps are not proof of omitted speech; flag for listening rather than inventing text.
    for previous, current in zip(segments, segments[1:]):
        if current.start < previous.end:
            previous.overlap = current.overlap = True
        if current.start - previous.end > 30:
            current.flags.append("Long preceding gap; listen for silence or missing speech")
    if segments[0].start > 30:
        segments[0].flags.append("Long opening gap; check for missing speech")
    duration = float(result.get("audio_duration") or project.duration)
    if duration and duration - segments[-1].end > 30:
        segments[-1].flags.append("Long trailing gap; check for missing speech")
    # Validate in isolation before replacing an existing usable transcript.
    candidate = Project(project.title, speakers=speakers, segments=segments)
    candidate.validate()
    project.speakers, project.segments = speakers, segments
    project.original_segments = [asdict(s) for s in segments]
    project.duration = max(duration, segments[-1].end)
    project.invalidate_summaries()
    if project.job:
        project.job.status, project.job.error = "completed", None
    project.validate()
