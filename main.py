"""Application coordinator. Run with `python main.py`."""
from __future__ import annotations

# Packaged PDF workers must dispatch before importing Qt or creating windows.
if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import (QObject, QThread, Signal, Qt, QMetaObject, QTimer, QLockFile, QSignalBlocker, QUrl)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon
from config import APP_NAME, APP_VERSION, clamp_position, data_directory
from settings import AppSettings, set_windows_startup
from overlay import StatsOverlay
from pet import PixelPet
from settings_window import SettingsWindow
from system_stats import SystemStats
from tray import SystemTray, buddy_icon
from focus import FocusSession
from process_window import ProcessWindow
from notes import NoteStore, NoteService, current_guidance, business_summary, business_details, fallback_reminder
from notepad_window import NotepadWindow, ReminderPopup, StickyNote
from launcher import QuickLauncher, LauncherWindow, start_app
from credentials import CredentialStore
from ai_chat import ChatWindow
from work_chat import WorkChatWindow
from shared_business import SharedBusinessController
from home_window import HomeWindow
from ui_style import apply_window_style
from folder_play import FolderHabitat
from letter_play import LetterPlayground
from real_desktop import RealDesktopPlay
from ai_brain import AutonomousBrain
from smart_notes import SmartNoteAssistant
from business_voice import BusinessVoice
from characters import ANIMATION_STATES, BUSINESS_ANIMATIONS
from auto_parts import dashboard_summary
from memory import MemoryStore
from memory_window import MemoryWindow
from memory_learning import MemoryLearner
from semantic_memory import SemanticMemory, SemanticService


class BuddyApp(QObject):
    interval_changed = Signal(int)
    process_monitor_changed = Signal(bool)

    def __init__(self, app, settings=None, show_tray=True):
        super().__init__()
        self.app = app
        self.settings = settings or AppSettings()
        self.shutting_down = False
        self.overlay = StatsOverlay(self.settings)
        self.pet = PixelPet(self.settings)
        self.dialog = SettingsWindow(self.settings)
        self.process_window = ProcessWindow(self.settings)
        self.focus = FocusSession(self)
        self.stickies = {}
        self.notes_store = NoteStore(self.settings.path.parent / "notes.json")
        self.memory_store = MemoryStore(self.settings.path.parent / "memory.json")
        self.note_service = NoteService(self.notes_store, self.settings, lambda: not self.note_popup.isVisible(), self)
        self.note_popup = ReminderPopup(self.settings)
        self.notepad = NotepadWindow(self.note_service, self.settings)
        self.launcher = QuickLauncher(self.settings.path.parent / "shortcuts.json", self)
        self.launcher_window = LauncherWindow(self.launcher, self.settings)
        self.folders = FolderHabitat(self.pet, self.settings, self.settings.path.parent / "folders.json", self)
        self.letters = LetterPlayground(self.pet, self.settings)
        self.desktop = RealDesktopPlay(self.pet, self.settings, self)
        self.desktop.leave_other = lambda: self.folders.emerge(silent=True)
        self.credentials = CredentialStore()
        self.memory_window = MemoryWindow(self.memory_store, self.settings)
        self.memory_learner = MemoryLearner(self.memory_store, self.settings, self.credentials, self)
        self.semantic = SemanticService(SemanticMemory(self.settings.path.parent / "semantic.sqlite",
            model_path=self.settings["semantic_model_path"], documents=self.notes_store.documents), self)
        self.semantic.status_changed.connect(self.dialog.semantic_status.setText)
        self.semantic_timer = QTimer(self)
        self.semantic_timer.setInterval(10_000)
        self.semantic_timer.timeout.connect(self.refresh_semantic_memory)
        self.configure_semantic_memory({})
        self.latest_snapshot = None
        self.chat = ChatWindow(self.settings, self.credentials, self.ai_context, self.run_buddy_action,
            memory_store=self.memory_store, notebook_store=self.notes_store, semantic_service=self.semantic)
        self.work_chat = WorkChatWindow(self.settings)
        self.home = HomeWindow(self.note_service, self.settings, self.focus)
        self.home.navigate_requested.connect(self.home_navigate)
        self.home.create_requested.connect(self.new_business_entry)
        self.home.record_requested.connect(self.open_note)
        self.home.preference_requested.connect(lambda key, value: self.apply_settings({key: value}))
        for window in (self.notepad, self.chat, self.work_chat, self.dialog,
                       self.memory_window, self.process_window, self.launcher_window):
            if hasattr(window, 'home_requested'):
                window.home_requested.connect(self.show_home)
        self.business_sync = SharedBusinessController(self.note_service, self)
        self.notepad.shared_business = self.business_sync
        self.business_sync.status_changed.connect(self.notepad.set_shared_status)
        self.business_sync.status_changed.connect(self.home.set_connection)
        self.work_chat.session_changed.connect(self.business_sync.set_session)
        self.business_sync.session_expired.connect(self.work_chat._clear_session)
        self.notepad.connection_work_chat_requested.connect(self.show_work_chat)
        self.notepad.set_shared_status('Sign in to Work Chat to use shared orders and checklists.', False)
        self.chat.user_message.connect(self.memory_learner.observe)
        self.chat.notepad_requested.connect(self.show_notepad)
        self.chat.memory_requested.connect(self.show_memory)
        self.chat.document_ready.connect(self.review_web_source)
        self.memory_learner.changed.connect(self.memory_window.refresh)
        self.memory_learner.status_changed.connect(self.chat.memory_hint.setText)
        self.memory_window.preferences_changed.connect(self.apply_ai_preferences)
        self.memory_window.changed.connect(self.memory_learner.reset)
        self.notepad.chat_requested.connect(self.show_chat)
        self.notepad.business_event.connect(self.business_interaction)
        self.chat.connection_changed.connect(self.memory_learner.reset)
        self.chat.preferences_changed.connect(self.apply_ai_preferences)
        self.chat.reply_ready.connect(self.pet.say)
        self.chat.client.busy_changed.connect(lambda busy: self.pet.perform("THINK" if busy else "TALK", 3))
        self.folders.error.connect(self.note_error)
        self.pet.folder_dropped.connect(self.desktop.jump_path)
        self.brain = AutonomousBrain(self.settings, self.credentials, self.ai_context, lambda: self.desktop.bridge.targets,
                                    self.run_buddy_action, self.pet.say, self.brain_available, self)
        self.brain.status_changed.connect(self.chat.behavior_status.setText)
        self.chat.connection_changed.connect(self.brain.reset)
        self.chat.behavior_requested.connect(self.brain.request)
        self.smart_notes = SmartNoteAssistant(self.note_service, self.settings, self.credentials, self)
        self.smart_notes.memory_context = lambda query: self.memory_store.context(query)["memories"] if self.settings["ai_share_memory"] else []
        self.smart_notes.status_changed.connect(self.notepad.ai_status.setText)
        self.smart_notes.guidance_ready.connect(self.smart_note_ready)
        self.chat.connection_changed.connect(self.smart_notes.reset)
        self.notepad.ai_preferences_requested.connect(self.apply_ai_preferences)
        self.notepad.ai_requested.connect(self.smart_notes.request_note)
        self.notepad.connection_requested.connect(self.show_ai_connection)
        self.voice = BusinessVoice(self.settings, self.credentials, self.ai_context, lambda: self.notes_store.notes,
                                   self.pet.say, self.brain_available, self)
        self.pet.ai_speech_connected = True
        self.pet.conversation_requested.connect(self.voice.request)
        self.chat.connection_changed.connect(self.voice.reset)
        QTimer.singleShot(1500, lambda: self.voice.request('startup'))
        self.desktop.activity_blocked = lambda: bool(self.chat.client.reply is not None or self.brain.inflight)
        self.pet.hop_completed.connect(self.letter_hop_finished)
        self.tray = SystemTray(self)
        self.menu = self.tray.menu
        self.build_menu(self.menu)
        self.focus.changed.connect(self.focus_changed)
        self.focus.completed.connect(self.focus_complete)
        self.update_identity()
        self.launcher.changed.connect(self.refresh_quick_menu)
        self.launcher.notepad_requested.connect(self.show_notepad)
        self.launcher.error.connect(lambda message: QMessageBox.warning(self.launcher_window, "Quick Launch", message))
        self.notepad.open_text_requested.connect(self.open_linked_notepad)
        self.notepad.reading.connect(lambda: self.pet.perform("READ", 3))
        self.note_service.changed.connect(self.sync_stickies)
        self.note_service.changed.connect(self.sync_task_memory)
        self.note_service.notification.connect(self.deliver_note)
        self.note_service.error.connect(self.note_error)
        self.note_popup.open_requested.connect(self.open_note)
        self.note_popup.done_requested.connect(lambda identifier: self.note_action(identifier, "done"))
        self.note_popup.snooze_requested.connect(lambda identifier: self.note_action(identifier, "snooze"))
        self.note_popup.snooze_for_requested.connect(self.snooze_note)
        self.tray.open_requested.connect(self.show_home)
        self.overlay.position_changed.connect(lambda pos: self.persist({"overlay_position": pos}))
        self.pet.position_changed.connect(lambda pos: self.persist({"pet_position": pos}))
        self.pet.overlay_requested.connect(self.show_home)
        self.overlay.settings_requested.connect(lambda: self.show_settings(1))
        self.pet.menu_requested.connect(self.popup_menu)
        self.overlay.menu_requested.connect(self.popup_menu)
        self.dialog.apply_requested.connect(self.apply_settings)
        self.thread = QThread(self)
        self.worker = SystemStats(self.effective_interval())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.thread.finished.connect(self.worker.deleteLater)
        self.interval_changed.connect(self.worker.set_interval)
        self.process_monitor_changed.connect(self.worker.set_process_monitor)
        self.process_window.monitoring_changed.connect(self.process_monitor_changed.emit)
        self.worker.processes_updated.connect(self.process_window.accept_rows)
        self.worker.updated.connect(self.overlay.accept_snapshot)
        self.worker.updated.connect(self.pet.accept_snapshot)
        self.worker.updated.connect(self.remember_snapshot)
        self.thread.start()
        self.app.aboutToQuit.connect(self.shutdown)
        self.app.screenAdded.connect(self.screens_changed)
        self.app.screenRemoved.connect(self.screens_changed)
        self.connected_screens = set()
        self.screens_changed()
        tray_available = QSystemTrayIcon.isSystemTrayAvailable() and show_tray
        if tray_available:
            self.tray.show()
        # A missing system tray must never strand an invisible app.
        if (self.settings["overlay_visible"] and not self.settings["launch_minimized"]) or not tray_available:
            self.overlay.show()
        if self.settings["pet_enabled"]:
            self.pet.show()
        if not tray_available and self.settings["click_through"] and not self.settings["pet_enabled"]:
            self.settings.values["click_through"] = False
            self.overlay.configure()
        if self.settings.warning:
            logging.warning(self.settings.warning)
        if self.notes_store.warning:
            self.note_error(self.notes_store.warning)
        self.sync_stickies()
        self.sync_task_memory()
        self.note_service.start()
        self.app.styleHints().colorSchemeChanged.connect(self.interface_scheme_changed)
        if show_tray and self.settings['home_on_start'] and not self.settings['launch_minimized']:
            QTimer.singleShot(0, self.show_home)

    def build_menu(self, menu):
        apply_window_style(menu, self.settings)
        menu.addAction('Open Jeffery Home', self.show_home)
        menu.addSeparator()
        chat = menu.addMenu("Chat")
        chat.addAction("Work Chat · Coworkers", self.show_work_chat)
        chat.addAction("Talk to Jeffery · Groq", self.show_chat)
        notes = menu.addMenu("Famous Twins")
        notes.addAction("Business Overview", lambda: self.show_business_workspace("overview"))
        sections = notes.addMenu("Workspace")
        for label, section in (("Writing", "writing"), ("Checklists", "checklists"),
                               ("Orders and Quotes", "orders"), ("Schedule", "schedule")):
            sections.addAction(label, lambda checked=False, value=section: self.show_business_workspace(value))
        create = notes.addMenu("Create")
        create.addAction("New Writing", lambda: self.new_business_entry("note"))
        create.addAction("New Checklist", lambda: self.new_business_entry("list"))
        create.addAction("New Customer Order", lambda: self.new_business_entry("order"))
        notes.addAction("Today's Business Brief", self.business_briefing)
        files = notes.addMenu("Files and Sources")
        files.addAction("Read PDF or Webpage", lambda: self.show_business_workspace("import"))
        files.addAction("Open Linked Text File", self.open_linked_notepad)
        notes.addSeparator()
        self.reminder_action = notes.addAction("Note Reminders")
        self.reminder_action.setCheckable(True)
        self.reminder_action.setChecked(self.settings["note_reminders"])
        self.reminder_action.triggered.connect(lambda checked: self.apply_settings({"note_reminders": checked}))
        focus_menu = notes.addMenu("Focus Timer")
        self.focus_start_action = focus_menu.addAction(f"Start {self.settings['focus_minutes']}-minute session", lambda: self.focus.start(self.settings["focus_minutes"]))
        self.focus_pause_action = focus_menu.addAction("Pause", self.focus.toggle_pause)
        self.focus_pause_action.setEnabled(False)
        self.focus_reset_action = focus_menu.addAction("Reset", self.focus.reset)
        self.focus_reset_action.setEnabled(False)
        self.quick_menu = menu.addMenu("Quick Launch")
        self.refresh_quick_menu()
        menu.addSeparator()
        play = menu.addMenu("Desktop Play")
        play.addAction("Real Folders, Tabs and Windows", self.desktop.show_panel)
        play.addAction("Jump into a Real Folder", lambda: self.run_buddy_action({"action": "hide"}))
        play.addAction("Ride a Real Tab", lambda: self.run_buddy_action({"action": "ride_tab"}))
        play.addAction("Selected Letters · Ctrl+Alt+J", self.desktop.capture_countdown)
        play.addAction("Peek Out", lambda: self.run_buddy_action({"action": "peek"}))
        play.addAction("Come Out / Stop Play", self.stop_play)
        toys = play.addMenu("Optional Toys")
        toys.addAction("Separate Letter Tiles", lambda: self.play_letters())
        toys.addAction("Add a Folder Portal", self.add_portal_toy)
        mouse = play.addMenu("Mouse Play")
        self.mouse_actions = {}
        for label, mode in (("Watch and greet", "watch"), ("Chase cursor", "chase"), ("Shy: run away", "shy"), ("Off", "off")):
            action = mouse.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.settings["mouse_mode"] == mode)
            action.triggered.connect(lambda checked=False, m=mode: self.set_mouse_mode(m))
            self.mouse_actions[mode] = action
        interact_menu = menu.addMenu("Play with Jeffery")
        self.interact_actions = {}
        for label, state in (("Pet", "PET"), ("Feed", "EAT"), ("Wave", "WAVE"), ("Dance", "DANCE"), ("Jump", "JUMP"), ("Take a nap", "SLEEP")):
            self.interact_actions[state] = interact_menu.addAction(label, lambda checked=False, s=state: self.pet.interact(s))
        tricks = interact_menu.addMenu("More Moves")
        business_moves = interact_menu.addMenu("At the Parts Counter")
        business_states = {state for _, state in BUSINESS_ANIMATIONS}
        for label, state in BUSINESS_ANIMATIONS:
            business_moves.addAction(label, lambda checked=False, value=state: self.pet.interact(value))
        expressions = tricks.addMenu("Expressions")
        props = tricks.addMenu("Props and Tricks")
        movement = tricks.addMenu("Movement")
        for state in ANIMATION_STATES[25:]:
            if state in business_states:
                continue
            group = expressions if state in {"SNEEZE", "SCARED", "LAUGH", "GROOM", "SALUTE", "FACEPALM"} else props if state in {"MAGIC", "UMBRELLA", "JUGGLE"} else movement
            group.addAction(state.replace('_', ' ').title(), lambda checked=False, s=state: self.run_buddy_action({'action': 'animate', 'animation': s}))
        buddy = menu.addMenu("Buddy Controls")
        buddy.addAction("Show / Hide Buddy", self.toggle_pet)
        self.pause_action = buddy.addAction("Pause Buddy", self.toggle_pause)
        self.free_action = buddy.addAction("Free Buddy: Walk Anywhere")
        self.free_action.setCheckable(True)
        self.free_action.setChecked(self.settings["roaming_mode"] == "free")
        self.free_action.triggered.connect(lambda checked: self.apply_settings({"roaming_mode": "free" if checked else "bottom"}))
        characters = buddy.addMenu("Choose Character")
        self.character_actions = {}
        for label, character in (("Robot", "robot"), ("Cat", "cat"), ("Knight", "knight")):
            action = characters.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.settings["character"] == character)
            action.triggered.connect(lambda checked=False, c=character: self.apply_settings({"character": c}))
            self.character_actions[character] = action
        buddy.addSeparator()
        self.quiet_action = buddy.addAction("Quiet Mode")
        self.quiet_action.setCheckable(True)
        self.quiet_action.setChecked(self.settings["quiet_mode"])
        self.quiet_action.triggered.connect(lambda checked: self.apply_settings({"quiet_mode": checked}))
        self.power_action = buddy.addAction("Low Power Mode")
        self.power_action.setCheckable(True)
        self.power_action.setChecked(self.settings["low_power"])
        self.power_action.triggered.connect(lambda checked: self.apply_settings({"low_power": checked}))
        monitor = menu.addMenu("System Monitor")
        monitor.addAction("System Monitor", self.show_overlay)
        monitor.addAction("Top Apps: CPU / Memory", self.show_processes)
        monitor.addSeparator()
        monitor.addAction("Show / Hide Overlay", self.toggle_overlay)
        self.click_action = monitor.addAction("Click-through Overlay")
        self.click_action.setCheckable(True)
        self.click_action.setChecked(self.settings["click_through"])
        self.click_action.triggered.connect(lambda value: self.apply_settings({"click_through": value}))
        menu.addSeparator()
        settings = menu.addMenu("Settings")
        settings.addAction("General Settings", self.show_settings)
        settings.addAction("Buddy Settings", lambda: self.show_settings(2))
        settings.addAction("Overlay Settings", lambda: self.show_settings(1))
        settings.addSeparator()
        self.startup_action = settings.addAction("Start with Windows")
        self.startup_action.setCheckable(True)
        self.startup_action.setChecked(self.settings["start_with_windows"])
        self.startup_action.setEnabled(sys.platform == "win32")
        self.startup_action.triggered.connect(lambda value: self.apply_settings({"start_with_windows": value}))
        settings.addAction("Reset Positions", self.reset_positions)
        menu.addSeparator()
        menu.addAction("Exit", self.app.quit)

    def persist(self, changes):
        try:
            self.settings.save(changes)
            return True
        except OSError as exc:
            logging.exception("Could not save settings")
            QMessageBox.warning(self.dialog, APP_NAME, f"Could not save settings: {exc}")
            return False

    def apply_settings(self, changes):
        if changes.get("click_through", self.settings["click_through"]) and not self.tray.isVisible() and not changes.get("pet_enabled", self.settings["pet_enabled"]):
            changes["click_through"] = False
        startup_changed = "start_with_windows" in changes and changes["start_with_windows"] != self.settings["start_with_windows"]
        if startup_changed:
            try:
                set_windows_startup(changes["start_with_windows"])
            except OSError as exc:
                QMessageBox.warning(self.dialog, "Startup setting", str(exc))
                self.startup_action.setChecked(self.settings["start_with_windows"])
                self.dialog.status.setText("Startup could not be changed.")
                return
        if not self.persist(changes):
            if startup_changed:
                try:
                    set_windows_startup(self.settings["start_with_windows"])
                except OSError:
                    logging.exception("Could not restore startup setting")
            return
        self.overlay.configure()
        self.pet.configure()
        if not self.settings["folder_play"] and (self.pet.hidden_in in self.folders.cards or self.pet.hop_target in self.folders.cards):
            self.folders.emerge(silent=True)
            self.pet.cancel_hop()
        self.pet.setVisible(self.settings["pet_enabled"] and not self.pet.hidden_in)
        self.dialog.configure()
        self.process_window.configure()
        self.notepad.configure()
        self.note_popup.configure()
        self.launcher_window.configure()
        self.chat.configure()
        self.work_chat.configure()
        self.home.configure()
        apply_window_style(self.menu, self.settings)
        self.configure_semantic_memory(changes)
        self.memory_window.configure()
        if not self.settings["memory_enabled"] or not self.settings["memory_ai"] or not self.settings["ai_share_memory"]:
            self.memory_learner.reset()
        self.letters.configure()
        self.folders.refresh()
        self.desktop.configure()
        self.brain.configure()
        self.smart_notes.configure()
        self.voice.configure()
        for sticky in self.stickies.values():
            sticky.configure()
        if self.settings["quiet_mode"] or not self.settings["note_reminders"]:
            self.note_popup.hide()
        self.update_identity()
        self.interval_changed.emit(self.effective_interval())
        self.click_action.setChecked(self.settings["click_through"])
        self.startup_action.setChecked(self.settings["start_with_windows"])
        self.free_action.setChecked(self.settings["roaming_mode"] == "free")
        self.quiet_action.setChecked(self.settings["quiet_mode"])
        self.power_action.setChecked(self.settings["low_power"])
        self.reminder_action.setChecked(self.settings["note_reminders"])
        for character, action in self.character_actions.items():
            action.setChecked(self.settings["character"] == character)
        for mode, action in self.mouse_actions.items():
            action.setChecked(self.settings["mouse_mode"] == mode)
        self.focus_start_action.setText(f"Start {self.settings['focus_minutes']}-minute session")
        if not self.tray.isVisible() and not self.pet.isVisible():
            self.overlay.show()
            self.settings.values["overlay_visible"] = True
        self.screens_changed()
        self.dialog.status.setText("Saved. Changes are live.")

    def effective_interval(self):
        return max(2000, self.settings["interval_ms"]) if self.settings["low_power"] else self.settings["interval_ms"]

    def configure_semantic_memory(self, changes):
        enabled = self.settings["semantic_memory_enabled"]
        sharing = self.settings["ai_share_notes"]
        if any(key in changes for key in ("semantic_memory_enabled", "semantic_model_path", "ai_share_notes")):
            self.semantic.configure(model_path=self.settings["semantic_model_path"])
        if not enabled or not sharing:
            self.semantic.cancel()
            self.dialog.semantic_status.setText("Meaning-based recall is off." if not enabled else
                "Turn on notebook sharing to use meaning-based recall in chat.")
        background = enabled and sharing and not self.settings["low_power"] and not self.settings["quiet_mode"]
        if background and not self.semantic_timer.isActive():
            self.semantic_timer.start()
        elif not background:
            self.semantic_timer.stop()

    def refresh_semantic_memory(self):
        if (not self.shutting_down and self.settings["semantic_memory_enabled"]
                and self.settings["ai_share_notes"] and not self.settings["low_power"]
                and not self.settings["quiet_mode"] and not self.semantic.busy):
            self.semantic.refresh([dict(note) for note in self.notes_store.notes])

    def remember_snapshot(self, snapshot):
        self.latest_snapshot = snapshot

    def apply_ai_preferences(self, changes):
        if self.persist(changes) and hasattr(self, 'brain'):
            self.configure_semantic_memory(changes)
            self.brain.configure()
            self.smart_notes.configure()
            self.voice.configure()
            self.notepad.configure()
            self.memory_window.configure()
            if any(key in changes for key in ("memory_enabled", "memory_ai", "ai_share_memory")):
                self.memory_learner.reset()
            with QSignalBlocker(self.chat.notes):
                self.chat.notes.setChecked(self.settings['ai_share_notes'])
            current = self.notes_store.find(self.note_popup.identifier)
            if current:
                self.note_popup.update_guidance(current)

    def brain_available(self):
        return not (self.shutting_down or self.pet.paused or self.pet.drag_offset is not None or self.pet.drop_motion or self.pet.hop_motion
                    or self.chat.client.reply is not None or self.chat.input.text().strip() or self.pet.action_state)

    def emerge_all(self, silent=True):
        self.desktop.emerge(silent=silent)
        self.folders.emerge(silent=silent)

    def ai_context(self, metrics, notes):
        result = {"buddy_name": self.settings["pet_name"], "character": self.settings["character"],
                  'business_mode': self.settings['business_mode'], 'business_name': self.settings['business_name'],
                  "mouse_mode": self.settings["mouse_mode"], "hidden": bool(self.pet.hidden_in),
                  "animation_states": list(ANIMATION_STATES),
                  "focus_timer": self.focus.clock.state}
        result['desktop_targets'] = [{**{'id': t['id'], 'kind': t['kind']},
            **({'name': t['name']} if self.settings['ai_share_desktop_names'] else {})} for t in self.desktop.bridge.targets][:60]
        result['desktop_integration'] = self.desktop.bridge.supported and self.settings['desktop_enabled']
        if metrics and self.latest_snapshot is not None:
            result["readings"] = {"cpu_percent": self.latest_snapshot.cpu, "memory_percent": self.latest_snapshot.memory_percent}
        if notes:
            # Document notes contain short previews and metadata, while full
            # document text is retrieved separately for each chat question.
            saved_notes = self.notes_store.notes
            result['local_now'] = datetime.now().astimezone().isoformat(timespec='seconds')
            result['business_summary'] = business_summary(saved_notes)
            ordered = sorted((n for n in saved_notes if not n['done']), key=lambda n: n['next_due'] or float('inf'))
            result["incomplete_notes"] = [{"title": n["title"], "body": n["body"][:1000], "repeat_minutes": n["repeat_minutes"],
                **business_details(n),
                **({"document_source": n["document_source"][:300], "document_characters": n.get("body_characters", 0)}
                   if n.get("document_source") else {}),
                "note_saved_at": datetime.fromtimestamp(n['updated']).astimezone().isoformat(),
                "next_reminder": datetime.fromtimestamp(n['next_due']).astimezone().isoformat() if n['next_due'] else None,
                "next_step": current_guidance(n).get('next_step', '')} for n in ordered[:20]]
        if self.settings["ai_share_memory"]:
            result["user_memory"] = self.memory_store.context()["memories"]
        return result

    def show_memory(self):
        self.memory_window.refresh()
        self.memory_window.show()
        self.memory_window.raise_()
        self.memory_window.activateWindow()

    def review_web_source(self, result):
        self.show_notepad()
        self.notepad.review_document(result)

    def sync_task_memory(self):
        daily = {"task:" + note["id"]: note for note in self.notes_store.notes
                 if not note["done"] and note["repeat_minutes"] == 1440 and not note.get("document_source") and not note.get("source")}
        try:
            for entry in self.memory_store.entries():
                source = entry["source"]
                if source.startswith("task:") and (source not in daily or entry["text"] != daily[source]["title"]):
                    self.memory_store.forget(entry["id"])
            known = {entry["source"] for entry in self.memory_store.entries()}
            for source, note in daily.items():
                if source not in known and self.settings["memory_enabled"]:
                    # Task cleanup must never delete an equivalent user-owned routine.
                    self.memory_store.remember("routine", note["title"], source=source, preserve_existing=True)
            self.memory_window.refresh()
        except (OSError, ValueError) as exc:
            self.chat.memory_hint.setText("Could not save task memory: " + str(exc))

    def show_chat(self):
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()

    def show_ai_connection(self):
        self.chat.tabs.setCurrentIndex(1)
        self.show_chat()

    def set_mouse_mode(self, mode):
        self.emerge_all()
        values = {"mouse_mode": mode, "follow_mouse": False}
        if mode in ("chase", "shy"):
            values["roaming_mode"] = "free"
        self.apply_settings(values)
        self.pet.set_paused(False)

    def play_letters(self, text="HELLO JEFFERY"):
        if not self.settings["pet_enabled"]:
            self.apply_settings({"pet_enabled": True})
        self.emerge_all()
        self.letters.open_play(text)
        return "Letter tiles are on your desktop. Drag them, scatter them, or let me push them."

    def letter_hop_finished(self, identifier):
        if not identifier and self.letters.isVisible():
            self.pet.perform("PUSH", 4)

    def stop_play(self):
        self.emerge_all()
        self.pet.cancel_hop()
        self.letters.hide()
        self.desktop.countdown.stop()
        self.desktop.text_window.hide()
        self.desktop.panel.hide()
        self.brain.client.cancel()
        self.brain.inflight = False
        self.set_mouse_mode("off")
        self.pet.perform("WAVE", 2)

    def add_portal_toy(self):
        self.apply_settings({'portal_toys': True, 'folder_play': True})
        self.folders.choose_folder()

    def run_buddy_action(self, action):
        kind = action.get("action")
        interactions = {"pet": "PET", "feed": "EAT", "wave": "WAVE", "dance": "DANCE", "jump": "JUMP", "nap": "SLEEP"}
        if kind in interactions:
            if not self.settings["pet_enabled"]:
                self.apply_settings({"pet_enabled": True})
            self.emerge_all()
            self.pet.interact(interactions[kind])
            return "Playing the " + kind + " animation."
        if kind == "hide":
            if not self.settings["desktop_enabled"] or not self.settings["pet_enabled"]:
                self.apply_settings({"desktop_enabled": True, "pet_enabled": True})
            name = (action.get("folder_name") or action.get('target_name', '')).casefold()
            identifier = action.get('target_id') or next((t['id'] for t in self.desktop.bridge.targets if t['kind'] == 'folder' and (not name or t['name'].casefold() == name)), None)
            if not identifier:
                self.desktop.show_panel()
                return 'Keep a real folder icon visible, then refresh Real desktop and choose it.'
            target = self.desktop.bridge.target(identifier)
            if not target or target['kind'] != 'folder':
                return 'Choose a visible real folder icon first.'
            self.folders.emerge(silent=True)
            return self.desktop.visit(identifier)
        if kind == "peek":
            return self.desktop.peek() if self.pet.hidden_in.startswith('desktop:') else self.folders.peek()
        if kind == "come_out":
            self.emerge_all(silent=False)
            return 'Back on the desktop.'
        if kind in ('ride_tab', 'window_edge', 'select_tab'):
            desired = 'window' if kind == 'window_edge' else 'tab'
            name = action.get('target_name', '').casefold()
            matches = [t for t in self.desktop.bridge.targets if t['kind'] == desired and (not name or name in t['name'].casefold())]
            if name and len(matches) != 1 and not action.get('target_id'):
                self.desktop.show_panel()
                return 'Choose the matching tab or window in Real desktop.'
            identifier = action.get('target_id') or (matches[0]['id'] if matches else None)
            if not identifier:
                self.desktop.show_panel()
                return 'Keep a real ' + desired + ' visible and refresh the desktop list.'
            if kind == 'select_tab':
                return self.desktop.select_tab(identifier)
            target = self.desktop.bridge.target(identifier)
            if not target or target['kind'] != desired:
                return 'Choose a visible ' + desired + ' first.'
            self.folders.emerge(silent=True)
            return self.desktop.visit(identifier)
        if kind == 'selected_text':
            self.desktop.capture_countdown()
            return 'Select letters in your editor now. I capture them in three seconds; drag them and Apply to edit.'
        if kind == 'animate':
            state = action.get('animation', 'WAVE').upper()
            if state not in ANIMATION_STATES or state in ('DRAG', 'PARACHUTE', 'HOP', 'LANDING'):
                raise ValueError('Choose a normal Jeffery animation.')
            self.emerge_all()
            self.pet.perform(state, 4)
            return 'Here comes a ' + state.lower() + '.'
        if kind in ("follow_mouse", "shy_mouse", "watch_mouse", "stop_mouse"):
            mode = {"follow_mouse": "chase", "shy_mouse": "shy", "watch_mouse": "watch", "stop_mouse": "off"}[kind]
            self.set_mouse_mode(mode)
            return "Mouse play is now " + mode + "."
        if kind == "letters":
            return self.play_letters(action.get("text") or "HELLO JEFFERY")
        if kind == "open_notepad":
            self.show_notepad()
            return "Your notebook is open."
        if kind == "draft_note":
            if self.notepad.dirty:
                self.show_notepad()
                return "There is an unsaved note open. Save or discard it before asking for a new draft."
            self.notepad.new_note()
            self.notepad.title.setText(action.get("title", ""))
            self.notepad.body.setPlainText(action.get("body", ""))
            self.notepad.repeat.setValue(action.get("repeat_minutes", 30))
            self.show_notepad()
            return "The note is drafted. Review it and click Save to start its reminders."
        raise ValueError("Unsupported companion action.")

    def update_identity(self):
        icon = buddy_icon(self.settings["pet_palette"], self.settings["character"])
        self.tray.setIcon(icon)
        self.app.setWindowIcon(icon)
        self.tray.setToolTip(f"PixelSystem Buddy · {self.settings['pet_name']}")

    def show_processes(self):
        self.process_window.show()
        self.process_window.raise_()
        self.process_window.activateWindow()

    def focus_changed(self, label):
        self.overlay.set_focus_label(label)
        state = self.focus.clock.state
        self.focus_pause_action.setEnabled(state in ("running", "paused"))
        self.focus_pause_action.setText("Resume" if state == "paused" else "Pause")
        self.focus_reset_action.setEnabled(state != "idle")

    def focus_complete(self):
        self.pet.perform("CELEBRATE", 3)
        self.pet.say("Focus session done. Take a break!")
        if self.tray.isVisible() and not self.settings["quiet_mode"]:
            self.tray.showMessage("Focus complete", "Your session is done. Time for a short break.", QSystemTrayIcon.Information, 5000)

    def show_notepad(self):
        self.notepad.refresh()
        self.notepad.show()
        self.notepad.raise_()
        self.notepad.activateWindow()
        self.pet.perform("READ", 3)

    def show_home(self):
        if self.shutting_down:
            return
        self.home.refresh()
        self.home.show()
        self.home.raise_()
        self.home.activateWindow()

    def home_navigate(self, destination):
        if destination in ('overview', 'writing', 'checklists', 'orders', 'schedule'):
            self.show_business_workspace(destination)
            return
        actions = {'home': self.show_home, 'coworkers': self.show_work_chat,
                   'assistant': self.show_chat, 'settings': self.show_settings,
                   'monitor': self.show_overlay, 'processes': self.show_processes,
                   'quick_launch': self.show_launcher, 'memory': self.show_memory}
        if destination in actions:
            actions[destination]()
        elif destination == 'setup':
            QDesktopServices.openUrl(QUrl('https://github.com/vancore876/pixel-pet/blob/main/WORK_CHAT.md'))

    def interface_scheme_changed(self, *_):
        if self.settings['interface_appearance'] != 'system' or self.shutting_down:
            return
        for window in (self.home, self.dialog, self.notepad, self.work_chat, self.chat,
                       self.memory_window, self.process_window, self.launcher_window,
                       self.note_popup, self.desktop.panel, self.desktop.text_window):
            window.configure()
        for sticky in self.stickies.values():
            sticky.configure()
        apply_window_style(self.menu, self.settings)

    def show_business_workspace(self, section="overview"):
        self.show_notepad()
        self.notepad.show_section(section)

    def new_business_entry(self, kind):
        self.show_notepad()
        if kind == "note":
            self.notepad.new_note()
        else:
            self.notepad.new_entry(kind)

    def business_interaction(self, event):
        if self.shutting_down or not self.settings["business_mode"]:
            return
        animation = {"order_saved": "SCAN_PART", "order_ready": "PACK_ORDER",
                     "checklist_saved": "CHECK_STOCK"}.get(event, "HIGH_FIVE")
        self.pet.perform(animation, 2.5)

    def business_briefing(self):
        self.show_business_workspace("overview")
        counts = dashboard_summary(self.notes_store.notes)
        self.pet.perform("CHECK_STOCK", 3)
        self.pet.say(f"{self.settings['business_name']}: {counts['open_orders']} open orders, "
                     f"{counts['ready_orders']} ready for pickup, and {counts['late_orders']} past deadline. "
                     f"{counts['parts_to_source']} part units need a sourcing check.")

    def open_note(self, identifier):
        if self.notepad.maybe_leave():
            self.show_notepad()
            self.notepad.load_note(identifier)

    def note_error(self, message):
        logging.warning(message)
        self.notepad.status.setText(message)

    def note_action(self, identifier, action):
        try:
            if self.business_sync.is_business(identifier) and action in ('done', 'snooze'):
                if action == 'done':
                    self.business_sync.complete(identifier, True, self.shared_note_result)
                else:
                    self.business_sync.modify(identifier, {'next_due': time.time() + 5 * 60}, self.shared_note_result)
                return
            if action == "done":
                self.note_service.complete(identifier)
                self.pet.perform("CELEBRATE", 3)
            elif action == "snooze":
                self.note_service.snooze(identifier)
            elif action == "unpin":
                self.note_service.modify(identifier, pinned=False)
        except (OSError, ValueError) as exc:
            self.note_error(str(exc))

    def shared_note_result(self, note, error):
        if error:
            self.note_error(error)
        elif note:
            self.pet.perform('CHECK_STOCK', 3)

    def deliver_note(self, note, kind):
        if self.shutting_down:
            return
        if self.pet.hidden_in:
            self.emerge_all()
        self.pet.perform("REMINDER", 3)
        guidance = current_guidance(note) if self.settings['ai_share_notes'] else {}
        text = guidance.get('reminder')
        recent = note.get('reminder_history', [])
        if not text or text in recent:
            text = fallback_reminder(note, kind)
        # Preserve familiar note titles in offline acknowledgments.
        if kind == 'added' and note.get('kind', 'note') == 'note' and note['title'] not in text:
            text = 'New note: ' + note['title'] + '. ' + text
        self.pet.say(text)
        stored = self.notes_store.find(note['id'])
        if stored:
            try:
                self.note_service.modify(note['id'], reminder_history=[*recent, text][-5:])
            except (OSError, ValueError) as exc:
                self.note_error(str(exc))
        self.note_popup.present(note, kind, self.pet.geometry().center())
        if stored:
            self.smart_notes.request_reminder(stored, kind)

    def smart_note_ready(self, note):
        if self.shutting_down or not self.settings['ai_share_notes']:
            return
        self.notepad.refresh_advice()
        if self.note_popup.isVisible() and self.note_popup.identifier == note['id'] and not self.settings['quiet_mode']:
            self.note_popup.update_guidance(note)
            self.pet.say(current_guidance(note)['reminder'])

    def snooze_note(self, identifier, minutes):
        if minutes not in (5, 15, 30, 60):
            return
        try:
            if self.business_sync.is_business(identifier):
                self.business_sync.modify(identifier, {'next_due': time.time() + minutes * 60}, self.shared_note_result)
                return
            self.note_service.snooze(identifier, minutes)
            self.pet.say(f"Okay, I’ll remind you in {minutes} minutes.")
        except (OSError, ValueError) as exc:
            self.note_error(str(exc))

    def sync_stickies(self, *args):
        wanted = {n["id"]: n for n in self.notes_store.notes if n["pinned"] and not n["done"]}
        for identifier in list(self.stickies):
            if identifier not in wanted:
                card = self.stickies.pop(identifier)
                card.hide()
                card.deleteLater()
        for identifier, note in wanted.items():
            if identifier not in self.stickies:
                card = StickyNote(note, self.settings)
                if not note["pin_position"]:
                    offset = 24 * (len(self.stickies) % 10)
                    point = clamp_position([card.x() + offset, card.y() + offset], card.width(), card.height(), self.app.screens())
                    card.move(point)
                card.open_requested.connect(self.open_note)
                card.done_requested.connect(lambda key: self.note_action(key, "done"))
                card.unpin_requested.connect(lambda key: self.note_action(key, "unpin"))
                card.position_changed.connect(self.pin_position)
                self.stickies[identifier] = card
                card.show()
            else:
                self.stickies[identifier].update_note(note)
        current = self.notes_store.find(self.note_popup.identifier)
        if self.note_popup.identifier and (current is None or current["done"]):
            self.note_popup.hide()

    def pin_position(self, identifier, position):
        try:
            self.note_service.modify(identifier, pin_position=position)
        except (OSError, ValueError) as exc:
            self.note_error(str(exc))

    def open_linked_notepad(self):
        try:
            linked = self.notes_store.state["linked_file"]
            if not linked:
                path = self.settings.path.parent / "JefferyNotes.txt"
                if not path.exists():
                    path.write_text("", encoding="utf-8")
                self.note_service.link(path)
                linked = str(path)
            if sys.platform == "win32":
                start_app("notepad.exe", [linked])
            else:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(linked)):
                    raise OSError("No text editor was available. Open the linked .txt file manually.")
        except (OSError, ValueError) as exc:
            self.note_error(str(exc))
            self.show_notepad()

    def refresh_quick_menu(self):
        self.quick_menu.clear()
        self.quick_menu.addAction("Manage Quick Buttons", self.show_launcher)
        self.quick_menu.addSeparator()
        for label, identifier in (("Browser", "browser"), ("VS Code", "vscode"), ("Jeffery's Notepad", "notepad"), ("Documents", "documents")):
            self.quick_menu.addAction(label, lambda checked=False, key=identifier: self.launcher.launch(key))
        for item in self.launcher.store.shortcuts:
            self.quick_menu.addAction(item["label"], lambda checked=False, key=item["id"]: self.launcher.launch(key))

    def show_launcher(self):
        self.launcher_window.show()
        self.launcher_window.raise_()
        self.launcher_window.activateWindow()

    def show_work_chat(self):
        self.work_chat.show()
        self.work_chat.raise_()
        self.work_chat.activateWindow()

    def popup_menu(self, position):
        self.pause_action.setText("Resume Buddy" if self.pet.paused else "Pause Buddy")
        self.menu.popup(position)

    def show_overlay(self):
        self.overlay.show()
        self.overlay.raise_()
        self.persist({"overlay_visible": True})

    def toggle_overlay(self):
        visible = not self.overlay.isVisible()
        if not visible and not self.tray.isVisible() and not self.pet.isVisible():
            visible = True
        self.overlay.setVisible(visible)
        self.persist({"overlay_visible": visible})

    def toggle_pet(self):
        self.apply_settings({"pet_enabled": not self.pet.isVisible()})

    def toggle_pause(self):
        self.pet.set_paused(not self.pet.paused)
        self.pause_action.setText("Resume Buddy" if self.pet.paused else "Pause Buddy")

    def show_settings(self, tab=0):
        self.dialog.refresh(int(tab))
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def screens_changed(self, *args):
        screens = self.app.screens()
        self.connected_screens.intersection_update(screens)
        for screen in screens:
            if screen not in self.connected_screens:
                screen.availableGeometryChanged.connect(self.screens_changed)
                self.connected_screens.add(screen)
        self.overlay.resize_for_rows()
        for window in (self.overlay, self.pet):
            window.move(clamp_position([window.x(), window.y()], window.width(), window.height(), self.app.screens()))
        self.pet.real_x = float(self.pet.x())
        self.pet.real_y = float(self.pet.y())
        if self.pet.drop_motion:
            self.pet.drop_motion.y = self.pet.real_y
        for card in self.stickies.values():
            card.move(clamp_position([card.x(), card.y()], card.width(), card.height(), screens))
        for card in self.folders.cards.values():
            card.move(clamp_position([card.x(), card.y()], card.width(), card.height(), screens))
        if self.letters.isVisible():
            self.letters.adjust_screen()

    def reset_positions(self):
        rect = self.app.primaryScreen().availableGeometry()
        self.overlay.move(rect.right() - self.overlay.width() - 24, rect.top() + 28)
        self.pet.move(rect.left() + 120, rect.bottom() - self.pet.height() + 1)
        self.screens_changed()
        self.persist({"overlay_position": [self.overlay.x(), self.overlay.y()], "pet_position": [self.pet.x(), self.pet.y()]})

    def shutdown(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        self.semantic_timer.stop()
        self.semantic.shutdown()
        self.home.shutdown()
        self.overlay.stop_animation()
        self.notepad.shutdown()
        self.chat.shutdown()
        self.business_sync.shutdown()
        self.work_chat.shutdown()
        self.memory_learner.stop()
        self.memory_window.hide()
        self.pet.timer.stop()
        self.chat.client.cancel()
        self.chat.transcript.stop()
        self.brain.stop()
        self.smart_notes.stop()
        self.voice.stop()
        self.desktop.stop()
        self.folders.stop()
        self.pet.timer.stop()
        self.letters.hide()
        self.note_service.stop()
        self.note_popup.timer.stop()
        self.note_popup.hide()
        for card in self.stickies.values():
            card.hide()
        self.focus.timer.stop()
        self.process_window.hide()
        self.pet.bubble.hide()
        self.tray.hide()
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.worker, "stop", Qt.BlockingQueuedConnection)
            self.thread.quit()
            self.thread.wait()
        try:
            self.settings.save({"overlay_position": [self.overlay.x(), self.overlay.y()], "pet_position": [self.pet.x(), self.pet.y()]})
        except OSError:
            logging.exception("Could not save final positions")


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--smoke-test", action="store_true", help="Run for four seconds then exit")
    args = parser.parse_args()
    data = data_directory()
    logging.basicConfig(filename=data / "buddy.log", level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("PixelSystemBuddy")
    app.setWindowIcon(buddy_icon())
    app.setQuitOnLastWindowClosed(False)
    # Qt high-DPI handling uses device-independent coordinates automatically.
    lock = QLockFile(str(data / "buddy.lock"))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "PixelSystem Buddy is already running. Check the tray icon beside your clock.")
        return 0
    buddy = BuddyApp(app)
    if args.smoke_test:
        QTimer.singleShot(4000, app.quit)
    try:
        return app.exec()
    finally:
        buddy.shutdown()
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
