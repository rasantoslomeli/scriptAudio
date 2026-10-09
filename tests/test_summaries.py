from threading import Event
import pytest
from podcast_studio.summaries import validate_claims, evidence, summarize
from podcast_studio.providers import ProviderError, Transport


def test_rejects_fabricated_and_out_of_scope_ids(project):
    claims = validate_claims(project, [{"text": "Supported", "segment_ids": ["s1"]},
                                     {"text": "Fabricated", "segment_ids": ["s1", "invented"]},
                                     {"text": "No source", "segment_ids": []},
                                     {"text": "Other speaker", "segment_ids": ["s2"]}], {"s1"})
    assert [c.status for c in claims] == ["supported", "unsupported", "unsupported", "unsupported"]
    assert evidence(project, claims[0]) == [{"id": "s1", "speaker": "Speaker A", "start": 0, "end": 2,
                                          "text": "Remote teams collaborate."}]


def test_sources_always_resolve_current_timestamps(project):
    claim = validate_claims(project, [{"text": "Model invented 99:99", "segment_ids": ["s3"], "start": 999999}])[0]
    assert evidence(project, claim)[0]["start"] == 14400
    project.rename("a", "Jordan")
    assert evidence(project, claim)[0]["speaker"] == "Jordan"


def test_local_individual_and_topic(project):
    result = summarize(project, "Individual speakers", ["a", "b"], "", "", "", Event(), lambda _: None)
    assert len(result) == 2 and len(result[0].claims) == 2
    assert result[0].provider == "Local extractive (not AI)"
    assert all(project.segment(i).speaker_id == "a" for c in result[0].claims for i in c.segment_ids)
    topic = summarize(project, "Topic / question", [], "noise", "", "", Event(), lambda _: None)
    assert [c.segment_ids for c in topic[0].claims] == [["s2"]]


def test_cloud_structured_response_and_chunk_scope(project, monkeypatch):
    calls = []
    def request(self, method, url, **kwargs):
        import json
        source = json.loads(kwargs["json"]["messages"][1]["content"])
        calls.append(source)
        return {"choices": [{"message": {"content": json.dumps({"claims": [
            {"text": "Speaker assertion", "segment_ids": [source[0]["id"]]},
            {"text": "Invalid assertion", "segment_ids": ["nonexistent"]}]})}}]}
    monkeypatch.setattr(Transport, "request", request)
    result = summarize(project, "Chronological", [], "", "dummy", "fixture-model", Event(), lambda _: None)
    assert len(calls) == 1
    assert result[0].claims[0].status == "supported" and result[0].claims[1].status == "unsupported"
    assert [s["id"] for s in calls[0]] == ["s1", "s2", "s3"]


def test_long_transcript_all_chunks_covered(project, monkeypatch):
    from podcast_studio.model import Segment
    project.segments = [Segment(f"s{i}", "a", i*60, i*60+55, "x"*12000, "x"*12000) for i in range(20)]
    seen = []
    def request(self, *args, **kwargs):
        import json
        data = json.loads(kwargs["json"]["messages"][1]["content"])
        seen.extend(s["id"] for s in data)
        return {"choices": [{"message": {"content": '{"claims": []}'}}]}
    monkeypatch.setattr(Transport, "request", request)
    summarize(project, "Entire podcast", [], "", "dummy", "test", Event(), lambda _: None)
    assert seen == [f"s{i}" for i in range(20)]


def test_malformed_citations_fail(project):
    with pytest.raises(ProviderError):
        validate_claims(project, [{"text": "bad", "segment_ids": "s1"}])


def test_quotation_requires_the_cited_passage(project):
    claim = validate_claims(project,[{"text":"They said 'Let us\n test remote audio.'", "segment_ids":["s1"]}])[0]
    assert claim.status == "unsupported"
    assert "Quoted text" in claim.note
    correct = validate_claims(project,[{"text":"They said 'Let us\n test remote audio.'", "segment_ids":["s3"]}])[0]
    assert correct.status == "supported"


def test_quote_normalization_and_contractions(project):
    claims = validate_claims(project,[{"text":"They said “remote   teams collaborate.”", "segment_ids":["s1"]},
                                    {"text":"It's a team's assertion", "segment_ids":["s1"]}])
    assert all(c.status == "supported" for c in claims)


@pytest.mark.parametrize("reason",["unassigned","overlap"])
def test_uncertain_source_requires_review_even_when_model_denies_it(project,reason):
    if reason=="unassigned":project.segments[0].flags.append("Unassigned voice")
    else:project.segments[0].overlap=True
    claim=validate_claims(project,[{"text":"Speaker assertion", "segment_ids":["s1"], "ambiguous":False}])[0]
    assert claim.status == "ambiguous"


def test_single_oversized_passage_is_not_truncated(project, monkeypatch):
    project.segments = [project.segments[0]]
    project.segments[0].text = 'long passage ' * 10000
    fragments = []
    def request(self, *args, **kwargs):
        import json
        data = json.loads(kwargs['json']['messages'][1]['content'])
        fragments.extend(s['text'] for s in data)
        assert all(s['id'] == 's1' for s in data)
        return {'choices': [{'message': {'content': '{"claims": []}'}}]}
    monkeypatch.setattr(Transport, 'request', request)
    summarize(project, 'Entire podcast', [], '', 'dummy', 'test', Event(), lambda _: None)
    assert len(fragments) > 1 and ''.join(fragments) == project.segments[0].text
