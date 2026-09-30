"""Read an owned Kobo ebook from Kobo's signed-in web reader.

The reader supplies rendered XHTML and image blobs for the account's book.
This module reads those pages through the saved Chrome session; it does not
read Kobo desktop/device databases or handle Adobe download files.
"""

import hashlib
import heapq
import posixpath
import urllib.parse

from bs4 import BeautifulSoup


class KoboReaderError(RuntimeError):
    pass


class KoboWebReader:
    _SNAPSHOT_JS = r"""async (known) => {
      const seen = new Set(known);
      const frames = [...document.querySelectorAll('iframe[data-chapterurl]')];
      const paths = frames.map(f => f.dataset.chapterurl).filter(Boolean);
      const sections = [];
      for (const frame of frames) {
        const path = frame.dataset.chapterurl;
        if (!path || seen.has(path)) continue;
        let doc;
        try { doc = frame.contentDocument; } catch (_) { continue; }
        if (!doc?.body || !doc.body.innerHTML.trim()) continue;
        const text = doc.body.innerText || '';
        const encoded = [...text].filter(c => {
          const value = c.codePointAt(0);
          return value >= 0x1f60 && value <= 0x1fff;
        }).length;
        if (/^en\b/i.test(frame.lang || doc.documentElement.lang || '') &&
            text.length > 50 && encoded > text.length / 20) continue;
        const clone = doc.body.cloneNode(true);
        const images = [];
        const originals = [...doc.body.querySelectorAll('img')];
        const copies = [...clone.querySelectorAll('img')];
        for (let i = 0; i < originals.length; i++) {
          const source = originals[i].currentSrc || originals[i].src || '';
          if (!source) continue;
          copies[i].setAttribute('src', source);
          let data = source.startsWith('data:image/') ? source : null;
          if (!data) {
            try {
              const response = await doc.defaultView.fetch(source, {
                credentials: 'include'
              });
              if (response.ok) {
                const blob = await response.blob();
                data = await new Promise(resolve => {
                  const reader = new FileReader();
                  reader.onload = () => resolve(reader.result);
                  reader.onerror = () => resolve(null);
                  reader.readAsDataURL(blob);
                });
              }
            } catch (_) {}
          }
          if (!data && originals[i].complete &&
              originals[i].naturalWidth && originals[i].naturalHeight) {
            try {
              const canvas = doc.createElement('canvas');
              canvas.width = originals[i].naturalWidth;
              canvas.height = originals[i].naturalHeight;
              canvas.getContext('2d').drawImage(originals[i], 0, 0);
              data = canvas.toDataURL('image/png');
            } catch (_) {}
          }
          images.push({url: source, data});
        }
        sections.push({path, html: clone.innerHTML, images});
      }
      return {paths, sections};
    }"""

    def __init__(self, log, stop_requested=lambda: False):
        self.log = log
        self.stop_requested = stop_requested

    @staticmethod
    def _path(href, base='OEBPS/Text/toc.xhtml'):
        parsed = urllib.parse.urlsplit(href or '')
        path = urllib.parse.unquote(parsed.path).lstrip('/')
        if not path or parsed.scheme or parsed.netloc:
            return ''
        if not path.startswith(('OEBPS/', 'OPS/')):
            path = posixpath.join(posixpath.dirname(base), path)
        return posixpath.normpath(path)

    @staticmethod
    def _anchor(path):
        return 'kobo-' + hashlib.sha1(path.encode('utf-8')).hexdigest()[:12]

    @staticmethod
    def _ordered_paths(snapshots):
        """Merge overlapping reader windows without losing spine order."""
        edges = {}
        indegree = {}
        first_seen = {}
        for snapshot in snapshots:
            paths = list(dict.fromkeys(snapshot))
            for path in paths:
                if path not in first_seen:
                    first_seen[path] = len(first_seen)
                edges.setdefault(path, set())
                indegree.setdefault(path, 0)
            for left, right in zip(paths, paths[1:]):
                if right not in edges[left]:
                    edges[left].add(right)
                    indegree[right] += 1
        ready = [(first_seen[path], path) for path, count in indegree.items()
                 if count == 0]
        heapq.heapify(ready)
        result = []
        while ready:
            _, path = heapq.heappop(ready)
            result.append(path)
            for next_path in edges[path]:
                indegree[next_path] -= 1
                if indegree[next_path] == 0:
                    heapq.heappush(ready, (first_seen[next_path], next_path))
        if len(result) != len(indegree):
            raise KoboReaderError('Kobo reader returned inconsistent section order.')
        return result

    @staticmethod
    def _toc_entries(page):
        return page.evaluate(r"""() => {
          for (const frame of document.querySelectorAll('iframe[data-chapterurl]')) {
            let doc;
            try { doc = frame.contentDocument; } catch (_) { continue; }
            const nav = doc?.querySelector('nav');
            if (!nav) continue;
            const entries = [...nav.querySelectorAll('a[href]')].map(a => ({
              title: a.innerText?.trim() || a.textContent?.trim() || '',
              href: a.getAttribute('href') || ''
            })).filter(x => x.title && /\.xhtml?(?:#|$)/i.test(x.href));
            if (entries.length > 1) return {
              base: frame.dataset.chapterurl, entries
            };
          }
          return null;
        }""")

    @staticmethod
    def _open_contents(page):
        page.locator('[role="button"][aria-label="Open table of contents"]').first.click()
        dialog = page.locator('[role="dialog"][aria-label="Table of contents"]')
        dialog.wait_for(state='visible', timeout=12000)
        return dialog

    def _load_toc(self, page):
        dialog = self._open_contents(page)
        links = dialog.locator('[role="link"]')
        labels = [value.strip() for value in links.all_text_contents()]
        if not labels:
            raise KoboReaderError('Kobo reader did not expose its source contents.')
        links.first.click()
        page.wait_for_function(
            "() => [...document.querySelectorAll('iframe[data-chapterurl]')]"
            ".some(f => f.contentDocument?.querySelector('nav a[href]'))",
            timeout=15000,
        )
        page.wait_for_timeout(1000)
        toc = self._toc_entries(page)
        if not toc:
            raise KoboReaderError('Kobo reader source contents were not loaded.')
        if len(labels) >= len(toc['entries']):
            for entry, label in zip(toc['entries'], labels):
                entry['title'] = label
        return toc

    @staticmethod
    def _clean_html(source, path, anchors):
        soup = BeautifulSoup(source, 'html.parser')
        for node in soup.find_all(['script', 'iframe', 'object', 'embed']):
            node.decompose()
        for node in soup.find_all(True):
            for attribute in list(node.attrs):
                if attribute.lower().startswith('on'):
                    del node[attribute]
        for link in soup.select('a[href]'):
            href = link.get('href', '')
            target = KoboWebReader._path(href, path)
            if target in anchors:
                link['href'] = '#' + anchors[target]
        return str(soup)

    def extract(self, context, reader_url, title, cover_url=''):
        parsed = urllib.parse.urlsplit(reader_url)
        if parsed.scheme != 'https' or parsed.hostname != 'readnow.kobo.com':
            raise KoboReaderError('Kobo Read Now link is invalid.')
        page = context.new_page()
        try:
            self.log(f'  [Kobo] Opening owned volume in Kobo Web Reader: {title}')
            page.goto(reader_url, wait_until='domcontentloaded', timeout=45000)
            page.locator('[role="button"][aria-label="Open table of contents"]').first.wait_for(
                state='visible', timeout=45000)
            toc = self._load_toc(page)
            entries = [
                {'title': item['title'],
                 'path': self._path(item['href'], toc['base'])}
                for item in toc['entries']
            ]
            entries = [item for item in entries if item['path']]
            if not entries:
                raise KoboReaderError('Kobo reader table of contents is empty.')
            collected = {}
            snapshots = []
            for index, entry in enumerate(entries):
                if self.stop_requested():
                    raise KoboReaderError('Download cancelled.')
                dialog = self._open_contents(page)
                links = dialog.locator('[role="link"]')
                if index >= links.count():
                    raise KoboReaderError('Kobo reader table of contents changed.')
                links.nth(index).click()
                path = entry['path']
                page.wait_for_function(
                    "path => [...document.querySelectorAll('iframe[data-chapterurl]')]"
                    ".some(f => f.dataset.chapterurl === path && "
                    "f.contentDocument?.body?.innerHTML?.trim())",
                    arg=path, timeout=20000,
                )
                page.wait_for_timeout(450)
                snapshot = page.evaluate(self._SNAPSHOT_JS, list(collected))
                snapshots.append(snapshot['paths'])
                for section in snapshot['sections']:
                    collected[section['path']] = section
                self.log(f'  [Kobo] Read source section {index + 1}/'
                         f'{len(entries)}: {entry["title"]}')
            missing = [item['title'] for item in entries
                       if item['path'] not in collected]
            if missing:
                raise KoboReaderError('Kobo reader did not load: ' +
                                      ', '.join(missing[:3]))
            ordered = self._ordered_paths(snapshots)
            uncaptured = [path for path in ordered if path not in collected]
            if uncaptured:
                raise KoboReaderError('Kobo reader did not render every '
                                      'source section: ' +
                                      ', '.join(uncaptured[:3]))
            anchors = {path: self._anchor(path) for path in ordered}
            bodies = []
            images = []
            image_urls = set()
            text_parts = []
            cover_data = ''
            for path in ordered:
                section = collected[path]
                clean = self._clean_html(section['html'], path, anchors)
                bodies.append('<section class="kobo-source-section" id="' +
                              anchors[path] + '">' + clean + '</section>')
                text_parts.append(BeautifulSoup(clean, 'html.parser').get_text(
                    '\n', strip=True))
                for image in section['images']:
                    url = image.get('url') or ''
                    data = image.get('data') or ''
                    if url.startswith('blob:') and not data:
                        raise KoboReaderError(
                            'A Kobo reader image did not load in ' + path)
                    if not url or url in image_urls:
                        continue
                    image_urls.add(url)
                    if path == entries[0]['path'] and not cover_data:
                        cover_data = data
                    mime = data.split(';', 1)[0].split('/')[-1].lower()
                    extension = {'jpeg': 'jpg', 'png': 'png', 'gif': 'gif',
                                 'webp': 'webp', 'svg+xml': 'svg'}.get(mime, 'jpg')
                    images.append({
                        'url': url, 'data': data,
                        'name': f'kobo-{len(images) + 1:03d}.{extension}',
                    })
            content_text = '\n\n'.join(text_parts)
            encoded = sum(0x1f60 <= ord(char) <= 0x1fff
                          for char in content_text)
            if (content_text and encoded > len(content_text) / 100
                    and any(item['title'].startswith('Chapter')
                            for item in entries)):
                raise KoboReaderError('Kobo reader text is still font-encoded; '
                                      'a readable EPUB cannot be produced.')
            return {
                'chapterName': title,
                'sourceChapterName': title,
                'chapterUrl': reader_url,
                'coverUrl': cover_url,
                '_coverData': cover_data,
                'contentHtml': '<div class="kobo-content">' + ''.join(bodies) +
                               '</div>',
                'contentText': content_text,
                'tocSections': [
                    {'title': item['title'], 'id': anchors[item['path']]}
                    for item in entries
                ],
                'contentCss': (
                    '.kobo-content p { line-height: 1.55; margin: 0 0 .75em; } '
                    '.kobo-source-section { page-break-after: always; } '
                    '.kobo-source-section img { max-width: 100%; height: auto; }'
                ),
                'images': images,
            }
        finally:
            page.close()
