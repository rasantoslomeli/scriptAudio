from PySide6.QtCore import QUrl, Qt
from podcast_studio.app import Studio
from podcast_studio.model import Summary, Claim


def test_desktop_navigation_edit_and_citation(qtbot, project, tmp_path):
    window = Studio(tmp_path)
    qtbot.addWidget(window)
    window.add_project(project)
    assert window.transcript.count() == 3
    item = window.transcript.item(0)
    window.select_segment(item)
    window.editor.setPlainText("Corrected remote collaboration.")
    assert project.segment("s1").text == "Corrected remote collaboration."
    assert project.segment("s1").original_text == "Remote teams collaborate."
    window.query.setText("noise")
    assert window.transcript.count() == 1
    window.navigate_result(1)
    assert window.editing == "s2"
    assert "CONTEXT" in window.context.text() and "remote" in window.context.text()
    project.summaries.append(Summary("summary", "Entire podcast", "Claim", [Claim("Remote collaboration", ["s3"])], "test"))
    window.render_summaries()
    assert "04:00:00" in window.summary_view.toPlainText()
    window.citation_clicked(QUrl("segment:s3"))
    assert window.editing == "s3" and window.tabs.currentIndex() == 0
    window.position_changed(14401000)
    assert "s3" in window.active_item
    window.checkpoint()
    assert window.autosave_path(project).exists()
    window.apply_theme(True)
    assert "#C8F15A" in window.styleSheet()
    window.close()


def test_worker_runs_and_finishes(qtbot, tmp_path):
    window = Studio(tmp_path)
    qtbot.addWidget(window)
    results = []
    window.run_task(lambda progress, cancel: 42, results.append)
    qtbot.waitUntil(lambda: window.task is None, timeout=10000)
    assert results == [42] and not window.cancel_button.isEnabled()
    window.close()


def test_failed_worker_restores_persisted_job(qtbot, project, tmp_path, monkeypatch):
    from podcast_studio.providers import AssemblyAI, ProviderError
    from podcast_studio.model import Job
    window = Studio(tmp_path)
    qtbot.addWidget(window)
    window.add_project(project)
    window.assembly_key = 'fixture'
    monkeypatch.setattr(window, 'cloud_consent', lambda **kwargs: True)
    warnings = []
    monkeypatch.setattr(window, 'fail', warnings.append)
    def failed(self, snapshot, options, checkpoint, progress):
        snapshot.job = Job('local', 'processing', 'remote-persisted')
        checkpoint()
        raise ProviderError('Synthetic network interruption')
    monkeypatch.setattr(AssemblyAI, 'transcribe', failed)
    project.segments.clear()  # No replacement confirmation is needed for an untranscribed recording.
    window.process_current()
    qtbot.waitUntil(lambda: window.task is None, timeout=10000)
    assert window.project.job.remote_id == 'remote-persisted'
    assert window.project.job.status == 'interrupted'
    assert warnings and window.autosave_path(window.project).exists()
    window.close()


def test_import_to_transcript_correction_summary_and_export(qtbot, tmp_path, monkeypatch):
    import wave
    from podcast_studio.providers import AssemblyAI
    from podcast_studio.model import Job, Project
    from podcast_studio.exports import export, FORMATS, EXTENSIONS
    recording = tmp_path / 'fictional.wav'
    with wave.open(str(recording), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\x00\x00' * 32000)
    window = Studio(tmp_path / 'state')
    qtbot.addWidget(window)
    warnings = []
    monkeypatch.setattr(window, 'fail', warnings.append)
    monkeypatch.setattr(window, 'cloud_consent', lambda **kwargs: True)
    window.import_files([str(recording), str(recording)])
    qtbot.waitUntil(lambda: window.task is None, timeout=10000)
    assert window.project_choice.count() == 2
    assert len(list((window.data / 'projects').glob('*.json'))) == 2
    window.assembly_key = 'fixture'
    window.summary_key = ''
    def transcribed(self, snapshot, options, checkpoint, progress):
        snapshot.job = Job('job', 'completed', 'fixture-remote')
        checkpoint()
        return {'audio_duration': 2, 'utterances': [
            {'speaker': 'A', 'start': 0, 'end': 1000, 'text': 'Hello remote teams.', 'confidence': .95},
            {'speaker': 'B', 'start': 1000, 'end': 2000, 'text': 'Let us check that.', 'confidence': .9}]}
    monkeypatch.setattr(AssemblyAI, 'transcribe', transcribed)
    window.process_current()
    qtbot.waitUntil(lambda: window.task is None, timeout=10000)
    assert window.transcript.count() == 2 and window.project.job.remote_id == 'fixture-remote'
    window.project.rename('speaker_A', 'Jordan')
    window.refresh_all()
    window.select_segment(window.transcript.item(0))
    window.editor.setPlainText('Hello, remote teams.')
    assert window.project.segments[0].original_text == 'Hello remote teams.'
    window.generate_summary()
    qtbot.waitUntil(lambda: window.task is None, timeout=10000)
    assert len(window.project.summaries) == 1
    claim = window.project.summaries[0].claims[0]
    assert claim.segment_ids == [window.project.segments[0].id]
    for fmt in FORMATS:
        destination = tmp_path / ('result' + EXTENSIONS[fmt])
        export(window.project, str(destination), fmt)
        assert destination.stat().st_size > 0
    restored = Project.load(tmp_path / 'result.json')
    assert restored.speakers['speaker_A'].name == 'Jordan'
    assert not warnings
    window.close()
