"""
Discord slash-command bot for Novelpia Downloader.

Requirements (install):
  pip install discord.py pillow requests

Usage:
  1) Put your Discord bot token in environment variable DISCORD_TOKEN.
  2) (Optional) Populate config.json with email/password or loginkey, same as GUI.
  3) Run: python bot.py

Command:
  /download
     novel_id            (required)
     start               (optional, chapter start number; enables range when set)
     end                 (optional, chapter end number; enables range when set)
     compress_images     (bool, default True)
     image_quality       (int 10-100, default 50)
     compress_cover      (bool, default False)
     cover_quality       (int 10-100, default 90)
     font_mapping_path   (string path to mapping json; optional)
     threads             (int, default 4)
     interval            (float seconds, min 0.5; values below fallback to 0.5)

The bot saves the EPUB/TXT to a temporary downloads/ folder and sends it back to the user.
"""
import asyncio
import os
import json
import re
import html
import io
import time
import tempfile
import threading
import http.server
import socketserver
import urllib.parse
import base64
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import discord
from discord import app_commands
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:
    AESGCM = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from PIL import Image
except Exception:
    Image = None

from novelpia_auth import NovelpiaAuth
from downloader_core import DownloaderCore
from epub_generator import EpubGenerator
from font_mapper import FontMapper

DEFAULT_THREADS = 4
MIN_INTERVAL = 0.5
DEFAULT_INTERVAL = 0.5
DEFAULT_IMG_QUALITY = 50
DEFAULT_COVER_QUALITY = 90
DISCORD_FILE_LIMIT = 10 * 1024 * 1024  # use uploads for anything larger
SELF_HOST_PORT = int(os.getenv("DL_HOST_PORT", "8080"))
SELF_HOST_BIND = os.getenv("DL_HOST_BIND", "0.0.0.0")
SELF_HOST_BASE = os.getenv("DL_HOST_BASE")
GOFILE_TOKEN = os.getenv("GOFILE_TOKEN")
ORACLE_PAR_BASE = os.getenv("ORACLE_PAR_BASE", "https://objectstorage.me-dubai-1.oraclecloud.com/p/20JGu8w1-ti2PBeyGZCxeKlMNy-kTnAUlMEo7Xtu9kHD05hJP3QOw_-6OLe12iuu/n/ax6s00dkbmzr/b/bucket-20251220-1009/o/")  # set to your PAR base ending with /o/
ADMIN_USERS = {"breadfull"}  # usernames (discriminator-less)
USER_CFG_MASTER_KEY = os.getenv("USER_CFG_MASTER_KEY", "")
USER_CFG_DIR = os.path.join(BASE_DIR, "user_configs")
os.makedirs(USER_CFG_DIR, exist_ok=True)
# cache derived keys for current process lifetime
_auth_key_cache: dict[int, bytes] = {}
CSS_TEMPLATE = """div.svg_outer {
   display: block;
   margin-bottom: 0;
   margin-left: 0;
   margin-right: 0;
   margin-top: 0;
   padding-bottom: 0;
   padding-left: 0;
   padding-right: 0;
   padding-top: 0;
   text-align: left;
}
div.svg_inner {
   display: block;
   text-align: center;
}
h1, h2 {
   text-align: center;
   margin-bottom: 10%;
   margin-top: 10%;
}
h3, h4, h5, h6 {
   text-align: center;
   margin-bottom: 15%;
   margin-top: 10%;
}
ol, ul {
   padding-left: 8%;
}
body {
  margin: 2%;
}
p {
  overflow-wrap: break-word;
}
dd, dt, dl {
  padding: 0;
  margin: 0;
}
img {
   display: block;
   min-height: 1em;
   max-height: 100%;
   max-width: 100%;
   padding-bottom: 0;
   padding-left: 0;
   padding-right: 0;
   padding-top: 0;
   margin-left: auto;
   margin-right: auto;
   margin-bottom: 2%;
   margin-top: 2%;
}
img.inline {
   display: inline;
   min-height: 1em;
   margin-bottom: 0;
   margin-top: 0;
}
.thumbcaption {
  display: block;
  font-size: 0.9em;
  padding-right: 5%;
  padding-left: 5%;
}
hr {
   color: black;
   background-color: black;
   height: 2px;
}
a:link {
   text-decoration: none;
   color: #0B0080;
}
a:visited {
   text-decoration: none;
}
a:hover {
   text-decoration: underline;
}
a:active {
   text-decoration: underline;
}table {
   width: 90%;
   border-collapse: collapse;
}
table, th, td {
   border: 1px solid black;
}
"""


def load_config():
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(cfg: dict):
    try:
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


# --- Per-user encrypted auth storage ---

def _require_crypto():
    if AESGCM is None:
        raise RuntimeError("cryptography not installed (pip install cryptography)")


def _get_key_bytes():
    if not USER_CFG_MASTER_KEY:
        raise RuntimeError("USER_CFG_MASTER_KEY not set")
    raw = USER_CFG_MASTER_KEY.strip().encode()
    if len(raw) == 32:
        return raw
    # accept hex/base64 or arbitrary string
    try:
        return base64.b64decode(raw, validate=False)[:32].ljust(32, b"\0")
    except Exception:
        pass
    h = hashlib.sha256(raw).digest()
    return h


def _encrypt_json(data: dict) -> str:
    _require_crypto()
    key = _get_key_bytes()
    aes = AESGCM(key)
    iv = secrets.token_bytes(12)
    plaintext = json.dumps(data).encode("utf-8")
    ct = aes.encrypt(iv, plaintext, None)  # ct||tag
    return base64.b64encode(iv + ct).decode("ascii")


def _decrypt_json(b64data: str) -> dict:
    _require_crypto()
    key = _get_key_bytes()
    raw = base64.b64decode(b64data)
    iv, ct = raw[:12], raw[12:]
    aes = AESGCM(key)
    plaintext = aes.decrypt(iv, ct, None)
    return json.loads(plaintext.decode("utf-8"))


def _derive_key_from_passphrase(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _encrypt_with_passphrase(data: dict, passphrase: str) -> dict:
    _require_crypto()
    salt = secrets.token_bytes(16)
    key = _derive_key_from_passphrase(passphrase, salt)
    aes = AESGCM(key)
    iv = secrets.token_bytes(12)
    ct = aes.encrypt(iv, json.dumps(data).encode("utf-8"), None)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "data": base64.b64encode(iv + ct).decode("ascii"),
    }


def _decrypt_with_passphrase(blob: dict, passphrase: str) -> dict:
    _require_crypto()
    salt_b = base64.b64decode(blob.get("salt", ""))
    data_b = base64.b64decode(blob.get("data", ""))
    if not salt_b or not data_b:
        raise ValueError("Missing salt/data")
    key = _derive_key_from_passphrase(passphrase, salt_b)
    aes = AESGCM(key)
    iv, ct = data_b[:12], data_b[12:]
    plaintext = aes.decrypt(iv, ct, None)
    return json.loads(plaintext.decode("utf-8"))


def _load_user_blob(user_id: int) -> dict:
    path = os.path.join(USER_CFG_DIR, f"{user_id}.enc")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            enc = f.read().strip()
        return _decrypt_json(enc)
    except Exception:
        return {}


def _save_user_blob(user_id: int, blob: dict):
    enc = _encrypt_json(blob)
    path = os.path.join(USER_CFG_DIR, f"{user_id}.enc")
    with open(path, "w", encoding="utf-8") as f:
        f.write(enc)


def save_user_auth(user_id: int, email: str, password: str, loginkey: str, passphrase: str | None):
    blob = _load_user_blob(user_id)
    if passphrase:
        enc = _encrypt_with_passphrase({"email": email, "wd": password, "loginkey": loginkey}, passphrase)
        blob["auth"] = {"mode": "passphrase", "payload": enc}
        _auth_key_cache[user_id] = _derive_key_from_passphrase(passphrase, base64.b64decode(enc["salt"]))
    else:
        blob["auth"] = {"mode": "master", "payload": {"email": email, "wd": password, "loginkey": loginkey}}
    _save_user_blob(user_id, blob)


def load_user_auth(user_id: int, passphrase: str | None = None) -> dict:
    blob = _load_user_blob(user_id)
    auth_blob = blob.get("auth")
    if not auth_blob:
        return {}

    mode = auth_blob.get("mode")
    payload = auth_blob.get("payload")
    if mode == "master":
        return payload or {}

    if mode == "passphrase":
        enc = payload or {}
        if user_id in _auth_key_cache:
            key = _auth_key_cache[user_id]
        elif passphrase:
            try:
                salt_b = base64.b64decode(enc.get("salt", ""))
                key = _derive_key_from_passphrase(passphrase, salt_b)
                _auth_key_cache[user_id] = key
            except Exception:
                key = None
        else:
            raise RuntimeError("Passphrase required; provide passphrase.")
        if not key:
            raise RuntimeError("Passphrase required; provide passphrase.")
        try:
            aes = AESGCM(key)
            data_b = base64.b64decode(enc.get("data", ""))
            iv, ct = data_b[:12], data_b[12:]
            pt = aes.decrypt(iv, ct, None)
            return json.loads(pt.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Decrypt failed; ensure passphrase is correct ({e})")

    # unknown format
    return {}


def save_user_prefs(user_id: int, prefs: dict):
    blob = _load_user_blob(user_id)
    blob["prefs"] = prefs
    _save_user_blob(user_id, blob)


def load_user_prefs(user_id: int) -> dict:
    blob = _load_user_blob(user_id)
    return blob.get("prefs", {})


def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c not in "\\/:*?\"<>|").strip()


def _strip_base64_blobs(text: str) -> str:
    # remove standalone or embedded long base64-ish tokens (noise from API)
    if not text:
        return text
    # First remove hidden paragraphs with base64 (common pattern)
    text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # remove embedded tokens (must be at least 40 chars and mostly base64 chars)
    text = re.sub(r"[A-Za-z0-9+/=]{40,}", "", text)
    return text


def extract_chapter_content_and_images(content_json, font_mapper, session, compress_images,
                                       jpeg_quality, image_format, logger, next_image_no):
    html_parts = []
    images = []
    try:
        data = json.loads(content_json)
        segments = data.get("s")
        if not isinstance(segments, list):
            return f"<p>{html.escape(str(data))}</p>", images

        img_pat = re.compile(r"<img[^>]+?(?:src|data-src|data-original)=[\"']([^\"']+)[\"'][^>]*>")
        data_url_pat = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+")

        first_img_logged = False
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text", "")
            if not text:
                continue
            if "cover-wrapper" in text:
                continue

            urls = img_pat.findall(text)
            if urls and not first_img_logged:
                logger(f"Found img url sample: {urls[0]}")
                first_img_logged = True
            # Inline data URLs not wrapped in <img>
            data_urls = data_url_pat.findall(text)
            if data_urls and not urls:
                urls = data_urls
            if urls:
                def handle_img_match(m_or_str):
                    url = m_or_str.group(1) if hasattr(m_or_str, "group") else m_or_str
                    try:
                        if url.startswith("data:image"):
                            # data URL: data:image/png;base64,...
                            import base64
                            header, b64 = url.split(',', 1)
                            mime = header.split(';')[0].split(':')[1] if ':' in header else 'image/png'
                            ext = 'png'
                            if 'jpeg' in mime:
                                ext = 'jpg'
                            elif 'webp' in mime:
                                ext = 'webp'
                            img_bytes = base64.b64decode(b64)
                        else:
                            if url.startswith("//"):
                                url_dl = "https:" + url
                            elif url.startswith("/"):
                                url_dl = "https://novelpia.com" + url
                            elif url.startswith("http"):
                                url_dl = url
                            else:
                                url_dl = "https://" + url.lstrip('/')
                            r = session.get(url_dl, timeout=15)
                            if r.status_code != 200 or not r.content:
                                logger(f"Image fetch failed ({r.status_code}): {url_dl}")
                                return ""
                            img_bytes = r.content
                            ext = "jpg"
                        if compress_images and Image is not None:
                            try:
                                im = Image.open(io.BytesIO(img_bytes))
                                if im.mode not in ("RGB", "L"):
                                    im = im.convert("RGB")
                                out = io.BytesIO()
                                if image_format == "WEBP":
                                    im.save(out, format="WEBP", quality=int(jpeg_quality))
                                    ext = "webp"
                                elif image_format == "PNG":
                                    im.save(out, format="PNG", optimize=True)
                                    ext = "png"
                                else:
                                    im.save(out, format="JPEG", quality=int(jpeg_quality), optimize=True)
                                    ext = "jpg"
                                img_bytes = out.getvalue()
                            except Exception:
                                pass
                        n = next_image_no()
                        fname = f"{n}.{ext}"
                        images.append((fname, img_bytes))
                        logger(f"Image saved ({ext}): {fname}")
                        return f"<img alt=\"{n}\" src=\"../Images/{fname}\" width=\"100%\"/>"
                    except Exception as ex:
                        logger(f"Image error: {ex}")
                        return ""

                # apply replacements for both <img> tags and raw data urls
                if data_urls and not img_pat.search(text):
                    for du in data_urls:
                        text = text.replace(du, handle_img_match(du))
                else:
                    text = img_pat.sub(handle_img_match, text)
                text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
                html_parts.append(f"<p>{text}</p>")
                continue

            text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
            # Before stripping tags, also convert bare data URLs to images
            if data_urls:
                for du in data_urls:
                    replacement = handle_img_match(du)
                    text = text.replace(du, replacement)
            text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
            text = text.replace("\n", "")
            text = _strip_base64_blobs(text)
            if not text or re.fullmatch(r"[A-Za-z0-9+/=]{40,}", text):
                continue
            text = html.unescape(text)
            text = _strip_base64_blobs(text)
            if font_mapper is not None:
                try:
                    text = font_mapper.decode(text)
                except Exception:
                    pass
            if text:
                html_parts.append(f"<p>{html.escape(text)}</p>")

        if not html_parts:
            return "<p>[No text segments found in chapter]</p>", images
        return "".join(html_parts), images
    except Exception as e:
        return f"<p>[Failed to parse chapter: {html.escape(str(e))}]</p>", images


def run_download(user_id: int,
                 novel_id: str, start: int | None, end: int | None, compress_images: bool | None,
                 image_quality: int | None, compress_cover: bool | None, cover_quality: int | None,
                 font_mapping_path: str | None, threads: int | None, interval: float | None,
                 save_format: str | None = None, include_notices: bool | None = None,
                 image_format: str | None = None, cover_format: str | None = None,
                 log_sink: list[str] | None = None,
                 passphrase: str | None = None) -> tuple[str, list[str]]:
    """Blocking download workflow. Returns (output_path, logs)."""
    auth_cfg = load_user_auth(user_id, passphrase) or {}
    prefs = load_user_prefs(user_id) or {}
    auth = NovelpiaAuth()
    if auth_cfg.get("email") and auth_cfg.get("wd"):
        auth.login(auth_cfg["email"], auth_cfg["wd"])
    elif auth_cfg.get("loginkey"):
        auth.set_manual_key(auth_cfg["loginkey"])

    # apply stored prefs as defaults when params are None
    compress_images = prefs.get("compress_images", True) if compress_images is None else compress_images
    image_quality = prefs.get("image_quality", DEFAULT_IMG_QUALITY) if image_quality is None else image_quality
    compress_cover = prefs.get("compress_cover", False) if compress_cover is None else compress_cover
    cover_quality = prefs.get("cover_quality", DEFAULT_COVER_QUALITY) if cover_quality is None else cover_quality
    font_mapping_path = prefs.get("font_mapping_path") if font_mapping_path is None else font_mapping_path
    threads = prefs.get("threads", DEFAULT_THREADS) if threads is None else threads
    interval = prefs.get("interval", DEFAULT_INTERVAL) if interval is None else interval
    save_format = prefs.get("save_format", "epub") if save_format is None else save_format
    include_notices = prefs.get("include_notices", True) if include_notices is None else include_notices
    image_format = prefs.get("image_format", "WEBP") if image_format is None else image_format
    cover_format = prefs.get("cover_format", "JPEG") if cover_format is None else cover_format

    logger_msgs: list[str] = []
    def logger(msg):
        logger_msgs.append(msg)
        if log_sink is not None:
            log_sink.append(msg)
        print(msg)

    downloader = DownloaderCore(auth, logger)
    font_mapper = FontMapper(font_mapping_path) if font_mapping_path else None

    meta = downloader.fetch_metadata(novel_id)
    if not meta:
        raise RuntimeError("Failed to fetch metadata.")

    # Bot-only safeguard: if title fell back to placeholder (e.g., gated page), try og:title once more
    if meta.get("title", "").startswith("Novel_"):
        try:
            page = auth.session.get(f"https://novelpia.com/novel/{novel_id}", timeout=15).text
            m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\'](.+?)["\']', page, flags=re.IGNORECASE)
            if m:
                meta["title"] = html.unescape(m.group(1))
        except Exception:
            pass

    chapters = downloader.fetch_chapter_list(novel_id)
    if not chapters:
        raise RuntimeError("No chapters found.")

    notice_items = []
    if include_notices:
        try:
            notice_items = downloader.fetch_notice_ids(novel_id) or []
            for n in notice_items:
                n['is_notice'] = True
        except Exception:
            notice_items = []

    # Range selection (enable when either provided)
    if start is not None or end is not None:
        start_idx = max(0, (start or 1) - 1)
        end_idx = min(len(chapters), end or len(chapters))
        selected = chapters[start_idx:end_idx]
    else:
        selected = chapters

    if not selected and not notice_items:
        raise RuntimeError("No chapters selected.")

    base_name = meta.get('title', f"novel_{novel_id}")
    safe_base = sanitize_filename(base_name)
    ext = 'epub' if save_format == 'epub' else 'txt'
    out_dir = os.path.join(os.getcwd(), 'downloads')
    os.makedirs(out_dir, exist_ok=True)
    filename = f"[{novel_id}] {safe_base}.{ext}"
    output_path = os.path.join(out_dir, filename)

    save_as_epub = save_format == 'epub'
    epub = EpubGenerator(meta, output_path if save_as_epub else f"temp.epub", CSS_TEMPLATE, zip_compress_images=False)

    # Cover
    if meta.get('cover_url'):
        try:
            r = auth.session.get(meta['cover_url'], timeout=15)
            if r.status_code == 200 and r.content:
                data = r.content
                mime = (r.headers.get("Content-Type") or "").lower()
                cover_ext = "jpg"

                # If not compressing, still ensure extension matches the data and convert WEBP to JPEG when possible
                if not compress_cover:
                    if "webp" in mime:
                        if Image is not None:
                            try:
                                im = Image.open(io.BytesIO(data))
                                if im.mode not in ("RGB", "L"):
                                    im = im.convert("RGB")
                                out = io.BytesIO()
                                im.save(out, format="JPEG", quality=cover_quality, optimize=True)
                                data = out.getvalue()
                                cover_ext = "jpg"
                            except Exception:
                                cover_ext = "webp"
                        else:
                            cover_ext = "webp"
                    elif "png" in mime:
                        cover_ext = "png"
                    elif "jpeg" in mime or "jpg" in mime:
                        cover_ext = "jpg"
                else:
                    if Image is not None:
                        try:
                            im = Image.open(io.BytesIO(data))
                            if im.mode not in ("RGB", "L"):
                                im = im.convert("RGB")
                            out = io.BytesIO()
                            if cover_format == "WEBP":
                                im.save(out, format="WEBP", quality=cover_quality)
                                cover_ext = "webp"
                            elif cover_format == "PNG":
                                im.save(out, format="PNG", optimize=True)
                                cover_ext = "png"
                            else:
                                im.save(out, format="JPEG", quality=cover_quality, optimize=True)
                                cover_ext = "jpg"
                            data = out.getvalue()
                        except Exception:
                            pass
                    else:
                        # No PIL: at least align extension to MIME if we can
                        if "webp" in mime:
                            cover_ext = "webp"
                        elif "png" in mime:
                            cover_ext = "png"
                epub.add_image(f'cover.{cover_ext}', data)
        except Exception:
            pass

    # Info page
    try:
        title = meta.get('title', '')
        author = meta.get('author', '')
        tags = meta.get('tags', []) or []
        tags_str = ', '.join([str(t) for t in tags]) if tags else ''
        status = meta.get('status', '')
        description = _strip_base64_blobs(meta.get('description', '') or '')

        info_parts = []
        info_parts.append(f"  <h1>{html.escape(title)}</h1>\n")
        info_parts.append(f"  <p><strong>Author:</strong> {html.escape(author)}</p>\n")
        if tags_str:
            info_parts.append(f"  <p><strong>Tags:</strong> {html.escape(tags_str)}</p>\n")
        if status:
            info_parts.append(f"  <p><strong>Status:</strong> {html.escape(status)}</p>\n")
        info_parts.append('\n')
        info_parts.append('  <h2 class="sigil_not_in_toc">Synopsis</h2>\n')
        if description:
            paras = re.split(r"\r?\n\s*\r?\n", description.strip())
            for para in paras:
                para = para.strip()
                if not para:
                    continue
                safe = html.escape(para).replace('\n', '<br/>')
                info_parts.append(f"  <p>{safe}</p>\n")

        info_html = "\n".join(info_parts)
        epub.add_extra_page('info.xhtml', info_html)
    except Exception:
        pass

    selected_total = (notice_items + selected) if notice_items else selected
    results = [None] * len(selected_total)

    def next_image_no():
        counter = {'v': 1}
        def _inner():
            val = counter['v']
            counter['v'] += 1
            return val
        return _inner
    next_img = next_image_no()

    with ThreadPoolExecutor(max_workers=threads) as executor:
        for i in range(0, len(selected_total), threads):
            batch = range(i, min(i + threads, len(selected_total)))
            f_map = {executor.submit(downloader.download_chapter_content, selected_total[x]['id']): x for x in batch}
            for future in as_completed(f_map):
                idx = f_map[future]
                chap = selected_total[idx]
                try:
                    content_json = future.result()
                    if content_json:
                        hb, imgs = extract_chapter_content_and_images(
                            content_json, font_mapper, auth.session,
                            compress_images, image_quality,
                            image_format, logger, next_img
                        )
                        results[idx] = (chap['title'], hb, imgs, chap.get('is_notice', False))
                        logger(f"Downloaded: {chap['title']}")
                except Exception as e:
                    logger(f"Error {chap.get('title','?')}: {e}")
            if interval > 0:
                time.sleep(interval)

    if save_as_epub:
        total_imgs = 0
        for res in results:
            if res:
                t, h, imgs, notice = res
                for name, data in imgs:
                    epub.add_image(name, data)
                    total_imgs += 1
                epub.add_chapter(t, h, is_notice=notice)
        logger(f"Total images added to EPUB: {total_imgs}")
        epub.generate()
    else:
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                for res in results:
                    if res:
                        t, h, _, _ = res
                        plain = re.sub(r"</?[^>]+>", "", h)
                        f.write(f"{t}\n\n{html.unescape(plain)}\n\n")
        except Exception as e:
            raise RuntimeError(f"Save failed: {e}")

    return output_path, logger_msgs


class NovelpiaBot(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.allowed_users = None  # no allowlist needed now

    async def setup_hook(self):
        await self.tree.sync()

intents = discord.Intents.default()
bot = NovelpiaBot(intents=intents)

# --- Simple static file server for self-hosting downloads (fallback only) ---
_download_dir = os.path.join(os.getcwd(), "downloads")
_server_thread = None
_server_ready = threading.Event()


def _start_file_server():
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return

    handler = http.server.SimpleHTTPRequestHandler
    # functools.partial not imported; create subclass to set directory
    class Handler(handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=_download_dir, **kwargs)

    def run_server():
        with socketserver.TCPServer((SELF_HOST_BIND, SELF_HOST_PORT), Handler) as httpd:
            httpd.allow_reuse_address = True
            _server_ready.set()
            httpd.serve_forever()

    _server_thread = threading.Thread(target=run_server, daemon=True)
    _server_thread.start()
    _server_ready.wait(timeout=2)


def _file_url(path: str) -> str:
    rel = os.path.relpath(path, _download_dir).replace("\\", "/")
    base = SELF_HOST_BASE or f"http://{SELF_HOST_BIND}:{SELF_HOST_PORT}"
    return f"{base}/{urllib.parse.quote(rel)}"


def _send_any(interaction: discord.Interaction, content: str, file: discord.File | None = None):
    """Send via DM only; if DM fails, fall back to ephemeral followup."""
    async def _inner():
        try:
            await interaction.user.send(content, file=file)
            return
        except Exception:
            try:
                await interaction.followup.send(content, ephemeral=True, file=file)
            except Exception:
                pass
    return asyncio.ensure_future(_inner())


def _upload_to_tmpfiles(path: str) -> str:
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f, "application/octet-stream")}
        resp = requests.post("https://tmpfiles.org/api/v1/upload", files=files, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"tmpfiles HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        data = {}
    link = data.get("data", {}).get("url") if isinstance(data.get("data"), dict) else None
    if not link:
        text = resp.text.strip()
        if text.startswith("http"):
            link = text
    if not link:
        raise RuntimeError(f"tmpfiles response invalid: {resp.text[:200]}")
    return link


def _upload_to_oracle_par(path: str) -> str:
    base = ORACLE_PAR_BASE
    if not base:
        raise RuntimeError("ORACLE_PAR_BASE not set")
    base = base.rstrip('/') + '/'
    filename = os.path.basename(path)
    url = base + requests.utils.quote(filename)
    with open(path, "rb") as f:
        resp = requests.put(url, data=f, headers={"Content-Type": "application/octet-stream"}, timeout=120)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Oracle upload failed HTTP {resp.status_code}: {resp.text[:200]}")
    return url

def _upload_to_tempsh(path: str) -> str:
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f, "application/octet-stream")}
        resp = requests.post("https://temp.sh/upload", files=files, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"temp.sh HTTP {resp.status_code}")
    link = resp.text.strip()
    if not link.startswith("http"):
        raise RuntimeError(f"temp.sh response invalid: {link[:200]}")
    return link

def _upload_to_transfersh(path: str) -> str:
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        resp = requests.put(f"https://transfer.sh/{filename}", data=f, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"transfer.sh upload failed ({resp.status_code}): {resp.text[:200]}")
    return resp.text.strip()


def _upload_to_fileio(path: str) -> str:
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f, "application/octet-stream")}
        resp = requests.post("https://file.io/", files=files, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"file.io HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not data.get("success") or not data.get("link"):
        raise RuntimeError("file.io no link")
    return data.get("link")
    return data.get("link")


def _upload_to_sendcm(path: str) -> str:
    endpoints = ["https://api.send.cm/upload", "https://send.cm/api/upload"]
    last_err = None
    for ep in endpoints:
        try:
            with open(path, "rb") as f:
                files = {"file": (os.path.basename(path), f, "application/octet-stream")}
                resp = requests.post(ep, files=files, timeout=30)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                link = data.get("download_url") or data.get("url") or data.get("link")
                if link:
                    return link
            last_err = RuntimeError(f"send.cm HTTP {resp.status_code}")
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("send.cm upload failed")


def _upload_to_pixeldrain(path: str) -> str:
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f, "application/octet-stream")}
        resp = requests.post("https://pixeldrain.com/api/file", files=files, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Pixeldrain HTTP {resp.status_code}")
    data = resp.json()
    if not data.get("id"):
        raise RuntimeError("Pixeldrain: missing id")
    file_id = data["id"]
    return f"https://pixeldrain.com/u/{file_id}"


def _upload_to_catbox(path: str) -> str:
    # Limit ~200MB
    with open(path, "rb") as f:
        files = {"fileToUpload": (os.path.basename(path), f, "application/octet-stream")}
        data = {"reqtype": "fileupload"}
        resp = requests.post("https://catbox.moe/user/api.php", data=data, files=files, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"catbox upload failed ({resp.status_code}): {resp.text[:200]}")
    link = resp.text.strip()
    if not link.startswith("http"):
        raise RuntimeError(f"catbox response invalid: {link[:200]}")
    return link


def _upload_to_0x0(path: str) -> str:
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f, "application/octet-stream")}
        resp = requests.post("https://0x0.st", files=files, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"0x0.st upload failed ({resp.status_code}): {resp.text[:200]}")
    link = resp.text.strip()
    if not link.startswith("http"):
        raise RuntimeError(f"0x0.st response invalid: {link[:200]}")
    return link


def _upload_to_oracle_par(path: str) -> str:
    base = ORACLE_PAR_BASE
    if not base:
        raise RuntimeError("ORACLE_PAR_BASE not set")
    base = base.rstrip('/') + '/'
    filename = os.path.basename(path)
    url = base + requests.utils.quote(filename)
    with open(path, "rb") as f:
        resp = requests.put(url, data=f, headers={"Content-Type": "application/octet-stream"}, timeout=120)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Oracle upload failed HTTP {resp.status_code}: {resp.text[:200]}")
    return url


def _upload_to_krakenfiles(path: str) -> str:
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f, "application/octet-stream")}
        resp = requests.post("https://krakenfiles.com/api/upload", files=files, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Krakenfiles HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        data = {}
    if data.get("status") not in ("success", True):
        raise RuntimeError("Krakenfiles status not success")
    url = data.get("data", {}).get("url")
    if not url:
        raise RuntimeError(f"Krakenfiles response missing url: {data}")
    return url


def _upload_to_gofile(path: str) -> str:
    last_err = None
    for attempt in range(3):
        try:
            server_resp = requests.get("https://api.gofile.io/getServer", timeout=15)
            server_json = server_resp.json()
            server = server_json.get("data", {}).get("server")
            if not server:
                raise RuntimeError(f"Failed to get Gofile server: {server_json}")

            with open(path, "rb") as f:
                files = {"file": (os.path.basename(path), f, "application/octet-stream")}
                data = {"token": GOFILE_TOKEN} if GOFILE_TOKEN else {}
                upload_resp = requests.post(
                    f"https://{server}.gofile.io/uploadFile",
                    files=files,
                    data=data,
                    timeout=180,
                )
                try:
                    resp_json = upload_resp.json()
                except Exception:
                    resp_json = {"raw": upload_resp.text}
            if upload_resp.status_code == 200 and resp_json.get("status") == "ok":
                link = resp_json.get("data", {}).get("directLink") or resp_json.get("data", {}).get("downloadPage")
                if link:
                    return link
                raise RuntimeError(f"Gofile response missing link: {resp_json}")
            last_err = RuntimeError(f"Gofile upload failed ({upload_resp.status_code}): {resp_json}")
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("Unknown Gofile upload error")


def clamp_interval(value: float) -> float:
    try:
        v = float(value)
    except Exception:
        return MIN_INTERVAL
    return v if v >= MIN_INTERVAL else MIN_INTERVAL


@bot.tree.command(name="login", description="Save your Novelpia login credentials")
@app_commands.describe(email="Account email", password="Account password", passphrase="Optional passphrase to encrypt your creds (omit to use master key)")
async def login_cmd(interaction: discord.Interaction, email: str, password: str, passphrase: str | None = None):
    existing = _load_user_blob(interaction.user.id).get("auth")
    # If previously passphrase-protected and now no passphrase provided, reject to avoid lockout confusion
    if existing and existing.get("mode") == "passphrase" and not passphrase:
        await interaction.response.send_message("Passphrase needed to update existing passphrase-protected credentials.", ephemeral=True)
        return
    try:
        save_user_auth(interaction.user.id, email, password, "", passphrase)
        await interaction.response.send_message("Credentials saved with your passphrase. Keep it safe; the bot cannot recover it.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed to save credentials: {e}", ephemeral=True)


@bot.tree.command(name="reset", description="Clear your saved credentials and settings")
async def reset_cmd(interaction: discord.Interaction):
    path = os.path.join(USER_CFG_DIR, f"{interaction.user.id}.enc")
    try:
        if os.path.exists(path):
            os.remove(path)
        _auth_key_cache.pop(interaction.user.id, None)
        await interaction.response.send_message("Your saved credentials and settings have been cleared.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Reset failed: {e}", ephemeral=True)


@bot.tree.command(name="setting", description="Show your saved settings and prefs")
async def setting_cmd(interaction: discord.Interaction):
    prefs = load_user_prefs(interaction.user.id) or {}
    try:
        auth = load_user_auth(interaction.user.id)
    except Exception:
        auth = {"locked": True}
    if not prefs and not auth:
        await interaction.response.send_message("No saved settings yet. Use /login and /download first.", ephemeral=True)
        return
    lines = ["Your saved settings (excluding ranges):"]
    def fmt_bool(val):
        return "True" if val else "False"
    lines.append(f"compress_images: {fmt_bool(prefs.get('compress_images', True))}")
    lines.append(f"image_quality: {prefs.get('image_quality', DEFAULT_IMG_QUALITY)}")
    lines.append(f"compress_cover: {fmt_bool(prefs.get('compress_cover', False))}")
    lines.append(f"cover_quality: {prefs.get('cover_quality', DEFAULT_COVER_QUALITY)}")
    lines.append(f"include_notices: {fmt_bool(prefs.get('include_notices', True))}")
    lines.append(f"threads: {prefs.get('threads', DEFAULT_THREADS)}")
    lines.append(f"interval: {prefs.get('interval', DEFAULT_INTERVAL)}")
    lines.append(f"save_format: {prefs.get('save_format', 'epub')}")
    lines.append(f"image_format: {prefs.get('image_format', 'WEBP')}")
    lines.append(f"cover_format: {prefs.get('cover_format', 'JPEG')}")
    fm = prefs.get('font_mapping_path') or "(none)"
    lines.append(f"font_mapping_path: {fm}")
    if auth.get("locked"):
        lines.append("Credentials: saved but locked (provide passphrase in /download)")
    elif auth:
        mode = (_load_user_blob(interaction.user.id).get("auth") or {}).get("mode")
        if mode == "passphrase":
            lines.append("Credentials: saved (passphrase-protected)")
        else:
            lines.append("Credentials: saved (master-key protected)")
    else:
        lines.append("Credentials: not saved (run /login)")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="download", description="Download a Novelpia novel and return the EPUB/TXT.")
@app_commands.describe(
    novel_id="Novel ID (required)",
    start="Chapter start number (optional)",
    end="Chapter end number (optional)",
    compress_images="Compress chapter images (default True)",
    image_quality="Image quality 10-100 (default 50)",
    compress_cover="Compress cover image (default False)",
    cover_quality="Cover quality 10-100 (default 90)",
    font_mapping_path="Path to font mapping json (optional)",
    include_notices="Download author notices (default False)",
    threads="Download threads (default 4)",
    interval="Delay between batches in seconds (min 0.5, default 0.5)",
    save_format="epub or txt (default epub)")
@app_commands.choices(save_format=[
    app_commands.Choice(name="EPUB", value="epub"),
    app_commands.Choice(name="TXT", value="txt"),
])
async def download_cmd(interaction: discord.Interaction, novel_id: str,
                       start: int | None = None, end: int | None = None,
                       compress_images: bool | None = None, image_quality: int | None = None,
                       compress_cover: bool | None = None, cover_quality: int | None = None,
                       font_mapping_path: str | None = None,
                       include_notices: bool | None = None,
                       threads: int | None = None, interval: float | None = None,
                       save_format: str | None = None,
                       passphrase: str | None = None):

    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        auth_cfg = load_user_auth(interaction.user.id, passphrase)
    except Exception as e:
        await interaction.followup.send(f"Cannot decrypt creds: {e}", ephemeral=True)
        return
    if not auth_cfg:
        await interaction.followup.send("No credentials found. Please run /login first.", ephemeral=True)
        return

    # initial status message to edit with live logs
    status_msg = await interaction.followup.send("Starting download...", wait=True)

    logs_shared: list[str] = [f"Starting download for novel {novel_id}"]
    done_evt = asyncio.Event()

    # Immediate first update
    try:
        await interaction.followup.edit_message(status_msg.id, content=f"Working...\nLogs:\n```{logs_shared[0]}```")
    except Exception:
        pass

    async def stream_logs():
        last_len = -1  # force initial update
        while not done_evt.is_set():
            await asyncio.sleep(1)
            if len(logs_shared) != last_len:
                last_len = len(logs_shared)
                snippet = "\n".join(logs_shared[-10:]) or "(no logs yet)"
                if len(snippet) > 1800:
                    snippet = "...\n" + snippet[-1800:]
                try:
                    await status_msg.edit(content=f"Working...\nLogs:\n```{snippet}```")
                except Exception as ex:
                    print(f"[log-stream] edit failed: {ex}")
                    pass
        # final refresh after completion signal
        snippet = "\n".join(logs_shared[-10:]) or "(no logs yet)"
        if len(snippet) > 1800:
            snippet = "...\n" + snippet[-1800:]
        try:
            await status_msg.edit(content=f"Finishing...\nLogs:\n```{snippet}```")
        except Exception:
            pass

    streamer_task = asyncio.create_task(stream_logs())

    # fill defaults if omitted (so we can clamp safely)
    if image_quality is None:
        image_quality = DEFAULT_IMG_QUALITY
    if cover_quality is None:
        cover_quality = DEFAULT_COVER_QUALITY
    if threads is None:
        threads = DEFAULT_THREADS
    if interval is None:
        interval = DEFAULT_INTERVAL
    if include_notices is None:
        include_notices = True

    image_quality = max(10, min(100, image_quality))
    cover_quality = max(10, min(100, cover_quality))
    threads = max(1, min(4, threads))
    interval = clamp_interval(interval)
    save_format = (save_format or "epub").lower()
    if save_format not in ("epub", "txt"):
        save_format = "epub"

    loop = asyncio.get_running_loop()
    try:
        output_path, logs = await loop.run_in_executor(
            None,
            run_download,
            interaction.user.id,
            novel_id, start, end,
            compress_images, image_quality,
            compress_cover, cover_quality,
            font_mapping_path, threads, interval,
            save_format, include_notices,
            "WEBP", "JPEG",  # default formats
            logs_shared,
            passphrase,
        )
    except Exception as e:
        done_evt.set()
        streamer_task.cancel()
        await _send_any(interaction, f"Download failed: {e}")
        return

    # keep streaming through upload; we'll stop after upload attempts

    def log_snippet(lines: int = 10) -> str:
        source = logs_shared if logs_shared else logs
        if not source:
            return "(no logs)"
        tail = source[-lines:]
        txt = "\n".join(tail)
        if len(txt) > 1800:
            txt = "...\n" + txt[-1800:]
        return txt

    # Final log update on status message
    try:
        final_snip = "\n".join(logs_shared[-10:]) if logs_shared else "(no logs)"
        if len(final_snip) > 1800:
            final_snip = "...\n" + final_snip[-1800:]
        await status_msg.edit(content=f"Finishing...\nLogs:\n```{final_snip}```")
    except Exception:
        pass

    # persist user prefs (exclude ranges)
    image_format = "WEBP"
    cover_format = "JPEG"
    prefs_to_save = {
        "compress_images": compress_images,
        "image_quality": image_quality,
        "compress_cover": compress_cover,
        "cover_quality": cover_quality,
        "font_mapping_path": font_mapping_path,
        "threads": threads,
        "interval": interval,
        "save_format": save_format,
        "include_notices": include_notices,
        "image_format": image_format,
        "cover_format": cover_format,
    }
    save_user_prefs(interaction.user.id, prefs_to_save)

    file_size = os.path.getsize(output_path)
    if file_size <= DISCORD_FILE_LIMIT:
        logs_shared.append("Uploading to Discord...")
        send_ok = False
        try:
            await interaction.user.send(
                f"Done!\nLast logs:\n```{log_snippet()}```",
                file=discord.File(output_path)
            )
            send_ok = True
            logs_shared.append("Discord DM upload succeeded")
        except Exception as e_dm:
            logs_shared.append(f"Discord DM upload failed: {e_dm}")
            # Only if DM fails, fall back to ephemeral server reply
            try:
                await interaction.followup.send(
                    f"Done! (DM failed, sending here instead)\nLast logs:\n```{log_snippet()}```",
                    file=discord.File(output_path),
                    ephemeral=True,
                )
                send_ok = True
                logs_shared.append("Discord ephemeral upload succeeded")
            except Exception as e_ep:
                logs_shared.append(f"Discord ephemeral upload failed: {e_ep}")
        if send_ok:
            done_evt.set()
            try:
                streamer_task.cancel()
            except Exception:
                pass
            return
        # fall through to upload flow if Discord rejected attachment (e.g., >8MB limit)
        logs_shared.append("Falling back to upload hosts due to Discord file send failure")

    # Large file or Discord send failed: try multiple hosts
    upload_errors = []
    upload_hosts = (
        ( _upload_to_tempsh, "temp.sh"),
        ( _upload_to_tmpfiles, "tmpfiles.org"),
        ( _upload_to_oracle_par, "oracle"),
        ( _upload_to_pixeldrain, "Pixeldrain"),
        ( _upload_to_sendcm, "send.cm"),
        ( _upload_to_catbox, "catbox"),
        ( _upload_to_0x0, "0x0.st"),
        ( _upload_to_fileio, "file.io"),
        ( _upload_to_krakenfiles, "Krakenfiles"),
    )

    def _short_err_text(err: Exception) -> str:
        s = str(err)
        # Strip simple HTML tags to keep logs readable
        s = re.sub(r"<[^>]+>", "", s)
        return (s[:300] + "...") if len(s) > 300 else s

    for uploader, name in upload_hosts:
        logs_shared.append(f"Uploading via {name}...")
        try:
            url = await asyncio.wait_for(loop.run_in_executor(None, uploader, output_path), timeout=60)
            logs_shared.append(f"Upload via {name} succeeded: {url}")
            fname = os.path.basename(output_path)
            await _send_any(interaction,
                            f"File is large (~{file_size/1024/1024:.1f} MB). Download via {name}: {fname}\n<{url}>\n\nLogs:\n```{log_snippet()}```")
            done_evt.set()
            try:
                streamer_task.cancel()
            except Exception:
                pass
            return
        except Exception as e:
            short_msg = _short_err_text(e)
            logs_shared.append(f"Upload via {name} failed: {short_msg}")
            upload_errors.append(f"{name}: {short_msg}")
            # give the streamer a chance to show the failure before next host
            await asyncio.sleep(0.5)
            continue

    # Fallback to local self-host
    _start_file_server()
    url = _file_url(output_path)
    await _send_any(interaction,
                    f"All uploads failed: {'; '.join(upload_errors)}\nFallback link (requires your host reachable):\n<{url}>\n\nLogs:\n```{log_snippet()}```")

    done_evt.set()
    try:
        streamer_task.cancel()
    except Exception:
        pass


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN environment variable.")
    bot.run(token)


if __name__ == "__main__":
    main()
