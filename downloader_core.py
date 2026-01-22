import re
import json
import time
import html
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

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
            # The API expects form-data for pagination
            data = {"novel_no": novel_id, "sort": "DOWN", "page": page}
            
            try:
                # Set proper Referer header to avoid empty responses
                headers = {"Referer": f"https://novelpia.com/novel/{novel_id}"}
                response = self.auth.session.post(url, data=data, headers=headers)
                if "Authentication required" in response.text:
                    self.log("Error: Authentication required during scan.")
                    break
                
                # Regex logic from C#
                matches = re.findall(r'id="bookmark_(\d+)"></i>(.+?)</b>', response.text)
                
                if not matches:
                    # Check for end of list indicator
                    break
                
                new_items = False
                for chap_id, chap_name in matches:
                    if chap_id not in discovered_ids:
                        chapters.append({
                            "id": chap_id, 
                            "title": html.unescape(chap_name.strip())
                        })
                        discovered_ids.add(chap_id)
                        new_items = True
                
                # If a page returns no new items, we have likely reached the end or duplicates
                if not new_items and page > 0:
                    break
                    
                page += 1
                time.sleep(0.2) # Throttle to prevent rate limiting
                
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
                    return None

                return text  # Returns raw JSON string

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