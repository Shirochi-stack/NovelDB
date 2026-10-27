"""Chapter text and image extraction for the npia pipeline (moved from gui.py)."""

import html
import io
import json
import re

from downloader_core import DownloaderCore, validate_chapter_payload
from noveldb.core.images import (
    Image,
    _detect_source_ext,
    _download_image_with_progress,
    _encode_image_bytes,
    _format_size,
)


def _strip_leading_whitespace(text):
    """Remove leading whitespace (including \u00a0 / &nbsp;) that Npia
    injects at the start of every text segment.  Works on both raw HTML
    text and already-unescaped text, without removing line breaks."""
    # Newlines are controlled separately by the "Remove Newlines" option.
    text = re.sub(r'^(?:[ \t\f\v\u00a0]|&nbsp;)+', '', text)
    return text


def process_text_content(
    content_json,
    strip_leading_spaces=False,
    remove_newlines=False,
):
    """Parse the viewer_data JSON into readable HTML paragraphs."""
    try:
        data = json.loads(content_json)
        segments = data.get("s")
        if not isinstance(segments, list):
            return f"<p>{html.escape(str(data))}</p>"

        paragraph_html = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text", "")
            if not text:
                continue
            if "cover-wrapper" in text:
                continue

            text = re.sub(r"<img.+?>", "", text)
            text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
            if remove_newlines:
                text = re.sub(r"<br\s*/?>", "", text, flags=re.IGNORECASE)
                text = text.replace("\r", "").replace("\n", "")
            else:
                text = re.sub(r"<br\s*/?>", "<br/>", text, flags=re.IGNORECASE)
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                text = text.replace("\n", "<br/>")
            if strip_leading_spaces:
                text = _strip_leading_whitespace(text)
            paragraph_html.append(text)

        if not paragraph_html:
            return "<p>[No text segments found in chapter]</p>"

        return "".join(paragraph_html)
    except Exception as e:
        return f"<p>[Failed to parse chapter: {html.escape(str(e))}]</p>"


def extract_chapter_content_and_images(
    content_json,
    font_mapper,
    session,
    compress_images,
    jpeg_quality,
    image_format,
    logger,
    next_image_no,
    chapter_title="",
    chapter_num=None,
    convert_gifs=False,
    static_only=False,
    save_image_urls_only=False,
    strip_leading_spaces=False,
    remove_newlines=False,
    max_retries=3,
    retry_delay=2.0,
):
    """Return (html, images, image_failures).

    image_failures is the number of <img> tags whose download/processing
    failed; callers can use it to avoid baking a "hole" into the full
    image cache (see _download_worker's cache-store logic).

    In URL-only mode, source images remain viewable remote ``<img>`` elements
    and the session is never used to fetch their bytes.

    When `static_only` is True every animated source (GIF, animated WebP,
    APNG, animated AVIF) is flattened to its first frame — applied even to
    the GIF-preservation branch so it overrides `convert_gifs=False`.
    """
    # Validate outside the broad processing try/except. An error payload must
    # fail the chapter instead of becoming a visible paragraph in the EPUB.
    validated_data = validate_chapter_payload(content_json)
    html_parts = []
    images = []
    _img_counter = [0]
    _img_failures = [0]
    try:
        data = validated_data
        segments = data.get("s")
        if not isinstance(segments, list):
            return f"<p>{html.escape(str(data))}</p>", images, 0

        img_pat = re.compile(
            r"<img\b[^>]*\bsrc=(['\"])(.*?)\1[^>]*>",
            flags=re.IGNORECASE | re.DOTALL,
        )

        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text", "")
            if not text:
                continue
            if "cover-wrapper" in text:
                continue

            urls = img_pat.findall(text)
            if urls:
                # Use a single-pass regex substitution with a callback to handle each <img> sequentially.
                def handle_img_match(m):
                    url = m.group(2)

                    _img_counter[0] += 1
                    ch_tag = f" [{chapter_title}]" if chapter_title else ""
                    ch_num = f"[{chapter_num}] " if chapter_num is not None else ""
                    lbl = f"{ch_num}Image #{_img_counter[0]}{ch_tag}"

                    # Dead references (Word/HWP paste leftovers, blank srcs) can
                    # never succeed, so drop them without marking the chapter as
                    # retryable \u2014 otherwise its images are re-processed forever.
                    skip_reason = DownloaderCore.unfetchable_image_reason(url)
                    if skip_reason:
                        logger(f"  \u26a0 {lbl}: skipped, {skip_reason}")
                        return ""

                    if save_image_urls_only:
                        remote_tag = DownloaderCore.remote_image_html(
                            url,
                            alt=f"Image {_img_counter[0]}",
                        )
                        if remote_tag:
                            return remote_tag
                        logger(f"  \u2717 {lbl}: invalid image URL")
                        _img_failures[0] += 1
                        return ""

                    url_dl = DownloaderCore.normalize_chapter_image_url(url)
                    if not url_dl:
                        logger(f"  \u2717 {lbl}: invalid image URL")
                        _img_failures[0] += 1
                        return ""
                    try:
                        img_bytes = _download_image_with_progress(
                            session,
                            url_dl,
                            logger,
                            label=lbl,
                            max_retries=max_retries,
                            retry_delay=retry_delay,
                        )
                        if not img_bytes:
                            _img_failures[0] += 1
                            return ""
                        # Detect the *actual* format from the downloaded bytes.
                        # Relying on the URL extension loses GIFs served from
                        # extension-less URLs (common on Npia's imagebox).
                        ext = _detect_source_ext(img_bytes, url_dl)
                        original_size = len(img_bytes)
                        if compress_images and Image is not None:
                            try:
                                im = Image.open(io.BytesIO(img_bytes))
                                # Default behavior: preserve GIFs as .gif so animation
                                # is kept byte-for-byte. If the user opted in to
                                # "Convert GIFs" OR "Static images only", fall through
                                # to the shared encoder (which flattens when asked and
                                # preserves animation for WEBP/AVIF/APNG otherwise).
                                if ext == "gif" and not convert_gifs and not static_only:
                                    out = io.BytesIO()
                                    im.save(out, format="GIF", save_all=True, optimize=True)
                                    img_bytes = out.getvalue()
                                elif ext == "gif" and not convert_gifs and static_only:
                                    # Keep the .gif extension but drop to a single frame.
                                    out = io.BytesIO()
                                    im.save(out, format="GIF", optimize=True)
                                    img_bytes = out.getvalue()
                                else:
                                    img_bytes, ext = _encode_image_bytes(
                                        im, image_format, jpeg_quality,
                                        logger=logger, static_only=static_only,
                                    )
                            except Exception as conv_err:
                                logger(f"  \u26a0 {lbl}: conversion failed ({conv_err}), keeping original.")
                            if len(img_bytes) < original_size:
                                saved = (1 - len(img_bytes) / original_size) * 100
                                logger(f"  ⚙ {lbl}: {_format_size(original_size)} → {_format_size(len(img_bytes))} ({saved:.0f}% saved)")

                        n = next_image_no()
                        fname = f"{n}.{ext}"
                        images.append((fname, img_bytes))
                        return f'<img alt="{n}" src="../Images/{fname}" width="100%"/>'
                    except Exception as ex:
                        logger(f"  \u2717 {lbl}: {ex}")
                        # Remove tag on exception to match gui.py
                        _img_failures[0] += 1
                        return ""

                text = img_pat.sub(handle_img_match, text)
                text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
                if remove_newlines:
                    text = re.sub(r"<br\s*/?>", "", text, flags=re.IGNORECASE)
                    text = text.replace("\r", "").replace("\n", "")
                else:
                    text = re.sub(r"<br\s*/?>", "<br/>", text, flags=re.IGNORECASE)
                    text = text.replace("\r\n", "\n").replace("\r", "\n")
                    text = text.replace("\n", "<br/>")
                html_parts.append(f"<p>{text}</p>")
                continue

            text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
            # Preserve inline formatting tags through the pipeline
            _fmt_placeholders = {}
            _fmt_counter = [0]
            def _save_fmt_tag(m):
                key = f"\x00FMT{_fmt_counter[0]}\x00"
                _fmt_counter[0] += 1
                _fmt_placeholders[key] = m.group(0)
                return key
            preserved_tags = (
                r'b|strong|i|em|u|s|strike|del|sub|sup|ruby|rb|rt|rp|rtc'
            )
            if not remove_newlines:
                preserved_tags += r'|br'
            text = re.sub(rf'</?(?:{preserved_tags})\b[^>]*>',
                          _save_fmt_tag, text, flags=re.IGNORECASE)
            # Remove remaining HTML tags (tags starting with ASCII letters)
            # This preserves Korean/other text in angle brackets like <주인공>
            text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
            if remove_newlines:
                text = text.replace("\r", "").replace("\n", "")
                if not text.replace('\x00', '').strip():
                    continue
            else:
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                if not text and not _fmt_placeholders:
                    continue
            text = html.unescape(text)
            if strip_leading_spaces:
                text = _strip_leading_whitespace(text)
            if font_mapper is not None:
                try:
                    text = font_mapper.decode(text)
                except Exception:
                    pass
            if text:
                # Escape the text for safe HTML output (this will convert < to &lt; and > to &gt;)
                safe = html.escape(text)
                if not remove_newlines:
                    safe = safe.replace("\n", "<br/>")
                # Restore preserved formatting tags
                for key, tag in _fmt_placeholders.items():
                    if re.match(r"</?br\b", tag, flags=re.IGNORECASE):
                        tag = "<br/>"
                    safe = safe.replace(key, tag)
                html_parts.append(f"<p>{safe}</p>")

        if not html_parts:
            return "<p>[No text segments found in chapter]</p>", images, _img_failures[0]
        return "".join(html_parts), images, _img_failures[0]
    except Exception as e:
        return f"<p>[Failed to parse chapter: {html.escape(str(e))}]</p>", images, _img_failures[0]
