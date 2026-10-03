"""Close only browsers that own the external downloader's saved profile."""

import ctypes
import json
import os
import re
import subprocess
import sys


def _hidden_kwargs():
    if sys.platform != 'win32':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {'startupinfo': startup,
            'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}


def _select_profile_processes(records, user_data_dir):
    target = os.path.normcase(os.path.abspath(user_data_dir)).rstrip('\\/')
    browsers = {int(record['pid']): record for record in records
                if str(record.get('name', '')).lower() in
                ('chrome.exe', 'msedge.exe', 'chromium.exe')}
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


def profile_processes(user_data_dir):
    if sys.platform != 'win32':
        return []
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
    return _select_profile_processes(json.loads(output or '[]'), user_data_dir)


def close_profile_windows(records):
    if sys.platform != 'win32':
        return
    pids = {int(record['pid']) for record in records}
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


def terminate_profile_processes(records):
    if sys.platform != 'win32' or not records:
        return
    # Recheck creation times so a recycled PID cannot terminate another app.
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
