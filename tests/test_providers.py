from threading import Event
import requests
import pytest
from podcast_studio.providers import AssemblyAI, Transport, Cancelled, ProviderError, apply_transcript
from podcast_studio.model import Job, Project


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
    def json(self):
        return self.data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("Do not leak response")


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def test_complete_upload_poll_and_persist(tmp_path, monkeypatch):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"synthetic fixture")
    project = Project("Test", str(source))
    session = Session([Response({"upload_url": "https://fixture.invalid/audio"}), Response({"id": "remote-job"}),
                       Response({"status": "processing"}), Response({"status": "completed", "utterances": []})])
    provider = AssemblyAI("fixture-key", Event(), session)
    monkeypatch.setattr(provider.transport, "wait", lambda _: None)
    states = []
    result = provider.transcribe(project, {"language_detection": True}, lambda: states.append(project.to_dict()), lambda _: None)
    assert result["status"] == "completed" and project.job.remote_id == "remote-job"
    assert len(session.calls) == 4
    assert session.calls[1][2]["json"]["speaker_labels"] is True
    assert states[2]["job"]["remote_id"] == "remote-job"
    assert b"synthetic fixture" == source.read_bytes()
    assert "fixture-key" not in str(states)


def test_resume_does_not_upload_again(project):
    project.job = Job("local-job", "interrupted", "existing-remote")
    session = Session([Response({"status": "completed"})])
    result = AssemblyAI("key", Event(), session).transcribe(project, {}, lambda: None, lambda _: None)
    assert result["status"] == "completed" and session.calls[0][0] == "GET"
    assert len(session.calls) == 1


def test_network_error_retry_sanitized(monkeypatch):
    session = Session([requests.ConnectionError("SECRET")]*4)
    transport = Transport("SECRET", Event(), session)
    monkeypatch.setattr(transport, "wait", lambda _: None)
    with pytest.raises(ProviderError) as error:
        transport.request("GET", "https://fixture.invalid")
    assert "SECRET" not in str(error.value) and len(session.calls) == 4


def test_no_duplicate_post_on_failure(monkeypatch):
    session = Session([requests.ConnectionError("secret")])
    with pytest.raises(ProviderError):
        Transport("key", Event(), session).request("POST", "https://fixture.invalid", retry=False)
    assert len(session.calls) == 1


def test_cancel_before_network():
    cancel = Event()
    cancel.set()
    session = Session([])
    with pytest.raises(Cancelled):
        Transport("key", cancel, session).request("GET", "https://fixture.invalid")
    assert not session.calls


@pytest.mark.parametrize("status", [401, 403, 400])
def test_failed_auth_or_request_no_retry(status):
    session = Session([Response({}, status)])
    with pytest.raises(ProviderError):
        Transport("key", Event(), session).request("GET", "https://fixture.invalid")
    assert len(session.calls) == 1


def test_diarization_preserves_returning_speaker_and_flags(project):
    result = {"audio_duration": 100, "utterances": [
        {"speaker": "A", "start": 0, "end": 3000, "text": "Hola, café.", "confidence": .7,
         "words": [{"text": "Hola,", "start": 0, "end": 1000, "confidence": .9}]},
        {"speaker": "B", "start": 2000, "end": 4000, "text": "Cross-talk", "confidence": .9},
        {"speaker": "A", "start": 60000, "end": 65000, "text": "Returning voice", "confidence": .9}]}
    apply_transcript(project, result)
    assert project.segments[0].speaker_id == project.segments[2].speaker_id
    assert project.segments[0].overlap and project.segments[1].overlap
    assert project.segments[2].flags
    assert project.segments[0].words[0].end == 1
    assert project.segments[0].original_text == "Hola, café."


def test_invalid_provider_data_does_not_replace_transcript(project):
    before = project.to_dict()
    with pytest.raises(ValueError):
        apply_transcript(project, {"utterances": [{"speaker": "X", "start": 2000, "end": 1000, "text": "invalid"}]})
    assert project.to_dict() == before


def test_rate_limit_recovers_without_changing_credentials(monkeypatch):
    session = Session([Response({}, 429), Response({'status': 'completed'})])
    transport = Transport('key', Event(), session)
    waits = []
    monkeypatch.setattr(transport, 'wait', waits.append)
    assert transport.request('GET', 'https://fixture.invalid')['status'] == 'completed'
    assert waits == [1] and len(session.calls) == 2
