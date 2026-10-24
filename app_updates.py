"""Published GitHub updates; application files are separate from user data."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

REPO = 'vancore876/pixel-pet'
RELEASE = f'https://github.com/{REPO}/releases/download/desktop-latest'
MANIFEST_URL = RELEASE + '/update.json'
ROOT = Path(__file__).resolve().parent
MAX_DOWNLOAD = 180 * 1024 * 1024


def managed_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        return False
    if len(path.parts) == 1:
        return path.suffix in ('.py', '.bat', '.md') or path.name.startswith('requirements') and path.suffix == '.txt'
    return path.parts[0] in ('assets', 'tools', 'work_server') and not any(
        part.startswith('.') or part in ('__pycache__', 'data', 'server-data') for part in path.parts)


def file_digest(data, name):
    if PurePosixPath(name).suffix in ('.py', '.bat', '.md', '.txt', '.html', '.css', '.js', '.json'):
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def source_fingerprints(root):
    root = Path(root)
    paths = list(root.glob('*'))
    for name in ('assets', 'tools', 'work_server'):
        if (root / name).is_dir():
            paths.extend((root / name).rglob('*'))
    return {path.relative_to(root).as_posix(): file_digest(path.read_bytes(), path.name)
            for path in paths if path.is_file() and managed_path(path.relative_to(root).as_posix())}


def validate_manifest(value):
    if not isinstance(value, dict) or not re.fullmatch('[0-9a-f]{40}', str(value.get('commit', ''))):
        raise ValueError('Invalid update version.')
    files = value.get('files')
    if not isinstance(files, dict) or not files or len(files) > 6000 or 'main.py' not in files:
        raise ValueError('Invalid update file list.')
    for name, digest in files.items():
        if not managed_path(name) or not re.fullmatch('[0-9a-f]{64}', str(digest)):
            raise ValueError('Invalid update file or checksum.')
    if not re.fullmatch('[0-9a-f]{64}', str(value.get('exe_sha256', ''))):
        raise ValueError('Invalid application checksum.')
    return value


def update_available(manifest, installed_files, installed_commit='', installed_time=0):
    validate_manifest(manifest)
    if installed_commit:
        if installed_time and manifest.get('committed_at', 0) <= installed_time:
            return False
        return manifest['commit'] != installed_commit
    return any(installed_files.get(name) != digest for name, digest in manifest['files'].items())


def download(url, target, maximum=MAX_DOWNLOAD):
    request = urllib.request.Request(url, headers={'User-Agent': 'Jeffery-Updater', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=20) as response, Path(target).open('wb') as output:
        size = 0
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ValueError('Update download exceeds its size limit.')
            output.write(chunk)


def install_source(archive, root, manifest):
    """Verify the entire staged package before replacing any managed files."""
    validate_manifest(manifest)
    root = Path(root).resolve()
    if (root / '.git').exists():
        raise ValueError('This is a developer checkout. Use git pull origin main, then restart Jeffery.')
    def atomic_copy(source, target):
        descriptor, temporary = tempfile.mkstemp(prefix='.jeffery-code-', dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='jeffery-update-') as temporary:
        stage, backup = Path(temporary) / 'stage', Path(temporary) / 'backup'
        stage.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            members = {}
            expanded = 0
            for entry in bundle.infolist():
                parts = PurePosixPath(entry.filename).parts
                if len(parts) < 2 or entry.is_dir():
                    continue
                name = '/'.join(parts[1:])
                if name not in manifest['files']:
                    continue
                if name in members or not managed_path(name) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Invalid update archive member.')
                expanded += entry.file_size
                if expanded > 350 * 1024 * 1024:
                    raise ValueError('Expanded update is too large.')
                data = bundle.read(entry)
                if file_digest(data, name) != manifest['files'][name]:
                    raise ValueError('Update checksum failed: ' + name)
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                members[name] = destination
            if set(members) != set(manifest['files']):
                raise ValueError('Update package is incomplete.')
        replaced = []
        try:
            for name in members:
                target = root / name
                if target.is_symlink() or not target.resolve().is_relative_to(root):
                    raise ValueError('Update target leaves the application folder.')
                existed = target.exists()
                if existed:
                    saved = backup / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                replaced.append((name, existed))
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(members[name], target)
        except Exception:
            for name, existed in reversed(replaced):
                if existed:
                    atomic_copy(backup / name, root / name)
                else:
                    (root / name).unlink(missing_ok=True)
            raise


def restart_helper(folder, launch, new_exe=None):
    script = Path(folder) / 'restart-update.ps1'
    script.write_text('''param([int]$AppProcessId,[string]$LaunchPath,[string]$NewExecutable)
Wait-Process -Id $AppProcessId -ErrorAction SilentlyContinue
if ($NewExecutable) {
  $BackupPath = $LaunchPath + '.previous'
  $PendingPath = $LaunchPath + '.pending'
  try {
    Copy-Item -LiteralPath $NewExecutable -Destination $PendingPath -Force -ErrorAction Stop
    for ($Attempt = 0; $Attempt -lt 10; $Attempt++) {
      try {
        Move-Item -LiteralPath $LaunchPath -Destination $BackupPath -Force -ErrorAction Stop
        break
      } catch {
        if ($Attempt -eq 9) { throw }
        Start-Sleep -Milliseconds 500
      }
    }
    Move-Item -LiteralPath $PendingPath -Destination $LaunchPath -Force -ErrorAction Stop
  } catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'update-error.log')
    if (-not (Test-Path -LiteralPath $LaunchPath) -and (Test-Path -LiteralPath $BackupPath)) {
      Move-Item -LiteralPath $BackupPath -Destination $LaunchPath -Force
    }
  }
}
if ([IO.Path]::GetExtension($LaunchPath) -eq '.bat') {
  Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', ('"' + $LaunchPath + '"')) -WorkingDirectory (Split-Path -Parent $LaunchPath) -WindowStyle Hidden
} else {
  Start-Process -FilePath $LaunchPath -WindowStyle Hidden
}
''', encoding='utf-8')
    args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
            '-AppProcessId', str(os.getpid()), '-LaunchPath', str(launch)]
    if new_exe:
        args.extend(['-NewExecutable', str(new_exe)])
    subprocess.Popen(args, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


from PySide6.QtCore import QObject, QEvent, QTimer, Signal, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QMessageBox, QPushButton, QVBoxLayout, QLabel, QHBoxLayout, QWidget


class UpdateController(QObject):
    _checked = Signal(object)
    _installed = Signal(object)

    def __init__(self, app, data_folder, can_restart=lambda: True, root=ROOT, auto_check=True):
        super().__init__(app)
        self.app, self.root, self.data_folder = app, Path(root), Path(data_folder)
        self.can_restart = can_restart
        self.manifest = None
        self.busy = False
        self.stopped = False
        self.buttons = []
        self.frozen = bool(getattr(sys, 'frozen', False))
        self.installed_commit = ''
        self.installed_time = 0
        self.installed_files = {}
        if self.frozen:
            try:
                build = json.loads((ROOT / 'app_build.json').read_text())
                self.installed_commit = build['commit']
                self.installed_time = build.get('committed_at', 0)
            except (OSError, ValueError, KeyError):
                pass
        else:
            self.installed_files = source_fingerprints(self.root)
            if (self.root / '.git').exists():
                try:
                    result = subprocess.run(['git', '-C', str(self.root), 'show', '-s', '--format=%H %ct', 'HEAD'],
                                            capture_output=True, text=True, timeout=3)
                    revision, stamp = result.stdout.strip().split()
                    self.installed_commit, self.installed_time = revision, int(stamp)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
        self._checked.connect(self._accept_check)
        self._installed.connect(self._accept_install)
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.stop)
        self.timer = QTimer(self)
        self.timer.setInterval(10 * 60 * 1000)
        self.timer.timeout.connect(self.check)
        if auto_check:
            self.timer.start()
            QTimer.singleShot(3000, self.check)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and self.manifest:
            QTimer.singleShot(0, lambda: self.attach(obj))
        return False

    def attach(self, window):
        try:
            if (not window.isWindow() or isinstance(window, (QFileDialog, QMenu, QMessageBox))
                    or not isinstance(window, QDialog) or window.property('jeffery_update_button')):
                return
            button = QPushButton('Update available', window)
            button.setObjectName('primary')
            button.setAutoDefault(False)
            button.clicked.connect(lambda: self.show_update(window))
            layout = window.layout()
            if layout is not None and hasattr(layout, 'insertLayout'):
                bar = QHBoxLayout()
                bar.addStretch()
                bar.addWidget(button)
                layout.insertLayout(0, bar)
            else:
                button.adjustSize()
                button.move(max(0, window.width() - button.width() - 12), 8)
                button.raise_()
            window.setProperty('jeffery_update_button', True)
            button.show()
            self.buttons.append(button)
        except RuntimeError:
            pass

    def check(self):
        if self.busy or self.stopped:
            return
        self.busy = True
        def work():
            try:
                with urllib.request.urlopen(urllib.request.Request(MANIFEST_URL, headers={'User-Agent': 'Jeffery-Updater', 'Cache-Control': 'no-cache'}), timeout=10) as reply:
                    value = validate_manifest(json.loads(reply.read(1024 * 1024)))
                if not self.stopped:
                    self._checked.emit(value)
            except Exception:
                if not self.stopped:
                    self._checked.emit(None)
        threading.Thread(target=work, daemon=True, name='update-check').start()

    def _accept_check(self, value):
        if self.stopped:
            return
        self.busy = False
        if value and update_available(value, self.installed_files, self.installed_commit, self.installed_time):
            self.manifest = value
            for window in self.app.topLevelWidgets():
                self.attach(window)

    def show_update(self, parent=None):
        if self.busy or not self.manifest:
            return
        if (self.root / '.git').exists() and not self.frozen:
            QMessageBox.information(parent, 'Pixel Pet update', 'An update is available. This is a developer checkout; use git pull origin main, then restart Jeffery. Your working files will not be overwritten automatically.')
            return
        answer = QMessageBox.question(parent, 'Update Pixel Pet',
            'Install the published update and restart? Application code will be updated; notes, accounts and stock data are kept. Save any other open drafts first.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes or not self.can_restart():
            return
        self.busy = True
        for button in self.buttons:
            try:
                button.setText('Downloading update...')
                button.setEnabled(False)
            except RuntimeError:
                pass
        def work():
            try:
                folder = self.data_folder / 'updates'
                folder.mkdir(parents=True, exist_ok=True)
                if self.frozen:
                    target = folder / ('PixelSystemBuddy-' + self.manifest['commit'] + '.exe')
                    download(RELEASE + '/PixelSystemBuddy.exe', target)
                    if hashlib.sha256(target.read_bytes()).hexdigest() != self.manifest['exe_sha256']:
                        raise ValueError('Application checksum did not match. Retry the update.')
                    restart_helper(folder, Path(sys.executable), target)
                else:
                    archive = folder / ('source-' + self.manifest['commit'] + '.zip')
                    download(f"https://codeload.github.com/{REPO}/zip/{self.manifest['commit']}", archive)
                    install_source(archive, self.root, self.manifest)
                    launcher = self.root / ('Start_Work_Buddy.bat' if os.environ.get('JEFFERY_WORK_PROFILE') else 'Start_Buddy.bat')
                    restart_helper(folder, launcher)
                self._installed.emit(None)
            except Exception as error:
                self._installed.emit(str(error))
        threading.Thread(target=work, daemon=True, name='install-update').start()

    def _accept_install(self, error):
        self.busy = False
        if error:
            for button in self.buttons:
                try:
                    button.setText('Update available')
                    button.setEnabled(True)
                except RuntimeError:
                    pass
            QMessageBox.warning(None, 'Update not installed', error)
        else:
            self.app.quit()

    def stop(self):
        self.stopped = True
        self.timer.stop()
