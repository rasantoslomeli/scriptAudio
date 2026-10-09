import json
import pytest
from podcast_studio.model import Project, Summary, Claim, Word, stamp


def test_rename_preserves_timestamps_and_ids(project):
    before = [(s.id, s.start, s.end) for s in project.segments]
    project.rename("a", "Dr. Sarah Chen", "Guest")
    assert project.speakers["a"].name == "Dr. Sarah Chen"
    assert [(s.id, s.start, s.end) for s in project.segments] == before
    assert sum(project.speakers[s.speaker_id].name == "Dr. Sarah Chen" for s in project.segments) == 2


def test_edit_preserves_original_and_word_times(project):
    before = project.segments[0].words.copy()
    project.edit("s1", "Remote teams cooperate.", "b")
    project.edit("s1", "A readable variation.", readable=True)
    s = project.segment("s1")
    assert s.original_text == "Remote teams collaborate."
    assert s.text == "Remote teams cooperate." and s.readable_text == "A readable variation."
    assert s.words == before and (s.start, s.end) == (0, 2) and s.speaker_id == "b"


def test_json_atomic_roundtrip(project, tmp_path):
    path = tmp_path / "project.json"
    project.save(path)
    loaded = Project.load(path)
    assert loaded.to_dict() == project.to_dict()
    project.rename("b", "Casey")
    project.save(path)
    assert Project.load(path).speakers["b"].name == "Casey"
    assert not list(tmp_path.glob(".pss-*"))


def test_search_accent_case_filter_context(project):
    project.edit("s2", "El café: NOISE and cross-talk.")
    assert [s.id for s in project.search("CAFÉ")] == ["s2"]
    assert [s.id for s in project.search("remote", "a")] == ["s1", "s3"]
    assert project.search("remote", "b") == []
    assert "s2" in [s.id for s in project.search(review=True)]
    project.segment("s2").reviewed = True
    assert "s2" not in [s.id for s in project.search(review=True)]


def test_merge_split_invalidates_citations(project):
    project.summaries.append(Summary("sum", "Entire podcast", "Summary", [Claim("Teams collaborate", ["s1"])], "test"))
    project.split("s1", 1.05, "Remote teams", "collaborate.", "b")
    assert len(project.segments) == 4
    assert project.segments[0].end == project.segments[1].start == 1.05
    assert project.segments[1].speaker_id == "b"
    assert project.summaries[0].stale and project.summaries[0].claims[0].status == "unsupported"
    assert len({s.id for s in project.segments}) == 4
    project.merge("b", "a")
    assert len(project.speakers) == 1 and all(s.speaker_id == "a" for s in project.segments)


def test_split_inside_word_refused(project):
    with pytest.raises(ValueError, match="between words"):
        project.split("s1", .25, "a", "b", "b")
    assert len(project.segments) == 3


@pytest.mark.parametrize("field,value", [("start", -1), ("end", float("nan")), ("speaker_id", "missing"), ("id", "s2")])
def test_invalid_segments_rejected(project, field, value):
    setattr(project.segments[0], field, value)
    with pytest.raises(ValueError):
        project.validate()


def test_long_recording_timestamps_and_stats(project):
    assert stamp(14400) == "04:00:00"
    assert stamp(3599.9996, True) == "01:00:00.000"
    assert project.speaker_stats("a") == (2, 7)
    assert [s.id for s in project.search("remote")] == ["s1", "s3"]


def test_relative_audio_resolved(project, tmp_path):
    project.audio_path = "recording.wav"
    project.save(tmp_path / "p.json")
    assert Project.load(tmp_path / "p.json").audio_path == str(tmp_path / "recording.wav")
