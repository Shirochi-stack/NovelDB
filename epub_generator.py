import zipfile
import html
import uuid
import os
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from datetime import datetime


class _VisibleTextCounter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"head", "script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"head", "script", "style"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data:
            self._parts.append(data)

    def char_count(self):
        text = "".join(self._parts)
        return len(re.sub(r"\s+", "", text))


def _visible_text_char_count(html_text):
    parser = _VisibleTextCounter()
    parser.feed(html_text or "")
    parser.close()
    return parser.char_count()

class EpubGenerator:
    CONTENT_RETRY_RATIO = 0.80
    CONTENT_RETRY_MIN_CHARS = 1000

    def __init__(self, metadata, output_path, css_template, zip_compress_images=False):
        self.meta = metadata
        self.output_path = output_path
        self.css = css_template
        self.zip_compress_images = zip_compress_images
        # Chapters will store title, content, and a concrete filename
        self.chapters = []
        # Extra pages (like info.xhtml) that should appear after cover but
        # before regular chapters. These are not included in the NCX TOC.
        self.extra_pages = []
        self.images = []
        self.book_uuid = str(uuid.uuid4())
        # Separate counters for normal and notice chapters so filenames
        # are deterministic and match the requested pattern.
        self._normal_index = 1
        self._notice_index = 1

    def add_chapter(self, title, html_content, is_notice: bool = False,
                    show_title: bool = True):
        """Add a chapter to the book.

        Normal chapters are named chapter0001.xhtml, chapter0002.xhtml, ...
        Notice chapters are named chapter_notice0001.xhtml, chapter_notice0002.xhtml, ...
        """
        if is_notice:
            filename = f"chapter_notice{self._notice_index:04d}.xhtml"
            self._notice_index += 1
        else:
            filename = f"chapter{self._normal_index:04d}.xhtml"
            self._normal_index += 1
        self.chapters.append({
            "title": title,
            "content": html_content,
            "filename": filename,
            "show_title": show_title,
        })

    def add_image(self, filename, data):
        self.images.append({"filename": filename, "data": data})

    def add_extra_page(self, filename, html_content):
        """Add an extra XHTML page (e.g. info.xhtml) that will be written
        to OEBPS/Text/{filename} and included in the manifest/spine before
        normal chapters. These pages are not added to the NCX TOC."""
        # filename should be a safe string like 'info.xhtml'
        self.extra_pages.append({"filename": filename, "content": html_content})

    def _create_container_xml(self):
        # Static content from EpubTemplate.cs 
        return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""

    def _create_toc_ncx(self):
        # Generates the Navigation Control file for the Table of Contents
        nav_points = ""
        for idx, chap in enumerate(self.chapters):
            play_order = idx + 1
            href = chap["filename"]
            nav_points += f"""
    <navPoint id="navPoint-{play_order}" playOrder="{play_order}">
        <navLabel><text>{html.escape(chap['title'])}</text></navLabel>
        <content src="Text/{href}"/>
    </navPoint>"""
        
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head>
    <meta name="dtb:uid" content="urn:uuid:{self.book_uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{html.escape(self.meta['title'])}</text></docTitle>
<navMap>{nav_points}</navMap>
</ncx>"""

    def _create_content_opf(self):
        # Generates the OPF manifest listing all resources
        manifest_items = []
        spine_refs = []

        # Required NCX and CSS resources
        manifest_items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        manifest_items.append('<item id="css" href="Styles/style.css" media-type="text/css"/>')

        # Cover page (HTML) first with a valid ID
        manifest_items.append('<item id="coverpage" href="Text/cover.html" media-type="application/xhtml+xml"/>')
        spine_refs.append('<itemref idref="coverpage"/>')

        # Extra pages (info.xhtml etc.) - include before normal chapters
        for extra in self.extra_pages:
            filename = extra["filename"]
            file_id = os.path.splitext(filename)[0]
            manifest_items.append(f'<item id="{file_id}" href="Text/{filename}" media-type="application/xhtml+xml"/>')
            spine_refs.append(f'<itemref idref="{file_id}"/>')

        # Chapters: use the concrete filenames stored per chapter.
        for idx, chap in enumerate(self.chapters):
            filename = chap["filename"]
            # Use a stable ID derived from the filename (without extension).
            file_id = os.path.splitext(filename)[0]
            manifest_items.append(f'<item id="{file_id}" href="Text/{filename}" media-type="application/xhtml+xml"/>')
            spine_refs.append(f'<itemref idref="{file_id}"/>')

        # Images
        # Images: assign stable, valid IDs; ensure cover.jpg gets id "cover-image"
        for index, img in enumerate(self.images):
            fname = img['filename']
            ext = fname.split('.')[-1].lower()
            if ext in ['jpg', 'jpeg']:
                mime = 'image/jpeg'
            elif ext == 'png':
                mime = 'image/png'
            elif ext == 'webp':
                mime = 'image/webp'
            elif ext == 'gif':
                mime = 'image/gif'
            elif ext == 'avif':
                mime = 'image/avif'
            else:
                mime = 'application/octet-stream'
            # Check if this is a cover image (any extension)
            if fname.startswith('cover.'):
                item_id = 'cover-image'
            else:
                item_id = f'img_{index+1}'
            manifest_items.append(f'<item id="{item_id}" href="Images/{fname}" media-type="{mime}"/>')

        # Optional metadata additions
        subjects = ''
        for tag in self.meta.get('tags', []) or []:
            subjects += f"\n    <dc:subject>{html.escape(str(tag))}</dc:subject>"
        description = self.meta.get('description') or ''
        description_xml = f"\n    <dc:description>{html.escape(description)}</dc:description>" if description else ''

        # Mark cover image for readers if present (any cover extension)
        cover_present = any(img['filename'].startswith('cover.') for img in self.images)
        cover_meta = "\n    <meta name=\"cover\" content=\"cover-image\"/>" if cover_present else ''

        return f"""<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" unique-identifier="BookId" xmlns="http://www.idpf.org/2007/opf">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{html.escape(self.meta['title'])}</dc:title>
    <dc:creator opf:role="aut">{html.escape(self.meta['author'])}</dc:creator>
    <dc:language>{self.meta.get('language', 'ko')}</dc:language>
    <dc:identifier id="BookId" opf:scheme="UUID">urn:uuid:{self.book_uuid}</dc:identifier>{description_xml}{subjects}{cover_meta}
</metadata>
<manifest>
    {chr(10).join(manifest_items)}
</manifest>
<spine toc="ncx">
    {chr(10).join(spine_refs)}
</spine>
<guide>
    <reference type="cover" title="Cover" href="Text/cover.html"/>
</guide>
</package>"""

    def _chapter_body_html(self, chap):
        title_html = (
            f"<h1>{html.escape(chap['title'])}</h1>"
            if chap.get('show_title', True) else ''
        )
        return f"{title_html}{chap['content']}"

    def _content_opf_text_paths(self):
        """Return reading-order Text/*.xhtml paths from content.opf."""
        try:
            with zipfile.ZipFile(self.output_path, "r") as zf:
                opf_text = zf.read("OEBPS/content.opf")
            root = ET.fromstring(opf_text)
        except Exception:
            return []

        ns = {"opf": "http://www.idpf.org/2007/opf"}
        manifest = {}
        for item in root.findall(".//opf:manifest/opf:item", ns):
            item_id = item.attrib.get("id")
            href = item.attrib.get("href")
            media_type = item.attrib.get("media-type", "")
            if item_id and href and media_type == "application/xhtml+xml":
                manifest[item_id] = href

        paths = []
        for itemref in root.findall(".//opf:spine/opf:itemref", ns):
            href = manifest.get(itemref.attrib.get("idref"))
            if href and href.startswith("Text/"):
                paths.append(f"OEBPS/{href}")
        return paths

    def content_retry_candidates(self, ratio_threshold=None, min_chars=None):
        """Find chapter XHTML files whose visible text is unexpectedly short.

        The content.opf spine is used as the source of truth for EPUB reading
        order, then matched back to the concrete chapter filenames generated by
        add_chapter(). Cover and extra pages are intentionally ignored.
        """
        ratio_threshold = (
            self.CONTENT_RETRY_RATIO if ratio_threshold is None
            else ratio_threshold
        )
        min_chars = (
            self.CONTENT_RETRY_MIN_CHARS if min_chars is None else min_chars
        )
        expected_by_path = {
            f"OEBPS/Text/{chap['filename']}": chap
            for chap in self.chapters
        }
        spine_paths = [
            path for path in self._content_opf_text_paths()
            if path in expected_by_path
        ]

        candidates = []
        try:
            with zipfile.ZipFile(self.output_path, "r") as zf:
                for path in spine_paths:
                    chap = expected_by_path[path]
                    expected_chars = _visible_text_char_count(
                        self._chapter_body_html(chap)
                    )
                    if expected_chars < min_chars:
                        continue
                    try:
                        actual_html = zf.read(path).decode("utf-8", "replace")
                    except KeyError:
                        actual_chars = 0
                    else:
                        actual_chars = _visible_text_char_count(actual_html)
                    ratio = actual_chars / expected_chars if expected_chars else 1
                    if ratio < ratio_threshold:
                        candidates.append({
                            "filename": path,
                            "title": chap.get("title", ""),
                            "actual_chars": actual_chars,
                            "expected_chars": expected_chars,
                            "ratio": ratio,
                        })
        except Exception:
            return candidates
        return candidates

    def _log_content_retry_candidates(self, logger):
        if not logger:
            return
        for item in self.content_retry_candidates():
            logger(
                "WARNING: EPUB content check: "
                f"{item['filename']} ({item['title']}) has "
                f"{item['actual_chars']}/{item['expected_chars']} visible "
                f"characters ({item['ratio']:.0%}, below "
                f"{self.CONTENT_RETRY_RATIO:.0%}); flagging for retry."
            )

    def generate(self, logger=None):
        with zipfile.ZipFile(self.output_path, "w") as zf:
            # 1. Write Mimetype (Uncompressed) - FIXES LEGACY BUG 
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            
            # 2. Container XML
            zf.writestr("META-INF/container.xml", self._create_container_xml(), compress_type=zipfile.ZIP_DEFLATED)
            
            # 3. CSS
            zf.writestr("OEBPS/Styles/style.css", self.css, compress_type=zipfile.ZIP_DEFLATED)
            
            # 4. Cover page (HTML with dynamic cover filename)
            cover_img_tag = ''
            cover_img = next((img for img in self.images if img['filename'].startswith('cover.')), None)
            if cover_img:
                cover_img_tag = f'<img alt="Cover" src="../Images/{cover_img["filename"]}" />'
            cover_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Cover</title><link href="../Styles/style.css" type="text/css" rel="stylesheet"/></head>
<body><div style="text-align:center">{cover_img_tag}</div></body></html>"""
            zf.writestr("OEBPS/Text/cover.html", cover_xhtml, compress_type=zipfile.ZIP_DEFLATED)
            # 5. Extra pages (info.xhtml etc.)
            for extra in self.extra_pages:
                xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{html.escape(self.meta.get('title',''))}</title><link href="../Styles/style.css" type="text/css" rel="stylesheet"/></head>
<body>{extra['content']}</body></html>"""
                zf.writestr(f"OEBPS/Text/{extra['filename']}", xhtml, compress_type=zipfile.ZIP_DEFLATED)

            # 6. Chapters
            for idx, chap in enumerate(self.chapters):
                title_html = (
                    f"<h1>{html.escape(chap['title'])}</h1>"
                    if chap.get('show_title', True) else ''
                )
                xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{html.escape(chap['title'])}</title><link href="../Styles/style.css" type="text/css" rel="stylesheet"/></head>
<body>{title_html}{chap['content']}</body></html>"""
                zf.writestr(f"OEBPS/Text/{chap['filename']}", xhtml, compress_type=zipfile.ZIP_DEFLATED)
                
            # 6. Images (use ZIP_STORED by default, ZIP_DEFLATED if user enables it)
            img_compress_type = zipfile.ZIP_DEFLATED if self.zip_compress_images else zipfile.ZIP_STORED
            for img in self.images:
                zf.writestr(f"OEBPS/Images/{img['filename']}", img['data'], compress_type=img_compress_type)
                
            # 7. TOC and OPF
            zf.writestr("OEBPS/toc.ncx", self._create_toc_ncx(), compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/content.opf", self._create_content_opf(), compress_type=zipfile.ZIP_DEFLATED)

        self._log_content_retry_candidates(logger)
