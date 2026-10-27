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


def _psutil_records():
    records = []
    for process in psutil.process_iter(['pid', 'ppid', 'name', 'cmdline', 'create_time']):
        info = process.info
        if not _is_browser_name(info.get('name')):
            continue
        records.append({
            'pid': info['pid'],
            'parent': info.get('ppid') or 0,
            'name': info.get('name') or '',
            'command': _command_line(info.get('cmdline')),
            'created': info.get('create_time'),
        })
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
