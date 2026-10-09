import hashlib
import math
import struct
import wave
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioBufferOutput, QMediaPlayer
from podcast_studio.app import Studio
from podcast_studio.media import import_audio


def write_tone(path, seconds=3):
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(b''.join(struct.pack('<h', int(1500*math.sin(2*math.pi*440*i/16000)))
                                  for i in range(seconds*16000)))


def test_import_probe_never_modifies_source(tmp_path):
    path = tmp_path / 'recording.wav'
    write_tone(path)
    before = hashlib.sha256(path.read_bytes()).digest()
    project = import_audio(str(path))
    assert project.file_size == path.stat().st_size
    assert project.audio_path == str(path.resolve())
    assert before == hashlib.sha256(path.read_bytes()).digest()
    if 'note' not in project.media:
        assert project.duration == 3 and project.media['channels'] == 1


def test_reject_invalid_media(tmp_path):
    with pytest.raises(ValueError):
        import_audio(str(tmp_path / 'missing.mp3'))


def test_actual_qt_audio_decode_and_timestamp_seek(qtbot, tmp_path):
    path = tmp_path / 'recording.wav'
    write_tone(path)
    window = Studio(tmp_path / 'state')
    qtbot.addWidget(window)
    buffers = QAudioBufferOutput(window)
    received = []
    buffers.audioBufferReceived.connect(lambda buffer: received.append((buffer.startTime(), buffer.sampleCount())))
    window.player.setAudioBufferOutput(buffers)
    window.add_project(import_audio(str(path)))
    qtbot.waitUntil(lambda: window.player.duration() == 3000, timeout=10000)
    window.seek(1.5)
    window.player.play()
    qtbot.waitUntil(lambda: any(count > 0 for _, count in received), timeout=10000)
    assert window.player.error() == QMediaPlayer.Error.NoError
    assert any(start >= 1400000 and count > 0 for start, count in received)
    assert window.player.position() >= 1500
    window.player.pause()
    window.close()
