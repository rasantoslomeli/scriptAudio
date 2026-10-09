import csv
import json
import pytest
from podcast_studio.exports import FORMATS, EXTENSIONS, SCOPES, export, report
from podcast_studio.model import Claim, Summary, Project


@pytest.mark.parametrize("format", FORMATS)
def test_all_formats_preserve_content(project, tmp_path, format):
    path = tmp_path / ("export" + EXTENSIONS[format])
    export(project, path, format)
    assert path.stat().st_size > 100
    if format == "PDF":
        assert path.read_bytes().startswith(b"%PDF")
    elif format == "DOCX":
        from docx import Document
        content = "\n".join(p.text for p in Document(path).paragraphs)
        assert "Speaker A" in content and "04:00:00" in content
    elif format == "JSON":
        assert Project.load(path).to_dict() == project.to_dict()
    else:
        content = path.read_text(encoding="utf-8-sig")
        assert "Speaker A" in content and "Remote teams collaborate." in content
        assert ("04:00:00,000" if format == "SRT" else "04:00:00") in content


def test_speaker_filter_and_summary_citations(project, tmp_path):
    project.summaries.append(Summary("sum", "Individual speakers", "A summary", [Claim("Teams work remotely", ["s1"])], "fixture"))
    assert "I disagree" not in report(project, SCOPES[1], "a")
    text = report(project, SCOPES[4])
    assert "Source s1" in text and "Evidence: Remote teams collaborate." in text
    assert "not independently verified" in text
    path = tmp_path / "speaker.csv"
    export(project, path, "CSV", SCOPES[1], "b")
    rows = list(csv.reader(path.open(encoding="utf-8-sig")))
    assert len(rows) == 2 and rows[1][2] == "Speaker B"


def test_srt_rejects_summary_scope(project, tmp_path):
    with pytest.raises(ValueError, match="SRT"):
        export(project, tmp_path / "bad.srt", "SRT", SCOPES[2])


def test_csv_formula_neutralized(project, tmp_path):
    project.edit("s1", "=DANGEROUS()")
    export(project, tmp_path / "safe.csv", "CSV")
    assert "'=DANGEROUS()" in (tmp_path / "safe.csv").read_text(encoding="utf-8-sig")


def test_readability_never_replaces_original(project, tmp_path):
    project.edit("s1", "Edited readability", readable=True)
    assert "Edited readability" not in report(project, SCOPES[0])
    assert "Edited readability" in report(project, SCOPES[0], readable=True)
    assert project.segment("s1").original_text == "Remote teams collaborate."
