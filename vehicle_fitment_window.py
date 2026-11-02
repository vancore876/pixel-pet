"""Review and record vehicle-to-part links using staff-supplied evidence."""
from __future__ import annotations

import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ui_style import apply_window_style
from vehicle_fitment import candidate_parts


def _button(text, callback):
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.clicked.connect(callback)
    return button


class VehicleFitmentWindow(QDialog):
    """Local, explicit links; imported descriptions are candidates only."""

    def __init__(self, store, catalog, settings, parent=None):
        super().__init__(parent)
        self.store, self.catalog, self.settings = store, catalog, settings
        self.current_chassis = None
        self.vehicle_dirty = False
        self._loading = False
        self._chassis_needs_load = False
        self._vehicle_token = None
        self._part_token = None
        self._editing_part = ''
        self.setWindowTitle('Jeffery · Vehicle and parts mapping')
        self.resize(850, 760)
        self.setMinimumSize(650, 500)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        page = QVBoxLayout(body)
        heading = QLabel('Vehicle and parts mapping')
        heading.setObjectName('pageHeading')
        page.addWidget(heading)
        note = QLabel('Saved on this PC. Enter vehicle details and confirm fitment from a reliable source. '
                      'The catalogue suggestions below are not verified fitment.')
        note.setWordWrap(True)
        page.addWidget(note)
        exchange = QHBoxLayout()
        self.import_button = _button('Import mappings…', self.import_mappings)
        self.export_button = _button('Export mappings…', self.export_mappings)
        exchange.addWidget(self.import_button)
        exchange.addWidget(self.export_button)
        exchange.addStretch(1)
        page.addLayout(exchange)
        exchange_note = QLabel('To share, export to a shared folder and import on other PCs. Changes do not sync automatically.')
        exchange_note.setWordWrap(True)
        page.addWidget(exchange_note)
        form = QFormLayout()
        self.chassis = QLineEdit()
        self.chassis.setPlaceholderText('Enter or scan a chassis / frame number')
        self.make = QLineEdit()
        self.model = QLineEdit()
        self.year = QSpinBox()
        self.year.setRange(0, 2100)
        self.year.setSpecialValueText('Unknown')
        self.engine = QLineEdit()
        self.vehicle_note = QLineEdit()
        self.vehicle_note.setPlaceholderText('Source or notes (registration, manual, invoice…)')
        form.addRow('Chassis number', self.chassis)
        form.addRow('Make', self.make)
        form.addRow('Model', self.model)
        form.addRow('Year', self.year)
        form.addRow('Engine', self.engine)
        form.addRow('Vehicle source / notes', self.vehicle_note)
        page.addLayout(form)
        actions = QHBoxLayout()
        self.load_button = _button('Load vehicle', self.load_vehicle)
        self.save_button = _button('Save vehicle', self.save_vehicle)
        actions.addWidget(self.load_button)
        actions.addWidget(self.save_button)
        actions.addStretch(1)
        page.addLayout(actions)
        page.addWidget(QLabel('Parts linked to this chassis'))
        self.linked = QListWidget()
        self.linked.setMinimumHeight(110)
        self.linked.itemDoubleClicked.connect(self.edit_link)
        page.addWidget(self.linked)
        self.remove_button = _button('Remove selected part link…', self.remove_link)
        page.addWidget(self.remove_button)
        link_form = QFormLayout()
        self.part_number = QLineEdit()
        self.part_number.setPlaceholderText('Enter or scan a part number')
        self.part_source = QLineEdit()
        self.part_source.setPlaceholderText('How was fitment checked?')
        self.part_note = QLineEdit()
        self.part_note.setPlaceholderText('Optional fitment notes')
        link_form.addRow('Part number', self.part_number)
        link_form.addRow('Confirmation source', self.part_source)
        link_form.addRow('Part notes', self.part_note)
        page.addLayout(link_form)
        self.add_button = _button('Save confirmed part link', self.add_part)
        page.addWidget(self.add_button)
        page.addWidget(QLabel('Catalogue suggestions — verify fitment before saving a link'))
        candidates = QHBoxLayout()
        self.candidate_query = QLineEdit()
        self.candidate_query.setPlaceholderText('Optional: brake pad, filter, or part number')
        self.candidate_button = _button('Find possible parts', self.find_candidates)
        candidates.addWidget(self.candidate_query, 1)
        candidates.addWidget(self.candidate_button)
        page.addLayout(candidates)
        self.candidates = QListWidget()
        self.candidates.setMinimumHeight(110)
        self.candidates.itemDoubleClicked.connect(self.choose_candidate)
        page.addWidget(self.candidates)
        hint = QLabel('Double-click a suggestion to copy its number above. Confirm the part and its source before saving.')
        hint.setWordWrap(True)
        page.addWidget(hint)
        self.status = QLabel('Enter a chassis number to load or add its vehicle.')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        page.addWidget(self.status)
        page.addStretch(1)
        self.chassis.textEdited.connect(self._chassis_edited)
        self.part_number.textEdited.connect(self._forget_selected_part)
        for field in (self.make, self.model, self.engine, self.vehicle_note):
            field.textEdited.connect(self._mark_dirty)
        self.year.valueChanged.connect(self._mark_dirty)
        self.configure()

    def configure(self):
        apply_window_style(self, self.settings)

    def import_mappings(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import vehicle mappings', '', 'Jeffery vehicle mappings (*.json)')
        if not path:
            return
        try:
            result = self.store.import_from(path)
            self._invalidate_selection()
            self._chassis_needs_load = True
            self.status.setText(f"Imported {result['vehicles']:,} vehicles and {result['parts']:,} part links on this PC. "
                                'Load a chassis to review its mappings.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText('Import failed; existing mappings were preserved. ' + str(exc))

    def export_mappings(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export vehicle mappings', 'vehicle-mappings.json',
                                              'Jeffery vehicle mappings (*.json)')
        if not path:
            return
        try:
            result = self.store.export_to(path)
            self.status.setText(f"Exported {result['vehicles']:,} vehicles and {result['parts']:,} part links. "
                                'Other PCs must import this file; changes do not sync automatically.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText('Export failed. ' + str(exc))

    def _invalidate_selection(self, *_):
        self.current_chassis = None
        self.vehicle_dirty = False
        self._vehicle_token = None
        self._forget_selected_part()
        self.linked.clear()
        self.candidates.clear()

    def _chassis_edited(self, *_):
        if self.current_chassis:
            self._chassis_needs_load = True
        self._invalidate_selection()
        if self._chassis_needs_load:
            self.status.setText('Load this chassis before saving so the previous vehicle details cannot be copied accidentally.')

    def _mark_dirty(self, *_):
        if not self._loading and self.current_chassis:
            self.vehicle_dirty = True
            self.status.setText('Save the edited vehicle details before linking or searching for parts.')

    def _forget_selected_part(self, *_):
        self._part_token = None
        self._editing_part = ''

    def load_vehicle(self):
        try:
            vehicle = self.store.get_vehicle(self.chassis.text().strip())
            self._chassis_needs_load = False
            if vehicle is None:
                self._invalidate_selection()
                self._loading = True
                self.make.clear()
                self.model.clear()
                self.year.setValue(0)
                self.engine.clear()
                self.vehicle_note.clear()
                self._loading = False
                self.status.setText('No saved vehicle for this chassis on this PC. Enter its details and save.')
                return
            self.current_chassis = vehicle['chassis']
            self._vehicle_token = vehicle['updated_at']
            self._forget_selected_part()
            self._loading = True
            self.chassis.setText(vehicle['chassis'])
            self.make.setText(vehicle['make'])
            self.model.setText(vehicle['model'])
            self.year.setValue(vehicle.get('year') or 0)
            self.engine.setText(vehicle.get('engine') or '')
            self.vehicle_note.setText(vehicle.get('note') or '')
            self._loading = False
            self.vehicle_dirty = False
            self.refresh_links()
            self.candidates.clear()
            self.status.setText('Vehicle loaded. Review its details and linked parts.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._invalidate_selection()
            self.status.setText(str(exc))

    def save_vehicle(self):
        if self._chassis_needs_load:
            self.status.setText('Load this chassis before saving so the previous vehicle details cannot be copied accidentally.')
            return
        try:
            code = self.chassis.text().strip()
            self.store.save_vehicle(code, self.make.text(), self.model.text(),
                                    year=self.year.value() or None, engine=self.engine.text(),
                                    note=self.vehicle_note.text(), expected_updated_at=self._vehicle_token)
            self.load_vehicle()
            self.status.setText('Vehicle saved on this PC. Review any links marked for re-confirmation.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))

    def refresh_links(self):
        self.linked.clear()
        if not self.current_chassis:
            return
        for part in self.store.parts_for(self.current_chassis):
            status = 'Confirmed' if part.get('confirmed_for_current_vehicle') else 'Needs re-confirmation'
            details = ' · '.join(value for value in (part.get('source', ''), part.get('note', '')) if value)
            item = QListWidgetItem(f"{part['part_number']} · {status}" + (f' · {details}' if details else ''))
            item.setData(Qt.UserRole, part)
            self.linked.addItem(item)

    def edit_link(self, item):
        part = item.data(Qt.UserRole) or {}
        self.part_number.setText(part.get('part_number', ''))
        self.part_source.setText(part.get('source', ''))
        self.part_note.setText(part.get('note', ''))
        self._part_token = part.get('updated_at')
        self._editing_part = part.get('part_number', '')
        self.status.setText('Review the source and save the link to update or re-confirm it.')

    def remove_link(self):
        item = self.linked.currentItem()
        if not self.current_chassis or not item:
            self.status.setText('Select a saved part link to remove.')
            return
        if self.vehicle_dirty:
            self.status.setText('Save the edited vehicle details before removing a link.')
            return
        part = item.data(Qt.UserRole) or {}
        if QMessageBox.question(self, 'Remove part link?',
                'Remove the selected link from this PC? An older exported file can contain it.') != QMessageBox.Yes:
            return
        try:
            self.store.remove_part(self.current_chassis, part.get('part_number', ''),
                                   expected_updated_at=part.get('updated_at'))
            self.refresh_links()
            self.status.setText('Part link removed on this PC. Older exported files can contain it.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))

    def add_part(self):
        if not self.current_chassis:
            self.status.setText('Load or save a vehicle first.')
            return
        if self.vehicle_dirty:
            self.status.setText('Save the edited vehicle details before linking parts.')
            return
        source = self.part_source.text().strip()
        if not source:
            self.status.setText('Enter the source used to confirm this part fits the vehicle.')
            return
        try:
            number = self.part_number.text().strip()
            token = self._part_token if number.casefold() == self._editing_part.casefold() else None
            self.store.add_part(self.current_chassis, number, source=source, note=self.part_note.text().strip(),
                                expected_vehicle_updated_at=self._vehicle_token, expected_part_updated_at=token)
            self._forget_selected_part()
            self.refresh_links()
            self.status.setText('Confirmed part link saved on this PC.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))

    def find_candidates(self):
        if not self.current_chassis:
            self.status.setText('Load or save a vehicle first.')
            return
        if self.vehicle_dirty:
            self.status.setText('Save the edited vehicle details before searching for parts.')
            return
        try:
            vehicle = self.store.get_vehicle(self.current_chassis)
            if not vehicle:
                self._invalidate_selection()
                self.status.setText('The vehicle is no longer available. Load it again.')
                return
            metadata, rows = candidate_parts(self.catalog, vehicle['make'], vehicle['model'],
                                             query=self.candidate_query.text().strip(), limit=20)
            self.candidates.clear()
            for row in rows:
                number = row.get('part_number', '')
                item = QListWidgetItem(f"{number} · {row.get('description', '')}")
                item.setData(Qt.UserRole, number)
                self.candidates.addItem(item)
            date = metadata.get('source_date') or 'date unknown'
            self.status.setText(f'{len(rows)} possible parts from snapshot {date}. Descriptions are not verified fitment.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))

    def choose_candidate(self, item):
        self._forget_selected_part()
        self.part_number.setText(str(item.data(Qt.UserRole) or ''))
        self.status.setText('Part number copied. Verify fitment and enter your confirmation source.')

    def shutdown(self):
        self.hide()
