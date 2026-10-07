param([int]$OwnerPid)
# Local Windows UI Automation bridge. Commands arrive as JSON, never as code.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class BuddyWin {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int left, top, right, bottom; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct MONITORINFOEX {
    public int cbSize; public RECT monitor, work; public int flags;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string device;
  }
  public class Window { public long hwnd; public uint pid; public string name, cls; public int[] rect; }
  public class Monitor { public string name; public int[] rect; }
  public delegate bool EnumProc(IntPtr hwnd, IntPtr data);
  public delegate bool MonitorProc(IntPtr monitor, IntPtr hdc, ref RECT rect, IntPtr data);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr data);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr FindWindow(string cls, string name);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr FindWindowEx(IntPtr p, IntPtr c, string cls, string name);
  [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorProc proc, IntPtr data);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool GetMonitorInfo(IntPtr h, ref MONITORINFOEX info);
  [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
  [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr h, int a, out int value, int size);
  public static void PhysicalCoordinates() { try { SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch {} }
  public static uint Process(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
  public static List<Window> Windows(int owner) {
    var result = new List<Window>();
    EnumWindows((h,d) => {
      RECT r; uint p=Process(h); int cloaked=0;
      try { DwmGetWindowAttribute(h,14,out cloaked,4); } catch {}
      if(p==owner || !IsWindowVisible(h) || IsIconic(h) || cloaked!=0 || !GetWindowRect(h,out r)) return true;
      var title=new StringBuilder(300); var cls=new StringBuilder(100);
      GetWindowText(h,title,300); GetClassName(h,cls,100);
      if(title.Length==0 || r.right-r.left<100 || r.bottom-r.top<80 || cls.ToString()=="Progman" || cls.ToString()=="WorkerW") return true;
      result.Add(new Window { hwnd=h.ToInt64(), pid=p, name=title.ToString(), cls=cls.ToString(), rect=new int[]{r.left,r.top,r.right-r.left,r.bottom-r.top} });
      return result.Count<24;
    }, IntPtr.Zero);
    return result;
  }
  public static List<Monitor> Monitors() {
    var result=new List<Monitor>();
    EnumDisplayMonitors(IntPtr.Zero,IntPtr.Zero,(IntPtr h,IntPtr dc,ref RECT r,IntPtr data)=> {
      var info=new MONITORINFOEX(); info.cbSize=Marshal.SizeOf(info);
      if(GetMonitorInfo(h,ref info)) result.Add(new Monitor { name=info.device, rect=new int[]{info.monitor.left,info.monitor.top,info.monitor.right-info.monitor.left,info.monitor.bottom-info.monitor.top} });
      return true;
    },IntPtr.Zero); return result;
  }
  public static IntPtr Desktop() {
    IntPtr root=FindWindow("Progman",null), view=FindWindowEx(root,IntPtr.Zero,"SHELLDLL_DefView",null);
    if(view==IntPtr.Zero) EnumWindows((h,d)=> { view=FindWindowEx(h,IntPtr.Zero,"SHELLDLL_DefView",null); return view==IntPtr.Zero; },IntPtr.Zero);
    return view==IntPtr.Zero ? IntPtr.Zero : FindWindowEx(view,IntPtr.Zero,"SysListView32",null);
  }
  [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort vk, scan; public uint flags, time; public UIntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int x,y; public uint data,flags,time; public UIntPtr extra; }
  [StructLayout(LayoutKind.Explicit)] struct UNION { [FieldOffset(0)] public KEYBDINPUT key; [FieldOffset(0)] public MOUSEINPUT mouse; }
  [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION data; }
  [DllImport("user32.dll",SetLastError=true)] static extern uint SendInput(uint count, INPUT[] inputs, int size);
  [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
  public static void TypeText(string text, long hwnd, uint pid) {
    if(GetForegroundWindow().ToInt64()!=hwnd || Process(new IntPtr(hwnd))!=pid) throw new Exception("The original editor is no longer active.");
    foreach(int key in new int[]{16,17,18,91,92}) if((GetAsyncKeyState(key)&0x8000)!=0) throw new Exception("Release Shift, Ctrl, Alt, and Windows keys, then Apply again.");
    var items=new List<INPUT>();
    for(int index=0;index<text.Length;index++) {
      char c=text[index]; bool enter=c=='\n' || c=='\r';
      if(c=='\r' && index+1<text.Length && text[index+1]=='\n') continue;
      items.Add(new INPUT { type=1, data=new UNION { key=new KEYBDINPUT { vk=(ushort)(enter?13:0), scan=(ushort)(enter?0:c), flags=(uint)(enter?0:4) } } });
      items.Add(new INPUT { type=1, data=new UNION { key=new KEYBDINPUT { vk=(ushort)(enter?13:0), scan=(ushort)(enter?0:c), flags=(uint)(enter?2:6) } } });
    }
    if(SendInput((uint)items.Count,items.ToArray(),Marshal.SizeOf(typeof(INPUT)))!=items.Count) throw new Exception("Windows blocked some text input. Check the editor before trying again; use an editor running without administrator rights.");
  }
}
'@
[BuddyWin]::PhysicalCoordinates()
$Scope = [System.Windows.Automation.TreeScope]
$Walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$script:CapturedRanges = @{}
$script:CaptureOrder = [Collections.Generic.Queue[string]]::new()

function Emit($value) {
  [Console]::WriteLine(($value | ConvertTo-Json -Depth 12 -Compress))
  [Console]::Out.Flush()
}
function Elements($root, [int]$limit=650, [bool]$documents=$false) {
  $queue = [Collections.Generic.Queue[object]]::new()
  $queue.Enqueue(@($root,0)); $seen=0
  while($queue.Count -gt 0 -and $seen -lt $limit) {
    $pair=$queue.Dequeue(); $element=$pair[0]; $depth=[int]$pair[1]; $seen++
    try {
      if($element.Current.IsOffscreen) { continue }
      Write-Output $element
      if($depth -ge 14 -or (!$documents -and $element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Document)) { continue }
      $child=$Walker.GetFirstChild($element)
      while($null -ne $child -and $queue.Count -lt $limit) {
        $queue.Enqueue(@($child,$depth+1)); $child=$Walker.GetNextSibling($child)
      }
    } catch { }
  }
}
function Runtime($element) { return ($element.GetRuntimeId() -join ',') }
function Rectangle($element) {
  $r=$element.Current.BoundingRectangle
  return @([int]$r.X,[int]$r.Y,[int]$r.Width,[int]$r.Height)
}
function FolderMap($folder) {
  $map=@{}
  $count=0
  try {
    foreach($item in $folder.Items()) {
      $count++; if($count -gt 512) { break }
      if($item.IsFolder -and $item.IsFileSystem) { $map[[string]$item.Name]=[string]$item.Path }
      elseif($item.IsLink) {
        try { $target=$item.GetLink.Target; if($target.IsFolder -and $target.IsFileSystem) { $map[[string]$item.Name]=[string]$target.Path } } catch {}
      }
    }
  } catch {}
  return $map
}
function VisiblePoint($rect, $windows, [int]$before) {
  $x=$rect[0]+$rect[2]/2; $y=$rect[1]+$rect[3]/2
  for($i=0;$i -lt $before;$i++) {
    $r=$windows[$i].rect
    if($x -ge $r[0] -and $x -lt $r[0]+$r[2] -and $y -ge $r[1] -and $y -lt $r[1]+$r[3]) { return $false }
  }
  return $true
}
function Scan {
  $windows=@([BuddyWin]::Windows($OwnerPid)); $targets=[Collections.Generic.List[object]]::new()
  $shell=New-Object -ComObject Shell.Application
  $maps=@{}
  try { foreach($window in $shell.Windows()) { $maps[[string]$window.HWND]=FolderMap $window.Document.Folder } } catch {}
  $desktop=[BuddyWin]::Desktop()
  if($desktop -ne [IntPtr]::Zero) {
    try {
      $map=FolderMap $shell.NameSpace(0)
      foreach($e in (Elements ([System.Windows.Automation.AutomationElement]::FromHandle($desktop)) 420)) {
        if($e.Current.ControlType -ne [System.Windows.Automation.ControlType]::ListItem) { continue }
        $name=[string]$e.Current.Name; $r=Rectangle $e
        if($map.ContainsKey($name) -and $r[2] -gt 0 -and (VisiblePoint $r $windows $windows.Count)) {
          $rid=Runtime $e
          $targets.Add(@{id="f-$($desktop.ToInt64())-$rid";kind='folder';name=$name;path=$map[$name];rect=$r;hwnd=$desktop.ToInt64();pid=$e.Current.ProcessId;runtime=$rid})
        }
      }
    } catch {}
  }
  for($index=0;$index -lt [Math]::Min(12,$windows.Count);$index++) {
    $w=$windows[$index]
    if(VisiblePoint $w.rect $windows $index) { $targets.Add(@{id="w-$($w.hwnd)";kind='window';name=$w.name;rect=$w.rect;window_rect=$w.rect;hwnd=$w.hwnd;pid=$w.pid;runtime=''}) }
    if($w.cls -notin @('CabinetWClass','Chrome_WidgetWin_1','MozillaWindowClass')) { continue }
    try {
      $map=$maps[[string]$w.hwnd]
      foreach($e in (Elements ([System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$w.hwnd)))) {
        $type=$e.Current.ControlType; $name=[string]$e.Current.Name
        $kind=''; $path=''
        if($type -eq [System.Windows.Automation.ControlType]::TabItem) { $kind='tab' }
        elseif($null -ne $map -and $type -in @([System.Windows.Automation.ControlType]::ListItem,[System.Windows.Automation.ControlType]::DataItem) -and $map.ContainsKey($name)) { $kind='folder';$path=$map[$name] }
        if(!$kind) { continue }
        $r=Rectangle $e
        if($r[2] -le 0 -or $r[3] -le 0 -or !(VisiblePoint $r $windows $index)) { continue }
        $rid=Runtime $e
        $targets.Add(@{id="$kind-$($w.hwnd)-$rid";kind=$kind;name=$name;path=$path;rect=$r;window_rect=$w.rect;hwnd=$w.hwnd;pid=$w.pid;runtime=$rid})
        if($targets.Count -ge 100) { break }
      }
    } catch {}
  }
  return @{targets=@($targets.ToArray());monitors=@([BuddyWin]::Monitors());foreground=[BuddyWin]::GetForegroundWindow().ToInt64()}
}
function FindElement($request) {
  $h=[IntPtr][long]$request.hwnd
  if([BuddyWin]::Process($h) -ne [uint32]$request.pid) { throw 'That window closed. Refresh the desktop list.' }
  foreach($element in (Elements ([System.Windows.Automation.AutomationElement]::FromHandle($h)) 1000 $true)) {
    if((Runtime $element) -eq [string]$request.runtime) { return $element }
  }
  throw 'That control moved or closed. Capture it again.'
}
function SelectedText($element, [bool]$remember=$false) {
  if($element.Current.IsPassword) { throw 'Password fields cannot be captured.' }
  $pattern=$null
  if(!$element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$pattern)) { return $null }
  $ranges=$pattern.GetSelection()
  if($ranges.Length -ne 1) { return $null }
  $text=$ranges[0].GetText(121)
  if(!$text) { return $null }
  if($text.Length -gt 120) { throw 'Select up to 120 characters, then try again.' }
  $rects=@($ranges[0].GetBoundingRectangles() | ForEach-Object { ,@([int]$_.X,[int]$_.Y,[int]$_.Width,[int]$_.Height) })
  if(!$rects.Count) { throw 'The selected text is not visible.' }
  $value=$null; $editable=$false
  if($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value)) { $editable=!$value.Current.IsReadOnly }
  # TextPattern alone does not promise an editable control. Restrict keyboard edits.
  $h=[BuddyWin]::GetForegroundWindow(); $process=Get-Process -Id $element.Current.ProcessId
  if($process.ProcessName -in @('notepad','wordpad','WINWORD') -and $element.Current.ControlType -in @([System.Windows.Automation.ControlType]::Edit,[System.Windows.Automation.ControlType]::Document)) { $editable=$true }
  $owner=[BuddyWin]::Process($h)
  $runtime=Runtime $element
  $result=@{text=$text;rects=$rects;hwnd=$h.ToInt64();pid=$owner;runtime=$runtime;editable=$editable;app=$process.ProcessName}
  if($remember) {
    $token=[Guid]::NewGuid().ToString('N')
    $script:CapturedRanges[$token]=@{range=$ranges[0].Clone();hwnd=$h.ToInt64();pid=$owner;runtime=$runtime}
    $script:CaptureOrder.Enqueue($token)
    while($script:CaptureOrder.Count -gt 4) { $old=$script:CaptureOrder.Dequeue();$script:CapturedRanges.Remove($old) }
    $result.capture_id=$token
  }
  return $result
}
function CaptureText {
  $h=[BuddyWin]::GetForegroundWindow()
  if([BuddyWin]::Process($h) -eq $OwnerPid) { throw 'Select text in your editor first. Use Ctrl+Alt+J, or focus it during the countdown.' }
  $focused=[System.Windows.Automation.AutomationElement]::FocusedElement
  $selection=SelectedText $focused $true
  if($null -ne $selection) { return $selection }
  foreach($element in (Elements ([System.Windows.Automation.AutomationElement]::FromHandle($h)) 800 $true)) {
    $selection=SelectedText $element $true
    if($null -ne $selection) { return $selection }
  }
  throw 'Select visible text in Notepad, Word, or a compatible app first.'
}
function Dispatch($request) {
  switch([string]$request.action) {
    'scan' { return Scan }
    'capture_text' { return CaptureText }
    'select_tab' {
      $e=FindElement $request
      if($e.Current.ControlType -ne [System.Windows.Automation.ControlType]::TabItem -or $e.Current.Name -ne [string]$request.name) { throw 'The tab changed. Refresh the list.' }
      $p=$null
      if(!$e.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$p)) { throw 'This tab does not support Windows tab selection.' }
      [void][BuddyWin]::SetForegroundWindow([IntPtr][long]$request.hwnd)
      $p.Select()
      return @{selected=$true}
    }
    'replace_text' {
      $captured=$script:CapturedRanges[[string]$request.capture_id]
      if($null -eq $captured -or $captured.hwnd -ne [long]$request.hwnd -or $captured.pid -ne [uint32]$request.pid -or $captured.runtime -ne [string]$request.runtime) { throw 'That selection expired. Capture it again before applying.' }
      $e=FindElement $request
      if($e.Current.IsPassword -or !$request.text -or ([string]$request.text).Length -gt 120) { throw 'Invalid text replacement.' }
      [void][BuddyWin]::SetForegroundWindow([IntPtr][long]$request.hwnd)
      $e.SetFocus()
      $selected=SelectedText $e
      if($null -eq $selected -or !$selected.editable -or $selected.text -cne [string]$request.original) { throw 'The editor selection changed. Capture it again before applying.' }
      $pattern=$e.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
      $range=$pattern.GetSelection()[0]
      $start=[System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start
      $end=[System.Windows.Automation.Text.TextPatternRangeEndpoint]::End
      if($captured.range.CompareEndpoints($start,$range,$start) -ne 0 -or $captured.range.CompareEndpoints($end,$range,$end) -ne 0) { throw 'The editor selection moved. Capture it again before applying.' }
      [BuddyWin]::TypeText([string]$request.text,[long]$request.hwnd,[uint32]$request.pid)
      $script:CapturedRanges.Remove([string]$request.capture_id)
      return @{applied=$true}
    }
    default { throw 'Unsupported desktop action.' }
  }
}
Emit @{ready=$true}
while($null -ne ($line=[Console]::ReadLine())) {
  $request=$null
  try {
    if($line.Length -gt 12000) { throw 'Desktop request is too large.' }
    $request=$line | ConvertFrom-Json
    $result=Dispatch $request
    Emit @{id=$request.id;ok=$true;result=$result}
  } catch {
    Emit @{id=$request.id;ok=$false;error=$_.Exception.Message}
  }
}
