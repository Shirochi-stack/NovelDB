"""Close only browsers that own the external downloader's saved profile.

Works on Windows, macOS and Linux through psutil: browser processes are matched by name, the
exact `--user-data-dir`, and their children (renderers and helpers often omit the profile flag).
Windows asks each window to close (WM_CLOSE); macOS and Linux send SIGTERM to the main browser
process, which closes it the same graceful way. Forced termination rechecks each process's
start time so a recycled PID can never take down another app.
"""

import ctypes
import json
import os
import re
import signal
import subprocess
import sys

try:
    import psutil
except ImportError:  # pragma: no cover - psutil ships with the app
    psutil = None

_WINDOWS_NAMES = ('chrome.exe', 'msedge.exe', 'chromium.exe')
# Process names on macOS and Linux; helpers such as "Google Chrome Helper (Renderer)" match by prefix.
_POSIX_PREFIXES = ('google chrome', 'chromium', 'microsoft edge', 'brave browser')
_POSIX_NAMES = ('chrome', 'chromium', 'chromium-browser', 'google-chrome', 'msedge',
                'microsoft-edge', 'brave', 'brave-browser')


def _hidden_kwargs():
    if sys.platform != 'win32':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {'startupinfo': startup,
            'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}


def _is_browser_name(name):
    name = str(name or '').lower()
    if name in _WINDOWS_NAMES or name in _POSIX_NAMES:
        return True
    return name.startswith(_POSIX_PREFIXES)


def _command_line(cmdline):
    """One string from an argument list, double-quoting arguments with spaces."""
    parts = []
    for argument in cmdline or ():
        argument = str(argument)
        parts.append(f'"{argument}"' if (' ' in argument and not argument.startswith('"')) else argument)
    return ' '.join(parts)


def _select_profile_processes(records, user_data_dir):
    target = os.path.normcase(os.path.abspath(user_data_dir)).rstrip('\\/')
    browsers = {int(record['pid']): record for record in records
                if _is_browser_name(record.get('name'))}
    pattern = re.compile(
        r'(?:^|\s)(?:"--user-data-dir=([^"]+)"|'
        r'--user-data-dir(?:=|\s+)(?:"([^"]+)"|([^\s"]+)))(?=\s|$)',
        re.IGNORECASE,
    )
    selected, other_profiles = set(), set()
    for pid, record in browsers.items():
        match = pattern.search(record.get('command') or '')
        if match:
            path = next(value for value in match.groups() if value is not None)
            if os.path.normcase(os.path.abspath(path)).rstrip('\\/') == target:
                selected.add(pid)
            else:
                other_profiles.add(pid)
    # Renderers and crash handlers can omit the profile argument.
    while True:
        children = {pid for pid, record in browsers.items()
                    if pid not in other_profiles and
                    int(record.get('parent', 0)) in selected}
        if children <= selected:
            break
        selected.update(children)
    return [record for pid, record in browsers.items() if pid in selected]


# (pid, start time) -> command line. A process's command line never changes, and reading one is
# the costly part of a scan (it opens the process), so each is read once.
_COMMANDS = {}


def _windows_browser_processes():
    """(pid, parent pid, name) of the running browser processes, from one system snapshot.

    The Toolhelp snapshot lists every process with its name and parent in a single pass of native
    calls (which do not hold up Python's other threads); psutil's walk opens each process in turn.
    """
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
            ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
            ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
            ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
            ('szExeFile', ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, wintypes.HANDLE(-1).value):
        raise OSError('process snapshot unavailable')
    found = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if _is_browser_name(entry.szExeFile):
                found.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _browser_processes():
    """(psutil process, pid, parent pid, name, start time) of each running browser process."""
    if sys.platform == 'win32':
        try:
            listed = _windows_browser_processes()
        except Exception:
            listed = None
        if listed is not None:
            for pid, parent, name in listed:
                try:
                    process = psutil.Process(pid)
                    yield process, pid, parent, name, process.create_time()
                except (psutil.Error, OSError):
                    continue
            return
    for process in psutil.process_iter(['pid', 'ppid', 'name', 'create_time']):
        info = process.info
        if _is_browser_name(info.get('name')):
            yield process, info['pid'], info.get('ppid') or 0, info.get('name') or '', info.get('create_time')


def _psutil_records():
    """Every running browser process. Only browsers are looked at closely: asking every process
    on the machine for its command line, several times a second while Enter Browser is open, held
    the interpreter long enough to make the app's window stutter."""
    records = []
    alive = set()
    for process, pid, parent, name, created in _browser_processes():
        info = {'pid': pid, 'ppid': parent, 'name': name, 'create_time': created}
        key = (info['pid'], info.get('create_time'))
        alive.add(key)
        command = _COMMANDS.get(key)
        if command is None:
            try:
                command = _command_line(process.cmdline())
            except (psutil.Error, OSError):
                command = ''
            if command:
                _COMMANDS[key] = command
        records.append({
            'pid': info['pid'],
            'parent': info.get('ppid') or 0,
            'name': info.get('name') or '',
            'command': command,
            'created': info.get('create_time'),
        })
    for key in [key for key in _COMMANDS if key not in alive]:
        del _COMMANDS[key]
    return records


def _powershell_records():
    script = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$names = @('chrome.exe', 'msedge.exe', 'chromium.exe'); "
        "$items = @(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -and $names.Contains($_.Name.ToLowerInvariant()) "
        "} | ForEach-Object { [PSCustomObject]@{ "
        "pid=$_.ProcessId; parent=$_.ParentProcessId; name=$_.Name; "
        "command=$_.CommandLine; created=$_.CreationDate.ToString('o') "
        "} }); ConvertTo-Json -InputObject $items -Compress"
    )
    output = subprocess.check_output(
        ['powershell', '-NoProfile', '-Command', script],
        text=True, encoding='utf-8', errors='replace',
        stderr=subprocess.DEVNULL, timeout=8, **_hidden_kwargs(),
    )
    data = json.loads(output or '[]')
    return data if isinstance(data, list) else [data]


def profile_processes(user_data_dir):
    if psutil is not None:
        records = _psutil_records()
    elif sys.platform == 'win32':
        records = _powershell_records()
    else:
        return []
    return _select_profile_processes(records, user_data_dir)


def _close_windows_pids(pids):
    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
    ]
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
    ]
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]

    def close(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True

    user32.EnumWindows(callback_type(close), 0)


def _main_processes(records):
    """The browser processes whose parent is not part of the same profile (the ones to signal)."""
    pids = {int(record['pid']) for record in records}
    return [record for record in records if int(record.get('parent', 0)) not in pids]


def close_profile_windows(records):
    if not records:
        return
    if sys.platform == 'win32':
        _close_windows_pids({int(record['pid']) for record in records})
        return
    for record in _main_processes(records):
        try:
            os.kill(int(record['pid']), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass


def _same_process(process, record):
    created = record.get('created')
    if isinstance(created, (int, float)):
        return abs(process.create_time() - float(created)) < 0.01
    return False


def terminate_profile_processes(records):
    if not records:
        return
    if psutil is not None and all(isinstance(r.get('created'), (int, float)) for r in records):
        for record in reversed(records):
            try:
                process = psutil.Process(int(record['pid']))
                if _same_process(process, record) and _is_browser_name(process.name()):
                    process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return
    if sys.platform != 'win32':
        return
    # Records from PowerShell carry CIM creation dates; recheck them the same way.
    entries = '; '.join(
        f"@{{pid={int(record['pid'])}; created='" +
        str(record['created']).replace("'", "''") + "'}"
        for record in reversed(records)
    )
    script = (
        "$ErrorActionPreference = 'Stop'; $expected = @(" + entries + "); "
        "foreach ($entry in $expected) { "
        "$p = Get-CimInstance Win32_Process -Filter "
        "('ProcessId = ' + $entry.pid); "
        "if ($p -and $p.CreationDate.ToString('o') -eq $entry.created -and "
        "@('chrome.exe','msedge.exe','chromium.exe').Contains("
        "$p.Name.ToLowerInvariant())) { "
        "Stop-Process -Id $entry.pid -Force -ErrorAction SilentlyContinue "
        "} }"
    )
    subprocess.run(
        ['powershell', '-NoProfile', '-Command', script], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=12, **_hidden_kwargs(),
    )
