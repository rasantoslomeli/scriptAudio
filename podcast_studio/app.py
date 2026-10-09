"""Qt desktop presentation. All media probing and provider work run on workers."""
from __future__ import annotations
import copy
import os
import sys
from pathlib import Path
from threading import Event
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QStandardPaths, QSettings, QTimer
from PySide6.QtGui import QAction, QKeySequence, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QToolBar, QFileDialog, QMessageBox, QLineEdit, QListWidget, QListWidgetItem,
    QComboBox, QCheckBox, QSplitter, QTabWidget, QTextEdit, QTextBrowser, QSlider, QDialog,
    QDialogButtonBox, QFormLayout, QInputDialog, QProgressBar, QGroupBox, QDoubleSpinBox,
    QAbstractItemView)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaMetaData
from .model import Project, Speaker, Job, Bookmark, uid, stamp
from .media import import_audio, SUPPORTED
from .providers import AssemblyAI, Cancelled, apply_transcript
from .summaries import MODES, summarize, evidence
from .exports import FORMATS, SCOPES, EXTENSIONS, export, report


class Task(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation, self.cancel = operation, Event()

    def run(self):
        try:
            self.succeeded.emit(self.operation(self.progress.emit, self.cancel))
        except Cancelled:
            self.cancelled.emit()
        except Exception as error:
            # Exception messages from our provider layer are sanitized; never log credentials.
            self.failed.emit(str(error) if not isinstance(error, OSError) else "Cannot read or write a required local file.")


class SettingsDialog(QDialog):
    def __init__(self, settings, assembly_key, summary_key, parent):
        super().__init__(parent)
        self.setWindowTitle("Processing settings")
        self.resize(550, 380)
        layout = QVBoxLayout(self)
        notice = QLabel("Cloud transcription uploads the original recording to AssemblyAI.\n"
                        "AI summaries send transcript passages to OpenAI. Provider charges and retention policies apply.\n"
                        "Keys entered here remain in memory for this session and are never saved in projects.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.assembly = QLineEdit(assembly_key)
        self.summary = QLineEdit(summary_key)
        for field in (self.assembly, self.summary):
            field.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = QLineEdit(settings.value("summary_model", "gpt-4o-mini"))
        self.language = QLineEdit(settings.value("language", ""))
        self.language.setPlaceholderText("Blank = automatic; otherwise en, es, …")
        self.speakers = QComboBox()
        self.speakers.addItems(["Automatic"] + [str(i) for i in range(2, 21)])
        self.speakers.setCurrentText(settings.value("speaker_count", "Automatic"))
        self.default_mode = QComboBox()
        self.default_mode.addItems(["Full Transcript Only", "Transcript + entire podcast AI summary"])
        self.default_mode.setCurrentText(settings.value("processing_mode", "Full Transcript Only"))
        for label, field in [("AssemblyAI API key", self.assembly), ("OpenAI API key (optional)", self.summary),
                             ("Summary model", self.model), ("Language", self.language),
                             ("Expected speaker count", self.speakers), ("After transcription", self.default_mode)]:
            form.addRow(label, field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class Studio(QMainWindow):
    def __init__(self, data_dir=None):
        super().__init__()
        self.setWindowTitle("Podcast Speaker Studio")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)
        self.setAcceptDrops(True)
        self.data = Path(data_dir or os.environ.get("PSS_DATA_DIR") or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        self.data.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings(str(self.data / "settings.ini"), QSettings.Format.IniFormat)
        self.assembly_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
        self.summary_key = os.environ.get("PSS_SUMMARY_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.projects: dict[str, Project] = {}
        self.project: Project | None = None
        self.task = None
        self.queue = []
        self.visible_segments = []
        self.editing = None
        self.loading_editor = False
        self.active_item = None
        self.job_project_id = None
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(.8)
        self.build_ui()
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.metaDataChanged.connect(self.media_metadata_changed)
        self.player.errorOccurred.connect(lambda *_: self.statusBar().showMessage("Playback error: " + self.player.errorString()))
        self.apply_theme(self.settings.value("dark", False, type=bool))
        self.statusBar().showMessage("Import a recording or open the synthetic sample project. Full Transcript Only is the default.")
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(800)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self.checkpoint)

    def button(self, text, callback, layout=None):
        button = QPushButton(text)
        button.clicked.connect(callback)
        if layout:
            layout.addWidget(button)
        return button

    def action(self, label, callback, shortcut=None):
        action = QAction(label, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        self.addAction(action)
        return action

    def build_ui(self):
        toolbar = QToolBar("Project", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for label, callback, shortcut in [("Import audio", self.browse_audio, "Ctrl+I"), ("Open project", self.open_project, "Ctrl+O"),
                                         ("Save project", self.save_project, "Ctrl+S"), ("Export", self.export_dialog, "Ctrl+E")]:
            toolbar.addAction(self.action(label, callback, shortcut))
        toolbar.addSeparator()
        self.transcribe_action = toolbar.addAction("Transcribe / resume", self.process_current)
        self.batch_action = toolbar.addAction("Process batch", self.process_batch)
        toolbar.addAction("Settings", self.configure)
        toolbar.addAction("Recover autosave", self.recover)
        self.dark = QCheckBox("Dark mode")
        self.dark.toggled.connect(self.apply_theme)
        toolbar.addWidget(self.dark)
        body = QWidget()
        outer = QVBoxLayout(body)
        heading = QHBoxLayout()
        title = QLabel("Podcast Speaker Studio")
        title.setObjectName("brand")
        heading.addWidget(title)
        heading.addStretch()
        self.project_choice = QComboBox()
        self.project_choice.setMinimumWidth(280)
        self.project_choice.currentIndexChanged.connect(self.select_project)
        heading.addWidget(self.project_choice)
        outer.addLayout(heading)
        self.metadata = QLabel("Your conversation, with every source a click away.")
        self.metadata.setWordWrap(True)
        outer.addWidget(self.metadata)
        splitter = QSplitter()
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search transcript…  Ctrl+F")
        self.query.textChanged.connect(self.refresh_transcript)
        side.addWidget(self.query)
        self.speaker_list = QListWidget()
        self.speaker_list.currentItemChanged.connect(self.refresh_transcript)
        side.addWidget(QLabel("SPEAKERS · estimated speaking time"))
        side.addWidget(self.speaker_list, 1)
        speaker_buttons = QHBoxLayout()
        self.button("Rename / role", self.rename_speaker, speaker_buttons)
        self.button("Merge", self.merge_speaker, speaker_buttons)
        side.addLayout(speaker_buttons)
        self.button("Add speaker", self.add_speaker, side)
        self.review_filter = QCheckBox("Only segments needing review")
        self.review_filter.toggled.connect(self.refresh_transcript)
        side.addWidget(self.review_filter)
        self.follow = QCheckBox("Follow playback")
        self.follow.setChecked(True)
        side.addWidget(self.follow)
        side.addWidget(QLabel("BOOKMARKS"))
        self.bookmarks = QListWidget()
        self.bookmarks.setMaximumHeight(140)
        self.bookmarks.itemClicked.connect(lambda item: self.seek(item.data(Qt.ItemDataRole.UserRole)))
        side.addWidget(self.bookmarks)
        self.button("Bookmark current time", self.add_bookmark, side)
        splitter.addWidget(sidebar)
        self.tabs = QTabWidget()
        transcript_panel = QWidget()
        transcript_layout = QVBoxLayout(transcript_panel)
        nav = QHBoxLayout()
        self.result_count = QLabel("No transcript yet")
        nav.addWidget(self.result_count)
        nav.addStretch()
        self.button("Previous result", lambda: self.navigate_result(-1), nav)
        self.button("Next result", lambda: self.navigate_result(1), nav)
        self.button("Copy transcript", self.copy_transcript, nav)
        transcript_layout.addLayout(nav)
        self.transcript = QListWidget()
        self.transcript.setWordWrap(True)
        self.transcript.setSpacing(7)
        self.transcript.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.transcript.itemClicked.connect(self.select_segment)
        self.transcript.itemDoubleClicked.connect(lambda item: self.seek(self.project.segment(item.data(Qt.ItemDataRole.UserRole)).start))
        transcript_layout.addWidget(self.transcript, 3)
        self.context = QLabel("Search results retain the full segment; select one to inspect surrounding conversation.")
        self.context.setWordWrap(True)
        self.context.setMinimumHeight(65)
        self.context.setMaximumHeight(100)
        self.context.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        transcript_layout.addWidget(self.context)
        editor_box = QGroupBox("Selected passage · edit without changing source timestamps")
        editor = QVBoxLayout(editor_box)
        editor_row = QHBoxLayout()
        self.edit_speaker = QComboBox()
        self.edit_speaker.currentIndexChanged.connect(self.editor_changed)
        editor_row.addWidget(self.edit_speaker)
        self.readable = QCheckBox("Edit readability copy")
        self.readable.toggled.connect(self.load_editor)
        editor_row.addWidget(self.readable)
        self.reviewed = QCheckBox("Reviewed")
        self.reviewed.toggled.connect(self.review_changed)
        editor_row.addWidget(self.reviewed)
        self.button("Play timestamp", self.play_selected, editor_row)
        self.button("Split segment", self.split_segment, editor_row)
        self.button("Inspect original / words", self.inspect_original, editor_row)
        editor.addLayout(editor_row)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Select a passage above to make inline corrections.")
        self.editor.setMaximumHeight(130)
        self.editor.textChanged.connect(self.editor_changed)
        editor.addWidget(self.editor)
        transcript_layout.addWidget(editor_box)
        self.tabs.addTab(transcript_panel, "Conversation")
        summary_panel = QWidget()
        summary_layout = QVBoxLayout(summary_panel)
        summary_controls = QHBoxLayout()
        self.summary_mode = QComboBox()
        self.summary_mode.addItems(MODES)
        summary_controls.addWidget(self.summary_mode)
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("Topic or question (topic mode)")
        summary_controls.addWidget(self.topic)
        self.summary_button = self.button("Generate summary", self.generate_summary, summary_controls)
        summary_layout.addLayout(summary_controls)
        note = QLabel("AI claims describe speaker assertions, not verified facts. Inspect every supporting passage.\n"
                      "Without an OpenAI key, this produces explicitly labelled local source excerpts, not an AI summary.")
        note.setWordWrap(True)
        summary_layout.addWidget(note)
        self.summary_view = QTextBrowser()
        self.summary_view.setOpenLinks(False)
        self.summary_view.anchorClicked.connect(self.citation_clicked)
        summary_layout.addWidget(self.summary_view)
        self.tabs.addTab(summary_panel, "Summaries & evidence")
        splitter.addWidget(self.tabs)
        splitter.setSizes([300, 1050])
        outer.addWidget(splitter, 1)
        progress_row = QHBoxLayout()
        self.progress_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.cancel_button = self.button("Cancel", self.cancel_task)
        self.cancel_button.setEnabled(False)
        progress_row.addWidget(self.progress_label, 2)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel_button)
        outer.addLayout(progress_row)
        player_row = QHBoxLayout()
        self.button("−15 s", lambda: self.seek(self.player.position()/1000 - 15), player_row)
        self.play_button = self.button("Play / pause", self.toggle_play, player_row)
        self.button("+15 s", lambda: self.seek(self.player.position()/1000 + 15), player_row)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.sliderReleased.connect(lambda: self.player.setPosition(self.timeline.value()))
        player_row.addWidget(self.timeline, 1)
        self.time_label = QLabel("00:00:00 / 00:00:00")
        player_row.addWidget(self.time_label)
        self.speed = QComboBox()
        self.speed.addItems(["0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"])
        self.speed.setCurrentText("1×")
        self.speed.currentTextChanged.connect(lambda value: self.player.setPlaybackRate(float(value[:-1])))
        player_row.addWidget(self.speed)
        outer.addLayout(player_row)
        self.setCentralWidget(body)
        self.action("Find", lambda: self.query.setFocus(), "Ctrl+F")
        self.action("Play/pause", self.toggle_play, "Ctrl+Space")
        self.action("Back 15 seconds", lambda: self.seek(self.player.position()/1000-15), "Alt+Left")
        self.action("Forward 15 seconds", lambda: self.seek(self.player.position()/1000+15), "Alt+Right")

    def apply_theme(self, dark):
        self.dark.blockSignals(True)
        self.dark.setChecked(dark)
        self.dark.blockSignals(False)
        self.settings.setValue("dark", dark)
        bg, panel, text, muted, border = (("#1E2519", "#2C3524", "#F5F1E6", "#B9C4A3", "#46513A") if dark else
                                         ("#F5F1E6", "#FFFFFF", "#2C3B20", "#68735A", "#E0E8C5"))
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {bg}; color: {text}; font-size: 13px; }}
            QLabel#brand {{ font-size: 25px; font-weight: 700; padding: 8px 0; }}
            QToolBar {{ border: none; padding: 10px; spacing: 8px; }}
            QPushButton {{ background: #C8F15A; color: #2C3B20; border: none; border-radius: 8px; padding: 8px 12px; }}
            QPushButton:hover {{ background: #D9FA89; }}
            QPushButton:disabled {{ background: {border}; color: {muted}; }}
            QListWidget, QTextEdit, QTextBrowser, QLineEdit, QComboBox {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 8px; }}
            QListWidget::item {{ padding: 10px; border-bottom: 1px solid {border}; }}
            QListWidget::item:selected {{ background: {border}; color: {text}; }}
            QGroupBox {{ border: 1px solid {border}; border-radius: 10px; margin-top: 12px; padding-top: 14px; }}
            QTabBar::tab {{ padding: 10px 20px; }}
            QTabBar::tab:selected {{ background: #C8F15A; color: #2C3B20; border-radius: 8px; }}
            QProgressBar {{ border: 1px solid {border}; border-radius: 6px; text-align: center; }}
            QProgressBar::chunk {{ background: #C8F15A; }}
            QSlider::groove:horizontal {{ background: {border}; height: 6px; }}
            QSlider::handle:horizontal {{ background: #C8F15A; width: 16px; margin: -5px 0; border-radius: 8px; }}
        """)

    def fail(self, message):
        QMessageBox.warning(self, "Podcast Speaker Studio", message)

    def autosave_path(self, project):
        return self.data / "projects" / (project.id + ".pss.json")

    def checkpoint(self):
        if self.project and not self.busy():
            try:
                self.project.save(self.autosave_path(self.project))
            except (OSError, ValueError) as error:
                self.fail("Autosave failed: " + str(error))

    def busy(self):
        return self.task is not None

    def mark_changed(self):
        self.autosave_timer.start()
        self.render_summaries()

    def add_project(self, project):
        if project.id in self.projects:
            self.projects[project.id] = project
            self.project_choice.setCurrentIndex(self.project_choice.findData(project.id))
            self.select_project(self.project_choice.currentIndex())
            return
        self.projects[project.id] = project
        self.project_choice.addItem(project.title, project.id)
        self.project_choice.setCurrentIndex(self.project_choice.count()-1)
        self.checkpoint()

    def select_project(self, index):
        if index < 0:
            return
        self.flush_editor()
        self.project = self.projects[self.project_choice.itemData(index)]
        self.editing = None
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(self.project.audio_path) if self.project.audio_path else QUrl())
        self.refresh_all()

    def refresh_all(self):
        p = self.project
        if not p:
            return
        properties = " · ".join(f"{k}: {v}" for k, v in p.media.items() if v is not None)
        self.metadata.setText(f"{p.title} · {stamp(p.duration)} · {p.file_size/1048576:.1f} MB · {properties}")
        selected = self.selected_speaker()
        self.speaker_list.blockSignals(True)
        self.speaker_list.clear()
        all_item = QListWidgetItem("All speakers")
        self.speaker_list.addItem(all_item)
        self.speaker_list.setCurrentRow(0)
        for speaker in p.speakers.values():
            count, duration = p.speaker_stats(speaker.id)
            item = QListWidgetItem(f"{speaker.name} · {speaker.role}\n{count} passages · {stamp(duration)}\n{speaker.id}")
            item.setData(Qt.ItemDataRole.UserRole, speaker.id)
            item.setForeground(QColor(speaker.color))
            self.speaker_list.addItem(item)
            if speaker.id == selected:
                self.speaker_list.setCurrentItem(item)
        self.speaker_list.blockSignals(False)
        self.bookmarks.clear()
        for b in p.bookmarks:
            item = QListWidgetItem(f"{stamp(b.time)} · {b.label}")
            item.setData(Qt.ItemDataRole.UserRole, b.time)
            self.bookmarks.addItem(item)
        self.refresh_transcript()
        self.render_summaries()
        self.load_editor()

    def selected_speaker(self):
        item = self.speaker_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_transcript(self, *_):
        if not self.project:
            return
        identity = self.editing
        self.transcript.blockSignals(True)
        self.transcript.clear()
        self.visible_segments = self.project.search(self.query.text(), self.selected_speaker(), self.review_filter.isChecked())
        for s in self.visible_segments:
            item = QListWidgetItem(self.segment_label(s))
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            item.setToolTip(" · ".join(s.review_reasons()) or "No provider review flags")
            self.transcript.addItem(item)
            if s.id == identity:
                self.transcript.setCurrentItem(item)
        self.transcript.blockSignals(False)
        self.result_count.setText(f"{len(self.visible_segments)} of {len(self.project.segments)} passages")
        self.active_item = None

    def segment_label(self, s):
        reasons = s.review_reasons()
        return (f"{self.project.speakers[s.speaker_id].name}    {stamp(s.start, True)}–{stamp(s.end, True)}\n"
                f"{s.text}\n" + ("✓ Reviewed" if s.reviewed else "Review: " + "; ".join(reasons) if reasons else ""))

    def select_segment(self, item):
        self.flush_editor()
        self.editing = item.data(Qt.ItemDataRole.UserRole)
        self.load_editor()
        segment = self.project.segment(self.editing)
        self.seek(segment.start)
        index = self.project.segments.index(segment)
        neighbors = self.project.segments[max(0,index-1):index+2]
        self.context.setText("CONTEXT  " + "  |  ".join(f"{stamp(s.start)} {self.project.speakers[s.speaker_id].name}: {s.text[:180]}" for s in neighbors))

    def load_editor(self, *_):
        self.loading_editor = True
        self.edit_speaker.clear()
        if self.project:
            for sp in self.project.speakers.values():
                self.edit_speaker.addItem(sp.name, sp.id)
        if self.project and self.editing and any(s.id == self.editing for s in self.project.segments):
            s = self.project.segment(self.editing)
            self.editor.setPlainText(s.readable_text if self.readable.isChecked() and s.readable_text is not None else s.text)
            self.edit_speaker.setCurrentIndex(self.edit_speaker.findData(s.speaker_id))
            self.reviewed.setChecked(s.reviewed)
        else:
            self.editor.clear()
            self.reviewed.setChecked(False)
        self.loading_editor = False

    def editor_changed(self, *_):
        if self.loading_editor or self.busy() or not self.project or not self.editing:
            return
        speaker = self.edit_speaker.currentData()
        if speaker is None:
            return
        self.project.edit(self.editing, self.editor.toPlainText(), speaker, self.readable.isChecked())
        item = self.transcript.currentItem()
        if item and item.data(Qt.ItemDataRole.UserRole) == self.editing:
            item.setText(self.segment_label(self.project.segment(self.editing)))
        self.mark_changed()

    def flush_editor(self):
        if not self.busy():
            self.checkpoint()

    def review_changed(self, value):
        if not self.loading_editor and not self.busy() and self.project and self.editing:
            self.project.segment(self.editing).reviewed = value
            self.mark_changed()
            self.refresh_transcript()

    def navigate_result(self, direction):
        count = self.transcript.count()
        if count:
            row = (self.transcript.currentRow() + direction) % count
            self.transcript.setCurrentRow(row)
            self.select_segment(self.transcript.item(row))

    def rename_speaker(self):
        sid = self.selected_speaker()
        if not self.project or not sid or self.busy():
            return
        name, ok = QInputDialog.getText(self, "Rename speaker globally", "Name", text=self.project.speakers[sid].name)
        if ok and name.strip():
            roles = ["Unconfirmed", "Host", "Guest", "Moderator", "Interviewer"]
            role, confirmed = QInputDialog.getItem(self, "User-confirmed role", "Role (optional)", roles, editable=False)
            self.project.rename(sid, name, role if confirmed else None)
            self.refresh_all()
            self.checkpoint()

    def add_speaker(self):
        if not self.project or self.busy():
            return
        name, ok = QInputDialog.getText(self, "Add speaker", "User-supplied label")
        if ok and name.strip():
            identity = uid("speaker")
            self.project.speakers[identity] = Speaker(identity, name.strip())
            self.refresh_all()
            self.checkpoint()

    def merge_speaker(self):
        sid = self.selected_speaker()
        if not self.project or not sid or self.busy():
            return
        targets = [s for s in self.project.speakers.values() if s.id != sid]
        if not targets:
            return
        labels = [f"{s.name} ({s.id})" for s in targets]
        choice, ok = QInputDialog.getItem(self, "Merge selected speaker into", "All affected passages will be reassigned", labels, editable=False)
        if ok:
            self.project.merge(sid, targets[labels.index(choice)].id)
            self.refresh_all()
            self.checkpoint()

    def split_segment(self):
        if not self.project or not self.editing or self.busy():
            return
        s = self.project.segment(self.editing)
        dialog = QDialog(self)
        dialog.setWindowTitle("Split passage at a speaker change")
        layout = QFormLayout(dialog)
        at = QDoubleSpinBox()
        at.setDecimals(3)
        at.setRange(s.start, s.end)
        at.setValue((s.start+s.end)/2)
        left, right = QTextEdit(s.text), QTextEdit()
        speaker = QComboBox()
        for sp in self.project.speakers.values():
            speaker.addItem(sp.name, sp.id)
        layout.addRow("Split time (seconds; between words)", at)
        layout.addRow("Left passage", left)
        layout.addRow("Right passage", right)
        layout.addRow("Right speaker", speaker)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            try:
                if not left.toPlainText().strip() or not right.toPlainText().strip():
                    raise ValueError("Enter the recognized speech for both halves")
                self.project.split(s.id, at.value(), left.toPlainText(), right.toPlainText(), speaker.currentData())
                self.editing = None
                self.refresh_all()
                self.checkpoint()
            except ValueError as error:
                self.fail(str(error))

    def inspect_original(self):
        if not self.project or not self.editing:
            return
        s = self.project.segment(self.editing)
        dialog = QDialog(self)
        dialog.setWindowTitle("Original recognized speech and word alignment")
        dialog.resize(700, 500)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText("ORIGINAL PROVIDER PASSAGE\n" + s.original_text + "\n\nWORD TIMESTAMPS\n" +
                          "\n".join(f"{stamp(w.start, True)}–{stamp(w.end, True)} {w.text} ({w.confidence})" for w in s.words))
        layout.addWidget(text)
        self.button("Close", dialog.accept, layout)
        dialog.exec()

    def add_bookmark(self):
        if not self.project or self.busy():
            return
        label, ok = QInputDialog.getText(self, "Bookmark", "Label")
        if ok:
            self.project.bookmarks.append(Bookmark(self.player.position()/1000, label or "Bookmark"))
            self.refresh_all()
            self.checkpoint()

    def seek(self, seconds):
        self.player.setPosition(round(max(0, seconds)*1000))

    def play_selected(self):
        if self.project and self.editing:
            self.seek(self.project.segment(self.editing).start)
            self.player.play()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def media_metadata_changed(self):
        if not self.project or self.busy():
            return
        meta = self.player.metaData()
        for name, key in [("codec", QMediaMetaData.Key.AudioCodec), ("audio_bitrate", QMediaMetaData.Key.AudioBitRate),
                          ("format", QMediaMetaData.Key.FileFormat)]:
            value = meta.stringValue(key)
            if value:
                self.project.media[name] = value
        self.refresh_all()
        self.checkpoint()

    def duration_changed(self, milliseconds):
        self.timeline.setRange(0, max(1, milliseconds))
        if self.project and milliseconds and not self.project.duration and not self.busy():
            self.project.duration = milliseconds/1000
            self.refresh_all()
            self.checkpoint()

    def position_changed(self, milliseconds):
        if not self.timeline.isSliderDown():
            self.timeline.setValue(milliseconds)
        self.time_label.setText(f"{stamp(milliseconds/1000)} / {stamp(self.player.duration()/1000)}")
        if not self.project:
            return
        seconds = milliseconds/1000
        current_ids = {s.id for s in self.visible_segments if s.start <= seconds < s.end}
        if current_ids == self.active_item:
            return
        self.active_item = current_ids
        for row in range(self.transcript.count()):
            item = self.transcript.item(row)
            active = item.data(Qt.ItemDataRole.UserRole) in current_ids
            font = item.font()
            font.setBold(active)
            item.setFont(font)
            if active and self.follow.isChecked():
                self.transcript.scrollToItem(item)

    def browse_audio(self):
        if self.busy():
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Import recordings", "", "Podcast media (" + " ".join("*"+e for e in sorted(SUPPORTED)) + ")")
        self.import_files(files)

    def import_files(self, files):
        if not files or self.busy():
            return
        def operation(progress, cancel):
            projects, errors = [], []
            for file in files:
                if cancel.is_set():
                    raise Cancelled()
                progress("Inspecting " + Path(file).name)
                try:
                    projects.append(import_audio(file))
                except Exception:
                    errors.append(Path(file).name)
            return projects, errors
        def completed(result):
            projects, errors = result
            for project in projects:
                self.add_project(project)
                project.save(self.autosave_path(project))
            if errors:
                self.fail("Could not import: " + ", ".join(errors) + ". Check media validity and supported codecs.")
        self.run_task(operation, completed)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.busy():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.import_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def open_project(self):
        if self.busy():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Studio projects (*.json)")
        if path:
            try:
                self.add_project(Project.load(path))
                if self.project.audio_path and not Path(self.project.audio_path).is_file():
                    QMessageBox.information(self, "Relink audio", "The transcript is available, but its recording has moved. Select the original audio to restore playback.")
                    audio, _ = QFileDialog.getOpenFileName(self, "Relink original recording")
                    if audio:
                        self.project.audio_path = str(Path(audio).resolve())
                        self.player.setSource(QUrl.fromLocalFile(self.project.audio_path))
                        self.checkpoint()
            except (OSError, ValueError, TypeError, KeyError):
                self.fail("Cannot open this project. Check that it is a supported, valid Studio JSON project.")

    def recover(self):
        if self.busy():
            return
        choices = []
        for path in sorted((self.data / "projects").glob("*.pss.json")):
            try:
                project = Project.load(path)
                choices.append((f"{project.title} · {project.job.status if project.job else 'saved'} · {project.id}", project))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        if not choices:
            QMessageBox.information(self, "Recovery", "No valid autosaved projects found.")
            return
        label, ok = QInputDialog.getItem(self, "Recover project", "Resume cloud jobs with Transcribe / resume", [c[0] for c in choices], editable=False)
        if ok:
            project = next(p for name, p in choices if name == label)
            if project.id in self.projects:
                self.projects[project.id] = project
                self.project_choice.setCurrentIndex(self.project_choice.findData(project.id))
                self.select_project(self.project_choice.currentIndex())
            else:
                self.add_project(project)

    def save_project(self):
        if not self.project or self.busy():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save editable project", self.project.title + ".pss.json", "Studio projects (*.json)")
        if path:
            try:
                self.project.save(path)
                self.statusBar().showMessage("Project saved. Audio is referenced separately and never modified.")
            except (OSError, ValueError):
                self.fail("Could not save the project.")

    def configure(self):
        dialog = SettingsDialog(self.settings, self.assembly_key, self.summary_key, self)
        if dialog.exec():
            self.assembly_key, self.summary_key = dialog.assembly.text().strip(), dialog.summary.text().strip()
            for name, value in [("summary_model", dialog.model.text().strip() or "gpt-4o-mini"),
                                ("language", dialog.language.text().strip()), ("speaker_count", dialog.speakers.currentText()),
                                ("processing_mode", dialog.default_mode.currentText())]:
                self.settings.setValue(name, value)

    def cloud_consent(self, summary=False):
        text = ("Send transcript passages to OpenAI for AI summarization?" if summary else
                "Upload the selected original recordings to AssemblyAI for speaker-labeled transcription?")
        text += "\nProvider charges and retention policies apply. Source media is never modified."
        return QMessageBox.question(self, "Cloud processing", text,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def process_current(self):
        if self.project and not self.busy():
            self.begin_processing([self.project.id])

    def process_batch(self):
        if not self.busy():
            self.begin_processing([p.id for p in self.projects.values() if not p.segments])

    def begin_processing(self, identities):
        if not identities:
            return
        if not self.assembly_key:
            self.configure()
            if not self.assembly_key:
                return
        if not self.cloud_consent():
            return
        automatic_summary = self.settings.value("processing_mode", "Full Transcript Only") != "Full Transcript Only"
        if automatic_summary and self.summary_key and not self.cloud_consent(summary=True):
            automatic_summary = False
        # Existing transcripts require a deliberate replacement decision.
        for identity in identities:
            if self.projects[identity].segments:
                if QMessageBox.question(self, "Replace transcript", "Retranscription will replace edits and speaker corrections. Continue?",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
        for identity in identities:
            job = self.projects[identity].job
            if job and not job.remote_id and job.submitted_options:
                remote_id, accepted = QInputDialog.getText(self, "Recover interrupted submission",
                    "Submission may have reached AssemblyAI. Paste the transcript ID from its dashboard to resume.\n"
                    "Leave blank to submit a new potentially chargeable job; Cancel to stop.")
                if not accepted:
                    return
                if remote_id.strip():
                    job.remote_id = remote_id.strip()
        self.queue = [(identity, automatic_summary) for identity in identities]
        self.start_next()

    def start_next(self):
        if not self.queue or self.busy():
            return
        identity, automatic_summary = self.queue.pop(0)
        self.project_choice.setCurrentIndex(self.project_choice.findData(identity))
        project = copy.deepcopy(self.projects[identity])
        if project.segments:
            project.job = Job(uid("job"))
        options = {}
        language = self.settings.value("language", "")
        if language:
            options["language_code"] = language
        else:
            options["language_detection"] = True
        count = self.settings.value("speaker_count", "Automatic")
        if count != "Automatic":
            options["speakers_expected"] = int(count)
        key = self.assembly_key
        summary_key, summary_model = self.summary_key, self.settings.value("summary_model", "gpt-4o-mini")
        path = self.autosave_path(project)
        self.job_project_id = project.id
        def operation(progress, cancel):
            try:
                result = AssemblyAI(key, cancel).transcribe(project, options, lambda: project.save(path), progress)
                apply_transcript(project, result)
                project.save(path)
                if automatic_summary:
                    try:
                        project.summaries.extend(summarize(project, MODES[0], [], "", summary_key, summary_model, cancel, progress))
                    except Cancelled:
                        project.save(path)
                        raise
                    except Exception:
                        progress("Transcript saved; automatic summary failed. Retry from the Summaries tab.")
                project.save(path)
                return project
            except Exception as error:
                if project.job:
                    project.job.status = "cancelled" if isinstance(error, Cancelled) else "interrupted"
                    project.job.error = "Cancelled locally; cloud work may continue" if isinstance(error, Cancelled) else "Processing interrupted; retry to resume"
                project.save(path)
                raise
        self.run_task(operation, self.processing_completed)

    def processing_completed(self, project):
        self.projects[project.id] = project
        if self.project and self.project.id == project.id:
            self.project = project
            self.editing = None
            self.refresh_all()
        self.statusBar().showMessage("Complete speaker-labeled transcript saved. Review uncertain assignments before export.")

    def run_task(self, operation, completed):
        if self.busy():
            return
        self.checkpoint()
        self.task = Task(operation, self)
        self.set_busy(True)
        self.task.progress.connect(self.progress_label.setText)
        self.task.succeeded.connect(completed)
        self.task.failed.connect(self.task_failed)
        self.task.cancelled.connect(self.task_cancelled)
        self.task.finished.connect(self.task_finished)
        self.task.start()

    def set_busy(self, busy):
        self.progress.setRange(0, 0 if busy else 1)
        self.cancel_button.setEnabled(busy)
        self.transcribe_action.setEnabled(not busy)
        self.batch_action.setEnabled(not busy)
        self.summary_button.setEnabled(not busy)
        self.editor.setEnabled(not busy)
        self.edit_speaker.setEnabled(not busy)
        self.reviewed.setEnabled(not busy)

    def task_failed(self, message):
        self.queue.clear()
        self.progress_label.setText("Interrupted — autosave retained")
        self.fail(message + "\nUse Recover autosave, then Transcribe / resume for a submitted job.")

    def task_cancelled(self):
        self.queue.clear()
        self.progress_label.setText("Cancelled locally; cloud work may continue and incur charges")

    def task_finished(self):
        old = self.task
        self.task = None
        self.set_busy(False)
        if self.job_project_id:
            path = self.autosave_path(self.projects[self.job_project_id])
            if path.exists():
                try:
                    recovered = Project.load(path)
                    self.projects[recovered.id] = recovered
                    if self.project.id == recovered.id:
                        self.project = recovered
                        self.editing = None
                        self.refresh_all()
                except (OSError, ValueError, TypeError, KeyError):
                    self.fail("Could not reload the job checkpoint; use Recover autosave.")
            self.job_project_id = None
        if self.progress_label.text().startswith(("AssemblyAI:", "Summarizing", "Inspecting")):
            self.progress_label.setText("Completed and saved")
        old.deleteLater()
        if self.queue:
            QTimer.singleShot(0, self.start_next)

    def cancel_task(self):
        self.queue.clear()
        if self.task:
            self.task.cancel.set()
            self.progress_label.setText("Cancelling safely; waiting for any in-flight request…")

    def generate_summary(self):
        if not self.project or not self.project.segments or self.busy():
            return
        mode, topic = self.summary_mode.currentText(), self.topic.text().strip()
        if mode == "Topic / question" and not topic:
            self.fail("Enter a topic or question.")
            return
        selected = []
        if mode == "Individual speakers":
            dialog = QDialog(self)
            dialog.setWindowTitle("Choose participants")
            layout = QVBoxLayout(dialog)
            choices = []
            for speaker in self.project.speakers.values():
                box = QCheckBox(speaker.name + " · " + speaker.id)
                box.setChecked(speaker.id == self.selected_speaker())
                layout.addWidget(box)
                choices.append((box, speaker.id))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if not dialog.exec():
                return
            selected = [sid for box, sid in choices if box.isChecked()]
            if not selected:
                return
        if self.summary_key and not self.cloud_consent(summary=True):
            return
        snapshot = copy.deepcopy(self.project)
        key, model = self.summary_key, self.settings.value("summary_model", "gpt-4o-mini")
        def operation(progress, cancel):
            return snapshot.id, summarize(snapshot, mode, selected, topic, key, model, cancel, progress)
        def completed(result):
            identity, summaries = result
            self.projects[identity].summaries.extend(summaries)
            self.projects[identity].save(self.autosave_path(self.projects[identity]))
            if self.project.id == identity:
                self.render_summaries()
        self.run_task(operation, completed)

    def render_summaries(self):
        from html import escape
        if not self.project:
            self.summary_view.clear()
            return
        sections = []
        for summary in self.project.summaries:
            sections.append(f"<h2>{escape(summary.title)}</h2><p>{escape(summary.provider)}</p>")
            if summary.stale:
                sections.append("<p><b>STALE: transcript or speaker labels changed. Regenerate and review before relying on these claims.</b></p>")
            if not summary.claims:
                sections.append("<p>No supported passages found for this request.</p>")
            for claim in summary.claims:
                sections.append(f"<h3>[{escape(claim.status.upper())}] {escape(claim.text)}</h3><p>{escape(claim.note)}</p>")
                for source in evidence(self.project, claim):
                    sections.append(f"<p><a href='segment:{escape(source['id'], quote=True)}'>▶ {escape(source['speaker'])} · "
                                    f"{stamp(source['start'])}–{stamp(source['end'])}</a><br>"
                                    f"{escape(source['text'])}<br><small>{escape(source['id'])}</small></p>")
                if claim.status == "unsupported":
                    sections.append("<p><b>Invalid/missing source references — inspect manually.</b></p>")
        self.summary_view.setHtml("".join(sections) or "<h2>Your claims, connected to their sources</h2><p>Generate an optional summary after transcription.</p>")

    def citation_clicked(self, url):
        if url.scheme() != "segment" or not self.project:
            return
        identity = url.path()
        if not any(s.id == identity for s in self.project.segments):
            self.fail("This citation no longer resolves. Regenerate the stale summary.")
            return
        self.query.clear()
        self.review_filter.setChecked(False)
        self.speaker_list.setCurrentRow(0)
        self.tabs.setCurrentIndex(0)
        self.refresh_transcript()
        for row in range(self.transcript.count()):
            item = self.transcript.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == identity:
                self.transcript.setCurrentItem(item)
                self.transcript.scrollToItem(item)
                self.select_segment(item)
                self.player.play()
                break

    def copy_transcript(self):
        if not self.project:
            return
        timestamps = QMessageBox.question(self, "Copy transcript", "Include timestamps?",
                                           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        scope = SCOPES[1] if self.selected_speaker() else SCOPES[0]
        QApplication.clipboard().setText(report(self.project, scope, self.selected_speaker(), timestamps))

    def export_dialog(self):
        if not self.project or self.busy():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Export readable results")
        layout = QFormLayout(dialog)
        format_choice, scope_choice = QComboBox(), QComboBox()
        format_choice.addItems(FORMATS)
        scope_choice.addItems(SCOPES)
        timestamps = QCheckBox("Include timestamps")
        timestamps.setChecked(True)
        readable = QCheckBox("Use optional readability copies")
        layout.addRow("Format", format_choice)
        layout.addRow("Content", scope_choice)
        layout.addRow(timestamps)
        layout.addRow(readable)
        layout.addRow(QLabel("Speaker-specific exports use the selected sidebar speaker.\nJSON complete transcript saves the full editable project."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if not dialog.exec():
            return
        fmt = format_choice.currentText()
        filename = "".join(c if c.isalnum() or c in " -_" else "_" for c in self.project.title).strip() or "podcast"
        path, _ = QFileDialog.getSaveFileName(self, "Export", filename + EXTENSIONS[fmt], f"{fmt} (*{EXTENSIONS[fmt]})")
        if path:
            try:
                export(self.project, path, fmt, scope_choice.currentText(), self.selected_speaker(), timestamps.isChecked(), readable.isChecked())
                self.statusBar().showMessage("Export saved: " + Path(path).name)
            except Exception as error:
                self.fail("Export failed: " + str(error))

    def closeEvent(self, event):
        if self.busy():
            self.cancel_task()
            self.statusBar().showMessage("Cancellation requested. Close again after the worker stops; your submitted job is saved.")
            event.ignore()
        else:
            self.checkpoint()
            self.player.stop()
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Podcast Speaker Studio")
    app.setOrganizationName("PodcastSpeakerStudio")
    if "--smoke-test" in sys.argv:
        from .smoke import run_smoke
        return run_smoke(app)
    window = Studio()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        try:
            window.add_project(Project.load(sys.argv[1]))
        except Exception:
            window.fail("Cannot open the specified project.")
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
