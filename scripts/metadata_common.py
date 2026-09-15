"""Import-safe, anonymous metadata collection and durable source-scoped state.

Adapters are responsible for public endpoint allowlists and source semantics.
This module never imports a desktop downloader, credentials, or browser profile.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from uuid import uuid4
from urllib.parse import parse_qs, urljoin, urlsplit

import requests


ROOT = Path(__file__).resolve().parents[1]
SOURCE_LABELS = {"naver": "Naver Web Novel", "munpia": "Munpia", "joara": "Joara", "ridi": "Ridibooks", "naverseries": "Naver Series"}
FORMAT = "metadata-v1"
FIELDS = ("id", "title", "author", "cover", "tags", "views", "likes", "episodes",
          "complete", "updated", "age", "canonical_url", "tier", "purchase_url",
          "metrics", "rankings")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def source_date(value):
    """Preserve the native date representation and only its explicit precision/zone."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    precision, zone = None, None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        precision = "day"
    elif re.fullmatch(r"\d{14}", text):
        try:
            datetime.strptime(text, "%Y%m%d%H%M%S")
            precision = "second"
        except ValueError:
            pass
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?", text):
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            precision = "fractional_second" if re.search(r":\d{2}\.\d+", text) else "second" if text[16:17] == ":" else "minute"
            suffix = re.search(r"(Z|[+-]\d{2}:?\d{2})$", text)
            zone = ("UTC" if suffix.group(1) == "Z" else suffix.group(1)) if suffix else None
        except ValueError:
            pass
    return {"raw": value, "precision": precision, "timezone": zone}


@dataclass
class CatalogPage:
    records: list
    next_page: int | None
    complete: bool = True
    error: str | None = None
    observed_total: int | None = None
    skipped_rows: list = field(default_factory=list)
    next_cursor: str | None = None


def catalog_coverage(cursors):
    """Keep recoverable row omissions distinct from failures that stop a scan."""
    skipped = [{"partition": key, **row} for key, cursor in cursors.items()
               for row in cursor.get("skipped_rows", [])]
    return {
        "discovery_complete": bool(cursors) and not skipped and all(c.get("complete") for c in cursors.values()),
        "errors": [{"partition": key, "page": cursor.get("error_page", cursor.get("next_page")),
                    "error": cursor["error"]} for key, cursor in cursors.items() if cursor.get("error")],
        "skipped_rows": skipped,
    }


def log_progress(source, message):
    print(f"[{utc_now()}] [{source}] {message}", flush=True)


@dataclass
class MetadataResult:
    status: str
    record: dict | None = None
    reason: str | None = None


@dataclass
class RankingResult:
    key: str
    label: str
    records: list
    observed_at: str = field(default_factory=utc_now)
    success: bool = True
    error: str | None = None


class BudgetExceeded(RuntimeError):
    pass


class FetchError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as f:
            temp = Path(f.name)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def atomic_json(path, data):
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def atomic_text(path, text):
    atomic_bytes(path, text.encode("utf-8"))


def empty_state(source):
    if source not in SOURCE_LABELS:
        raise ValueError("Unknown metadata source")
    return {"version": 1, "source": source, "records": {}, "boards": {}, "progress": {}, "coverage": {}}


def load_state(source, state_dir):
    path = Path(state_dir) / (source + ".json.gz")
    if not path.exists():
        return empty_state(source)
    state = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    if state.get("version") != 1 or state.get("source") != source:
        raise ValueError("State version or source mismatch")
    if not isinstance(state.get("records"), dict) or not isinstance(state.get("boards"), dict):
        raise ValueError("Malformed metadata state")
    state.setdefault("progress", {})
    state.setdefault("coverage", {})
    return state


def save_state(state, state_dir):
    source = state["source"]
    if source not in SOURCE_LABELS:
        raise ValueError("Unknown metadata source")
    content = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    atomic_bytes(Path(state_dir) / (source + ".json.gz"), gzip.compress(content, mtime=0))


def valid_id(value):
    value = str(value) if value is not None else ""
    return value if value.isascii() and value.isdigit() and int(value) > 0 else None


def merge_record(state, observation, *, detail=False, observed_at=None):
    """Missing values cannot erase historical metadata; explicit zero/False can."""
    ident = valid_id(observation.get("id"))
    if ident is None:
        raise ValueError("Metadata ID must be a positive numeric string")
    stamp = observed_at or utc_now()
    record = state["records"].setdefault(ident, {"id": ident})
    for key, value in observation.items():
        if key == "id" or key.startswith("_") or key in ("history", "translations", "translation_history", "rankings"):
            continue
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, dict):
            record[key] = {**record.get(key, {}), **{k: v for k, v in value.items() if v is not None}}
        else:
            record[key] = value
    history = record.setdefault("history", {})
    history.setdefault("first_seen", stamp)
    history["last_seen"] = stamp
    if detail:
        history.update(last_attempt=stamp, last_success=stamp, latest_outcome="success")
        record.pop("detail_error", None)
    return record


def record_outcome(record, result):
    history = record.setdefault("history", {})
    history.update(last_attempt=utc_now(), latest_outcome=result.status)
    if result.status == "unavailable":
        history["explicit_unavailability"] = {"observed_at": utc_now(), "reason": result.reason}
    record["detail_error"] = result.reason or result.status


def listing_fingerprint(record):
    fields = {key: value for key, value in record.items()
              if not key.startswith("_") and key not in ("rankings", "history", "translations")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def needs_detail(record, fingerprint, now=None):
    if record.get("detail_listing_fingerprint") != fingerprint:
        return True
    try:
        last = datetime.fromisoformat(record["history"]["last_success"].replace("Z", "+00:00"))
        return ((now or datetime.now(timezone.utc)) - last).total_seconds() >= 30 * 86400
    except (KeyError, TypeError, ValueError):
        return True


def export_rows(state):
    ranks = {}
    for key, board in state["boards"].items():
        for item in board.get("records", []):
            ident, rank = valid_id(item.get("id")), item.get("rank")
            if ident and isinstance(rank, int) and not isinstance(rank, bool) and rank > 0:
                ranks.setdefault(ident, {})[key] = rank
    rows = []
    for ident, record in sorted(state["records"].items(), key=lambda item: int(item[0])):
        if not record.get("title"):
            continue
        row = [record.get(key) for key in FIELDS]
        row[0] = ident
        row[2] = record.get("author") or ""
        row[3] = record.get("cover") or ""
        row[4] = record.get("tags") or []
        for index in (5, 6, 7, 10):
            value = row[index]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                row[index] = None
        row[8] = int(row[8]) if row[8] in (True, False, 0, 1) and row[8] is not None else None
        row[14] = record.get("metrics") or {}
        row[15] = ranks.get(ident, {})
        rows.append(row)
    return rows


class AnonymousClient:
    """Fresh guest sessions, destination-checked redirects, and host-wide pacing."""

    def __init__(self, adapter, *, max_requests=None, max_runtime=18000, delay=0.5, retries=4,
                 clock=time.monotonic, sleep=time.sleep):
        self.adapter = adapter
        self.request_errors = (requests.RequestException,) + getattr(adapter, "request_errors", ())
        self.max_requests = max_requests
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.deadline = self.started + max_runtime
        self.delay, self.retries = delay, retries
        self.requests = 0
        self.log = []
        self._lock = threading.Lock()
        self._last_start = {}
        self._local = threading.local()
        self._sessions = []

    def _check(self):
        if self.clock() >= self.deadline:
            raise BudgetExceeded("Runtime budget reached")
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise BudgetExceeded("Request budget reached")

    def _allowed(self, url):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise FetchError("Request destination is outside the metadata allowlist") from None
        if (parsed.scheme != "https" or parsed.username is not None or port not in (None, 443)
                or not self.adapter.is_allowed_url(url)):
            raise FetchError("Request destination is outside the metadata allowlist")

    def _session(self):
        if not hasattr(self._local, "session"):
            factory = getattr(self.adapter, "create_session", None)
            session = factory() if factory else requests.Session()
            session.trust_env = False  # Prevent .netrc credentials or ambient proxy authentication.
            if not factory:
                session.headers.update({"User-Agent": "NovelMetadataIndex/1.0 (public catalog and metadata only)"})
            session.headers.update({"Accept": "application/json,text/html;q=0.9,*/*;q=0.1"})
            self._local.session = session
            with self._lock:
                self._sessions.append(session)
        return self._local.session

    def _start(self, url, params):
        parsed = urlsplit(url)
        with self._lock:
            self._check()
            wait = self._last_start.get(parsed.hostname, -float("inf")) + self.delay - self.clock()
            if wait > 0:
                if self.clock() + wait >= self.deadline:
                    raise BudgetExceeded("Runtime budget reached before next request")
                self.sleep(wait)
            self._check()
            self._last_start[parsed.hostname] = self.clock()
            self.requests += 1
            # No query values, cookies, application keys, device IDs, or response bodies.
            entry = {"at": utc_now(), "method": "GET", "host": parsed.hostname, "path": parsed.path,
                     "query_keys": sorted(set(parse_qs(parsed.query, keep_blank_values=True))
                                          | set((params or {}).keys())), "status": None}
            self.log.append(entry)
            return entry

    def _pause(self, seconds):
        if self.clock() + seconds >= self.deadline:
            raise BudgetExceeded("Runtime budget reached during retry delay")
        self.sleep(seconds)

    def get(self, url, params=None):
        self._allowed(url)
        original_url, original_params = url, params
        for attempt in range(self.retries):
            url, params = original_url, original_params
            for redirect in range(6):
                self._allowed(url)
                # Adapter query restrictions must cover separately supplied params too.
                try:
                    request_url = requests.Request("GET", url, params=params).prepare().url
                except (requests.RequestException, ValueError):
                    raise FetchError("Invalid metadata request parameters") from None
                self._allowed(request_url)
                entry = self._start(url, params)
                remaining = max(0.1, min(30, self.deadline - self.clock()))
                try:
                    response = self._session().get(url, params=params, timeout=remaining, allow_redirects=False)
                except self.request_errors:
                    entry["status"] = "network_error"
                    if attempt + 1 >= self.retries:
                        raise FetchError("Metadata request failed after bounded attempts") from None
                    log_progress(self.adapter.source, f"Retry {attempt+1}/{self.retries}: {urlsplit(url).path}; network error")
                    self._pause(min(30, 2 ** attempt))
                    break
                entry["status"] = response.status_code
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(response.url, response.headers.get("Location", ""))
                    params = None
                    response.close()
                    if redirect == 5:
                        raise FetchError("Too many metadata redirects")
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    status = response.status_code
                    retry_after = response.headers.get("Retry-After", "")
                    response.close()
                    if attempt + 1 >= self.retries:
                        raise FetchError("Metadata server did not recover after bounded attempts", status)
                    delay = min(30, 2 ** attempt)
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            pass
                    log_progress(self.adapter.source, f"Retry {attempt+1}/{self.retries}: {urlsplit(url).path}; HTTP {status}; wait {delay:.1f}s")
                    self._pause(max(0, delay))
                    break
                if response.status_code >= 400:
                    status = response.status_code
                    response.close()
                    raise FetchError("Metadata endpoint returned HTTP " + str(status), status)
                response.encoding = "utf-8"
                return response
        raise FetchError("Metadata request failed")

    def get_text(self, url, params=None):
        response = self.get(url, params)
        try:
            return response.text
        finally:
            response.close()

    def get_json(self, url, params=None):
        response = self.get(url, params)
        try:
            return response.json()
        except ValueError:
            raise FetchError("Metadata endpoint returned malformed JSON", response.status_code) from None
        finally:
            response.close()

    def close(self):
        for session in self._sessions:
            session.close()


def merge_board(state, result):
    old = state["boards"].get(result.key)
    if not result.success:
        if old is not None:
            old.update(stale=True, last_attempt=utc_now(), error=result.error)
        else:
            state["boards"][result.key] = {"label": result.label, "observed_at": None,
                                           "stale": True, "records": [], "error": result.error}
        return
    seen, items = set(), []
    for item in result.records:
        ident, rank = valid_id(item.get("id")), item.get("rank")
        if not ident or not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ValueError("Malformed native ranking entry")
        if ident not in seen:
            items.append({**item, "id": ident})
            seen.add(ident)
    state["boards"][result.key] = {"label": result.label, "observed_at": result.observed_at,
                                  "stale": False, "records": items, "last_attempt": utc_now()}


def run_source(adapter, args, *, client=None):
    output_dir = Path(args.output_dir).resolve()
    state_dir = Path(args.state_dir).resolve() if args.state_dir else output_dir / ".state"
    if args.mode == "sample" and output_dir.is_relative_to((ROOT / "docs" / "data").resolve()):
        raise ValueError("Samples must use staging outside docs/data")
    if state_dir.is_relative_to((ROOT / "docs").resolve()):
        raise ValueError("Durable metadata state must remain outside the website directory")
    if args.dry_run:
        return {"source": adapter.source, "mode": args.mode, "dry_run": True,
                "requests": 0, "writes": 0, "output_dir": str(output_dir), "state_dir": str(state_dir)}
    state = load_state(adapter.source, state_dir)
    previous_coverage = state["coverage"]
    previous_baseline = previous_coverage.get("has_complete_baseline", False)
    progress = state["progress"]
    pending = dict.fromkeys(progress.get("pending_details", []))
    started = time.monotonic()
    old_catalog = previous_coverage.get("catalog", {})
    if not old_catalog and progress.get("partitions"):
        old_catalog = {"started": True, **catalog_coverage(progress["partitions"])}
    owned_client = client is None
    client = client or AnonymousClient(adapter, max_requests=args.max_requests, max_runtime=args.max_runtime,
                                       delay=args.delay, retries=args.retries)
    coverage = {"mode": args.mode, "started_at": utc_now(), "complete": False,
                "has_complete_baseline": previous_baseline, "status": "running", "errors": [],
                "pages": 0, "details": 0, "successful_details": 0, "successful_boards": 0,
                "catalog": dict(old_catalog), "rankings": dict(previous_coverage.get("rankings", {}))}
    state["coverage"] = coverage
    observed, detail_count, detail_attempted = set(), 0, set()
    had_budget = False

    last_save, last_detail_log, changes = time.monotonic(), time.monotonic(), 0
    initial_revision = progress.get("revision", 0)
    progress.setdefault("scan_id", uuid4().hex)

    def log(message):
        elapsed = max(0.001, time.monotonic() - started)
        log_progress(adapter.source, message + f" | {client.requests} requests ({client.requests/elapsed:.2f}/s), "
                     f"{max(0, args.max_runtime-elapsed):.0f}s remaining")

    def checkpoint(force=False):
        nonlocal last_save, changes
        if not force and changes < 500 and time.monotonic() - last_save < 60:
            return
        progress["pending_details"] = list(pending)
        coverage["requests"] = client.requests
        before = time.monotonic()
        save_state(state, state_dir)
        last_save, changes = time.monotonic(), 0
        log(f"Checkpoint: {len(state['records']):,} records, {len(pending):,} details pending; "
            f"saved in {last_save-before:.2f}s; revision {progress.get('revision', 0)}")

    def advanced(count=1):
        nonlocal changes
        changes += count
        if args.mode != "rankings":
            progress["revision"] = progress.get("revision", 0) + 1

    request_slots = threading.BoundedSemaphore(args.workers)

    def fetch_details(force_checkpoint=True):
        nonlocal detail_count, last_detail_log
        if not pending or (args.max_details is not None and detail_count >= args.max_details):
            return
        progress["phase"] = "details"
        detail_started = time.monotonic()
        detail_start_count = coverage["details"]
        last_detail_log = detail_started
        log(f"Details: {len(pending):,} pending; {args.workers} workers")
        checkpoint(force=force_checkpoint)
        ranking_ids = {str(item["id"]) for board in state["boards"].values() for item in board["records"]}
        queue = deque(sorted((ident for ident in pending if ident not in detail_attempted
                      and (args.mode != "rankings" or ident in ranking_ids)),
                      key=lambda ident: (bool(state["records"][ident].get("synopsis")), int(ident))))
        budget_error = None
        def fetch(ident):
            try:
                with request_slots:
                    return adapter.detail(client, dict(state["records"][ident]))
            except BudgetExceeded as exc:
                return exc
            except Exception as exc:
                return MetadataResult("failed", reason=type(exc).__name__ + ": metadata detail failed")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            active = {}
            while queue or active:
                while queue and len(active) < args.workers and not budget_error and (
                        args.max_details is None or detail_count + len(active) < args.max_details):
                    ident = queue.popleft()
                    active[pool.submit(fetch, ident)] = ident
                if not active:
                    break
                done, _ = wait(active, timeout=10, return_when=FIRST_COMPLETED)
                for future in done:
                    ident = active.pop(future)
                    result = future.result()
                    if isinstance(result, BudgetExceeded):
                        budget_error = result
                        continue
                    detail_count += 1
                    detail_attempted.add(ident)
                    coverage["details"] += 1
                    if (not isinstance(result, MetadataResult)
                            or result.status not in {"success", "restricted", "unavailable", "failed"}
                            or (result.record is not None and not isinstance(result.record, dict))
                            or (result.status == "success" and result.record is None)):
                        result = MetadataResult("failed", reason="Malformed metadata detail result")
                    if result.record is not None:
                        if valid_id(result.record.get("id")) != ident:
                            result = MetadataResult("failed", reason="Detail identity mismatch")
                        elif result.status in {"success", "restricted"}:
                            record = merge_record(state, result.record, detail=result.status == "success")
                    if result.status == "success":
                        record["detail_listing_fingerprint"] = record.get("listing_fingerprint")
                        coverage["successful_details"] += 1
                    else:
                        record_outcome(state["records"][ident], result)
                        log(f"Detail {ident}: {result.status}; {result.reason}")
                    if result.status != "failed":
                        pending.pop(ident, None)
                        advanced()
                if time.monotonic() - last_detail_log >= 10:
                    log(f"Details: {coverage['details']:,} attempted, {coverage['successful_details']:,} successful, "
                        f"{len(pending):,} pending, {len(active)} in flight; "
                        f"{(coverage['details']-detail_start_count)*60/max(.001, time.monotonic()-detail_started):.1f} details/min")
                    last_detail_log = time.monotonic()
                checkpoint()
        checkpoint(force=force_checkpoint)
        if budget_error:
            raise budget_error

    log(f"Start {args.mode}; resume={args.resume}; workers={args.workers}; host delay={args.delay}s; "
        f"request limit={args.max_requests or 'none'}; scan={progress['scan_id']}")
    try:
        if args.mode != "rankings":
            if not args.resume or progress.get("pass_complete"):
                progress["partitions"] = {}
                progress["pass_started_at"] = utc_now()
                progress["scan_id"] = uuid4().hex
                progress.pop("ranking_pass", None)
            progress["pass_complete"] = False
            progress["phase"] = "discovery"
            coverage["catalog"]["started"] = True
            checkpoint(force=True)
            # Resume enrichment before spending another run discovering listings.
            # The pending queue is durable even when a recovered scan differs.
            if args.mode == "catalog":
                fetch_details()
            progress["phase"] = "discovery"
            partitions = adapter.partitions(client)
            if not partitions:
                raise ValueError("No anonymous catalog partitions were discovered")
            cursors = progress.setdefault("partitions", {})
            try:
                from .metadata_pages import catalog_pages
            except ImportError:
                from metadata_pages import catalog_pages
            from contextlib import closing
            with closing(catalog_pages(adapter, client, partitions, cursors, args, log, request_slots)) as pages:
                for job, page, result in pages:
                    partition, cursor = job["partition"], job["cursor"]
                    key, tier = partition["key"], partition["tier"]
                    cursor_mode = partition.get("pagination")
                    overlap, overlap_for = job["overlap"], job["overlap_for"]
                    page_signatures = job["signatures"]
                    coverage["pages"] += 1
                    if not result.complete or result.error:
                        cursor["error"] = result.error or "Incomplete catalog response"
                        cursor["error_page"] = page
                        coverage["errors"].append({"partition": key, "page": page, "error": cursor["error"]})
                        log(f"Catalog {key} page {page}: {cursor['error']}")
                        checkpoint()
                        continue
                    if cursor_mode and result.next_page is not None and (
                            not isinstance(result.next_cursor, str) or not result.next_cursor
                            or result.next_cursor == cursor.get("cursor_point")):
                        cursor["error"] = "Missing or non-advancing catalog cursor"
                        cursor["error_page"] = page
                        coverage["errors"].append({"partition": key, "page": page, "error": cursor["error"]})
                        log(f"Catalog {key} page {page}: {cursor['error']}")
                        checkpoint()
                        continue
                    ids = [valid_id(item.get("id")) for item in result.records]
                    signature = hashlib.sha256(json.dumps(sorted(set(ids), key=str)).encode()).hexdigest()
                    repeated_across_resume = (page != cursor.get("last_page")
                                              and signature == cursor.get("last_page_signature"))
                    if any(ident is None for ident in ids) or (ids and (signature in page_signatures or repeated_across_resume)):
                        cursor["error"] = "Invalid IDs or repeated catalog page"
                        cursor["error_page"] = page
                        coverage["errors"].append({"partition": key, "page": page, "error": cursor["error"]})
                        log(f"Catalog {key} page {page}: {cursor['error']}")
                        checkpoint()
                        continue
                    if not ids and result.next_page is not None:
                        cursor["error"] = "Unexplained empty catalog page"
                        cursor["error_page"] = page
                        coverage["errors"].append({"partition": key, "page": page, "error": cursor["error"]})
                        log(f"Catalog {key} page {page}: {cursor['error']}")
                        checkpoint()
                        continue
                    page_signatures.add(signature)
                    before_count = len(state["records"])
                    for item, ident in zip(result.records, ids):
                        fingerprint = listing_fingerprint(item)
                        old = state["records"].get(ident, {})
                        refresh = needs_detail(old, fingerprint)
                        enriched = bool(item.get("_detail_complete"))
                        observation = item
                        if not enriched and old.get("synopsis") and old.get("history", {}).get("last_success"):
                            # A truncated catalog preview cannot replace a last good
                            # synopsis while its full detail request is still pending.
                            observation = {k: v for k, v in item.items() if k != "synopsis"}
                            if "synopsis_is_preview" in old:
                                observation["synopsis_is_preview"] = old["synopsis_is_preview"]
                                observation["metrics"] = {**item.get("metrics", {}),
                                                          "synopsis_is_preview": old["synopsis_is_preview"]}
                        record = merge_record(state, observation, detail=enriched)
                        record["listing_fingerprint"] = fingerprint
                        if enriched:
                            record["detail_listing_fingerprint"] = fingerprint
                            if ident in pending:
                                pending.pop(ident, None)
                        observed.add(ident)
                        if refresh and not item.get("_detail_complete") and ident not in pending and ident not in detail_attempted:
                            pending[ident] = None
                    cursor.pop("error", None)
                    cursor.pop("error_page", None)
                    skipped_rows = [row for row in cursor.get("skipped_rows", []) if row["page"] != page]
                    skipped_rows.extend({**row, "page": page} for row in result.skipped_rows)
                    if skipped_rows:
                        cursor["skipped_rows"] = skipped_rows
                    else:
                        cursor.pop("skipped_rows", None)
                    for row in result.skipped_rows:
                        log(f"Catalog {key} page {page}, row {row['row']}, ID {row.get('id', 'unknown')}: "
                            f"skipped ({row['error']}); continuing with valid rows")
                    if overlap and page < overlap_for:
                        cursor["overlap_checked_for"] = overlap_for
                    if result.next_page is None:
                        cursor["complete"] = True
                    elif not isinstance(result.next_page, int) or result.next_page <= page:
                        cursor["error"] = "Non-advancing catalog pagination"
                        cursor["error_page"] = page
                        coverage["errors"].append({"partition": key, "page": page, "error": cursor["error"]})
                    else:
                        cursor["next_page"] = result.next_page
                        if cursor_mode:
                            cursor["cursor_point"] = result.next_cursor
                    cursor["last_page"] = page
                    cursor["last_page_signature"] = signature
                    advanced(len(ids))
                    log(f"Catalog {key} page {page}: {len(ids)} rows, "
                        f"{len(state['records'])-before_count} new, {len(state['records']):,} total; "
                        f"upstream total={result.observed_total}; next={result.next_page}; "
                        f"{coverage['pages']*60/max(.001, time.monotonic()-started):.1f} pages/min")
                    checkpoint()
                    if args.mode == "catalog":
                        fetch_details(force_checkpoint=False)
                        progress["phase"] = "discovery"
            discovery_complete = (all(cursors.get(p["key"], {}).get("complete") for p in partitions)
                                  and not any(c.get("skipped_rows") for c in cursors.values()))
            coverage["discovery_complete"] = discovery_complete
            coverage["catalog"].update(catalog_coverage(cursors))
            checkpoint(force=True)
            # Samples enrich their bounded discovery; catalogs enrich each page.
            discovery_settled = all(cursors.get(p["key"], {}).get("complete") or cursors.get(p["key"], {}).get("error") for p in partitions)
            if args.mode == "sample" or discovery_settled:
                fetch_details()
        if not getattr(adapter, "supports_rankings", True):
            coverage["rankings_complete"] = True
        elif args.mode in ("catalog", "rankings") and (args.mode == "rankings" or discovery_settled):
            progress["phase"] = "rankings"
            log("Rankings: refreshing native boards")
            ranking_pass = progress.get("ranking_pass", {})
            continuing = (args.resume and ranking_pass.get("mode") == args.mode
                          and not ranking_pass.get("complete", False))
            if not continuing:
                ranking_pass = {"mode": args.mode, "started_at": utc_now(),
                                "successful_keys": [], "complete": False}
                progress["ranking_pass"] = ranking_pass
            successful_keys = set(ranking_pass.get("successful_keys", []))
            # Prior observations remain stale except boards already completed in
            # this same interrupted pass; fresh refreshes always attempt all boards.
            for key, board in state["boards"].items():
                if key not in successful_keys:
                    board["stale"] = True
            checkpoint()
            for result in adapter.rankings(client, skip_keys=successful_keys):
                try:
                    merge_board(state, result)
                except ValueError as exc:
                    result = RankingResult(result.key, result.label, [], success=False, error=str(exc))
                    merge_board(state, result)
                if not result.success:
                    coverage["errors"].append({"board": result.key, "error": result.error})
                else:
                    coverage["successful_boards"] += 1
                    successful_keys.add(result.key)
                    ranking_pass["successful_keys"] = sorted(successful_keys)
                    for item in result.records:
                        ident = valid_id(item.get("id"))
                        if ident and ident not in state["records"]:
                            if item.get("_detail_complete"):
                                # Some boards include full catalog metadata, explicitly
                                # marked by the adapter. Persist it before a budget stop.
                                merge_record(state, {k: v for k, v in item.items() if k != "rank"}, detail=True)
                                continue
                            # Windowed ranking counts never become lifetime catalog metrics.
                            seed = {k: v for k, v in item.items() if k in ("id", "title", "author", "cover", "canonical_url", "tier")}
                            merge_record(state, seed)
                            pending[ident] = None
                advanced(len(result.records))
                log(f"Ranking {result.key}: {len(result.records)} records; success={result.success}")
                checkpoint()
            fetch_details()
            coverage["rankings_complete"] = bool(successful_keys) and not any(b.get("stale") for b in state["boards"].values())
            ranking_pass["complete"] = coverage["rankings_complete"] and not pending
    except BudgetExceeded as exc:
        had_budget = True
        coverage["stop_reason"] = str(exc)
        log(f"Budget stop: {exc}")
    except Exception as exc:
        # Avoid persisting network exception URLs containing public bootstrap keys.
        coverage["errors"].append({"error": type(exc).__name__ + ": collection could not finish"})
    finally:
        coverage["complete"] = bool(args.mode != "sample" and not had_budget and not coverage["errors"]
                                    and ((coverage.get("discovery_complete") and coverage.get("rankings_complete")) if args.mode == "catalog"
                                         else coverage.get("rankings_complete")) and not pending)
        coverage["has_complete_baseline"] = bool(previous_baseline or (coverage["complete"] and args.mode == "catalog"))
        if args.mode == "catalog":
            progress["pass_complete"] = coverage["complete"]
        if args.mode != "rankings":
            cursors = progress.get("partitions", {})
            coverage["catalog"].update(catalog_coverage(cursors))
        coverage["catalog"]["has_complete_baseline"] = bool(
            old_catalog.get("has_complete_baseline") or previous_baseline or
            (args.mode == "catalog" and coverage["catalog"].get("discovery_complete")))
        coverage["enrichment"] = {"pending": len(pending), "complete": not pending,
                                  "failed": sum(state["records"][i].get("history", {}).get("latest_outcome") == "failed" for i in pending)}
        coverage["rankings"] = {"available": getattr(adapter, "supports_rankings", True),
                                "complete": not getattr(adapter, "supports_rankings", True) or bool(state["boards"]) and not any(b.get("stale") for b in state["boards"].values()),
                                "boards": len(state["boards"])}
        coverage["status"] = "complete" if coverage["complete"] else "partial"
        coverage["continuation"] = {
            "eligible": bool(args.mode == "catalog" and had_budget and not coverage["errors"]
                             and not coverage["catalog"].get("errors") and not coverage["enrichment"]["failed"]
                             and progress.get("revision", 0) > initial_revision),
            "scan_id": progress["scan_id"], "revision": progress.get("revision", 0),
            "phase": progress.get("phase"), "workers": args.workers}

        if args.mode == "catalog":
            progress["catalog_continuation"] = dict(coverage["continuation"])
        coverage["observed_records"] = len(observed)
        coverage["metadata_updated"] = bool(observed or coverage["successful_details"] or coverage["successful_boards"]
                                            or (args.mode != "rankings" and progress.get("revision", 0) > initial_revision))
        coverage["finished_at"] = utc_now()
        checkpoint(force=True)
        log(f"Finished: {len(state['records']):,} records; {coverage['status']}; "
            f"discovery complete={coverage['catalog'].get('discovery_complete', False)}; "
            f"details pending={len(pending):,}; continuation={coverage['continuation']['eligible']}")
        rows = export_rows(state)
        atomic_json(output_dir / (adapter.source + "_novels.json"), rows)
        report = {"source": adapter.source, "format": FORMAT, "records": len(rows), "coverage": coverage,
                  "request_log": client.log}
        atomic_json(output_dir / (adapter.source + "_run_report.json"), report)
        cache_path = ROOT / ".cache" / "metadata" / adapter.source / (coverage["started_at"].replace(":", "-") + ".json")
        atomic_json(cache_path, report)
        if owned_client:
            client.close()
    return report


def make_parser(adapter):
    parser = argparse.ArgumentParser(description=adapter.label + " anonymous metadata collector")
    parser.add_argument("--mode", choices=("sample", "catalog", "rankings"), default="sample")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    for flag in ("max-pages", "max-details", "max-requests", "workers", "retries"):
        parser.add_argument("--" + flag, type=int)
    parser.add_argument("--max-runtime", "--max-seconds", dest="max_runtime", type=float)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration without network requests or writes")
    return parser


def resolve_args(adapter, argv=None):
    parser = make_parser(adapter)
    args = parser.parse_args(argv)
    sample = args.mode == "sample"
    defaults = {"workers": 1 if sample else 4, "delay": 2.0 if sample else 0.5, "retries": 4,
                "max_runtime": 300 if sample else 18000, "max_pages": 2 if sample else None,
                "max_details": 5 if sample else None, "max_requests": 20 if sample else None}
    for key, value in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    for key in ("max_pages", "max_details", "max_requests", "max_runtime", "workers", "retries"):
        value = getattr(args, key)
        if value is not None and value <= 0:
            parser.error("--" + key.replace("_", "-") + " must be positive")
    if args.delay < 0.5:
        parser.error("--delay must be at least 0.5 seconds")
    if args.retries > 4:
        parser.error("--retries permits at most four attempts")
    if args.workers > 16:
        parser.error("--workers permits at most 16 metadata workers")
    return args


def run_cli(adapter, argv=None):
    args = resolve_args(adapter, argv)
    try:
        report = run_source(adapter, args)
    except (ValueError, OSError) as exc:
        print("Metadata collection could not start: " + str(exc))
        return 2
    print(json.dumps({k: v for k, v in report.items() if k != "request_log"}, ensure_ascii=True, indent=2))
    return 0 if report.get("dry_run") or report.get("coverage", {}).get("metadata_updated") else 1
