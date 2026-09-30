"""Read an owned RIDI volume from the official Windows reader's rendered pages.

The browser profile supplies a short lived RIDI SSO ticket. The desktop reader
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


class RidiAppError(RuntimeError):
    pass


class RidiAppProxy:
    INSTALLER_URL = 'https://getapp.ridibooks.com/windows'

    def __init__(self, log, stop_requested=lambda: False):
        self.log = log
        self.stop_requested = stop_requested
        self.port = None
        self.process = None
        self.executable = os.path.join(
            os.environ.get('ProgramFiles', r'C:\Program Files'),
            'RIDI', 'Ridibooks', 'Ridibooks.exe',
        )

    def _tabs(self, port):
        try:
            with urllib.request.urlopen(
                f'http://127.0.0.1:{port}/json/list', timeout=2
            ) as response:
                tabs = json.load(response)
            if any(t.get('type') == 'page' and
                   '/RIDI/Ridibooks/resources/app.asar/' in
                   urllib.parse.unquote(t.get('url', '')) for t in tabs):
                return tabs
        except (OSError, ValueError):
            pass
        return []

    def connect(self):
        if self.port and self._tabs(self.port):
            return
        candidates = []
        configured = os.environ.get('NPIA_RIDI_APP_DEBUG_PORT', '')
        if configured.isdecimal():
            candidates.append(int(configured))
        try:
            import psutil
            app_running = False
            for proc in psutil.process_iter(['name', 'cmdline']):
                if (proc.info.get('name') or '').lower() != 'ridibooks.exe':
                    continue
                app_running = True
                for arg in proc.info.get('cmdline') or []:
                    match = re.fullmatch(r'--remote-debugging-port=(\d+)', arg)
                    if match:
                        candidates.append(int(match.group(1)))
        except Exception:
            app_running = False
        candidates.append(49318)
        for port in dict.fromkeys(candidates):
            if self._tabs(port):
                self.port = port
                return
        if app_running:
            raise RidiAppError(
                'Close the RIDI PC viewer and retry so it can start with '
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
                   'RIDI PC viewer did not start with local reader access.')

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
            for parts in (('RIDI', 'Ridibooks', 'Ridibooks.exe'),
                          ('Ridibooks', 'Ridibooks.exe')):
                candidate = os.path.join(root, *parts)
                if os.path.isfile(candidate):
                    return candidate
        return None

    def _install_viewer(self):
        """Install RIDI's signed Windows viewer for the current user."""
        self.log('  [Ridi] PC viewer is missing; downloading the official '
                 'RIDI installer...')
        try:
            with tempfile.TemporaryDirectory(prefix='npia-ridi-') as folder:
                installer = os.path.join(folder, 'RIDI-Viewer-Setup.exe')
                request = urllib.request.Request(
                    self.INSTALLER_URL,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    final = urllib.parse.urlparse(response.geturl())
                    host = (final.hostname or '').lower()
                    if (final.scheme != 'https' or
                            host not in ('getapp.ridibooks.com',
                                         'viewer-ota.ridicdn.net')):
                        raise RidiAppError(
                            'RIDI installer redirected outside its official '
                            'download servers.'
                        )
                    size = 0
                    with open(installer, 'wb') as target:
                        next_report = 10 * 1024 * 1024
                        while True:
                            if self.stop_requested():
                                raise RidiAppError('Download cancelled.')
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > 250 * 1024 * 1024:
                                raise RidiAppError('RIDI installer is too large.')
                            target.write(chunk)
                            if size >= next_report:
                                self.log(
                                    f'  [Ridi] Downloaded {size // (1024 * 1024)} '
                                    'MB of the PC viewer installer...'
                                )
                                next_report += 10 * 1024 * 1024
                with open(installer, 'rb') as downloaded:
                    header = downloaded.read(2)
                if size < 1024 or header != b'MZ':
                    raise RidiAppError('RIDI download is not a Windows installer.')
                env = os.environ.copy()
                env['NPIA_RIDI_INSTALLER_PATH'] = installer
                signature = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-NonInteractive',
                     '-Command',
                     '$s=Get-AuthenticodeSignature -LiteralPath '
                     '$env:NPIA_RIDI_INSTALLER_PATH; '
                     'Write-Output $s.Status; '
                     'Write-Output $s.SignerCertificate.Subject'],
                    env=env, capture_output=True, text=True, timeout=30,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                lines = signature.stdout.strip().splitlines()
                if (signature.returncode != 0 or len(lines) < 2 or
                        lines[0].strip() != 'Valid' or
                        'ridi corporation' not in lines[1].lower()):
                    raise RidiAppError(
                        'RIDI installer signature could not be verified.'
                    )
                self.log('  [Ridi] Official installer verified; installing '
                         'the PC viewer...')
                installed = subprocess.run(
                    [installer, '/S', '/currentuser'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=240,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if installed.returncode != 0:
                    raise RidiAppError(
                        f'RIDI installer exited with code '
                        f'{installed.returncode}.'
                    )
        except RidiAppError:
            raise
        except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            raise RidiAppError(
                f'Automatic RIDI PC viewer installation failed: {exc}'
            ) from exc
        executable = self._find_executable()
        if not executable:
            raise RidiAppError(
                'RIDI installer finished, but the PC viewer executable '
                'was not found.'
            )
        self.log('  [Ridi] PC viewer installed; opening the owned volume...')
        return executable

    def _wait(self, check, timeout, message, progress=None):
        end = time.monotonic() + timeout
        next_report = time.monotonic() + 10
        while time.monotonic() < end:
            if self.stop_requested():
                raise RidiAppError('Download cancelled.')
            result = check()
            if result:
                return result
            if progress and time.monotonic() >= next_report:
                self.log(progress)
                next_report = time.monotonic() + 10
            time.sleep(0.35)
        raise RidiAppError(message)

    @staticmethod
    def _reader_log_snapshot():
        log_dir = os.path.join(os.environ.get('APPDATA', ''),
                               'Ridibooks', 'Logs')
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

    def _wait_for_viewer(self, snapshot):
        end = time.monotonic() + 60
        next_report = time.monotonic() + 10
        while time.monotonic() < end:
            if self.stop_requested():
                raise RidiAppError('Download cancelled.')
            if self._tab('Viewer'):
                return
            if self._reader_cache_error(snapshot):
                raise RidiAppError(
                    'RIDI could not open its cached copy of this volume. '
                    'Remove and download the volume again in the RIDI PC app.'
                )
            if time.monotonic() >= next_report:
                self.log('  [Ridi] Waiting for the RIDI PC viewer to open '
                         'the owned volume...')
                next_report = time.monotonic() + 10
            time.sleep(0.35)
        raise RidiAppError('RIDI PC viewer did not open the owned volume.')

    def _tab(self, suffix):
        return next((t for t in self._tabs(self.port)
                     if t.get('type') == 'page' and
                     t.get('url', '').endswith('?' + suffix)), None)

    def _evaluate(self, suffix, expression):
        tab = self._tab(suffix)
        if not tab:
            return None
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
                result = reply.get('result') or {}
                if result.get('exceptionDetails'):
                    raise RidiAppError('RIDI viewer interaction failed: ' +
                                       str(result['exceptionDetails'].get('text')))
                return (result.get('result') or {}).get('value')
        finally:
            ws.close()

    @staticmethod
    def _native_ridi_dialog():
        """Return the foreground standard dialog only when RIDI owns it."""
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
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            window_class = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, window_class, len(window_class))
            if window_class.value != '#32770':
                return None
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if (psutil.Process(pid.value).name() or '').lower() != 'ridibooks.exe':
                return None
            return hwnd
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
        """Accept a RIDI reader JavaScript dialog through its own CDP tab."""
        if not self.port:
            return False
        try:
            tabs = self._tabs(self.port)
        except Exception:
            return False
        for tab in tabs:
            url = urllib.parse.unquote(tab.get('url', ''))
            if (tab.get('type') != 'page' or
                    '/RIDI/Ridibooks/resources/app.asar/' not in url or
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
        while not stop.is_set():
            try:
                hwnd = self._native_ridi_dialog()
                now = time.monotonic()
                if hwnd and (hwnd != last_native or now - last_press >= 3):
                    if self._press_enter_on_dialog(hwnd):
                        self.log('  [Ridi] Pressed Enter on a RIDI PC viewer popup.')
                        last_native, last_press = hwnd, now
                elif not hwnd:
                    last_native = None
                if self._accept_js_dialog():
                    self.log('  [Ridi] Accepted a RIDI PC viewer popup.')
            except Exception:
                pass
            stop.wait(0.8)

    def _sso(self, context):
        page = context.new_page()
        try:
            page.goto('https://account.ridibooks.com/',
                      wait_until='domcontentloaded', timeout=30000)
            otp = page.evaluate("""async () => {
              const r = await fetch('/sso/otp', {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams({redirectUri: 'ridi://download'})
              });
              return r.ok ? (await r.json()).otp : null;
            }""")
            if not otp:
                raise RidiAppError(
                    'Saved browser login could not authorize the RIDI PC viewer.'
                )
            return otp
        finally:
            page.close()

    def _open_owned_book(self, context, book_id, title):
        otp = self._sso(context)
        payload = json.dumps({'b_ids': [str(book_id)]}, separators=(',', ':'))
        link = ('ridi://download?sso_otp=' + urllib.parse.quote(otp) +
                '&payload=' + urllib.parse.quote(payload))
        try:
            # Windows may display "Get an app to open this 'ridi' link" even
            # when the RIDI executable is installed. Passing the URI directly
            # to that executable uses Electron's own second-instance handler.
            subprocess.Popen(
                [self.executable, link],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RidiAppError(
                'Windows could not launch the RIDI PC viewer.'
            ) from exc
        finally:
            del otp, link
        self._wait(lambda: self._tab('Books'), 30,
                   'RIDI PC viewer did not open its library after the '
                   'sign-in handoff.',
                   '  [Ridi] Waiting for the RIDI library to open...')
        self.log('  [Ridi] RIDI library opened; locating the owned volume...')
        title_js = json.dumps(title, ensure_ascii=False)
        book_id_js = json.dumps(str(book_id))
        def click_book():
            return self._evaluate('Books', """(() => {
              const title = %s;
              const bookId = %s;
              const matches = [...document.querySelectorAll('*')].filter(e =>
                e.textContent?.trim() === title &&
                ![...e.children].some(c => c.textContent?.trim() === title));
              if (!matches.length) return false;
              let cover = '';
              for (let node = matches[0], depth = 0;
                   node && depth < 7; node = node.parentElement, depth++) {
                const image = [...node.querySelectorAll('img')].find(img =>
                  (img.currentSrc || img.src || '').includes(
                    '/cover/' + bookId + '/'));
                if (image) {
                  cover = image.currentSrc || image.src;
                  break;
                }
              }
              matches[0].click();
              return {cover};
            })()""" % (title_js, book_id_js))
        snapshot = self._reader_log_snapshot()
        selected = self._wait(click_book, 120,
                   'Owned volume did not appear in the RIDI PC library.',
                   '  [Ridi] Waiting for the owned volume to appear in '
                   'the RIDI library...')
        self.log('  [Ridi] Found the owned volume; opening the reader...')
        self._wait_for_viewer(snapshot)
        cover = selected.get('cover', '') if isinstance(selected, dict) else ''
        return cover.split('#', 1)[0] or (
            f'https://img.ridicdn.net/cover/{book_id}/large'
        )

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
            const doc = frame.contentDocument;
            const page = doc?.querySelector('ridi-page-container[data-front]');
            const content = doc?.querySelector('ridi-column-container');
            if (!page || !content) continue;
            const index = page.getAttribute('data-spine-index');
            if (!/^\\d+$/.test(index || '')) continue;
            sections.push({spine: Number(index),
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

    def _send_viewer_key(self, key, virtual_code):
        tab = self._tab('Viewer')
        if not tab:
            raise RidiAppError('RIDI viewer closed while reading front matter.')
        import websocket
        ws = websocket.create_connection(
            tab['webSocketDebuggerUrl'], timeout=12, suppress_origin=True
        )
        try:
            for kind in ('keyDown', 'keyUp'):
                ws.send(json.dumps({
                    'id': 2, 'method': 'Input.dispatchKeyEvent',
                    'params': {
                        'type': kind, 'key': key, 'code': key,
                        'windowsVirtualKeyCode': virtual_code,
                        'nativeVirtualKeyCode': virtual_code,
                    },
                }))
                while json.loads(ws.recv()).get('id') != 2:
                    continue
        finally:
            ws.close()

    def _front_matter(self, first_chapter_page):
        """Read the source cover and pages omitted from the reader's TOC menu."""
        focused = self._evaluate('Viewer', """(() => {
          const slider = document.querySelector('input[type="range"]');
          if (!slider) return false;
          slider.focus(); return true;
        })()""")
        if not focused:
            raise RidiAppError('RIDI viewer page control was not found.')
        self._send_viewer_key('Home', 36)
        self._wait(lambda: self._reader_page() == 0, 10,
                   'RIDI viewer did not return to the first page.')
        pages = []
        images = {}
        cover_data = ''
        seen = set()
        turns = 0
        while self._reader_page() < first_chapter_page - 1:
            if self.stop_requested():
                raise RidiAppError('Download cancelled.')
            frames = self._wait(self._front_sections, 10,
                                'RIDI front matter did not render.')
            for frame in sorted(frames, key=lambda item: item['spine']):
                spine = frame['spine']
                if spine in seen:
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
                    cover.decompose()
                    content = str(soup)
                if not soup.get_text(strip=True):
                    continue
                for image in self._images(content):
                    if not image.get('data'):
                        raise RidiAppError(
                            'RIDI front-matter image could not be read.'
                        )
                    images[image['url']] = image['data']
                pages.append({'spine': spine, 'html': content})
            old_page = self._reader_page()
            self._send_viewer_key('ArrowRight', 39)
            self._wait(lambda: self._reader_page() > old_page, 10,
                       'RIDI viewer did not advance through front matter.')
            turns += 1
            if turns > 80:
                raise RidiAppError('RIDI front matter is unexpectedly long.')
        return pages, images, cover_data

    @staticmethod
    def _part_heading(front_pages):
        for index, page in enumerate(front_pages, 1):
            soup = BeautifulSoup(page['html'], 'html.parser')
            for node in soup.select('.mtitle-h1-subtitle, h1.subtitle'):
                title = node.get_text(' ', strip=True)
                if re.search(r'\d+\s*부', title):
                    return title, f'ridi-front-{index}'
        return '', ''

    @staticmethod
    def _link_printed_contents(content, chapter_titles):
        soup = BeautifulSoup(content, 'html.parser')
        normalized = {
            re.sub(r'\s+', '', title): f'#ridi-section-{index}'
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
        self.log(f'  [Ridi] Opening owned {title} in the RIDI PC viewer...')
        cover_url = self._open_owned_book(context, book_id, title)
        self._wait(lambda: self._evaluate('Viewer', """(() => {
          const b = [...document.querySelectorAll('button')].find(e =>
            e.innerText?.trim() === '더보기');
          if (!b) return false;
          b.click();
          return true;
        })()"""), 30, 'RIDI viewer menu was not found.')
        self._wait(lambda: self._evaluate('Viewer', """(() => {
          const e = [...document.querySelectorAll('[role="group"]')].find(x =>
            x.innerText?.trim() === '목차');
          if (!e) return false;
          e.click(); return true;
        })()"""), 10, 'RIDI table of contents was not found.')
        self._wait(lambda: self._tab('TocModal'), 10,
                   'RIDI table of contents did not open.')
        rows = self._wait(self._toc_rows, 15,
                          'RIDI table of contents is empty.')
        front_pages = []
        front_images = {}
        cover_data = ''
        if self._open_toc_row(rows[0]['index']):
            try:
                self._wait(
                    lambda: abs(self._reader_page() -
                                (rows[0]['page'] - 1)) <= 1,
                    20, 'RIDI did not navigate to the first chapter.'
                )
                self._wait(self._front_sections, 10,
                           'RIDI first chapter did not render.')
                time.sleep(0.5)
                front_pages, front_images, cover_data = self._front_matter(
                    rows[0]['page']
                )
                self.log(f'  [Ridi] Read {len(front_pages)} source '
                         'front-matter page(s).')
            except RidiAppError as exc:
                if self.stop_requested():
                    raise
                self.log(f'  [Ridi] Front-matter warning: {exc}')
        else:
            self.log('  [Ridi] Front-matter warning: first chapter could '
                     'not be selected before reading the title pages.')
        sections = []
        seen = set()
        image_data = dict(front_images)
        for row in rows:
            clicked = self._open_toc_row(row['index'])
            if not clicked:
                raise RidiAppError('Could not open RIDI section: ' + row['title'])
            target_page = row['page']
            self._wait(lambda: self._evaluate('Viewer', """(() => {
              const text = document.body.innerText || '';
              const match = text.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
              return match && Math.abs(Number(match[1]) - %d) <= 1;
            })()""" % target_page), 20,
                'RIDI did not navigate to ' + row['title'])
            frames = self._wait(self._front_sections, 10,
                                'RIDI section did not render: ' + row['title'])
            section = self._select_front_section(frames)
            if not section or section['spine'] in seen:
                raise RidiAppError('RIDI section was repeated or empty: ' +
                                   row['title'])
            seen.add(section['spine'])
            content = self._clean_section(section['html'])
            if not BeautifulSoup(content, 'html.parser').get_text(strip=True):
                raise RidiAppError('RIDI section has no text: ' + row['title'])
            for item in self._images(content):
                if not item.get('data'):
                    raise RidiAppError('RIDI section image could not be read: '
                                       + row['title'])
                image_data[item['url']] = item['data']
            sections.append((row['title'], content))
            self.log(f'  [Ridi] Read section {len(sections)}/{len(rows)}: '
                     + row['title'])
        part_title, part_id = self._part_heading(front_pages)
        chapter_points = [
            {'title': section_title, 'id': f'ridi-section-{i}'}
            for i, (section_title, _) in enumerate(sections, 1)
        ]
        front_body = ''.join(
            '<div class="ridi-front-section" id="ridi-front-%d">%s</div>' % (
                index,
                self._link_printed_contents(
                    page['html'], [section_title for section_title, _ in sections]
                ),
            )
            for index, page in enumerate(front_pages, 1)
        )
        chapter_body = ''.join(
            '<div class="ridi-volume-section" id="ridi-section-%d">%s%s'
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
                raise RidiAppError('Unsupported RIDI section image type: '
                                   + mime)
            images.append({
                'url': url, 'data': data,
                'name': f'ridi-{book_id}-{i:03d}.{extension}',
            })
        return {
            'chapterName': title,
            'sourceChapterName': title,
            'chapterUrl': chapter_url,
            'coverUrl': cover_url,
            '_coverData': cover_data,
            'contentHtml': '<div class="ridi-content">' + body + '</div>',
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
                '.ridi-content p { margin: 0 0 .75em; line-height: 1.7; } '
                '.ridi-content img { max-width: 100%; height: auto; } '
                '.ridi-front-section, .ridi-volume-section { '
                'page-break-after: always; } '
                '.ridi-front-section .mtitle-container, '
                '.ridi-front-section .title-container { '
                'min-height: 75vh; display: flex; align-items: center; '
                'justify-content: center; text-align: center; } '
                '.ridi-front-section .subtitle, '
                '.ridi-front-section .mtitle-h1-subtitle { '
                'font-size: 1.2em; margin-top: 1.5em; } '
                '.ridi-front-section .contents-header { '
                'font-size: 1.3em; font-weight: bold; margin-top: 2em; } '
                '.ridi-front-section .contents-body p { margin: .5em 0; } '
                '.ridi-front-section .contents-body a { '
                'color: inherit; text-decoration: none; }'
            ),
            'images': images,
        }
