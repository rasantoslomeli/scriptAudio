"""Key-free packaged/source smoke check: exercise UI, exports, and actual decoding."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import sys
from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QAudioBufferOutput, QMediaPlayer
from .app import Studio
from .model import Project
from .summaries import summarize
from .exports import FORMATS, EXTENSIONS, export


def run_smoke(app):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    sample = root / 'samples' / 'roundtable.pss.json'
    temporary = TemporaryDirectory(prefix='pss-smoke-')
    window = Studio(Path(temporary.name) / 'state')
    decoded, errors = [], []
    output = QAudioBufferOutput(window)
    output.audioBufferReceived.connect(lambda buffer: decoded.append(buffer.sampleCount()))
    window.player.setAudioBufferOutput(output)
    window.show()

    def start():
        try:
            project = Project.load(sample)
            window.add_project(project)
            assert window.transcript.count() == 7
            times = [(s.id, s.start, s.end) for s in project.segments]
            project.rename('b', 'Jordan — smoke check')
            assert times == [(s.id, s.start, s.end) for s in project.segments]
            project.summaries.extend(summarize(project, 'Individual speakers', ['b'], '', '', '', Event(), lambda _: None))
            for fmt in FORMATS:
                path = Path(temporary.name) / ('export' + EXTENSIONS[fmt])
                export(project, str(path), fmt)
                assert path.stat().st_size > 0
            project.save(Path(temporary.name) / 'roundtrip.json')
            assert Project.load(Path(temporary.name) / 'roundtrip.json').to_dict() == project.to_dict()
            window.seek(8)
            window.player.play()
            QTimer.singleShot(1500, finish)
        except Exception as error:
            errors.append(type(error).__name__ + ': ' + str(error))
            finish()

    def finish():
        if not errors:
            if not any(count > 0 for count in decoded):
                errors.append('No decoded audio buffers received')
            if window.player.error() != QMediaPlayer.Error.NoError:
                errors.append(window.player.errorString())
        window.player.stop()
        window.close()
        if errors:
            print('SMOKE FAILED: ' + '; '.join(errors), flush=True)
        else:
            print('SMOKE PASSED: desktop, project roundtrip, speaker correction, local cited summary, seven exports, WAV decoding and seek', flush=True)
        app.exit(1 if errors else 0)

    QTimer.singleShot(300, start)
    QTimer.singleShot(15000, lambda: (errors.append('Smoke check timed out'), finish()))
    code = app.exec()
    temporary.cleanup()
    return code
