"""Read a purchased Rakuten Kobo book downloaded by the official PC app.

The local book format and device-key derivation were documented by the
Obok project: https://github.com/noDRM/DeDRM_tools/tree/master/Obok_plugin
This is a separate implementation using the dependencies already shipped by
the downloader. It only selects an exact CrossRevisionId from the signed-in
desktop app's library; a similarly titled catalog entry is never enough.
"""

import base64
import hashlib
import io
import os
import posixpath
import re
import sqlite3
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET


class KoboDesktopError(RuntimeError):
    pass


class KoboDesktopReader:
    _DEVICE_SALTS = ('88b3a2e13', 'XzUhGYdFp', 'NoCanLook', 'QJhwzAtXL')
    INSTALLER_URL = 'https://cdn.kobo.com/downloads/desktop/Rakutenbooks/kobosetup.exe'

    def __init__(self, log=lambda message: None, directory=None):
        self.log = log
        self.directory = directory or os.path.join(
            os.environ.get('LOCALAPPDATA', ''), 'Kobo', 'Kobo Desktop Edition'
        )

    @staticmethod
    def revision_id(url):
        from urllib.parse import urlparse
        parsed = urlparse(url or '')
        if parsed.scheme != 'https' or parsed.hostname != 'books.rakuten.co.jp':
            raise KoboDesktopError('Expected an official Rakuten Books URL.')
        match = re.fullmatch(r'/rk/([0-9a-fA-F]{32})/?', parsed.path)
        if not match:
            raise KoboDesktopError('Rakuten Books URL has no ebook ID.')
        return str(uuid.UUID(hex=match.group(1)))

    def _snapshot(self):
        database = os.path.join(self.directory, 'Kobo.sqlite')
        if not os.path.isfile(database):
            executable = self._find_executable()
            if not executable and os.name == 'nt':
                executable = self._install_app()
            if executable:
                try:
                    subprocess.Popen(
                        [executable],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError:
                    pass
            raise KoboDesktopError(
                'Sign in to Rakuten Kobo Desktop with Rakuten ID under More '
                'Sign-In Options, then sync and open this volume.'
            )
        source = sqlite3.connect(database, timeout=20)
        snapshot = sqlite3.connect(':memory:')
        try:
            source.backup(snapshot)
        finally:
            source.close()
        try:
            signed_in = bool(snapshot.execute('SELECT 1 FROM user LIMIT 1').fetchone())
        except sqlite3.DatabaseError:
            signed_in = False
        if not signed_in:
            snapshot.close()
            executable = self._find_executable()
            if executable:
                try:
                    subprocess.Popen(
                        [executable], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError:
                    pass
            raise KoboDesktopError(
                'Rakuten Kobo Desktop needs a Rakuten ID sign-in under '
                'More Sign-In Options.'
            )
        return snapshot

    @staticmethod
    def _find_executable():
        roots = (
            os.environ.get('ProgramFiles(x86)'),
            os.environ.get('ProgramFiles'),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs'),
            r'C:\Program Files (x86)',
        )
        for root in roots:
            if root:
                candidate = os.path.join(root, 'Kobo', 'Kobo.exe')
                if os.path.isfile(candidate):
                    return candidate
        return None

    def _install_app(self):
        self.log('  [Kobo] Rakuten Kobo Desktop is missing; downloading '
                 'the official JP installer...')
        try:
            with tempfile.TemporaryDirectory(prefix='npia-kobo-') as folder:
                installer = os.path.join(folder, 'kobosetup.exe')
                request = urllib.request.Request(
                    self.INSTALLER_URL, headers={'User-Agent': 'Mozilla/5.0'}
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    final = urllib.parse.urlparse(response.geturl())
                    if final.scheme != 'https' or final.hostname != 'cdn.kobo.com':
                        raise KoboDesktopError(
                            'Kobo installer redirected outside its official server.'
                        )
                    size = 0
                    with open(installer, 'wb') as output:
                        while True:
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            size += len(block)
                            if size > 200 * 1024 * 1024:
                                raise KoboDesktopError('Kobo installer is too large.')
                            output.write(block)
                with open(installer, 'rb') as downloaded:
                    if size < 1024 * 1024 or downloaded.read(2) != b'MZ':
                        raise KoboDesktopError('Kobo download is not a Windows installer.')
                env = os.environ.copy()
                env['NPIA_KOBO_INSTALLER_PATH'] = installer
                signature = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-NonInteractive',
                     '-Command',
                     '$s=Get-AuthenticodeSignature -LiteralPath '
                     '$env:NPIA_KOBO_INSTALLER_PATH; '
                     'Write-Output $s.Status; '
                     'Write-Output $s.SignerCertificate.Subject'],
                    env=env, capture_output=True, text=True, timeout=30,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                lines = signature.stdout.strip().splitlines()
                if (signature.returncode != 0 or len(lines) < 2
                        or lines[0].strip() != 'Valid'
                        or 'rakuten kobo inc.' not in lines[1].lower()):
                    raise KoboDesktopError(
                        'Rakuten Kobo installer signature could not be verified.'
                    )
                self.log('  [Kobo] Official JP installer verified; installing...')
                installed = subprocess.run(
                    [installer, '/S'], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=240,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if installed.returncode != 0:
                    raise KoboDesktopError(
                        f'Kobo installer exited with code {installed.returncode}.'
                    )
        except KoboDesktopError:
            raise
        except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            raise KoboDesktopError(
                f'Automatic Rakuten Kobo Desktop installation failed: {exc}'
            ) from exc
        executable = self._find_executable()
        if not executable:
            raise KoboDesktopError(
                'Kobo installer finished, but Kobo.exe was not found.'
            )
        self.log('  [Kobo] Rakuten Kobo Desktop installed.')
        return executable

    def lookup(self, url):
        revision = self.revision_id(url)
        db = self._snapshot()
        try:
            row = db.execute(
                'SELECT ContentID, Title, Attribution, ISBN, IsDownloaded '
                'FROM content WHERE CrossRevisionId = ? AND ContentType = 6 '
                'ORDER BY CASE WHEN IsDownloaded = \'true\' THEN 0 ELSE 1 END '
                'LIMIT 1', (revision,)
            ).fetchone()
            if not row:
                return None
            volume, title, author, isbn, downloaded = row
            path = os.path.join(self.directory, 'kepub', volume)
            return {
                'volume_id': volume,
                'title': title or '',
                'author': author or '',
                'isbn': isbn or '',
                'downloaded': str(downloaded).lower() == 'true'
                              and os.path.isfile(path),
                'path': path,
            }
        finally:
            db.close()

    @staticmethod
    def _mac_addresses():
        import psutil
        addresses = set()
        for entries in psutil.net_if_addrs().values():
            for item in entries:
                value = (item.address or '').replace('-', ':').upper()
                if re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', value):
                    addresses.add(value)
        node = uuid.getnode()
        addresses.add(':'.join(f'{(node >> shift) & 255:02X}'
                               for shift in range(40, -1, -8)))
        return sorted(addresses)

    @classmethod
    def _candidate_keys(cls, user_ids):
        for address in cls._mac_addresses():
            for salt in cls._DEVICE_SALTS:
                device = hashlib.sha256((salt + address).encode('ascii'))
                for user_id in user_ids:
                    key = hashlib.sha256(
                        (device.hexdigest() + user_id).encode('ascii')
                    ).digest()[16:]
                    yield key

    @staticmethod
    def _aes_decrypt(key, payload):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        return decryptor.update(payload) + decryptor.finalize()

    @classmethod
    def _decrypt_entry(cls, user_key, file_key, ciphertext):
        if len(file_key) != 16 or len(ciphertext) % 16:
            raise ValueError('Invalid Kobo encrypted file.')
        content_key = cls._aes_decrypt(user_key, file_key)
        padded = cls._aes_decrypt(content_key, ciphertext)
        padding = padded[-1]
        if not 1 <= padding <= 16 or padded[-padding:] != bytes([padding]) * padding:
            raise ValueError('Wrong Kobo content key.')
        return padded[:-padding]

    @staticmethod
    def _looks_readable(name, data):
        if name.lower().endswith(('.xhtml', '.html', '.xml')):
            start = data.lstrip(b'\xef\xbb\xbf \r\n\t')[:256].lower()
            return start.startswith((b'<?xml', b'<!doctype', b'<html'))
        if name.lower().endswith(('.jpg', '.jpeg')):
            return data.startswith(b'\xff\xd8\xff')
        if name.lower().endswith('.png'):
            return data.startswith(b'\x89PNG\r\n\x1a\n')
        return bool(data)

    @staticmethod
    def _responsive_image_page(content):
        """Make image-only pages fit ordinary EPUB reader windows."""
        svg = re.compile(
            rb'<svg\b[^>]*>\s*<image\b([^>]*)/>\s*</svg>',
            re.IGNORECASE | re.DOTALL,
        )
        matches = list(svg.finditer(content))
        body = re.search(rb'<body\b[^>]*>(.*?)</body>', content,
                         re.IGNORECASE | re.DOTALL)
        if not body:
            return content, False
        if len(matches) == 1 and body.start(1) <= matches[0].start() < body.end(1):
            remaining = content[body.start(1):matches[0].start()] + content[
                matches[0].end():body.end(1)]
            if re.sub(rb'<[^>]+>', b'', remaining).strip():
                return content, False
            image = re.search(rb'(?:xlink:)?href\s*=\s*(["\'])(.*?)\1',
                              matches[0].group(1), re.IGNORECASE)
            if not image:
                return content, False
            replacement = (b'<img class="npia-page-image" src="' +
                           image.group(2) + b'" alt=""/>')
            content = content[:matches[0].start()] + replacement + content[
                matches[0].end():]
        elif not matches:
            image_tag = re.compile(rb'<img\b[^>]*?/>', re.IGNORECASE)
            body_content = body.group(1)
            images = list(image_tag.finditer(body_content))
            if not images or len(images) > 4:
                return content, False
            remaining = image_tag.sub(b'', body_content)
            if re.sub(rb'<[^>]+>', b'', remaining).strip():
                return content, False
            if any(not re.search(rb'\bclass=["\'][^"\']*\bfit\b', image.group(),
                                 re.IGNORECASE) for image in images):
                return content, False

            def replace_image(match):
                tag = re.sub(rb'\s+class=["\'][^"\']*["\']', b'', match.group(),
                             count=1, flags=re.IGNORECASE)
                return tag.replace(b'<img', b'<img class="npia-page-image"', 1)

            content = (content[:body.start(1)] +
                       image_tag.sub(replace_image, body_content) +
                       content[body.end(1):])
        else:
            return content, False
        content = re.sub(
            rb'<meta\s+name=["\']viewport["\'][^>]*/>\s*', b'', content,
            count=1, flags=re.IGNORECASE,
        )
        style = (
            b'<style type="text/css">'
            b'html,body{margin:0;padding:0;width:100%;height:auto;}'
            b'.main{width:100%;text-align:center;writing-mode:horizontal-tb;}'
            b'.main p{margin:0;padding:0;page-break-after:always;}'
            b'.main p:last-child{page-break-after:auto;}'
            b'img.npia-page-image{display:block;width:auto;height:auto;'
            b'max-width:100%;max-height:95vh;margin:0 auto;}'
            b'</style>'
        )
        content = re.sub(rb'</head>', style + b'</head>', content,
                         count=1, flags=re.IGNORECASE)
        return content, True

    @staticmethod
    def _split_image_page(content):
        """Keep each full-page illustration in its own XHTML spine page."""
        main = re.search(
            rb'<div\b[^>]*\bclass=["\']main["\'][^>]*>(.*?)</div>',
            content, re.IGNORECASE | re.DOTALL,
        )
        if not main:
            return [content]
        paragraphs = re.findall(rb'<p\b[^>]*>.*?</p>', main.group(1),
                                re.IGNORECASE | re.DOTALL)
        if len(paragraphs) < 2:
            return [content]
        remaining = main.group(1)
        for paragraph in paragraphs:
            remaining = remaining.replace(paragraph, b'', 1)
            if (paragraph.count(b'npia-page-image') != 1 or
                    re.sub(rb'<[^>]+>', b'', paragraph).strip()):
                return [content]
        if remaining.strip():
            return [content]
        return [content[:main.start(1)] + paragraph + content[main.end(1):]
                for paragraph in paragraphs]

    @staticmethod
    def _add_split_image_entries(content, split_pages, opf_name):
        """Insert generated image pages immediately after their source pages."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise KoboDesktopError('Could not update Kobo image page order.') from exc
        namespace = {'opf': 'http://www.idpf.org/2007/opf'}
        manifest = root.find('opf:manifest', namespace)
        if manifest is None:
            raise KoboDesktopError('Kobo EPUB has no image page manifest.')
        opf_dir = posixpath.dirname(opf_name)
        new_by_id = {}
        all_ids = {item.get('id') for item in manifest.findall('opf:item', namespace)}
        for item in manifest.findall('opf:item', namespace):
            path = posixpath.normpath(posixpath.join(
                opf_dir, urllib.parse.unquote(item.get('href', ''))
            ))
            if path not in split_pages:
                continue
            original_id = item.get('id')
            additions = []
            for index, page in enumerate(split_pages[path], 2):
                new_id = f'{original_id}-npia-{index}'
                if new_id in all_ids:
                    raise KoboDesktopError('Kobo EPUB image page ID conflicts.')
                all_ids.add(new_id)
                href = urllib.parse.quote(
                    posixpath.relpath(page, opf_dir), safe='/._-'
                )
                additions.append((new_id, href))
            new_by_id[original_id] = additions
        if len(new_by_id) != len(split_pages):
            raise KoboDesktopError('Kobo EPUB image page is missing from manifest.')
        manifest_count = 0
        spine_count = 0

        def insert_manifest(match):
            nonlocal manifest_count
            tag = match.group()
            item_id = re.search(rb'\bid=["\']([^"\']+)["\']', tag)
            if not item_id:
                return tag
            additions = new_by_id.get(item_id.group(1).decode('utf-8'))
            if not additions:
                return tag
            manifest_count += 1
            return tag + b''.join(
                b'<item id="' + new_id.encode('utf-8') + b'" href="' +
                href.encode('ascii') +
                b'" media-type="application/xhtml+xml"/>'
                for new_id, href in additions
            )

        content = re.sub(rb'<item\b[^>]*?/>', insert_manifest, content,
                         flags=re.IGNORECASE)

        def insert_spine(match):
            nonlocal spine_count
            tag = match.group()
            idref = re.search(rb'\bidref=["\']([^"\']+)["\']', tag)
            if not idref:
                return tag
            additions = new_by_id.get(idref.group(1).decode('utf-8'))
            if not additions:
                return tag
            spine_count += 1
            return tag + b''.join(
                b'<itemref idref="' + new_id.encode('utf-8') + b'"/>'
                for new_id, _ in additions
            )

        content = re.sub(rb'<itemref\b[^>]*?/>', insert_spine, content,
                         flags=re.IGNORECASE)
        if manifest_count != len(split_pages) or spine_count != len(split_pages):
            raise KoboDesktopError('Kobo EPUB image page order is incomplete.')
        return content

    @staticmethod
    def _reflow_image_spine(content, image_pages, opf_name):
        """Remove fixed-layout spine flags for the pages changed above."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return content
        namespace = {'opf': 'http://www.idpf.org/2007/opf'}
        manifest = root.find('opf:manifest', namespace)
        if manifest is None:
            return content
        opf_dir = os.path.dirname(opf_name)
        ids = set()
        for item in manifest.findall('opf:item', namespace):
            path = os.path.normpath(os.path.join(
                opf_dir, urllib.parse.unquote(item.get('href', ''))
            )).replace('\\', '/')
            if path in image_pages:
                ids.add(item.get('id'))
        if not ids:
            return content

        def update(match):
            tag = match.group(0)
            idref = re.search(rb'\bidref=["\']([^"\']+)["\']', tag)
            if not idref or idref.group(1).decode('utf-8') not in ids:
                return tag
            flags = re.search(rb'\s+properties=["\']([^"\']*)["\']', tag)
            if not flags:
                return tag
            remove = {b'rendition:layout-pre-paginated', b'rendition:spread-none',
                      b'rendition:page-spread-center', b'access:scroll-both',
                      b'access:orientation-portrait'}
            remaining = b' '.join(flag for flag in flags.group(1).split()
                                  if flag not in remove)
            replacement = (b' properties="' + remaining + b'"') if remaining else b''
            return tag[:flags.start()] + replacement + tag[flags.end():]

        return re.sub(rb'<itemref\b[^>]*?/?>', update, content,
                      flags=re.IGNORECASE)

    @staticmethod
    def _horizontal_text_page(content):
        """Use the publisher's horizontal CSS rules for Japanese text."""
        html_tag = re.search(rb'<html\b[^>]*>', content, re.IGNORECASE)
        if not html_tag:
            return content, False
        tag = html_tag.group()
        classes = re.search(rb'\bclass=(["\'])(.*?)\1', tag, re.IGNORECASE)
        if not classes or b'vrtl' not in classes.group(2).split():
            return content, False
        names = [b'hltr' if name == b'vrtl' else name
                 for name in classes.group(2).split()]
        tag = (tag[:classes.start(2)] + b' '.join(names) +
               tag[classes.end(2):])
        direction = re.search(rb'\bdir=(["\'])(.*?)\1', tag, re.IGNORECASE)
        if direction:
            tag = tag[:direction.start(2)] + b'ltr' + tag[direction.end(2):]
        else:
            tag = tag[:-1] + b' dir="ltr">'
        return (content[:html_tag.start()] + tag + content[html_tag.end():],
                True)

    @staticmethod
    def _horizontal_spine(content):
        """Match left-to-right pages to horizontal text navigation."""
        def set_direction(match):
            tag = match.group()
            if re.search(rb'\bpage-progression-direction=', tag):
                return re.sub(
                    rb'\bpage-progression-direction=["\'][^"\']*["\']',
                    b'page-progression-direction="ltr"', tag, count=1,
                )
            return tag[:-1] + b' page-progression-direction="ltr">'

        content = re.sub(rb'<spine\b[^>]*>', set_direction, content,
                         count=1, flags=re.IGNORECASE)

        def drop_page_spread(match):
            tag = match.group()
            flags = re.search(rb'\s+properties=["\']([^"\']*)["\']', tag)
            if not flags:
                return tag
            remaining = b' '.join(
                flag for flag in flags.group(1).split()
                if flag not in (b'page-spread-left', b'page-spread-right',
                                b'page-spread-center')
            )
            replacement = (b' properties="' + remaining + b'"') if remaining else b''
            return tag[:flags.start()] + replacement + tag[flags.end():]

        return re.sub(rb'<itemref\b[^>]*?/?>', drop_page_spread, content,
                      flags=re.IGNORECASE)

    def export(self, url, horizontal_layout=True):
        book = self.lookup(url)
        if not book:
            raise KoboDesktopError(
                'This volume is not in the Rakuten Kobo Desktop library. '
                'Sign in with Rakuten ID under More Sign-In Options, then sync it.'
            )
        if not book['downloaded']:
            raise KoboDesktopError(
                'Open this volume in Rakuten Kobo Desktop once so its '
                'download finishes, then retry.'
            )
        db = self._snapshot()
        try:
            user_ids = [row[0] for row in db.execute('SELECT UserID FROM user')
                        if row[0]]
            encrypted = {
                row[0]: base64.b64decode(row[1])
                for row in db.execute(
                    'SELECT elementid, elementkey FROM content_keys '
                    'WHERE volumeid = ?', (book['volume_id'],)
                )
            }
        finally:
            db.close()
        if not user_ids:
            raise KoboDesktopError('Rakuten Kobo Desktop is not signed in.')
        with zipfile.ZipFile(book['path']) as source:
            if source.testzip() is not None:
                raise KoboDesktopError('Kobo Desktop book download is damaged.')
            absent = set(encrypted) - set(source.namelist())
            if absent:
                raise KoboDesktopError('Kobo Desktop book and library keys disagree.')
            if encrypted:
                sample_name = next(
                    (name for name in encrypted if name.endswith(('.xhtml', '.html'))),
                    next(iter(encrypted)),
                )
                sample_data = source.read(sample_name)
                key = None
                for candidate in self._candidate_keys(user_ids):
                    try:
                        clear = self._decrypt_entry(
                            candidate, encrypted[sample_name], sample_data
                        )
                        if self._looks_readable(sample_name, clear):
                            key = candidate
                            break
                    except (ValueError, IndexError):
                        pass
                if key is None:
                    raise KoboDesktopError(
                        'Could not read this desktop download with the '
                        'current Windows device key.'
                    )
            else:
                key = None
            entries = []
            image_pages = set()
            split_pages = {}
            archive_names = set(source.namelist())
            for name in source.namelist():
                if name == 'mimetype':
                    continue
                content = source.read(name)
                if name in encrypted:
                    content = self._decrypt_entry(key, encrypted[name], content)
                    if not self._looks_readable(name, content):
                        raise KoboDesktopError(
                            'Kobo Desktop returned an unreadable source section.'
                        )
                if name.lower().endswith(('.xhtml', '.html')):
                    content, changed = self._responsive_image_page(content)
                    if changed:
                        image_pages.add(name)
                        pages = self._split_image_page(content)
                        if len(pages) > 1:
                            stem, extension = posixpath.splitext(name)
                            additions = []
                            for index, page in enumerate(pages[1:], 2):
                                new_name = f'{stem}-npia-page-{index}{extension}'
                                if new_name in archive_names:
                                    raise KoboDesktopError(
                                        'Kobo EPUB image page name conflicts.'
                                    )
                                archive_names.add(new_name)
                                additions.append((new_name, page))
                            split_pages[name] = [page for page, _ in additions]
                            entries.append((name, pages[0]))
                            entries.extend(additions)
                            continue
                    elif horizontal_layout:
                        content, _ = self._horizontal_text_page(content)
                entries.append((name, content))
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as epub:
                epub.writestr('mimetype', 'application/epub+zip',
                              compress_type=zipfile.ZIP_STORED)
                for name, content in entries:
                    if name.lower().endswith('.opf') and split_pages:
                        content = self._add_split_image_entries(
                            content, split_pages, name
                        )
                    if name.lower().endswith('.opf') and image_pages:
                        content = self._reflow_image_spine(
                            content, image_pages, name
                        )
                    if name.lower().endswith('.opf') and horizontal_layout:
                        content = self._horizontal_spine(content)
                    epub.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
            value = output.getvalue()
        with zipfile.ZipFile(io.BytesIO(value)) as check:
            if check.testzip() is not None or 'META-INF/container.xml' not in check.namelist():
                raise KoboDesktopError('Exported EPUB did not pass archive validation.')
        self.log(f'  [Kobo] Read downloaded Rakuten Kobo volume: {book["title"]}')
        return book, value
