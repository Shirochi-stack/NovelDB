import re
import json
import time
import threading
import html
import base64
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

class AccessBlockedError(Exception):
    """Raised when chapter access is blocked (login/age verification required)."""
    pass


class DownloaderCore:
    def __init__(self, auth_instance, logger_func):
        self.auth = auth_instance
        self.log = logger_func
        self.stop_signal = False

    def fetch_metadata(self, novel_id):
        """
        Scrapes novel metadata using regex patterns from MainWin.Download.cs.
        """
        url = f"https://novelpia.com/novel/{novel_id}"
        self.log(f"Fetching metadata for Novel ID: {novel_id}...")
        
        try:
            # Use GET here, matching the original C# implementation.
            response = self.auth.session.get(url, timeout=15)
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
            
            # Cover Image Extraction - get full size image, not thumbnail
            # Try finding the original full-size cover first
            cover_match = re.search(r'"(//images\.novelpia\.com/imagebox/original/[^"]+)"', text)
            if not cover_match:
                # Fallback to cover path (may be resized)
                cover_match = re.search(r'"(//images\.novelpia\.com/imagebox/cover/[^"]+)"', text)
            cover_url = "https:" + cover_match.group(1) if cover_match else None
            
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
                self.log(f"  Cover: found")
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

    def fetch_chapter_list(self, novel_id):
        """
        Iterates through pages of the episode list until exhaustion.
        Replicates the while(true) loop in MainWin.Download.cs.
        """
        chapters = []
        page = 0
        discovered_ids = set()
        
        self.log("Analyzing novel to get chapter list...")
        
        while not self.stop_signal:
            url = "https://novelpia.com/proc/episode_list"
            data = {"novel_no": novel_id, "sort": "DOWN", "page": page}
            
            try:
                headers = {"Referer": f"https://novelpia.com/novel/{novel_id}"}
                response = self.auth.session.post(url, data=data, headers=headers)
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
                break
                
        self.log(f"Found {len(chapters)} chapters in total.")
        return chapters

    def download_chapter_content(self, chapter_id):
        """
        Fetches the JSON content for a specific chapter.
        Corresponds to the 'viewer_data' call in the legacy code.
        """
        url = f"https://novelpia.com/proc/viewer_data/{chapter_id}"
        for attempt in range(3):  # Hardcoded retry limit matching C# MAX_DOWNLOAD_RETRIES
            try:
                # Mirror C# PostRequest: POST with LOGINKEY cookie header
                response = self.auth.session.post(
                    url,
                    headers={"Cookie": f"LOGINKEY={self.auth.loginkey};"},
                    timeout=15,
                )

                if response.status_code != 200:
                    self.log(
                        f"Chapter {chapter_id}: HTTP {response.status_code} on attempt {attempt + 1}"
                    )
                    time.sleep(1)
                    continue

                text = response.text or ""
                if not text.strip():
                    self.log(
                        f"Chapter {chapter_id}: Empty response body on attempt {attempt + 1}"
                    )
                    time.sleep(1)
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

                return text  # Returns raw JSON string

            except AccessBlockedError:
                raise
            except Exception:
                self.log(f"Retrying chapter {chapter_id} (Attempt {attempt + 1})...")
                time.sleep(1)
        
        self.log(f"Failed to download chapter {chapter_id} after retries.")
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

    def fetch_all_novels(self, delay=0.5, rows=30, age_filter="", max_queries=50):
        """Fetch ALL novel IDs from Novelpia using multiple API calls.

        The API caps results at ~42K per query, so we search with multiple
        characters and union the results for better coverage.

        Returns:
            list of novel ID strings
        """
        url = "https://novelpia.com/proc/novel"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://novelpia.com/search",
        }

        # Tags first (highest unique yield), then Korean chars, English, digits
        ALL_SEARCHES = [
            '판타지', '현대', '패러디', '하렘', '라이트노벨', '일상', '로맨스',
            '현대판타지', 'TS', '먼치킨', '중세', '전생', '집착', '아카데미',
            '고수위', '드라마', 'SF', '순애', '빙의', '피폐', '성장', '착각',
            '무협', '블루아카이브', '후회', '코미디', '이세계', '기타', '백합',
            '회귀', '약피폐', '아포칼립스', '얀데레', '게임', '환생', '남성향',
            '헌터', '조교', '복수', '인터넷방송', '남녀역전', '대체역사', '모험',
            '원신', '상태창', '공포', '생존', '전쟁', '가면라이더', '액션',
        ] + list("타아다라사가마나자하카차바파") + list("abcdefghijklmnopqrstuvwxyz0123456789")
        SEARCH_CHARS = ALL_SEARCHES[:max_queries]

        self.log("Scraping all novel IDs from Novelpia...")
        if age_filter == "15":
            self.log("  Age filter: Non-adult only")
        elif age_filter == "19":
            self.log("  Age filter: Adult only")

        ids = set()

        for ci, ch in enumerate(SEARCH_CHARS):
            self.log(f"  Query {ci+1}/{len(SEARCH_CHARS)}: searching '{ch}'...")
            try:
                done = threading.Event()
                def _tick():
                    start = time.time()
                    while not done.wait(3):
                        self.log(f"    {int(time.time() - start)}s elapsed...")
                t = threading.Thread(target=_tick, daemon=True)
                t.start()
                try:
                    response = self.auth.session.get(url, params={
                        "cmd": "novel_search",
                        "search_type": "all",
                        "search_val": ch,
                        "page": 1,
                        "rows": 99999,
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
                    }, headers=headers, timeout=120)
                finally:
                    done.set()
                data = response.json()

                if data.get("status") != 200:
                    self.log(f"    API error: {data.get('errmsg', 'Unknown')}")
                    continue

                novel_list = data.get("list", [])
                before = len(ids)
                for novel in novel_list:
                    novel_id = str(novel.get("novel_no", ""))
                    if novel_id:
                        ids.add(novel_id)
                new_count = len(ids) - before
                self.log(f"    {len(novel_list)} results, {new_count} new (total: {len(ids)})")

            except Exception as e:
                self.log(f"    Error on '{ch}': {e}")



            time.sleep(0.5)

        self.log(f"  {len(ids)} novel(s) retrieved total")
        return list(ids)

    def fetch_novels_by_tags(self, tags, delay=0.5, rows=30, age_filter="", mode="AND"):
        """Fetch novel IDs from Novelpia's tag search API.

        Uses GET /proc/novel?cmd=novel_search&search_type=novel_genre&search_val={tag}.
        Each tag is searched separately. Results are combined based on mode:
          AND = intersection (novels must match ALL tags) — searches smallest tag first
          OR  = union (novels matching ANY tag)

        Args:
            tags: list of tag strings
            delay: seconds between page requests
            rows: results per page (max 30)
            age_filter: "" (all), "15" (non-adult), "19" (adult only)
            mode: "AND" or "OR"

        Returns:
            list of novel ID strings
        """
        url = "https://novelpia.com/proc/novel"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://novelpia.com/search",
        }

        self.log(f"Searching for novels with tags: {', '.join(tags)} (mode: {mode})")
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
                    self.log(f"    {int(time.time() - start)}s elapsed...")
            t = threading.Thread(target=_tick, daemon=True)
            t.start()
            try:
                return self.auth.session.get(**kwargs)
            finally:
                done.set()

        def _fetch_tag(tag, genre_filter="", include_genres=False):
            """Fetch all novel IDs for a tag in a single API call.
            If include_genres=True, returns (set_of_ids, {id: genre_list}).
            Otherwise returns set_of_ids."""
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

        if mode == "AND" and len(tags) > 1:
            # Fetch each tag (1 call each), intersect locally
            result_set = None
            for tag in tags:
                if self.stop_signal:
                    break
                tag_ids = _fetch_tag(tag)
                result_set = tag_ids if result_set is None else result_set & tag_ids
                if delay > 0 and tag != tags[-1]:
                    time.sleep(delay)
            result_ids = list(result_set or set())
            self.log(f"  AND intersection: {len(result_ids)} novel(s) match all {len(tags)} tag(s)")
        else:
            all_ids = set()
            for tag in tags:
                if self.stop_signal:
                    break
                tag_ids = _fetch_tag(tag)
                all_ids |= tag_ids
                if delay > 0 and tag != tags[-1]:
                    time.sleep(delay)
            result_ids = list(all_ids)

        self.log(f"Tag search complete: {len(result_ids)} novel(s) found.")
        return result_ids
