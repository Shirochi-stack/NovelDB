"""Read an owned RBOOKS volume from the official Windows reader's rendered pages.

The browser profile supplies a short lived RBOOKS SSO ticket. The desktop reader
does the download and rendering; this module only reads its visible DOM through
Electron's local debugging interface. It never reads or decrypts reader files.
"""

import html
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup
from scripts.source_names import dumps as source_dumps


class RbooksAppError(RuntimeError):
    pass


class RbooksAppProxy:
    INSTALLER_URL = 'https://getapp.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/windows'
    EXPORT_VERSION = 3
    # Reader overlays are HTML, so native Enter and JavaScript alert handlers
    # cannot close them. Cancel a synced position jump to keep our TOC selection.
    PAGE_POPUP_SCRIPT = r"""(() => {
      const visible = el => el && el.getClientRects().length &&
        getComputedStyle(el).visibility !== 'hidden' &&
        getComputedStyle(el).display !== 'none';
      const label = el => (el.innerText || el.value || el.getAttribute('aria-label') || '')
        .trim().replace(/\s+/g, ' ');
      const controls = root => Array.from(root.querySelectorAll(
        'button, [role="button"], input[type="button"], input[type="submit"]'))
        .filter(el => visible(el) && !el.disabled);
      for (const cancel of controls(document)) {
        if (!/^(취소|Cancel)$/i.test(label(cancel))) continue;
        for (let box = cancel.parentElement; box && box !== document.body;
             box = box.parentElement) {
          const text = box.innerText || '';
          if (text.length > 1200) break;
          if (/읽던\s*페이지/.test(text) && /다른\s*기기|현재\s*페이지/.test(text) &&
              controls(box).some(el => /^(이동|Move)$/i.test(label(el)))) {
            cancel.click();
            return 'reading-position';
          }
        }
      }
      for (const box of document.querySelectorAll(
        '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog[open]')) {
        if (!visible(box)) continue;
        const buttons = controls(box);
        // Only acknowledge a single-action notice. Multi-action dialogs may
        // change the book, purchase something, or delete data.
        if (buttons.length === 1 && /^(확인|닫기|OK|Close)$/i.test(label(buttons[0]))) {
          buttons[0].click();
          return 'notice';
        }
      }
      return null;
    })()"""

    def __init__(self, log, stop_requested=lambda: False):
        self.log = log
        self.stop_requested = stop_requested
        self.port = None
        self.process = None
        self._reader_targets = {}
        self._popup_lock = threading.Lock()
        self._popup_revision = 0
        self.executable = os.path.join(
            os.environ.get('ProgramFiles', r'C:\Program Files'),
            '\u0052\u0049\u0044\u0049', '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073', '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe',
        )

    @staticmethod
    def _is_rbooks_tab(tab):
        url = urllib.parse.urlparse(urllib.parse.unquote(tab.get('url', '')))
        local = (url.scheme == 'file' or
                 (url.scheme == 'http' and
                  url.hostname in ('localhost', '127.0.0.1')))
        return (tab.get('type') == 'page' and local and
                '\u002f\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073\u002f\u0072\u0065\u0073\u006f\u0075\u0072\u0063\u0065\u0073\u002f\u0061\u0070\u0070\u002e\u0061\u0073\u0061\u0072\u002f' in url.path.lower())

    def _tabs(self, port):
        try:
            with urllib.request.urlopen(
                f'http://127.0.0.1:{port}/json/list', timeout=2
            ) as response:
                tabs = json.load(response)
            return [t for t in tabs if self._is_rbooks_tab(t)]
        except (OSError, ValueError):
            pass
        return []

    def connect(self):
        if self.port and self._tabs(self.port):
            return
        candidates = []
        configured = os.environ.get('NPIA_RBOOKS_APP_DEBUG_PORT', '')
        if configured.isdecimal():
            candidates.append(int(configured))
        app_running = False
        executables = {}
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                if (proc.info.get('name') or '').lower() != '\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe':
                    continue
                app_running = True
                try:
                    args = proc.cmdline()
                    executable = proc.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
                for arg in args or []:
                    match = re.fullmatch(r'--remote-debugging-port=(\d+)', arg)
                    if match:
                        port = int(match.group(1))
                        candidates.append(port)
                        executables[port] = executable
        except (ImportError, OSError):
            pass
        candidates.append(49318)
        for port in dict.fromkeys(candidates):
            if self._tabs(port):
                self.port = port
                self.executable = (executables.get(port) or
                                   self._find_executable() or self.executable)
                return
        if app_running:
            raise RbooksAppError(
                'Close the RBOOKS PC viewer and retry so it can start with '
                'local reader access enabled.'
            )
        if not os.path.isfile(self.executable):
            self.executable = self._find_executable() or self._install_viewer()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.process = subprocess.Popen(
            [self.executable, f'--remote-debugging-port={port}'],
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.port = port
        self._wait(lambda: bool(self._tabs(port)), 30,
                   'RBOOKS PC viewer did not start with local reader access.')

    def _find_executable(self):
        local_app_data = os.environ.get('LOCALAPPDATA')
        roots = (
            os.environ.get('ProgramFiles'),
            os.environ.get('ProgramFiles(x86)'),
            os.path.join(local_app_data, 'Programs')
            if local_app_data else None,
        )
        for root in roots:
            if not root:
                continue
            for parts in (('\u0052\u0049\u0044\u0049', '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073', '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe'),
                          ('\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073', '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe')):
                candidate = os.path.join(root, *parts)
                if os.path.isfile(candidate):
                    return candidate
        return None

    def _install_viewer(self):
        """Install RBOOKS's signed Windows viewer for the current user."""
        self.log('  [Rbooks] PC viewer is missing; downloading the official '
                 'RBOOKS installer...')
        try:
            with tempfile.TemporaryDirectory(prefix='npia-rbooks-') as folder:
                installer = os.path.join(folder, 'RBOOKS-Viewer-Setup.exe')
                request = urllib.request.Request(
                    self.INSTALLER_URL,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    final = urllib.parse.urlparse(response.geturl())
                    host = (final.hostname or '').lower()
                    if (final.scheme != 'https' or
                            host not in ('getapp.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com',
                                         'viewer-ota.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net')):
                        raise RbooksAppError(
                            'RBOOKS installer redirected outside its official '
                            'download servers.'
                        )
                    size = 0
                    with open(installer, 'wb') as target:
                        next_report = 10 * 1024 * 1024
                        while True:
                            if self.stop_requested():
                                raise RbooksAppError('Download cancelled.')
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > 250 * 1024 * 1024:
                                raise RbooksAppError('RBOOKS installer is too large.')
                            target.write(chunk)
                            if size >= next_report:
                                self.log(
                                    f'  [Rbooks] Downloaded {size // (1024 * 1024)} '
                                    'MB of the PC viewer installer...'
                                )
                                next_report += 10 * 1024 * 1024
                with open(installer, 'rb') as downloaded:
                    header = downloaded.read(2)
                if size < 1024 or header != b'MZ':
                    raise RbooksAppError('RBOOKS download is not a Windows installer.')
                env = os.environ.copy()
                env['NPIA_RBOOKS_INSTALLER_PATH'] = installer
                signature = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-NonInteractive',
                     '-Command',
                     '$s=Get-AuthenticodeSignature -LiteralPath '
                     '$env:NPIA_RBOOKS_INSTALLER_PATH; '
                     'Write-Output $s.Status; '
                     'Write-Output $s.SignerCertificate.Subject'],
                    env=env, capture_output=True, text=True, timeout=30,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                lines = signature.stdout.strip().splitlines()
                if (signature.returncode != 0 or len(lines) < 2 or
                        lines[0].strip() != 'Valid' or
                        '\u0072\u0069\u0064\u0069\u0020\u0063\u006f\u0072\u0070\u006f\u0072\u0061\u0074\u0069\u006f\u006e' not in lines[1].lower()):
                    raise RbooksAppError(
                        'RBOOKS installer signature could not be verified.'
                    )
                self.log('  [Rbooks] Official installer verified; installing '
                         'the PC viewer...')
                installed = subprocess.run(
                    [installer, '/S', '/currentuser'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=240,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if installed.returncode != 0:
                    raise RbooksAppError(
                        f'RBOOKS installer exited with code '
                        f'{installed.returncode}.'
                    )
        except RbooksAppError:
            raise
        except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            raise RbooksAppError(
                f'Automatic RBOOKS PC viewer installation failed: {exc}'
            ) from exc
        executable = self._find_executable()
        if not executable:
            raise RbooksAppError(
                'RBOOKS installer finished, but the PC viewer executable '
                'was not found.'
            )
        self.log('  [Rbooks] PC viewer installed; opening the owned volume...')
        return executable

    def _wait(self, check, timeout, message, progress=None):
        end = time.monotonic() + timeout
        next_report = time.monotonic() + 10
        while time.monotonic() < end:
            if self.stop_requested():
                raise RbooksAppError('Download cancelled.')
            result = check()
            if result:
                return result
            if progress and time.monotonic() >= next_report:
                self.log(progress)
                next_report = time.monotonic() + 10
            time.sleep(0.35)
        raise RbooksAppError(message)

    @staticmethod
    def _reader_log_snapshot():
        log_dir = os.path.join(os.environ.get('APPDATA', ''),
                               '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073', 'Logs')
        try:
            logs = [os.path.join(log_dir, name) for name in os.listdir(log_dir)
                    if name.endswith('.log')]
            path = max(logs, key=os.path.getmtime)
            return path, os.path.getsize(path)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _reader_cache_error(snapshot):
        if not snapshot:
            return False
        path, offset = snapshot
        try:
            with open(path, 'rb') as reader:
                reader.seek(offset)
                new_lines = reader.read(65536)
            return (b'Invalid or unsupported zip format' in new_lines or
                    b'No END header found' in new_lines)
        except OSError:
            return False

    def _wait_for_viewer(self, snapshot, reopen=None, title=None):
        # A library click can start a download without opening its reader.
        # Slow disks/networks need more time, followed by another open click.
        end = time.monotonic() + 180
        next_report = time.monotonic() + 10
        while time.monotonic() < end:
            if self.stop_requested():
                raise RbooksAppError('Download cancelled.')
            tab = self._tab('Viewer')
            if tab:
                matches = not title or self._evaluate_target(tab, """(() => {
                  const normalize = text => text.replace(/\\s+/g, '').trim();
                  const expected = normalize(%s);
                  return (document.body.innerText || '').split('\\n')
                    .some(line => normalize(line) === expected);
                })()""" % source_dumps(title))
                if matches:
                    self._reader_targets['Viewer'] = tab.get('id')
                    self.log('  [Rbooks] Confirmed the requested volume in '
                             'the PC viewer.')
                    return
            if self._reader_cache_error(snapshot):
                raise RbooksAppError(
                    'RBOOKS could not open its cached copy of this volume. '
                    'Remove and download the volume again in the RBOOKS PC app.'
                )
            if time.monotonic() >= next_report:
                self.log('  [Rbooks] Waiting for the RBOOKS PC viewer to open '
                         'the owned volume...')
                if reopen:
                    reopen()
                next_report = time.monotonic() + 10
            time.sleep(0.35)
        raise RbooksAppError(
            'RBOOKS PC viewer did not open the owned volume after 180 seconds. '
            'Check its download status or popup; the saved browser login '
            'and library handoff already succeeded.'
        )

    def _tab(self, suffix):
        target_id = self._reader_targets.get(suffix)
        return next((t for t in self._tabs(self.port)
                     if t.get('type') == 'page' and
                     (not target_id or t.get('id') == target_id) and
                     t.get('url', '').endswith('?' + suffix)), None)

    def _evaluate(self, suffix, expression):
        tab = self._tab(suffix)
        if not tab:
            return None
        return self._evaluate_target(tab, expression)

    def _evaluate_target(self, tab, expression):
        import websocket
        ws = websocket.create_connection(
            tab['webSocketDebuggerUrl'], timeout=12, suppress_origin=True
        )
        try:
            ws.send(json.dumps({
                'id': 1, 'method': 'Runtime.evaluate',
                'params': {'expression': expression, 'returnByValue': True,
                           'awaitPromise': True},
            }))
            while True:
                reply = json.loads(ws.recv())
                if reply.get('id') != 1:
                    continue
                if reply.get('error'):
                    raise RbooksAppError('RBOOKS viewer command failed: ' +
                                         str(reply['error'].get('message')))
                result = reply.get('result') or {}
                if result.get('exceptionDetails'):
                    raise RbooksAppError('RBOOKS viewer interaction failed: ' +
                                       str(result['exceptionDetails'].get('text')))
                return (result.get('result') or {}).get('value')
        finally:
            ws.close()

    @staticmethod
    def _native_rbooks_dialog():
        """Find a visible standard dialog owned by RBOOKS, even in background."""
        if sys.platform != 'win32':
            return None
        try:
            import ctypes
            from ctypes import wintypes
            import psutil
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetClassNameW.argtypes = [wintypes.HWND,
                                             wintypes.LPWSTR, ctypes.c_int]
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            windows = [user32.GetForegroundWindow()]
            callback_type = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def collect(hwnd, _):
                if hwnd not in windows:
                    windows.append(hwnd)
                return True

            user32.EnumWindows(callback_type(collect), 0)
            for hwnd in windows:
                if not hwnd or not user32.IsWindowVisible(hwnd):
                    continue
                window_class = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, window_class, len(window_class))
                if window_class.value != '#32770':
                    continue
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    if psutil.Process(pid.value).name().lower() == '\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe':
                        return hwnd
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
        except Exception:
            return None

    @staticmethod
    def _press_enter_on_dialog(hwnd):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                            wintypes.WPARAM, wintypes.LPARAM]
            return bool(user32.PostMessageW(hwnd, 0x100, 0x0D, 0) and
                        user32.PostMessageW(hwnd, 0x101, 0x0D, 0))
        except Exception:
            return False

    def _accept_js_dialog(self):
        """Accept a RBOOKS reader JavaScript dialog through its own CDP tab."""
        if not self.port:
            return False
        try:
            tabs = self._tabs(self.port)
        except Exception:
            return False
        for tab in tabs:
            url = urllib.parse.unquote(tab.get('url', ''))
            if (not self._is_rbooks_tab(tab) or
                    not url.endswith(('?Viewer', '?Books', '?Login'))):
                continue
            ws_url = tab.get('webSocketDebuggerUrl')
            if not ws_url:
                continue
            try:
                import websocket
                ws = websocket.create_connection(ws_url, timeout=1,
                                                 suppress_origin=True)
                try:
                    ws.send(json.dumps({
                        'id': 1, 'method': 'Page.handleJavaScriptDialog',
                        'params': {'accept': True},
                    }))
                    while True:
                        reply = json.loads(ws.recv())
                        if reply.get('id') == 1:
                            if not reply.get('error'):
                                return True
                            break
                finally:
                    ws.close()
            except Exception:
                continue
        return False

    def _dismiss_viewer_popups(self, stop):
        last_native = None
        last_press = 0
        warned = set()
        while not stop.is_set():
            try:
                hwnd = self._native_rbooks_dialog()
                now = time.monotonic()
                if hwnd and (hwnd != last_native or now - last_press >= 3):
                    if self._press_enter_on_dialog(hwnd):
                        self.log('  [Rbooks] Pressed Enter on a RBOOKS PC viewer popup.')
                    elif hwnd not in warned:
                        self.log('  [Rbooks] Windows could not dismiss a RBOOKS '
                                 'popup. If RBOOKS is running as administrator, '
                                 'close it and restart it normally so the '
                                 'downloader can control its dialogs.')
                        warned.add(hwnd)
                    last_native, last_press = hwnd, now
                elif not hwnd:
                    last_native = None
                if self._accept_js_dialog():
                    self.log('  [Rbooks] Accepted a RBOOKS PC viewer popup.')
                self._dismiss_page_popup()
            except Exception:
                pass
            stop.wait(0.8)

    def _dismiss_page_popup(self):
        """Dismiss overlays only in the reader bound to the requested volume."""
        if not self._reader_targets.get('Viewer'):
            return False
        with self._popup_lock:
            try:
                kind = self._evaluate('Viewer', self.PAGE_POPUP_SCRIPT)
            except Exception:
                return False
            if kind not in ('reading-position', 'notice'):
                return False
            self._popup_revision += 1
            if kind == 'reading-position':
                self.log('  [Rbooks] Cancelled the synced reading-position popup; '
                         'keeping the requested source page.')
            else:
                self.log('  [Rbooks] Dismissed an in-page reader notice.')
            return True

    def _sso(self, context):
        page = context.new_page()
        try:
            page.goto('https://account.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/',
                      wait_until='domcontentloaded', timeout=30000)
            otp = page.evaluate("""async () => {
              const r = await fetch('/sso/otp', {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams({redirectUri: '\u0072\u0069\u0064\u0069://download'})
              });
              return r.ok ? (await r.json()).otp : null;
            }""")
            if not otp:
                raise RbooksAppError(
                    'Saved browser login could not authorize the RBOOKS PC viewer.'
                )
            return otp
        finally:
            page.close()

    def _open_owned_book(self, context, book_id, title):
        self._reader_targets.clear()
        otp = self._sso(context)
        payload = json.dumps({'b_ids': [str(book_id)]}, separators=(',', ':'))
        link = ('\u0072\u0069\u0064\u0069://download?sso_otp=' + urllib.parse.quote(otp) +
                '&payload=' + urllib.parse.quote(payload))
        try:
            # Windows may display "Get an app to open this 'rbooks' link" even
            # when the RBOOKS executable is installed. Passing the URI directly
            # to that executable uses Electron's own second-instance handler.
            subprocess.Popen(
                [self.executable, link],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RbooksAppError(
                'Windows could not launch the RBOOKS PC viewer.'
            ) from exc
        finally:
            del otp, link
        self._wait(lambda: self._tab('Books'), 30,
                   'RBOOKS PC viewer did not open its library after the '
                   'sign-in handoff.',
                   '  [Rbooks] Waiting for the RBOOKS library to open...')
        self.log('  [Rbooks] RBOOKS library opened; locating the owned volume...')
        self._close_reader_windows()
        title_js = source_dumps(title, ensure_ascii=False)
        book_id_js = source_dumps(str(book_id))
        def click_book():
            return self._evaluate('Books', """(() => {
              const title = %s;
              const bookId = %s;
              const matches = [...document.querySelectorAll('*')].filter(e =>
                e.textContent?.trim() === title &&
                ![...e.children].some(c => c.textContent?.trim() === title));
              if (!matches.length) return false;
              for (const match of matches) {
                for (let node = match, depth = 0;
                     node && depth < 7; node = node.parentElement, depth++) {
                  const images = [...node.querySelectorAll('img')];
                  if (images.length > 1) break;
                  const image = images.find(img =>
                    (img.currentSrc || img.src || '').includes(
                      '/cover/' + bookId + '/'));
                  if (image) {
                    match.click();
                    return true;
                  }
                }
              }
              return false;
            })()""" % (title_js, book_id_js))
        snapshot = self._reader_log_snapshot()
        self._wait(click_book, 120,
                   'Owned volume did not appear in the RBOOKS PC library.',
                   '  [Rbooks] Waiting for the owned volume to appear in '
                   'the RBOOKS library...')
        self.log('  [Rbooks] Found the owned volume; opening the reader...')
        self._wait_for_viewer(snapshot, reopen=click_book, title=title)
        # The library supplies a 165px thumbnail, never the export cover.
        return (
            f'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/cover/{book_id}/large'
        )

    def _close_reader_windows(self):
        """Retire old reader/TOC windows while keeping the library open."""
        tabs = [tab for tab in self._tabs(self.port)
                if tab.get('url', '').endswith(('?Viewer', '?TocModal'))]
        old_ids = {tab['id'] for tab in tabs}
        for tab in tabs:
            try:
                self._evaluate_target(tab, 'window.close()')
            except Exception:
                # Closing a window can disconnect its debugger before a reply.
                pass
        if old_ids:
            self._wait(lambda: not any(tab.get('id') in old_ids
                                      for tab in self._tabs(self.port)), 15,
                       'Previous RBOOKS reader windows did not close. '
                       'Export stopped to avoid reading the wrong volume.')
        self._reader_targets.clear()

    def _toc_rows(self):
        return self._evaluate('TocModal', """(() => {
          const groups = [...document.querySelectorAll('.simplebar-content')];
          const group = groups.find(e => [...e.children].some(c =>
            /^\\s*\\d+\\s*\\n/.test(c.innerText || '')));
          if (!group) return [];
          return [...group.children].map((row, index) => {
            const text = (row.innerText || '').trim();
            const match = text.match(/^(\\d+)\\s*\\n([\\s\\S]+)/);
            return match ? {index, page: Number(match[1]),
                            title: match[2].trim()} : null;
          }).filter(Boolean);
        })()""") or []

    def _front_sections(self):
        return self._evaluate('Viewer', """(() => {
          const sections = [];
          for (const frame of document.querySelectorAll('iframe')) {
            if (!frame.getClientRects().length ||
                getComputedStyle(frame).display === 'none') continue;
            const doc = frame.contentDocument;
            const page = doc?.querySelector('\u0072\u0069\u0064\u0069\u002d\u0070\u0061\u0067\u0065\u002d\u0063\u006f\u006e\u0074\u0061\u0069\u006e\u0065\u0072[data-front]');
            const content = doc?.querySelector('\u0072\u0069\u0064\u0069\u002d\u0063\u006f\u006c\u0075\u006d\u006e\u002d\u0063\u006f\u006e\u0074\u0061\u0069\u006e\u0065\u0072');
            if (!page || !content) continue;
            const index = page.getAttribute('data-spine-index');
            if (!/^\\d+$/.test(index || '')) continue;
            sections.push({spine: Number(index),
                           offset: page.style.cssText + ':' + doc.body.scrollTop,
                           ready: Number(doc.body.style.opacity || 1) === 1,
                           html: content.innerHTML, text: content.innerText});
          }
          return sections;
        })()""") or []

    @staticmethod
    def _clean_section(raw):
        soup = BeautifulSoup(raw, 'html.parser')
        for element in soup.find_all(['script', 'iframe', 'object', 'embed']):
            element.decompose()
        for element in soup.find_all(True):
            for attribute in list(element.attrs):
                if attribute.lower().startswith('on'):
                    del element[attribute]
        return str(soup)

    @staticmethod
    def _select_front_section(frames):
        # A two-page spread can hold the end of one spine and the start of
        # the next. The highest front spine is the section just selected.
        return max((x for x in frames if x.get('html')),
                   key=lambda x: x['spine'], default=None)

    def _images(self, section_html):
        urls = list(dict.fromkeys(html.unescape(url) for url in re.findall(
            r'<img\b[^>]*\bsrc=["\']([^"\']+)', section_html, re.I
        )))
        if not urls:
            return []
        direct = [{'url': url, 'data': url} for url in urls
                  if url.startswith('data:image/')]
        urls = [url for url in urls if not url.startswith('data:image/')]
        if not urls:
            return direct
        js = """(async () => {
          const urls = %s;
          const frames = [...document.querySelectorAll('iframe')];
          const out = [];
          for (const url of urls) {
            let data = null;
            for (const frame of frames) {
              try {
                const response = await frame.contentWindow.fetch(url);
                if (!response.ok) continue;
                const blob = await response.blob();
                data = await new Promise(resolve => {
                  const reader = new FileReader();
                  reader.onload = () => resolve(reader.result);
                  reader.onerror = () => resolve(null);
                  reader.readAsDataURL(blob);
                });
                if (data) break;
              } catch (_) {}
            }
            out.push({url, data});
          }
          return out;
        })()""" % json.dumps(urls)
        return direct + (self._evaluate('Viewer', js) or [])

    def _reader_page(self):
        value = self._evaluate('Viewer', """(() =>
          document.querySelector('input[type="range"]')?.value
        )()""")
        return int(value) if str(value).isdecimal() else -1

    @staticmethod
    def _frame_signature(frames):
        return tuple((frame['spine'], frame.get('offset'), frame['html'])
                     for frame in frames)

    def _navigate_reader_page(self, target):
        """Move the page slider and wait for its rendered frames to catch up."""
        previous_page = self._reader_page()
        previous = self._frame_signature(self._front_sections())
        expected = self._evaluate('Viewer', """(() => {
          const slider = document.querySelector('input[type="range"]');
          if (!slider) return null;
          const step = Number(slider.step) || 1;
          const target = Math.max(Number(slider.min) || 0,
            Math.min(Number(slider.max), Math.floor(%d / step) * step));
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
            .set.call(slider, String(target));
          slider.dispatchEvent(new Event('input', {bubbles: true}));
          slider.dispatchEvent(new Event('change', {bubbles: true}));
          return target;
        })()""" % target)
        if expected is None:
            raise RbooksAppError('RBOOKS viewer page control was not found.')
        stable = None
        stable_since = 0
        popup_revision = self._popup_revision

        def rendered():
            nonlocal stable, stable_since, popup_revision
            self._dismiss_page_popup()
            if popup_revision != self._popup_revision:
                popup_revision = self._popup_revision
                stable = None
            frames = self._front_sections()
            signature = self._frame_signature(frames)
            if (self._reader_page() != expected or not frames or
                    not all(frame.get('ready', True) for frame in frames) or
                    (expected != previous_page and signature == previous)):
                stable = None
                return None
            if signature != stable:
                stable, stable_since = signature, time.monotonic()
                return None
            return frames if time.monotonic() - stable_since >= 0.7 else None

        return self._wait(rendered, 20,
                          f'RBOOKS page {expected + 1} did not finish rendering.')

    def _front_matter(self, first_chapter_page, first_spine=None):
        """Read the source cover and pages omitted from the reader's TOC menu."""
        if first_spine is None:
            first_frames = self._navigate_reader_page(first_chapter_page - 1)
            first_section = self._select_front_section(first_frames)
            if not first_section:
                raise RbooksAppError('RBOOKS first chapter did not render.')
            first_spine = first_section['spine']
        pages = []
        images = {}
        cover_data = ''
        seen = set()
        turns = 0
        target = 0
        while target < first_chapter_page - 1:
            if self.stop_requested():
                raise RbooksAppError('Download cancelled.')
            frames = self._navigate_reader_page(target)
            for frame in sorted(frames, key=lambda item: item['spine']):
                spine = frame['spine']
                if spine in seen or spine >= first_spine:
                    continue
                seen.add(spine)
                content = self._clean_section(frame['html'])
                soup = BeautifulSoup(content, 'html.parser')
                cover = soup.select_one('.cover-image img, img[alt="cover"]')
                if cover and not cover_data:
                    cover_images = self._images(content)
                    cover_data = next(
                        (image.get('data') for image in cover_images
                         if image.get('url') == cover.get('src')
                         and image.get('data')), ''
                    )
                    if not cover_data:
                        raise RbooksAppError('RBOOKS source cover could not be read.')
                    cover.decompose()
                    content = str(soup)
                if not soup.get_text(strip=True):
                    continue
                for image in self._images(content):
                    if not image.get('data'):
                        raise RbooksAppError(
                            'RBOOKS front-matter image could not be read.'
                        )
                    images[image['url']] = image['data']
                pages.append({'spine': spine, 'html': content})
            step = self._evaluate('Viewer',
                "Number(document.querySelector('input[type=range]')?.step) || 1")
            target = self._reader_page() + max(1, int(step or 1))
            turns += 1
            if turns > 80:
                raise RbooksAppError('RBOOKS front matter is unexpectedly long.')
        return pages, images, cover_data

    def _read_front_matter(self, first_chapter_page, first_spine=None):
        for attempt in range(2):
            try:
                result = (self._front_matter(first_chapter_page)
                          if first_spine is None else
                          self._front_matter(first_chapter_page, first_spine))
                self.log(f'  [Rbooks] Read {len(result[0])} source '
                         'front-matter page(s).')
                return result
            except RbooksAppError as exc:
                if self.stop_requested() or attempt:
                    raise
                self.log(f'  [Rbooks] Retrying source opening pages: {exc}')

    @staticmethod
    def _part_heading(front_pages):
        for index, page in enumerate(front_pages, 1):
            soup = BeautifulSoup(page['html'], 'html.parser')
            for node in soup.select('.mtitle-h1-subtitle, h1.subtitle'):
                title = node.get_text(' ', strip=True)
                if re.search(r'\d+\s*부', title):
                    return title, f'rbooks-front-{index}'
        return '', ''

    @staticmethod
    def _link_printed_contents(content, chapter_titles):
        soup = BeautifulSoup(content, 'html.parser')
        normalized = {
            re.sub(r'\s+', '', title): f'#rbooks-section-{index}'
            for index, title in enumerate(chapter_titles, 1)
        }
        for paragraph in soup.select('.contents-body p'):
            label = re.sub(r'\s+', '', paragraph.get_text(' ', strip=True))
            href = normalized.get(label)
            if not href:
                continue
            link = soup.new_tag('a', href=href)
            for child in list(paragraph.contents):
                link.append(child.extract())
            paragraph.append(link)
        return str(soup)

    def _open_toc_row(self, index):
        return self._evaluate('TocModal', """(() => {
          const group = [...document.querySelectorAll('.simplebar-content')]
            .find(e => [...e.children].some(c =>
              /^\\s*\\d+\\s*\\n/.test(c.innerText || '')));
          const row = group?.children[%d];
          if (!row) return false;
          row.click(); return true;
        })()""" % index)

    @staticmethod
    def _normalized_label(label):
        return re.sub(r'[\s\u200b\u2060\u2063\ufeff]+', '', label)

    @classmethod
    def _section_matches_title(cls, section, title):
        soup = BeautifulSoup(section.get('html') or '', 'html.parser')
        expected = cls._normalized_label(title)
        headings = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        # Some sources use a styled paragraph instead of a heading element.
        if not headings:
            headings = soup.find_all(['p', 'div'], limit=5)
        return any(cls._normalized_label(node.get_text()) == expected
                   for node in headings)

    @classmethod
    def _section_has_content(cls, section, title):
        soup = BeautifulSoup(section.get('html') or '', 'html.parser')
        expected = cls._normalized_label(title)
        for node in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div']):
            if cls._normalized_label(node.get_text()) == expected:
                node.decompose()
        return bool(cls._normalized_label(soup.get_text()) or soup.find('img'))

    def _read_toc_section(self, row, seen):
        """Use the app's own TOC anchor, then verify the actual source heading."""
        for attempt in range(2):
            try:
                if not self._open_toc_row(row['index']):
                    raise RbooksAppError('Could not select source section: ' +
                                         row['title'])
                stable = None
                stable_since = 0
                popup_revision = self._popup_revision

                def rendered():
                    nonlocal stable, stable_since, popup_revision
                    self._dismiss_page_popup()
                    if popup_revision != self._popup_revision:
                        popup_revision = self._popup_revision
                        stable = None
                    matching = [frame for frame in self._front_sections()
                                if frame.get('ready', True) and
                                frame['spine'] not in seen and
                                self._section_matches_title(frame, row['title']) and
                                self._section_has_content(frame, row['title'])]
                    section = self._select_front_section(matching)
                    if not section:
                        stable = None
                        return None
                    signature = self._frame_signature([section])
                    if signature != stable:
                        stable, stable_since = signature, time.monotonic()
                        return None
                    return (section if time.monotonic() - stable_since >= 0.7
                            else None)

                return self._wait(
                    rendered, 20, 'RBOOKS source section did not match its TOC '
                    'or repeated an earlier section: ' + row['title'],
                    '  [Rbooks] Waiting to verify source section: ' + row['title'])
            except RbooksAppError as exc:
                if self.stop_requested() or attempt:
                    raise
                self.log(f'  [Rbooks] Retrying source section: {exc}')

    @staticmethod
    def _toc_signature(rows):
        return tuple((row['index'], row['page'], row['title']) for row in rows)

    @classmethod
    def is_verified_export(cls, result):
        proof = result.get('_rbooksVerification') or {}
        count = proof.get('expectedSections', 0)
        if (result.get('_rbooksAppExportVersion') != cls.EXPORT_VERSION or
                proof.get('complete') is not True or not proof.get('bookId') or
                not isinstance(count, int) or count <= 0 or
                proof.get('verifiedSections') != count):
            return False
        soup = BeautifulSoup(result.get('contentHtml') or '', 'html.parser')
        return len(soup.select('.rbooks-volume-section')) == count

    def _verify_sections(self, book_id, rows, sections, seen):
        if (len(sections) != len(rows) or len(seen) != len(rows) or
                [title for title, _ in sections] !=
                [row['title'] for row in rows] or
                any(not self._section_matches_title({'html': content}, title) or
                    not self._section_has_content({'html': content}, title)
                    for title, content in sections)):
            raise RbooksAppError('RBOOKS export is incomplete; source sections '
                                 'do not match the full table of contents.')
        if self._toc_signature(self._toc_rows()) != self._toc_signature(rows):
            raise RbooksAppError('RBOOKS table of contents changed during '
                                 'export. Retry the volume.')
        self.log(f'  [Rbooks] Completeness verified for volume {book_id}: '
                 f'{len(sections)}/{len(rows)} source TOC sections, '
                 'with matching headings and no repeated sections.')
        return {'bookId': str(book_id), 'expectedSections': len(rows),
                'verifiedSections': len(sections), 'complete': True}

    def extract(self, context, book_id, title, chapter_url):
        stop = threading.Event()
        watcher = threading.Thread(target=self._dismiss_viewer_popups,
                                   args=(stop,), daemon=True)
        watcher.start()
        try:
            return self._extract(context, book_id, title, chapter_url)
        finally:
            stop.set()
            watcher.join(timeout=2)

    def _extract(self, context, book_id, title, chapter_url):
        self.connect()
        self.log(f'  [Rbooks] Opening owned {title} in the RBOOKS PC viewer...')
        cover_url = self._open_owned_book(context, book_id, title)
        self._wait(lambda: self._evaluate('Viewer', """(() => {
          const b = [...document.querySelectorAll('button')].find(e =>
            e.innerText?.trim() === '더보기');
          if (!b) return false;
          b.click();
          return true;
        })()"""), 30, 'RBOOKS viewer menu was not found.')
        self._wait(lambda: self._evaluate('Viewer', """(() => {
          const e = [...document.querySelectorAll('[role="group"]')].find(x =>
            x.innerText?.trim() === '목차');
          if (!e) return false;
          e.click(); return true;
        })()"""), 10, 'RBOOKS table of contents was not found.')
        toc = self._wait(lambda: self._tab('TocModal'), 10,
                         'RBOOKS table of contents did not open.')
        self._reader_targets['TocModal'] = toc.get('id')
        rows = self._wait(self._toc_rows, 15,
                          'RBOOKS table of contents is empty.')
        first_section = self._read_toc_section(rows[0], set())
        front_pages, front_images, cover_data = self._read_front_matter(
            rows[0]['page'], first_section['spine'])
        sections = []
        seen = set()
        image_data = dict(front_images)
        for row in rows:
            section = self._read_toc_section(row, seen)
            seen.add(section['spine'])
            content = self._clean_section(section['html'])
            if not BeautifulSoup(content, 'html.parser').get_text(strip=True):
                raise RbooksAppError('RBOOKS section has no text: ' + row['title'])
            for item in self._images(content):
                if not item.get('data'):
                    raise RbooksAppError('RBOOKS section image could not be read: '
                                       + row['title'])
                image_data[item['url']] = item['data']
            sections.append((row['title'], content))
            self.log(f'  [Rbooks] Verified section {len(sections)}/{len(rows)}: '
                     + row['title'])
        verification = self._verify_sections(book_id, rows, sections, seen)
        part_title, part_id = self._part_heading(front_pages)
        chapter_points = [
            {'title': section_title, 'id': f'rbooks-section-{i}'}
            for i, (section_title, _) in enumerate(sections, 1)
        ]
        front_body = ''.join(
            '<div class="rbooks-front-section" id="rbooks-front-%d">%s</div>' % (
                index,
                self._link_printed_contents(
                    page['html'], [section_title for section_title, _ in sections]
                ),
            )
            for index, page in enumerate(front_pages, 1)
        )
        chapter_body = ''.join(
            '<div class="rbooks-volume-section" id="rbooks-section-%d">%s%s'
            '</div>' % (
                i,
                '' if BeautifulSoup(content, 'html.parser').find(
                    ['h1', 'h2', 'h3']) else '<h2>' +
                html.escape(section_title) + '</h2>',
                content,
            )
            for i, (section_title, content) in enumerate(sections, 1)
        )
        body = front_body + chapter_body
        image_extensions = {
            'jpeg': 'jpg', 'png': 'png', 'gif': 'gif',
            'webp': 'webp', 'avif': 'avif', 'svg+xml': 'svg',
        }
        images = []
        for i, (url, data) in enumerate(image_data.items(), 1):
            mime = data.split(';', 1)[0].split('/')[-1].lower()
            extension = image_extensions.get(mime)
            if not extension:
                raise RbooksAppError('Unsupported RBOOKS section image type: '
                                   + mime)
            images.append({
                'url': url, 'data': data,
                'name': f'rbooks-{book_id}-{i:03d}.{extension}',
            })
        return {
            '_rbooksAppExportVersion': self.EXPORT_VERSION,
            '_rbooksVerification': verification,
            '_rbooksAppHasSourceCover': bool(cover_data),
            'chapterName': title,
            'sourceChapterName': title,
            'chapterUrl': chapter_url,
            'coverUrl': cover_url,
            '_coverData': cover_data,
            'contentHtml': '<div class="rbooks-content">' + body + '</div>',
            'contentText': '\n'.join(
                BeautifulSoup(page['html'], 'html.parser').get_text(
                    '\n', strip=True
                ) for page in front_pages
            ) + '\n' + '\n'.join(
                BeautifulSoup(content, 'html.parser').get_text(
                    '\n', strip=True
                ) for _, content in sections
            ),
            'tocSections': (
                [{'title': part_title, 'id': part_id,
                  'children': chapter_points}]
                if part_title else chapter_points
            ),
            'contentCss': (
                '.rbooks-content p { margin: 0 0 .75em; line-height: 1.7; } '
                '.rbooks-content img { max-width: 100%; height: auto; } '
                '.rbooks-front-section, .rbooks-volume-section { '
                'page-break-after: always; } '
                '.rbooks-front-section .mtitle-container, '
                '.rbooks-front-section .title-container { '
                'min-height: 75vh; display: flex; align-items: center; '
                'justify-content: center; text-align: center; } '
                '.rbooks-front-section .subtitle, '
                '.rbooks-front-section .mtitle-h1-subtitle { '
                'font-size: 1.2em; margin-top: 1.5em; } '
                '.rbooks-front-section .contents-header { '
                'font-size: 1.3em; font-weight: bold; margin-top: 2em; } '
                '.rbooks-front-section .contents-body p { margin: .5em 0; } '
                '.rbooks-front-section .contents-body a { '
                'color: inherit; text-decoration: none; }'
            ),
            'images': images,
        }
