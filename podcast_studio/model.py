"""Versioned project data, corrections, and atomic persistence. Times are seconds."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import math
import os
import tempfile
import uuid


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def stamp(seconds: float, milliseconds: bool = False) -> str:
    total = max(0, round(seconds * 1000))
    hours, rem = divmod(total, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    value = f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{value}.{millis:03}" if milliseconds else value


@dataclass
class Speaker:
    id: str
    name: str
    color: str = "#68735A"
    role: str = "Unconfirmed"


@dataclass
class Word:
    text: str
    start: float
    end: float
    confidence: float | None = None


@dataclass
class Segment:
    id: str
    speaker_id: str
    start: float
    end: float
    text: str
    original_text: str
    words: list[Word] = field(default_factory=list)
    confidence: float | None = None
    speaker_confidence: float | None = None
    overlap: bool = False
    reviewed: bool = False
    flags: list[str] = field(default_factory=list)
    readable_text: str | None = None

    def review_reasons(self) -> list[str]:
        result = list(self.flags)
        if self.confidence is not None and self.confidence < .8:
            result.append("Low transcription confidence")
        if self.speaker_confidence is None:
            result.append("Speaker confidence unavailable")
        elif self.speaker_confidence < .8:
            result.append("Uncertain speaker assignment")
        if self.overlap:
            result.append("Overlapping speech")
        return list(dict.fromkeys(result))


@dataclass
class Claim:
    text: str
    segment_ids: list[str]
    status: str = "supported"
    note: str = "Speaker assertion; not independently verified"


@dataclass
class Summary:
    id: str
    mode: str
    title: str
    claims: list[Claim]
    provider: str
    stale: bool = False


@dataclass
class Job:
    id: str
    status: str = "pending"
    remote_id: str | None = None
    error: str | None = None
    submitted_options: dict = field(default_factory=dict)


@dataclass
class Bookmark:
    time: float
    label: str


@dataclass
class Project:
    title: str
    audio_path: str = ""
    duration: float = 0
    file_size: int = 0
    media: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uid("project"))
    speakers: dict[str, Speaker] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    summaries: list[Summary] = field(default_factory=list)
    bookmarks: list[Bookmark] = field(default_factory=list)
    job: Job | None = None
    schema_version: int = 1
    original_segments: list[dict] = field(default_factory=list)

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported project schema version")
        if not math.isfinite(self.duration) or self.duration < 0 or self.file_size < 0:
            raise ValueError("Invalid recording metadata")
        ids = set()
        for key, speaker in self.speakers.items():
            if key != speaker.id or not speaker.name.strip():
                raise ValueError("Invalid speaker identity")
        previous = -1.0
        for s in self.segments:
            if not s.id or s.id in ids or s.speaker_id not in self.speakers:
                raise ValueError("Duplicate segment or unknown speaker")
            if not all(math.isfinite(t) for t in (s.start, s.end)) or not 0 <= s.start <= s.end:
                raise ValueError("Invalid segment timestamps")
            if s.start < previous:
                raise ValueError("Segments must be in chronological order")
            previous = s.start
            ids.add(s.id)
            for w in s.words:
                if not all(math.isfinite(t) for t in (w.start, w.end)) or not s.start <= w.start <= w.end <= s.end:
                    raise ValueError("Invalid word timestamps")
        for b in self.bookmarks:
            if not math.isfinite(b.time) or b.time < 0:
                raise ValueError("Invalid bookmark")
        for summary in self.summaries:
            for claim in summary.claims:
                if not claim.segment_ids or any(i not in ids for i in claim.segment_ids):
                    claim.status = "unsupported"
                    claim.note = "Missing or invalid source references; review required"

    def segment(self, segment_id: str) -> Segment:
        return next(s for s in self.segments if s.id == segment_id)

    def invalidate_summaries(self) -> None:
        for summary in self.summaries:
            summary.stale = True

    def rename(self, speaker_id: str, name: str, role: str | None = None) -> None:
        if not name.strip():
            raise ValueError("Speaker name cannot be blank")
        self.speakers[speaker_id].name = name.strip()
        if role is not None:
            self.speakers[speaker_id].role = role
        self.invalidate_summaries()

    def edit(self, segment_id: str, text: str, speaker_id: str | None = None, readable: bool = False) -> None:
        s = self.segment(segment_id)
        if speaker_id is not None:
            if speaker_id not in self.speakers:
                raise ValueError("Unknown speaker")
            s.speaker_id = speaker_id
        if readable:
            s.readable_text = text
        else:
            s.text = text
        self.invalidate_summaries()

    def merge(self, source: str, target: str) -> None:
        if source == target or source not in self.speakers or target not in self.speakers:
            raise ValueError("Select two distinct speakers")
        for s in self.segments:
            if s.speaker_id == source:
                s.speaker_id = target
        del self.speakers[source]
        self.invalidate_summaries()

    def split(self, segment_id: str, at: float, left_text: str, right_text: str, right_speaker: str) -> None:
        s = self.segment(segment_id)
        if not s.start < at < s.end or right_speaker not in self.speakers:
            raise ValueError("Split must fall inside the segment and use an existing speaker")
        if any(w.start < at < w.end for w in s.words):
            raise ValueError("Choose a split between words; inspect word timestamps")
        left_words = [w for w in s.words if w.end <= at]
        right_words = [w for w in s.words if w.start >= at]
        # New identities deliberately invalidate old citations; never silently retarget evidence.
        original_left = " ".join(w.text for w in left_words) if s.words else s.original_text
        original_right = " ".join(w.text for w in right_words) if s.words else s.original_text
        flags = list(s.flags)
        if not s.words:
            flags.append("Original provider passage retained on both halves; no word alignment")
        left = Segment(uid("seg"), s.speaker_id, s.start, at, left_text, original_left,
                       left_words, s.confidence, s.speaker_confidence, s.overlap, False, flags.copy())
        right = Segment(uid("seg"), right_speaker, at, s.end, right_text, original_right,
                        right_words, s.confidence, None, s.overlap, False, flags.copy())
        index = self.segments.index(s)
        self.segments[index:index+1] = [left, right]
        self.segments.sort(key=lambda item: item.start)
        self.invalidate_summaries()
        self.validate()

    def search(self, query: str = "", speaker_id: str | None = None, review: bool = False) -> list[Segment]:
        return [s for s in self.segments if (not speaker_id or s.speaker_id == speaker_id)
                and query.casefold() in s.text.casefold()
                and (not review or bool(s.review_reasons()) and not s.reviewed)]

    def speaker_stats(self, speaker_id: str) -> tuple[int, float]:
        selected = [s for s in self.segments if s.speaker_id == speaker_id]
        # Union intervals: do not double-count overlapping segments for one speaker.
        duration, edge = 0.0, 0.0
        for s in sorted(selected, key=lambda item: item.start):
            duration += max(0, s.end - max(edge, s.start))
            edge = max(edge, s.end)
        return len(selected), duration

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Project:
        raw = dict(raw)
        raw["speakers"] = {k: Speaker(**v) for k, v in raw.get("speakers", {}).items()}
        raw["segments"] = [Segment(**{**s, "words": [Word(**w) for w in s.get("words", [])]})
                           for s in raw.get("segments", [])]
        raw["summaries"] = [Summary(**{**s, "claims": [Claim(**c) for c in s["claims"]]})
                            for s in raw.get("summaries", [])]
        raw["bookmarks"] = [Bookmark(**b) for b in raw.get("bookmarks", [])]
        raw["job"] = Job(**raw["job"]) if raw.get("job") else None
        project = cls(**raw)
        project.validate()
        return project

    @classmethod
    def load(cls, path: str | Path) -> Project:
        with open(path, encoding="utf-8") as source:
            project = cls.from_dict(json.load(source))
        if project.audio_path and not Path(project.audio_path).is_absolute():
            project.audio_path = str((Path(path).parent / project.audio_path).resolve())
        return project

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pss-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
