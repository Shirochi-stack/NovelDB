import re
import json
import time
import threading
import html
import base64
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from novelpia_search_terms import (
    ALL_SEARCH_TERMS,
    DEFAULT_SEARCH_QUERY_COUNT,
    RETRYABLE_STATUS_CODES,
)

class AccessBlockedError(Exception):
    """Raised when chapter access is blocked (login/age verification required)."""
    pass


class InvalidChapterPayloadError(ValueError):
    """Raised when viewer_data is an error response instead of chapter JSON."""
    pass


def validate_chapter_payload(payload):
    """Return parsed chapter JSON or reject HTML/JSON server errors."""
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload or "")
    stripped = text.lstrip()
    html_probe = stripped[:4096].lower()
    if html_probe.startswith(("<!doctype html", "<html")):
        raise InvalidChapterPayloadError(
            "HTTP 200 contained an HTML error page"
        )

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise InvalidChapterPayloadError(
            f"invalid viewer JSON: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise InvalidChapterPayloadError("viewer JSON root is not an object")

    status = data.get("status")
    code = data.get("code")
    if status == 500 or code == 500:
        message = (
            data.get("errmsg")
            or data.get("message")
            or data.get("error")
            or "server error"
        )
        raise InvalidChapterPayloadError(
            f"viewer returned status/code 500: {message}"
        )

    if not isinstance(data.get("s"), list):
        message = (
            data.get("errmsg")
            or data.get("message")
            or data.get("error")
            or "missing chapter segment list ('s')"
        )
        raise InvalidChapterPayloadError(
            f"viewer returned no chapter content: {message}"
        )

    return data


class DownloaderCore:
    def __init__(self, auth_instance, logger_func):
        self.auth = auth_instance
        self.log = logger_func
        self.stop_signal = False

    @staticmethod
    def _normalize_novelpia_image_url(value):
        if not value:
            return None
        value = html.unescape(str(value)).strip().strip("\"'")
        value = value.replace("\\/", "/")
        value = re.sub(r"[\s,;)]+$", "", value)
        if value.startswith("//"):
            value = "https:" + value
        elif value.startswith("/imagebox/"):
            value = "https://images.novelpia.com" + value
        elif value.startswith("imagebox/"):
            value = "https://images.novelpia.com/" + value
        value = re.sub(
            r"^https?://(?:image\.novelpia\.com|novelpia\.com)/imagebox/",
            "https://images.novelpia.com/imagebox/",
            value,
            flags=re.IGNORECASE,
        )
        if not re.match(r"^https://images?\.novelpia\.com/imagebox/", value, re.IGNORECASE):
            return None
        return value

    @staticmethod
    def normalize_chapter_image_url(value):
        """Return a usable absolute HTTP(S) URL for a chapter image.

        Viewer payloads commonly use scheme-relative URLs (``//...``), but
        older chapters may contain root-relative or escaped values.  Keep an
        already-absolute source URL unchanged so URL-only EPUB exports point
        at the same resource shown on the website.
        """
        if not value:
            return None

        value = html.unescape(str(value)).strip().strip("\"'")
        value = value.replace("\\/", "/")
        if not value:
            return None

        if value.startswith("//"):
            value = "https:" + value
        elif value.startswith("/imagebox/"):
            value = "https://images.novelpia.com" + value
        elif value.startswith("imagebox/"):
            value = "https://images.novelpia.com/" + value
        elif not urlparse(value).scheme:
            value = urljoin("https://novelpia.com/", value)

        parsed = urlparse(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            return None
        return value

    @classmethod
    def remote_image_html(cls, value, alt="Image"):
        """Build an XHTML-safe remote image element without fetching bytes."""
        url = cls.normalize_chapter_image_url(value)
        if not url:
            return None
        safe_url = html.escape(url, quote=True)
        safe_alt = html.escape(str(alt or "Image"), quote=True)
        return (
            f'<img class="remote-image" alt="{safe_alt}" '
            f'src="{safe_url}" width="100%"/>'
        )

    def _extract_cover_url(self, text):
        """Prefer the real cover URL over R19 venobox preview URLs."""
        candidates = []
        seen = {}

        def add(raw_url, source_bonus=0):
            url = self._normalize_novelpia_image_url(raw_url)
            if not url:
                return
            lower = url.lower()
            if "readycover" in lower:
                return
            score = source_bonus
            if "/imagebox/original/" in lower:
                score += 1000
            elif "/imagebox/cover/" in lower:
                score += 500
            else:
                score += 250
            score += -80 if "_q_" in lower or "_q_ori" in lower else 40
            if lower.endswith(".wimg"):
                score -= 120
            if "_ori.file" in lower:
                score += 30
            if url in seen:
                idx = seen[url]
                if score > candidates[idx][0]:
                    candidates[idx] = (score, idx, url)
                return
            seen[url] = len(candidates)
            candidates.append((score, len(candidates), url))

        for tag in re.findall(r"<meta\b[^>]*>", text, flags=re.IGNORECASE | re.DOTALL):
            if not re.search(r"\b(?:property|name)=['\"](?:og:image|twitter:image|image)['\"]", tag, re.IGNORECASE):
                continue
            match = re.search(r"\bcontent=(['\"])(.*?)\1", tag, flags=re.IGNORECASE | re.DOTALL)
            if match:
                add(match.group(2), 220)

        for tag in re.findall(r"<img\b[^>]*>", text, flags=re.IGNORECASE | re.DOTALL):
            if not re.search(r"\bclass=(['\"])[^'\"]*\bcover_img\b", tag, re.IGNORECASE):
                continue
            match = re.search(r"\bsrc=(['\"])(.*?)\1", tag, flags=re.IGNORECASE | re.DOTALL)
            if match:
                add(match.group(2), 200)

        for match in re.finditer(r"\bepnew-cover-box\b", text, flags=re.IGNORECASE):
            chunk = text[match.start():match.start() + 1600]
            for attr_match in re.finditer(r"\b(?:href|src)=(['\"])(.*?)\1", chunk, flags=re.IGNORECASE | re.DOTALL):
                add(attr_match.group(2), 120)

        for match in re.finditer(r"\bimageUrl\s*=\s*(['\"])(.*?)\1", text, flags=re.IGNORECASE | re.DOTALL):
            add(match.group(2), 160)

        scan_text = text.replace("\\/", "/")
        imagebox_pattern = (
            r"(?:https?:)?//(?:images?\.novelpia\.com|novelpia\.com)/imagebox/"
            r"(?:original|cover|[0-9a-f]{1,3})/[^\s\"'<>\\)]+"
            r"|/imagebox/(?:original|cover|[0-9a-f]{1,3})/[^\s\"'<>\\)]+"
        )
        for match in re.finditer(imagebox_pattern, scan_text, flags=re.IGNORECASE):
            add(match.group(0), 0)

        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], -item[1]))[2]

    def _request_with_retries(
        self,
        method,
        url,
        *,
        label,
        max_retries=None,
        require_body=False,
        **kwargs,
    ):
        if max_retries is None:
            max_retries = self.DEFAULT_MAX_RETRIES
        try:
            max_retries = max(1, int(max_retries))
        except (TypeError, ValueError):
            max_retries = self.DEFAULT_MAX_RETRIES

        request = getattr(self.auth.session, method.lower())
        last_error = None

        for attempt in range(1, max_retries + 1):
            if self.stop_signal:
                return None
            try:
                response = request(url, **kwargs)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = f"HTTP {response.status_code}"
                elif require_body and not (response.text or "").strip():
                    last_error = "empty response body"
                else:
                    if attempt > 1:
                        self.log(
                            f"{label}: recovered on attempt {attempt}/{max_retries}."
                        )
                    return response
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

            if attempt < max_retries:
                wait = min(8, 2 ** (attempt - 1))
                self.log(
                    f"{label}: attempt {attempt}/{max_retries} failed "
                    f"({last_error}). Retrying in {wait}s..."
                )
                time.sleep(wait)

        raise RuntimeError(
            f"{label}: failed after {max_retries} attempts ({last_error})"
        )

    def fetch_metadata(self, novel_id):
        """
        Scrapes novel metadata using regex patterns from MainWin.Download.cs.
        """
        url = f"https://novelpia.com/novel/{novel_id}"
        self.log(f"Fetching metadata for Novel ID: {novel_id}...")

        try:
            # Use GET here, matching the original C# implementation.
            response = self._request_with_retries(
                "get",
                url,
                label=f"Metadata {novel_id}",
                timeout=15,
                require_body=True,
            )
            if response is None:
                return None
            text = response.text
            
            # Title Extraction
            title_match = re.search(r"productName = '(.+?)';", text)
            if title_match:
                title = title_match.group(1)
            else:
                # Fallback: try og:title meta tag
                og_title_match = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.+?)["\']', text, flags=re.IGNORECASE)
                if og_title_match:
                    # og:title format: "노벨피아 - 웹소설로 꿈꾸는 세상! - [Title]"
                    # Extract the actual title after the last dash
                    full_title = og_title_match.group(1)
                    parts = full_title.split(' - ')
                    title = parts[-1].strip() if len(parts) > 1 else full_title
                else:
                    # Last resort: try <title> tag
                    title_tag_match = re.search(r'<title>(.+?)</title>', text, flags=re.IGNORECASE)
                    if title_tag_match:
                        full_title = title_tag_match.group(1)
                        parts = full_title.split(' - ')
                        title = parts[-1].strip() if len(parts) > 1 else full_title
                    else:
                        title = f"Novel_{novel_id}"
            
            # Author Extraction
            author_match = re.search(r'<a class="writer-name"[^>]*>\s*(.+?)\s*</a>', text)
            author = author_match.group(1).strip() if author_match else "Unknown Author"
            
            # Cover Image Extraction - prefer original/non-preview cover URLs.
            cover_url = self._extract_cover_url(text)
            
            # Tags Extraction (matches C# pattern: <span class="tag".*?>(#.+?)</span>)
            tag_matches = re.findall(r'<span class="tag".*?>(#.+?)</span>', text)
            tags = []
            if tag_matches:
                # Remove the # prefix and deduplicate while preserving order
                seen = set()
                for t in [html.unescape(m.lstrip('#').strip()) for m in tag_matches if m.strip()]:
                    if t.lower() not in seen:
                        seen.add(t.lower())
                        tags.append(t)

            # Description Extraction
            # NOTE: Don't use a naive ["']... ["'] terminator because the content may contain
            # the other quote character. Capture the delimiter and close with the same one.
            description = ""

            # 1) Try on-page synopsis (usually longer than SEO meta description)
            syn_match = re.search(
                r'<div[^>]*class=["\'][^"\']*\bsynopsis\b[^"\']*["\'][^>]*>(.*?)</div>',
                text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if syn_match:
                syn_html = syn_match.group(1)
                syn_txt = re.sub(r"<\s*br\s*/?\s*>", "\n", syn_html, flags=re.IGNORECASE)
                syn_txt = re.sub(r"</?[^>]+>", " ", syn_txt)
                syn_txt = html.unescape(syn_txt)
                syn_txt = re.sub(r"[ \t\f\v]+", " ", syn_txt)
                syn_txt = re.sub(r"\n{3,}", "\n\n", syn_txt)
                syn_txt = syn_txt.strip()
                if syn_txt:
                    description = syn_txt

            # 2) Fallback to meta description / og:description (often short)
            if not description:
                meta_desc = ""
                desc_match = re.search(
                    r'<meta[^>]*name=["\']description["\'][^>]*content=(["\'])(.*?)\1',
                    text,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if desc_match:
                    meta_desc = html.unescape(desc_match.group(2).strip())

                og_desc_match = re.search(
                    r'<meta[^>]*property=["\']og:description["\'][^>]*content=(["\'])(.*?)\1',
                    text,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                og_desc = html.unescape(og_desc_match.group(2).strip()) if og_desc_match else ""

                # Prefer the longer one (sometimes they differ)
                description = og_desc if len(og_desc) > len(meta_desc) else meta_desc
            # Status Extraction (best-effort): look for common Korean status words
            status_match = re.search(r'(완결|연재중|연재|휴재|완결됨)', text)
            status = status_match.group(1) if status_match else ''

            self.log(f"Metadata acquired: {title} by {author}")
            if cover_url:
                self.log(f"  Cover: {cover_url}")
            if tags:
                self.log(f"  Tags: {', '.join(tags[:5])}{'...' if len(tags) > 5 else ''}")
            if status:
                self.log(f"  Status: {status}")
            return {
                "id": novel_id,
                "title": title,
                "author": author,
                "cover_url": cover_url,
                "tags": tags,
                "description": description,
                "status": status
            }
        except Exception as e:
            self.log(f"Metadata fetch error: {e}")
            return None

    def fetch_chapter_list(self, novel_id, max_retries=None):
        """
        Iterates through pages of the episode list until exhaustion.
        Replicates the while(true) loop in MainWin.Download.cs.
        """
        if max_retries is None:
            max_retries = self.DEFAULT_MAX_RETRIES
        chapters = []
        page = 0
        discovered_ids = set()
        
        self.log("Analyzing novel to get chapter list...")
        
        while not self.stop_signal:
            url = "https://novelpia.com/proc/episode_list"
            data = {"novel_no": novel_id, "sort": "DOWN", "page": page}
            
            try:
                headers = {"Referer": f"https://novelpia.com/novel/{novel_id}"}
                response = self._request_with_retries(
                    "post",
                    url,
                    label=f"Episode list {novel_id} page {page}",
                    max_retries=max_retries,
                    data=data,
                    headers=headers,
                    timeout=30,
                    require_body=False,
                )
                if response is None:
                    break
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                if "Authentication required" in response.text:
                    self.log("Error: Authentication required during scan.")
                    break
                
                matches = re.findall(r'id="bookmark_(\d+)"></i>(.+?)</b>', response.text)
                
                if not matches:
                    break
                
                new_count = 0
                for chap_id, chap_name in matches:
                    if chap_id not in discovered_ids:
                        chapters.append({
                            "id": chap_id, 
                            "title": html.unescape(chap_name.strip())
                        })
                        discovered_ids.add(chap_id)
                        new_count += 1
                
                if new_count > 0:
                    self.log(f"  Discovered {len(chapters)} chapters...")
                
                if new_count == 0 and page > 0:
                    break
                    
                page += 1
                time.sleep(0.2)

            except Exception as e:
                self.log(f"Error scanning page {page}: {str(e)}")
                raise RuntimeError(
                    f"Failed to scan chapter list page {page}; aborting to avoid "
                    "building an incomplete download."
                ) from e

        self.log(f"Found {len(chapters)} chapters in total.")
        return chapters

    # Default retry budget. Exposed as a parameter so the caller (GUI / bot)
    # can raise it for flaky sessions without touching downloader_core.
    DEFAULT_MAX_RETRIES = 5

    # Escalating cooldowns applied when an entire retry round is exhausted.
    # After all cooldown tiers are spent the chapter is marked as failed.
    COOLDOWN_TIERS = (10, 30, 60)

    def download_chapter_content(self, chapter_id, max_retries=None):
        """
        Fetches the JSON content for a specific chapter.
        Corresponds to the 'viewer_data' call in the legacy code.

        Retries up to `max_retries` times (default 5) on transient errors:
          * HTTP status != 200
          * empty response body
          * network / exception

        If every attempt in a round fails, an escalating cooldown
        (10 s \u2192 30 s \u2192 60 s) is applied before the full round is retried.

        Login/age blocks raise AccessBlockedError immediately (no retry).
        Returns the raw JSON string on success, or None if every round
        fails \u2014 in which case a prominent "FAILED" line is logged.
        """
        if max_retries is None:
            max_retries = self.DEFAULT_MAX_RETRIES
        try:
            max_retries = max(1, int(max_retries))
        except (TypeError, ValueError):
            max_retries = self.DEFAULT_MAX_RETRIES

        url = f"https://novelpia.com/proc/viewer_data/{chapter_id}"

        last_failure_reason = ""

        def _schedule_retry(reason, attempt):
            """Log a uniform "attempt i/N failed \u2014 retrying in Ys" line and
            sleep with simple exponential backoff (capped at 8 s).
            On the final attempt of a round the reason is only recorded \u2014
            the outer cooldown loop decides what happens next."""
            nonlocal last_failure_reason
            last_failure_reason = reason
            if attempt < max_retries:
                wait = min(8, 2 ** (attempt - 1))  # 1, 2, 4, 8, 8, ...
                self.log(
                    f"Chapter {chapter_id}: attempt {attempt}/{max_retries} failed ({reason}). "
                    f"Retrying in {wait}s..."
                )
                time.sleep(wait)

        total_rounds = len(self.COOLDOWN_TIERS) + 1  # initial + cooldown rounds

        for round_idx in range(total_rounds):
            for attempt in range(1, max_retries + 1):
                if self.stop_signal:
                    return None
                try:
                    # LOGINKEY is already in session cookies via novelpia_auth.
                    # Don't manually override the Cookie header — it clobbers
                    # other cookies (USERKEY, NPK*) that the server needs.
                    response = self.auth.session.post(url, timeout=15)

                    if response.status_code != 200:
                        _schedule_retry(f"HTTP {response.status_code}", attempt)
                        continue

                    text = response.text or ""
                    if not text.strip():
                        # Detailed debug info to diagnose auth/empty response issues
                        has_loginkey = bool(self.auth.loginkey)
                        loginkey_preview = self.auth.loginkey[:8] + "..." if has_loginkey else "(empty)"
                        resp_ct = response.headers.get("content-type", "?")
                        resp_cl = response.headers.get("content-length", "?")
                        resp_cookies = ", ".join(f"{c.name}={c.value[:10]}..." for c in response.cookies) or "(none)"
                        set_cookie = response.headers.get("set-cookie", "(none)")[:100]
                        self.log(
                            f"Chapter {chapter_id}: empty response body on attempt {attempt}/{max_retries}\n"
                            f"  \u2192 HTTP {response.status_code} | Content-Type: {resp_ct} | Content-Length: {resp_cl}\n"
                            f"  \u2192 LOGINKEY: {loginkey_preview} | Response cookies: {resp_cookies}\n"
                            f"  \u2192 Set-Cookie header: {set_cookie}\n"
                            f"  \u2192 Raw body repr: {repr(response.content[:200])}\n"
                            f"  \u2192 Hint: If free chapters work but premium ones don't, check that you have an active subscription or have purchased this chapter."
                        )
                        _schedule_retry("empty response body", attempt)
                        continue

                    try:
                        validate_chapter_payload(text)
                    except InvalidChapterPayloadError as exc:
                        _schedule_retry(str(exc), attempt)
                        continue

                    # Common server-side blocks (login / age verification / generic auth).
                    lowered = text.lower()
                    if (
                        "\ubcf8\uc778\uc778\uc99d" in text  # "본인인증" in unicode form
                        or "\ub85c\uadf8\uc778" in text      # "로그인" in unicode form
                        or "authentication required" in lowered
                    ):
                        self.log(
                            f"Chapter {chapter_id}: access appears to be blocked (login/age verification)."
                        )
                        raise AccessBlockedError(
                            f"Chapter {chapter_id}: login/age verification required"
                        )

                    if attempt > 1 or round_idx > 0:
                        # Friendly confirmation when a retry finally succeeded.
                        self.log(
                            f"Chapter {chapter_id}: recovered on attempt {attempt}/{max_retries}"
                            f"{f' (cooldown round {round_idx})' if round_idx else ''}."
                        )
                    return text  # Returns raw JSON string

                except AccessBlockedError:
                    raise
                except Exception as e:
                    _schedule_retry(f"{type(e).__name__}: {e}", attempt)

            # -- All attempts in this round exhausted --
            if round_idx < len(self.COOLDOWN_TIERS):
                cooldown = self.COOLDOWN_TIERS[round_idx]
                self.log(
                    f"\u23f3 Chapter {chapter_id}: all {max_retries} attempts failed "
                    f"({last_failure_reason}). Cooldown {cooldown}s before round "
                    f"{round_idx + 2}/{total_rounds}..."
                )
                # Sleep in 1-second ticks so stop_signal stays responsive.
                for _ in range(cooldown):
                    if self.stop_signal:
                        return None
                    time.sleep(1)
            else:
                # Every round exhausted — emit a prominent final-failure log.
                total_attempts = total_rounds * max_retries
                self.log(
                    f"\u274c Chapter {chapter_id}: FAILED after {total_attempts} total "
                    f"attempts across {total_rounds} rounds ({last_failure_reason})."
                )

        return None

    def fetch_notices(self, novel_id):
        """
        Best-effort retrieval of author notices for a novel.
        Returns a list of dicts: { 'title': str, 'content': str, 'date': str }
        If no notices are found or endpoint is unavailable, returns [].
        """
        notices = []
        try:
            url = "https://novelpia.com/proc/notice_list"
            data = {"novel_no": novel_id, "page": 0}
            # Set proper Referer header to avoid empty responses
            headers = {"Referer": f"https://novelpia.com/novel/{novel_id}"}
            resp = self.auth.session.post(url, data=data, headers=headers, timeout=15)
            text = resp.text or ""
            if not text.strip():
                self.log("Notices: empty response.")
                return []
            try:
                obj = json.loads(text)
                items = obj.get("notices") or obj.get("data") or obj.get("list") or []
                for it in items:
                    title = html.unescape(str(it.get("title", "")).strip())
                    content = str(it.get("content", "")).strip()
                    date = str(it.get("date", "")).strip()
                    if title or content:
                        notices.append({"title": title, "content": content, "date": date})
                if notices:
                    self.log(f"Found {len(notices)} notices (JSON)")
                    return notices
            except Exception:
                pass

            blocks = re.findall(r"(<[^>]+class=\"[^\"]*notice[^\"]*\"[^>]*>.*?</[^>]+>)", text, flags=re.IGNORECASE | re.DOTALL)
            if not blocks:
                blocks = re.findall(r"(<li[^>]*>.*?공지.*?</li>)", text, flags=re.IGNORECASE | re.DOTALL)
            for blk in blocks:
                tmatch = re.search(r"<b[^>]*>(.+?)</b>", blk, flags=re.DOTALL)
                title = html.unescape(tmatch.group(1).strip()) if tmatch else ""
                content = re.sub(r"</?[^>]+>", "", blk)
                content = html.unescape(content.strip())
                dmatch = re.search(r"(\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2})", blk)
                date = dmatch.group(1) if dmatch else ""
                if title or content:
                    notices.append({"title": title, "content": content, "date": date})
            if notices:
                self.log(f"Found {len(notices)} notices (HTML)")
            else:
                self.log("No notices found.")
            return notices
        except Exception as e:
            self.log(f"Notices fetch error: {e}")
            return []

    def fetch_notice_ids(self, novel_id):
        """
        Scan the novel page for a notice_table and extract /viewer/<id> links.
        Returns a list of dicts: { 'id': str, 'title': str }.
        """
        results = []
        try:
            url = f"https://novelpia.com/novel/{novel_id}"
            self.log(f"Scanning notices on novel page for {novel_id}...")
            resp = self.auth.session.get(url, timeout=15)
            text = resp.text or ""
            m = re.search(r'<table[^>]+class="notice_table[^"]*"[^>]*>.*?</table>', text, flags=re.IGNORECASE | re.DOTALL)
            if not m:
                self.log("No notice table found.")
                return []
            table_html = m.group(0)
            matches = re.findall(r"location=['\"]/viewer/(\d+)['\"][^>]*><b>(.*?)</b>", table_html, flags=re.DOTALL)
            if not matches:
                matches = re.findall(r"href=['\"]/viewer/(\d+)['\"][^>]*><b>(.*?)</b>", table_html, flags=re.DOTALL)
            for chap_id, raw_title in matches:
                clean_title = re.sub(r"<.*?>", "", raw_title).strip()
                title = "Notice: " + clean_title if clean_title else "Notice"
                results.append({"id": chap_id, "title": html.unescape(title)})
            self.log(f"Found {len(results)} author notice(s).")
            return results
        except Exception as e:
            self.log(f"Notices scan error: {e}")
            return []

    def generate_pdf(self, metadata, output_path, chapters, css, image_map=None, cover_image=None, info_html=None,
                     show_toc=False, show_page_numbers=False, use_counter_layout=False):
        """
        Generate a single PDF from chapter HTML using WeasyPrint.

        chapters: list of dicts with keys: {title, html, is_notice}
        image_map: dict of filename -> bytes (used to inline ../Images/ references)
        cover_image: dict {filename, data} or None
        info_html: optional HTML snippet (inner body) for metadata section
        """
        try:
            from weasyprint import HTML
        except Exception as e:
            self.log(f"WeasyPrint not available: {e}")
            raise

        def guess_mime(filename):
            ext = filename.rsplit(".", 1)[-1].lower()
            if ext in ("jpg", "jpeg"):
                return "image/jpeg"
            if ext == "png":
                return "image/png"
            if ext == "webp":
                return "image/webp"
            if ext == "gif":
                return "image/gif"
            if ext == "avif":
                return "image/avif"
            return "application/octet-stream"

        def to_data_uri(filename, data):
            mime = guess_mime(filename)
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"

        images = image_map or {}

        def inline_images(html_content):
            if not html_content or not images:
                return html_content

            def repl(m):
                src = m.group(1)
                if src.startswith("../Images/"):
                    name = src.split("/")[-1]
                    data = images.get(name)
                    if data:
                        return f'src="{to_data_uri(name, data)}"'
                return m.group(0)

            return re.sub(r'src=["\']([^"\']+)["\']', repl, html_content)

        page_footer = "\n@page { @bottom-center { content: counter(page); } }" if show_page_numbers else ""
        toc_counter_css = """
.toc a::after { content: leader('.') target-counter(attr(href), page); }
""" if use_counter_layout else ""

        pdf_css = (
            css
            + "\n@page { size: A4; margin: 1in; }"
            + page_footer
            + """
body { font-family: serif; }
.chapter { page-break-before: always; }
.cover { page-break-after: always; text-align: center; }
.info { page-break-after: always; }
.toc { page-break-after: always; }
.toc h2 { text-align: center; }
.toc ul { list-style: none; padding-left: 0; }
.toc li { margin: 0.2em 0; }
img { max-width: 100%; height: auto; }
"""
            + toc_counter_css
        )
        def _anchor_id(title, idx):
            base = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip().lower()
            base = re.sub(r"\s+", "-", base)
            if not base:
                base = f"chapter-{idx}"
            return f"{base}-{idx}"

        toc_items = []
        for i, chap in enumerate(chapters or []):
            title = str(chap.get("title", ""))
            anchor = _anchor_id(title, i + 1)
            chap["_pdf_anchor"] = anchor
            toc_items.append((title, anchor))

        parts = [
            "<!DOCTYPE html>",
            "<html><head><meta charset=\"utf-8\">",
            f"<style>{pdf_css}</style>",
            "</head><body>",
        ]

        if cover_image and cover_image.get("data"):
            cover_src = to_data_uri(cover_image["filename"], cover_image["data"])
            parts.append(f"<div class=\"cover\"><img alt=\"Cover\" src=\"{cover_src}\"/></div>")

        if info_html:
            parts.append(f"<div class=\"info\">{info_html}</div>")
        if show_toc and toc_items:
            toc_list = "".join(
                f"<li><a href=\"#{anchor}\">{html.escape(title)}</a></li>"
                for title, anchor in toc_items
            )
            parts.append(f"<div class=\"toc\"><h2>Table of Contents</h2><ul>{toc_list}</ul></div>")

        for chap in chapters or []:
            title = html.escape(str(chap.get("title", "")))
            content = inline_images(chap.get("html", "") or "")
            anchor = chap.get("_pdf_anchor")
            anchor_attr = f" id=\"{anchor}\"" if anchor else ""
            parts.append(f"<div class=\"chapter\"><h1{anchor_attr}>{title}</h1>{content}</div>")

        parts.append("</body></html>")
        html_doc = "".join(parts)

        self.log("Generating PDF...")
        HTML(string=html_doc).write_pdf(output_path)
        self.log("PDF generation complete.")

    def fetch_top100_rankings(self, age_filter=""):
        """Fetch Top 100 novel IDs from Novelpia ranking pages.

        Scrapes /top100/ pages for each period (daily, weekly, monthly).
        The audience is determined by age_filter:
            "" (All)       -> all, adult, teen
            "15" (Non-adult) -> teen only
            "19" (Adult)     -> adult only

        Returns:
            dict: {label: [novel_id_str, ...]} where label is e.g. "daily all"
        """
        AUDIENCES_ALL = [
            ("all/plus",   "all"),
            ("adult/plus", "adult"),
            ("teen/plus",  "teen"),
        ]
        PERIODS = [
            ("today",  "daily"),
            ("weekly", "weekly"),
            ("month",  "monthly"),
        ]

        # Filter audiences based on age_filter
        if age_filter == "19":
            audiences = [("adult/plus", "adult")]
        elif age_filter == "15":
            audiences = [("teen/plus", "teen")]
        else:
            audiences = AUDIENCES_ALL

        results = {}
        for audience_url, audience_label in audiences:
            for period_url, period_label in PERIODS:
                if self.stop_signal:
                    self.log("  Stopped by user.")
                    return results

                label = f"{period_label} {audience_label}"
                url = f"https://novelpia.com/top100/all/{period_url}/view/{audience_url}"
                self.log(f"  Fetching {label}...")

                try:
                    r = self.auth.session.get(url, timeout=30)
                    if r.status_code != 200:
                        self.log(f"  {label}: HTTP {r.status_code}")
                        results[label] = []
                        continue
                    rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))[:100]
                    results[label] = rank_ids
                    self.log(f"  {label}: {len(rank_ids)} novels")
                except Exception as e:
                    self.log(f"  {label}: Error - {e}")
                    results[label] = []

                time.sleep(0.3)

        return results

    def fetch_all_novels(self, delay=0.5, rows=30, age_filter="", max_queries=DEFAULT_SEARCH_QUERY_COUNT, threads=1):
        """Fetch ALL novel IDs from Novelpia using multiple API calls.

        The API caps results at ~42K per query, so we search with multiple
        tags/sweep terms and union the results for better coverage.

        Args:
            delay: seconds between query starts
            age_filter: "" (all), "15" (non-adult), "19" (adult only)
            max_queries: how many search terms to use
            threads: number of concurrent query threads

        Returns:
            list of novel ID strings
        """
        url = "https://novelpia.com/proc/novel"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://novelpia.com/search",
        }

        if max_queries is None:
            max_queries = DEFAULT_SEARCH_QUERY_COUNT
        max_queries = max(1, min(int(max_queries), DEFAULT_SEARCH_QUERY_COUNT))
        SEARCH_CHARS = ALL_SEARCH_TERMS[:max_queries]

        self.log(f"Scraping all novel IDs from Novelpia... ({len(SEARCH_CHARS)} queries, {threads} thread(s))")
        if age_filter == "15":
            self.log("  Age filter: Non-adult only")
        elif age_filter == "19":
            self.log("  Age filter: Adult only")

        ids = set()
        lock = threading.Lock()
        completed = [0]  # mutable counter for progress

        def _query_one(ci, ch):
            """Run a single search query."""
            if self.stop_signal:
                return

            self.log(f"  Query {ci+1}/{len(SEARCH_CHARS)}: searching '{ch}'...")
            try:
                done_ev = threading.Event()
                def _tick():
                    start = time.time()
                    while not done_ev.wait(3):
                        if self.stop_signal:
                            break
                        self.log(f"    {int(time.time() - start)}s elapsed...")
                t = threading.Thread(target=_tick, daemon=True)
                t.start()

                page_novel_list = []
                ROWS_PER_PAGE = 30000
                try:
                    for pg in range(1, 21):  # up to 20 pages = 100k novels max
                        params = {
                            "cmd": "novel_search",
                            "search_type": "all",
                            "search_val": ch,
                            "page": pg,
                            "rows": ROWS_PER_PAGE,
                            "novel_type": "",
                            "start_count_book": "",
                            "end_count_book": "",
                            "novel_age": age_filter,
                            "start_days": "",
                            "sort_col": "last_viewdate",
                            "novel_genre": "",
                            "block_out": 0,
                            "block_stop": 0,
                            "is_contest": 0,
                            "is_complete": "",
                            "is_challenge": 0,
                            "list_display": "grid",
                        }

                        response = None
                        last_error = None
                        for attempt in range(1, 5):
                            if self.stop_signal:
                                break
                            try:
                                response = self.auth.session.get(url, params=params, headers=headers, timeout=120)
                                if response.status_code not in RETRYABLE_STATUS_CODES:
                                    break
                                last_error = f"HTTP {response.status_code}"
                            except Exception as e:
                                last_error = str(e)
                                response = None

                            if attempt < 4 and not self.stop_signal:
                                wait_s = min(2 ** (attempt - 1), 8)
                                self.log(f"    Retry {attempt}/4 for '{ch}' p{pg} after {last_error}; sleeping {wait_s}s")
                                time.sleep(wait_s)

                        if response is None:
                            self.log(f"    Failed page {pg} for '{ch}': {last_error}")
                            break

                        if response.status_code != 200:
                            self.log(f"    HTTP {response.status_code} on page {pg}, skipping")
                            break

                        text = response.text.strip()
                        if not text:
                            self.log(f"    Empty response on page {pg}, skipping")
                            break

                        try:
                            data = response.json()
                        except Exception:
                            self.log(f"    Invalid JSON for '{ch}' p{pg} (len={len(text)}): {text[:300]}")
                            break

                        if data.get("status") != 200:
                            self.log(f"    API error p{pg}: {data.get('errmsg', 'Unknown')}")
                            break

                        batch = data.get("list", [])
                        page_novel_list.extend(batch)

                        # If we got fewer than a full page, no more pages
                        if len(batch) < ROWS_PER_PAGE:
                            break
                finally:
                    done_ev.set()

                with lock:
                    before = len(ids)
                    for novel in page_novel_list:
                        novel_id = str(novel.get("novel_no", ""))
                        if novel_id:
                            ids.add(novel_id)
                    new_count = len(ids) - before
                    completed[0] += 1
                    self.log(f"    {len(page_novel_list)} results, {new_count} new (total: {len(ids)})")

            except Exception as e:
                self.log(f"    Error on '{ch}': {e}")

        if threads > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=threads) as pool:
                futures = []
                for ci, ch in enumerate(SEARCH_CHARS):
                    if self.stop_signal:
                        break
                    futures.append(pool.submit(_query_one, ci, ch))
                    time.sleep(delay)  # stagger submissions
                for f in futures:
                    f.result()  # wait for all to finish
        else:
            for ci, ch in enumerate(SEARCH_CHARS):
                if self.stop_signal:
                    self.log("  Stopped by user.")
                    break
                _query_one(ci, ch)
                time.sleep(delay)

        self.log(f"  {len(ids)} novel(s) retrieved total")
        return list(ids)

    def fetch_novels_by_tags(self, tags, delay=0.5, rows=30, age_filter="", mode="AND", threads=1):
        """Fetch novel IDs from Novelpia's tag search API.

        Uses GET /proc/novel?cmd=novel_search&search_type=novel_genre&search_val={tag}.
        Each tag is searched separately. Results are combined based on mode:
          AND = intersection (novels must match ALL tags) — searches smallest tag first
          OR  = union (novels matching ANY tag)
          GROUPS = tags is a list of groups (list of lists). Tags within a group
                   are OR'd, groups are AND'd. e.g. [['TS'], ['약피폐', '피폐']]
                   means: TS AND (약피폐 OR 피폐)

        Args:
            tags: list of tag strings, or list of lists for GROUPS mode
            delay: seconds between page requests
            rows: results per page (max 30)
            age_filter: "" (all), "15" (non-adult), "19" (adult only)
            mode: "AND", "OR", or "GROUPS"
            threads: number of concurrent fetch threads

        Returns:
            list of novel ID strings
        """
        from concurrent.futures import ThreadPoolExecutor

        url = "https://novelpia.com/proc/novel"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://novelpia.com/search",
        }

        if mode == "GROUPS":
            groups = tags  # list of lists
            self.log(f"Searching with {len(groups)} group(s), {threads} thread(s):")
            for gi, g in enumerate(groups):
                self.log(f"  Group {gi+1}: {' OR '.join(g)}")
                if gi < len(groups) - 1:
                    self.log(f"    AND")
        else:
            self.log(f"Searching for novels with tags: {', '.join(tags)} (mode: {mode}, {threads} thread(s))")
        if age_filter == "15":
            self.log("  Age filter: Non-adult only")
        elif age_filter == "19":
            self.log("  Age filter: Adult only")

        def _timed_get(label, **kwargs):
            """session.get with elapsed-time logging every 3s."""
            done = threading.Event()
            def _tick():
                start = time.time()
                while not done.wait(3):
                    if self.stop_signal:
                        break
                    self.log(f"    {int(time.time() - start)}s elapsed...")
            t = threading.Thread(target=_tick, daemon=True)
            t.start()
            try:
                return self.auth.session.get(**kwargs)
            finally:
                done.set()

        def _fetch_tag(tag, genre_filter="", include_genres=False):
            """Fetch all novel IDs for a tag in a single API call."""
            if self.stop_signal:
                return (set(), {}) if include_genres else set()
            params = {
                "cmd": "novel_search",
                "search_type": "novel_genre",
                "search_val": tag,
                "page": 1,
                "rows": 50000,
                "novel_type": "",
                "start_count_book": "",
                "end_count_book": "",
                "novel_age": age_filter,
                "start_days": "",
                "sort_col": "last_viewdate",
                "novel_genre": genre_filter,
                "block_out": 0,
                "block_stop": 0,
                "is_contest": 0,
                "is_complete": "",
                "is_challenge": 0,
                "list_display": "grid",
            }
            label = f"{tag}{' ∩ ' + genre_filter if genre_filter else ''}"
            response = _timed_get(label, url=url, params=params, headers=headers, timeout=60)
            data = response.json()

            if data.get("status") != 200:
                self.log(f"  [{tag}] API error: {data.get('errmsg', 'Unknown')}")
                return (set(), {}) if include_genres else set()

            novel_list = data.get("list", [])
            ids = set()
            genres_map = {}
            for novel in novel_list:
                novel_id = str(novel.get("novel_no", ""))
                if novel_id:
                    ids.add(novel_id)
                    if include_genres:
                        genres_map[novel_id] = novel.get("novel_genre_arr") or []

            self.log(f"  [{tag}{' ∩ ' + genre_filter if genre_filter else ''}] {len(ids)} novel(s)")
            if include_genres:
                return ids, genres_map
            return ids

        def _fetch_tags_parallel(tag_list):
            """Fetch multiple tags concurrently and return list of id sets."""
            if threads <= 1:
                results = []
                for tag in tag_list:
                    if self.stop_signal:
                        break
                    results.append(_fetch_tag(tag))
                    if delay > 0:
                        time.sleep(delay)
                return results
            else:
                with ThreadPoolExecutor(max_workers=threads) as pool:
                    futures = []
                    for tag in tag_list:
                        if self.stop_signal:
                            break
                        futures.append(pool.submit(_fetch_tag, tag))
                        time.sleep(delay)
                    return [f.result() for f in futures]

        if mode == "GROUPS":
            # Each group: OR the tags within it (parallel). Then AND the groups.
            result_set = None
            for gi, group in enumerate(tags):
                if self.stop_signal:
                    break
                tag_results = _fetch_tags_parallel(group)
                group_ids = set()
                for ids in tag_results:
                    group_ids |= ids
                self.log(f"  Group {gi+1} result: {len(group_ids)} novel(s)")
                if result_set is None:
                    result_set = group_ids
                else:
                    result_set &= group_ids
                    self.log(f"  After AND with group {gi+1}: {len(result_set)} novel(s)")
            result_ids = list(result_set or set())

        elif mode == "AND" and len(tags) > 1:
            tag_results = _fetch_tags_parallel(tags)
            result_set = None
            for ids in tag_results:
                result_set = ids if result_set is None else result_set & ids
            result_ids = list(result_set or set())
            self.log(f"  AND intersection: {len(result_ids)} novel(s) match all {len(tags)} tag(s)")
        else:
            tag_results = _fetch_tags_parallel(tags)
            all_ids = set()
            for ids in tag_results:
                all_ids |= ids
            result_ids = list(all_ids)

        self.log(f"Tag search complete: {len(result_ids)} novel(s) found.")
        return result_ids
