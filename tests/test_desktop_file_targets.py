"""Visible file targets retain metadata without adding file operations.

Set PIXEL_PET_POWERSHELL to run the pure Shell-metadata cases on Linux too.
They use mocked Shell objects, not Windows UI Automation or real file contents.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_bridge import DesktopBridge, validate_targets


def file_target(identifier="file-2048-42,10", **changes):
    return {
        "id": identifier, "kind": "file", "name": "Stock list",
        "path": r"C:\Users\Sales\Desktop\Stock list.xlsx",
        "rect": [-950, 120, 92, 80], "hwnd": 2048, "pid": 10,
        "runtime": "42,10", "window_rect": [-1280, 0, 1280, 900],
        **changes,
    }


class FileTargetValidationTests(unittest.TestCase):
    def test_file_metadata_keeps_hidden_extension_path_and_visible_label(self):
        original = file_target()
        result = validate_targets({"targets": [original]})
        self.assertEqual(result, [original])
        self.assertNotIn(original["name"], result[0]["id"])
        self.assertNotIn(original["path"], result[0]["id"])

    def test_unc_files_and_existing_target_kinds_can_coexist(self):
        targets = [file_target(path=r"\\office-server\Parts\Stock.xlsx")]
        targets += [file_target(kind, kind=kind, path="" if kind != "folder" else r"C:\Parts")
                    for kind in ("folder", "tab", "window")]
        self.assertEqual(validate_targets({"targets": targets}), targets)

    def test_guessed_virtual_relative_and_corrupted_file_paths_are_rejected(self):
        for path in ("", "Stock.xlsx", "C:Stock.xlsx", r"\Stock.xlsx", "shell:Downloads",
                     "https://example.com/Stock.xlsx", "/tmp/Stock.xlsx", None, [],
                     "C:\\Parts\\bad\x00.xlsx", "C:\\" + "x" * 1000):
            with self.subTest(path=path):
                self.assertEqual(validate_targets({"targets": [file_target(path=path)]}), [])

    def test_invalid_file_bounds_and_owner_are_rejected_without_losing_valid_file(self):
        invalid = [file_target(str(index), **changes) for index, changes in enumerate((
            {"rect": [0, 0, 0, 20]}, {"rect": [0, 0, True, 20]},
            {"rect": [100001, 0, 20, 20]}, {"pid": False}, {"hwnd": 0},
            {"window_rect": [0, 0, 5, -1]}, {"kind": "execute_file"},
        ))]
        self.assertEqual(validate_targets({"targets": invalid + [file_target()]}), [file_target()])

    def test_duplicate_and_oversized_native_scans_remain_bounded(self):
        targets = [file_target(str(index)) for index in range(150)]
        self.assertEqual(len(validate_targets({"targets": targets})), 100)
        self.assertEqual(validate_targets({"targets": [targets[0], targets[0], targets[1]]}), targets[:2])

    def test_scan_publishes_files_without_any_file_action(self):
        state = SimpleNamespace(scan_pending=True, targets=[], monitors=[],
                                set_status=Mock(), changed=SimpleNamespace(emit=Mock()))
        DesktopBridge.scanned(state, {"targets": [file_target()], "monitors": []}, "")
        self.assertEqual(state.targets, [file_target()])
        self.assertFalse(state.scan_pending)
        self.assertIn("files", state.set_status.call_args.args[0])
        state.changed.emit.assert_called_once_with()
        state.supported, state.stopped, state.settings, state.jobs = True, False, {"desktop_enabled": True}, []
        for action in ("open_file", "move_file", "delete_file", "read_file", "execute_file"):
            callback = Mock()
            self.assertFalse(DesktopBridge.request(state, action, {"path": file_target()["path"]}, callback))
            self.assertEqual(state.jobs, [])
            self.assertIsNone(callback.call_args.args[0])


POWERSHELL = os.environ.get("PIXEL_PET_POWERSHELL") or shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(POWERSHELL, "PowerShell is needed for isolated Shell metadata tests")
class ShellMetadataTests(unittest.TestCase):
    def run_metadata(self, body):
        source = Path(__file__).resolve().parents[1] / "assets/windows_desktop.ps1"
        script = r"""
param([string]$Source)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count) { throw ($errors | Out-String) }
foreach($name in @('AddItemAlias','ItemMap','VisiblePoint')) {
  $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
  if($null -eq $node) { throw "Missing function $name" }
  . ([ScriptBlock]::Create($node.Extent.Text))
}
function TestItem($name,$path,$display=$name,$folder=$false,$filesystem=$true,$link=$null) {
  return [pscustomobject]@{Name=$name;Path=$path;Display=$display;IsFolder=$folder;IsFileSystem=$filesystem;IsLink=($null -ne $link);GetLink=@{Target=$link}}
}
function TestFolder($items) {
  $folder=[pscustomobject]@{Entries=@($items)}
  $folder | Add-Member ScriptMethod Items { return $this.Entries }
  $folder | Add-Member ScriptMethod GetDetailsOf { param($item,$index) return $item.Display }
  return $folder
}
$clock=[pscustomobject]@{ElapsedMilliseconds=0}
""" + body
        with tempfile.TemporaryDirectory(prefix="pixel-pet-shell-test-") as temporary:
            path = Path(temporary) / "metadata.ps1"
            path.write_text(script, encoding="utf-8-sig")
            result = subprocess.run(
                [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path), str(source)],
                text=True, encoding="utf-8-sig", capture_output=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_shell_display_names_keep_files_folders_and_shortcuts_distinct(self):
        result = self.run_metadata(r"""
$directory=TestItem 'Parts' 'C:\Parts' 'Parts' $true
$file=TestItem 'Stock.xlsx' 'C:\Stock.xlsx' 'Stock'
$shortcut=TestItem 'Parts shortcut' 'C:\Parts shortcut.lnk' 'Parts shortcut' $false $true $directory
$virtual=TestItem 'Recycle Bin' '::{virtual}' 'Recycle Bin' $true $false
$map=ItemMap (TestFolder @($directory,$file,$shortcut,$virtual)) $clock
@{file=$map['Stock'];full=$map['Stock.xlsx'];folder=$map['Parts'];shortcut=$map['Parts shortcut'];virtual=$map.ContainsKey('Recycle Bin')} | ConvertTo-Json -Depth 5 -Compress
""")
        self.assertEqual(result["file"], {"kind": "file", "path": r"C:\Stock.xlsx"})
        self.assertEqual(result["full"], result["file"])
        self.assertEqual(result["folder"], {"kind": "folder", "path": r"C:\Parts"})
        self.assertEqual(result["shortcut"], result["folder"])
        self.assertFalse(result["virtual"])

    def test_ambiguous_hidden_extensions_and_explorer_tabs_never_pick_wrong_path(self):
        result = self.run_metadata(r"""
$map=ItemMap (TestFolder @((TestItem 'Report.pdf' 'C:\Report.pdf' 'Report'),(TestItem 'Report.xlsx' 'C:\Report.xlsx' 'Report'))) $clock
$map=ItemMap (TestFolder @((TestItem 'Report.pdf' 'D:\Report.pdf' 'Report'))) $clock $map
@{ambiguous=$map['Report'];sameLabelInOtherTab=$map['Report.pdf'];unambiguous=$map['Report.xlsx']} | ConvertTo-Json -Depth 5 -Compress
""")
        self.assertIsNone(result["ambiguous"])
        self.assertIsNone(result["sameLabelInOtherTab"])
        self.assertEqual(result["unambiguous"]["path"], r"C:\Report.xlsx")

    def test_failed_item_does_not_discard_later_files_and_mapping_is_bounded(self):
        result = self.run_metadata(r"""
$broken=[pscustomobject]@{}
$broken | Add-Member ScriptProperty IsFolder { throw 'Provider item vanished' }
$items=@($broken)
for($i=0;$i -lt 520;$i++) { $items+=TestItem "File$i" "C:\File$i" }
$map=ItemMap (TestFolder $items) $clock
$clock.ElapsedMilliseconds=6500
$expired=ItemMap (TestFolder @((TestItem 'Late' 'C:\Late'))) $clock
@{valid=$map['File0'];lastIncluded=$map.ContainsKey('File510');pastLimit=$map.ContainsKey('File511');expired=$expired.Count} | ConvertTo-Json -Depth 5 -Compress
""")
        self.assertEqual(result["valid"]["kind"], "file")
        self.assertTrue(result["lastIncluded"])
        self.assertFalse(result["pastLimit"])
        self.assertEqual(result["expired"], 0)

    def test_occlusion_respects_z_order_and_negative_monitor_coordinates(self):
        result = self.run_metadata(r"""
$windows=@(@{rect=@(-1000,0,500,500)},@{rect=@(0,0,800,600)})
@{covered=(VisiblePoint @(-900,100,40,40) $windows 2);clear=(VisiblePoint @(-400,100,40,40) $windows 2);ownWindow=(VisiblePoint @(-900,100,40,40) $windows 0)} | ConvertTo-Json -Compress
""")
        self.assertEqual(result, {"covered": False, "clear": True, "ownWindow": True})


if __name__ == "__main__":
    unittest.main()
