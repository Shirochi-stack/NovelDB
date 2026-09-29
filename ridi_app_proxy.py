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
import time
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup


class RidiAppError(RuntimeError):
    pass


class RidiAppProxy:
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
            raise RidiAppError('Install the official RIDI PC viewer and retry.')
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
        def click_book():
            return self._evaluate('Books', """(() => {
              const title = %s;
              const matches = [...document.querySelectorAll('*')].filter(e =>
                e.textContent?.trim() === title &&
                ![...e.children].some(c => c.textContent?.trim() === title));
              if (!matches.length) return false;
              matches[0].click();
              return true;
            })()""" % title_js)
        snapshot = self._reader_log_snapshot()
        self._wait(click_book, 120,
                   'Owned volume did not appear in the RIDI PC library.',
                   '  [Ridi] Waiting for the owned volume to appear in '
                   'the RIDI library...')
        self.log('  [Ridi] Found the owned volume; opening the reader...')
        self._wait_for_viewer(snapshot)

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
            sections.push({spine: Number(page.getAttribute('data-spine-index')),
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

    def extract(self, context, book_id, title, chapter_url):
        self.connect()
        self.log(f'  [Ridi] Opening owned {title} in the RIDI PC viewer...')
        self._open_owned_book(context, book_id, title)
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
        sections = []
        seen = set()
        image_data = {}
        for row in rows:
            index = row['index']
            clicked = self._evaluate('TocModal', """(() => {
              const group = [...document.querySelectorAll('.simplebar-content')]
                .find(e => [...e.children].some(c =>
                  /^\\s*\\d+\\s*\\n/.test(c.innerText || '')));
              const row = group?.children[%d];
              if (!row) return false;
              row.click(); return true;
            })()""" % index)
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
        body = ''.join(
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
            'contentHtml': '<div class="ridi-content">' + body + '</div>',
            'contentText': '\n'.join(BeautifulSoup(c, 'html.parser').get_text(
                '\n', strip=True) for _, c in sections),
            'tocSections': [
                {'title': section_title, 'id': f'ridi-section-{i}'}
                for i, (section_title, _) in enumerate(sections, 1)
            ],
            'contentCss': (
                '.ridi-content p { margin: 0 0 .75em; line-height: 1.7; } '
                '.ridi-content img { max-width: 100%; height: auto; } '
                '.ridi-volume-section + .ridi-volume-section { '
                'page-break-before: always; }'
            ),
            'images': images,
        }
