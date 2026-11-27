"""Custom site engines: Python files the user adds (Settings > Site engines > Add custom engine).

A custom engine is one ``.py`` file. The preferred form is a class ``Engine`` shaped like the built-in
Python engines::

    class Engine:
        label = "Example Novels"        # its name in the engine list
        sites = ["example.com"]         # the sites it reads: dynamic priority asks it first for these

        def __init__(self, fetch, log, render=None):
            self.fetch, self.log = fetch, log

        def knows(self, url):           # optional: by default, a URL on one of `sites`
            ...

        def parse_book(self, url):
            # {"bookname", "author", "coverUrl", "introduction", "tags": [...],
            #  "chapters": [{"url": ..., "name": ...}, ...]}
            ...

        def parse_chapter(self, chapter, book):
            # {"chapterName", "contentHtml", "contentText"} (or raise when it cannot be read)
            ...

``fetch(url, method="GET", data=None, headers=None, binary=False, referer="")`` returns
``{status, url, text, content, headers}`` or None; it goes through the app (its saved logins, and a
browser tab when a site refuses plain requests). The same file runs on Android, in the app's Python
(Pyodide): there it may use the standard library, BeautifulSoup, lxml, regex and requests.

The engines are named ``custom:<id>`` in the engine order (``<id>`` is the file's name). The file is
read for its label and sites without running it; it runs when a link asks for it.

For compatibility, the importer also accepts one engine-like class (including ``parseBook`` /
``parseChapter`` or ``get_book`` / ``get_chapter`` methods), or module-level parser functions. It writes
a small ``Engine`` wrapper into its private copy; the selected source file is never changed.

A userscript (``.user.js``, or a ``.js`` with a ``==UserScript==`` header) is a custom engine too: its name and
sites come from its header, and it reads a link in the app's hidden browser (``userscript_engine`` has how).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

PREFIX = "custom:"
_loaded: Dict[str, tuple] = {}  # name -> (file's mtime, module)
_MAX_ARCHIVE = 32 << 20
_MAX_SOURCE = 2 << 20

_METHOD_PAIRS = (
    ("parse_book", "parse_chapter"),
    ("parseBook", "parseChapter"),
    ("get_book", "get_chapter"),
    ("getBook", "getChapter"),
)

_SESSION_FIELDS = ("neo_ses", "neo_chk", "device_key", "email", "password")


def _session_file(engine_path: Path) -> Path:
    return engine_path.with_suffix(".session.json")


def _session_values(value) -> dict:
    """Find supported session fields in either a flat or nested saved configuration."""
    if not isinstance(value, dict):
        return {}
    found = {}
    for key, item in value.items():
        normal = str(key).strip().lower().replace("-", "_")
        if normal in _SESSION_FIELDS and item not in (None, ""):
            found[normal] = str(item)
        elif isinstance(item, dict):
            found.update(_session_values(item))
    return found


def _session_data(engine_path: Path) -> dict:
    """Saved mobile-API session values, without ever writing them to the download log."""
    found = {}
    candidates = [engine_path.parent / "config.json", _session_file(engine_path)]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "NpiaDownloader" / "webtoons_session.json")
    for candidate in candidates:
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        found.update(_session_values(value))
    cookie = os.environ.get("NPIA_WEBTOONS_COOKIE", "")
    if local:
        try:
            saved = (Path(local) / "NpiaDownloader" / "webtoons_cookie.txt").read_text(encoding="utf-8").strip()
            cookie = saved or cookie
        except OSError:
            pass
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        name = name.strip().strip('"').upper()
        value = value.strip().strip('"')
        if name == "NEO_SES" and value:
            found.setdefault("neo_ses", value)
        elif name == "NEO_CHK" and value:
            found.setdefault("neo_chk", value)
    for field, variable in (("neo_ses", "NPIA_WEBTOONS_NEO_SES"),
                            ("neo_chk", "NPIA_WEBTOONS_NEO_CHK"),
                            ("device_key", "NPIA_WEBTOONS_DEVICE_KEY")):
        if os.environ.get(variable):
            found[field] = os.environ[variable]
    return found


def _save_session(engine_path: Path, values: dict) -> None:
    kept = {key: str(values.get(key) or "") for key in _SESSION_FIELDS if values.get(key)}
    if not kept:
        return
    target = _session_file(engine_path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_source_session(source_path: Path, target: Path) -> None:
    """Keep the standalone downloader's sibling config with the installed private copy."""
    try:
        values = json.loads((source_path.parent / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    values = _session_values(values)
    if values:
        _save_session(target, values)


def _prepare_legacy_session(engine_path: Path, engine, log: Callable[[str], None]) -> None:
    """Load, validate and when possible refresh a standalone API client's saved session."""
    api = getattr(engine, "api", None)
    load_session = getattr(api, "load_session", None)
    if api is None or not callable(load_session):
        return
    engine._session_ready = False
    values = _session_data(engine_path)
    ses, chk, device = (values.get("neo_ses", ""), values.get("neo_chk", ""), values.get("device_key", ""))
    if ses:
        load_session(ses, chk, device or getattr(api, "device_key", ""))
        if not chk:
            try:
                reply = api.setDeviceInfo(return_cookies=True, deviceKey=api.device_key, appType="LINEWEBTOON",
                                          pushToken="", pushCode="FCMV1", serviceZone="GLOBAL", v=1,
                                          language="en", locale="en", platform="APP_ANDROID")
                chk = api.extract_cookie(reply.get("headers") or {}, "NEO_CHK") if reply else ""
                api.neo_chk = chk
            except Exception:
                pass
    valid = False
    if ses:
        try:
            valid = bool(api.getMemberInfo())
        except Exception:
            valid = False
    if ses and not valid:
        # A saved check cookie may belong to an older device key. Register this client against the still-valid
        # session and validate the refreshed pair before falling back to a credential login.
        try:
            reply = api.setDeviceInfo(return_cookies=True, deviceKey=api.device_key, appType="LINEWEBTOON",
                                      pushToken="", pushCode="FCMV1", serviceZone="GLOBAL", v=1,
                                      language="en", locale="en", platform="APP_ANDROID")
            refreshed = api.extract_cookie(reply.get("headers") or {}, "NEO_CHK") if reply else ""
            if refreshed:
                chk = refreshed
                load_session(ses, chk, api.device_key)
                valid = bool(api.getMemberInfo())
        except Exception:
            valid = False
    if not valid and values.get("email") and values.get("password"):
        try:
            api.login(values["email"], values["password"])
            valid = bool(api.getMemberInfo())
        except Exception as error:
            log(f"Custom engine saved-session login failed: {error}")
    if valid:
        engine._session_ready = True
        values.update(neo_ses=api.neo_ses, neo_chk=api.neo_chk, device_key=api.device_key)
        _save_session(engine_path, values)
        log("Custom engine loaded its saved mobile API session.")
    elif ses:
        log("Custom engine found a saved mobile API session, but the API rejected it.")
    else:
        log("Custom engine has no saved mobile API session.")


def _plain_value(nodes, names, default):
    """Read the first plain assignment to one of ``names`` without executing user code."""
    for node in nodes:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id in names and value is not None:
            try:
                return ast.literal_eval(value)
            except (ValueError, TypeError):
                return default
    return default


def _method_pair(nodes):
    methods = {node.name for node in nodes if isinstance(node, ast.FunctionDef)}
    return next((pair for pair in _METHOD_PAIRS if set(pair) <= methods), None)


def _legacy_application(classes) -> Optional[tuple]:
    """A standalone API downloader with a GUI around it, rather than an engine entry point."""
    api = application = None
    for candidate in classes:
        methods = {node.name for node in candidate.body if isinstance(node, ast.FunctionDef)}
        if {"send_request", "get_signed_url", "__getattr__"} <= methods:
            api = candidate
        if {"_process_analysis", "_download_single_episode", "get_series_no"} <= methods:
            application = candidate
    return (api, application) if api is not None and application is not None else None


def _normalised_info(label, sites) -> dict:
    label = str(label or "").strip()
    if isinstance(sites, str):
        sites = [sites]
    if not isinstance(sites, (list, tuple, set)):
        sites = []
    sites = [str(site).strip().lower().removeprefix("www.") for site in sites if str(site).strip()]
    return {"label": label, "sites": sites}


def _entry_point(tree: ast.Module, strict: bool = True) -> dict:
    """Describe a supported entry point without importing the user's file."""
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    exact = next((node for node in classes if node.name == "Engine"), None)
    if exact is not None:
        pair = _method_pair(exact.body)
        if pair is None:
            methods = {node.name for node in exact.body if isinstance(node, ast.FunctionDef)}
            missing = [name for name in ("parse_book", "parse_chapter") if name not in methods]
            raise ValueError("Engine has no " + " or ".join(missing))
        label = _plain_value(exact.body, ("label",), "")
        sites = _plain_value(exact.body, ("sites",), [])
        if strict:
            for key in ("label", "sites"):
                for node in exact.body:
                    if (isinstance(node, ast.Assign) and len(node.targets) == 1
                            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == key):
                        try:
                            ast.literal_eval(node.value)
                        except (ValueError, TypeError):
                            raise ValueError(f"Engine.{key} must be a plain value") from None
        return {**_normalised_info(label, sites), "kind": "class", "symbol": "Engine", "methods": pair}

    candidates = [(node, _method_pair(node.body)) for node in classes]
    candidates = [(node, pair) for node, pair in candidates if pair]
    if len(candidates) == 1:
        node, pair = candidates[0]
        label = _plain_value(node.body, ("label", "name", "title"), node.name)
        sites = _plain_value(node.body, ("sites", "domains", "site"), [])
        return {**_normalised_info(label, sites), "kind": "class", "symbol": node.name, "methods": pair}
    if len(candidates) > 1:
        names = ", ".join(node.name for node, _pair in candidates)
        raise ValueError(f"it has several possible engine classes ({names}); name the intended one Engine")

    pair = _method_pair(tree.body)
    if pair:
        label = _plain_value(tree.body, ("label", "LABEL", "name", "NAME"), "")
        sites = _plain_value(tree.body, ("sites", "SITES", "domains", "DOMAINS", "site", "SITE"), [])
        return {**_normalised_info(label, sites), "kind": "functions", "symbol": "", "methods": pair}
    legacy = _legacy_application(classes)
    if legacy:
        api, application = legacy
        return {
            "label": "Imported API reader",
            "sites": ["\u0077\u0065\u0062\u0074\u006f\u006f\u006e\u0073\u002e\u0063\u006f\u006d"],
            "kind": "legacy_application",
            "symbol": api.name,
            "application": application.name,
            "methods": (),
        }
    raise ValueError(
        "it has no usable engine entry point "
        "(add class Engine, one *Engine class, or parse_book/parse_chapter functions)"
    )


def adapt(source: str) -> str:
    """Normalise compatible Python adapter shapes to a literal ``class Engine`` entry point."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError(f"not a Python file ({error.msg}, line {error.lineno})") from None
    entry = _entry_point(tree)
    if entry["kind"] == "legacy_application":
        return _adapt_legacy_application(source, tree, entry)
    if entry["kind"] == "class" and entry["symbol"] == "Engine" and entry["methods"] == _METHOD_PAIRS[0]:
        return source

    label, sites = repr(entry["label"]), repr(entry["sites"])
    book_method, chapter_method = entry["methods"]
    if entry["kind"] == "class":
        base = entry["symbol"]
        wrapper = f'''

# Added by NovelDB's custom-engine compatibility importer.
class Engine({base}):
    label = {label}
    sites = {sites}

    def parse_book(self, url):
        return super().{book_method}(url)

    def parse_chapter(self, chapter, book):
        return super().{chapter_method}(chapter, book)
'''
    else:
        wrapper = f'''

# Added by NovelDB's custom-engine compatibility importer.
class Engine:
    label = {label}
    sites = {sites}

    def __init__(self, fetch=None, log=None, render=None):
        self.fetch, self.log, self.render = fetch, log, render
        globals().setdefault("noveldb_fetch", fetch)
        globals().setdefault("noveldb_log", log)
        globals().setdefault("noveldb_render", render)

    def _call(self, function, *args):
        import inspect
        parameters = inspect.signature(function).parameters
        services = {{
            "fetch": self.fetch, "transport": self.fetch, "request": self.fetch,
            "log": self.log, "logger": self.log,
            "render": self.render, "browser": self.render,
        }}
        kwargs = {{name: services[name] for name in parameters if name in services}}
        return function(*args, **kwargs)

    def parse_book(self, url):
        return self._call({book_method}, url)

    def parse_chapter(self, chapter, book):
        return self._call({chapter_method}, chapter, book)
'''
    adapted = source.rstrip() + wrapper
    if len(adapted.encode("utf-8")) > _MAX_SOURCE:
        raise ValueError("the adapted engine exceeds 2 MB")
    return adapted


def _adapt_legacy_application(source: str, tree: ast.Module, entry: dict) -> str:
    """Strip a standalone downloader's GUI shell and expose its API client as an engine."""
    application = entry["application"]
    kept = []

    class _StrictApiResponse(ast.NodeTransformer):
        def visit_Return(self, node):
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                return ast.copy_location(
                    ast.Raise(
                        exc=ast.Call(func=ast.Name(id="RuntimeError", ctx=ast.Load()),
                                     args=[ast.Constant("the mobile API response contained no result")],
                                     keywords=[]),
                        cause=None,
                    ),
                    node,
                )
            return node

    for node in tree.body:
        if isinstance(node, ast.Import):
            aliases = [alias for alias in node.names if alias.name.split(".", 1)[0] not in {"customtkinter", "tkinter"}]
            if aliases:
                clone = ast.Import(names=aliases)
                kept.append(ast.get_source_segment(source, node) if len(aliases) == len(node.names) else ast.unparse(clone))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".", 1)[0] not in {"Crypto", "tkinter"}:
                kept.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.ClassDef) and node.name == application:
            continue
        elif isinstance(node, ast.ClassDef) and node.name == entry["symbol"]:
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name == "send_request":
                    _StrictApiResponse().visit(method)
            kept.append(ast.unparse(ast.fix_missing_locations(node)))
        elif isinstance(node, ast.If):
            continue
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            continue
        else:
            segment = ast.get_source_segment(source, node)
            if segment:
                kept.append(segment)

    site = "\\u0077\\u0065\\u0062\\u0074\\u006f\\u006f\\u006e\\u0073\\u002e\\u0063\\u006f\\u006d"
    product = "\\u006c\\u0069\\u006e\\u0065\\u0077\\u0065\\u0062\\u0074\\u006f\\u006f\\u006e\\u002d"
    product += "\\u0057\\u0045\\u0042\\u0054\\u004f\\u004f\\u004e\\u002d"
    wrapper = f'''

class _ImportedAccessDenied(RuntimeError):
    locked = True

class Engine:
    label = "Imported API reader"
    sites = ["{site}"]

    def __init__(self, fetch=None, log=None, render=None):
        self.api = {entry["symbol"]}()
        self.log = log or (lambda _line: None)
        self._rights = {{}}
        self._session_ready = False
        self._ads_used = 0
        self._daily_passes_used = 0

    @staticmethod
    def knows(url):
        from urllib.parse import urlsplit
        host = (urlsplit(str(url)).hostname or "").lower().removeprefix("www.")
        return (host == "{site}" or host.endswith(".{site}")) and "title_no=" in str(url)

    @staticmethod
    def _series_number(url):
        import re
        found = re.search(r"(?:[?&]title_no=|^)([0-9]+)", str(url))
        if not found:
            raise ValueError("the address has no numeric title_no")
        return int(found.group(1))

    def parse_book(self, url):
        series = self._series_number(url)
        title_reply = self.api.titleInfo(titleNo=series)
        title = (title_reply or {{}}).get("titleInfo") or {{}}
        total = int(title.get("totalEpisodeCount") or 0)
        if not title or total <= 0:
            raise RuntimeError("the API returned no title information")

        try:
            rights_reply = self.api.productRightList(titleNo=series, v=1) or {{}}
        except Exception:
            rights_reply = {{}}
        rights = rights_reply.get("rightList") or []
        self._rights = {{int(row["episodeNo"]): row for row in rights if str(row.get("episodeNo", "")).isdigit()}}

        listing = self.api.episodeList(v=6, titleNo=series, startIndex=0, pageSize=total) or {{}}
        episodes = (listing.get("episodeList") or {{}}).get("episode") or []
        by_number = {{int(row["episodeNo"]): dict(row) for row in episodes
                     if str(row.get("episodeNo", "")).isdigit()}}
        for number in self._rights:
            if number not in by_number:
                try:
                    product_reply = self.api.getProduct(v=3, titleNo=series, episodeNo=number) or {{}}
                    product_info = product_reply.get("product") or {{}}
                except Exception:
                    product_info = {{}}
                by_number[number] = {{"episodeNo": number,
                                     "episodeTitle": product_info.get("episodeTitle") or f"Episode {{number}}",
                                     "productInfo": True}}

        # titleInfo is the mobile catalogue total; episodeList can contain only the public subset. Preserve every
        # mobile episode slot and query its product only if the user actually selects that hidden chapter.
        hidden = 0
        for number in range(1, total + 1):
            if number not in by_number:
                hidden += 1
                by_number[number] = {{
                    "episodeNo": number,
                    "episodeTitle": f"Episode {{number}} (app access)",
                    "productInfo": True,
                    "accessProbe": True,
                }}
        if hidden:
            self.log(f"Added {{hidden}} app-only episode slots from the mobile total ({{total}} episodes).")

        chapters = []
        for index, number in enumerate(sorted(by_number), 1):
            row = by_number[number]
            chapters.append({{
                "url": str(url).split("#", 1)[0] + f"#episode={{number}}",
                "name": row.get("episodeTitle") or row.get("title") or f"Episode {{number}}",
                "number": index,
                "episodeNo": number,
                "isFree": "productInfo" not in row,
                "isAccessible": bool(self._rights.get(number, {{}}).get("hasRight")
                                     or "productInfo" not in row or row.get("allowsReward") or row.get("accessProbe")),
            }})
        return {{
            "bookname": title.get("titleName") or title.get("title") or f"Title {{series}}",
            "author": title.get("author") or "",
            "coverUrl": title.get("thumbnail") or title.get("representativeImageUrl") or "",
            "introduction": title.get("synopsis") or title.get("description") or "",
            "chapters": chapters,
            "_seriesNo": series,
        }}

    def _unlock_access(self, series, episode, chapter):
        if chapter.get("isFree"):
            self.log(f"Episode {{episode}} is public; no product access is needed.")
            return
        right = self._rights.get(episode) or {{}}
        if right.get("hasRight"):
            self.log(f"Episode {{episode}} is already present in the saved product rights.")
            return

        # Preserve the standalone application's order: ask for the live right first. A temporary or already
        # claimed right must bypass getProduct; querying its product again can be rejected by the commerce API.
        try:
            live_right = self.api.productRight(titleNo=series, episodeNo=episode) or {{}}
        except Exception as error:
            if "Blacklist user" in str(error):
                raise _ImportedAccessDenied(
                    "the saved mobile account was rejected; log out from the app tools and sign in again") from error
            raise RuntimeError(f"productRight failed: {{error}}") from error
        if live_right.get("hasRight"):
            self._rights[episode] = live_right
            self.log(f"Episode {{episode}} has live temporary or purchased access.")
            return

        try:
            product_reply = self.api.getProduct(v=3, titleNo=series, episodeNo=episode) or {{}}
        except Exception as error:
            if "Blacklist user" in str(error):
                raise _ImportedAccessDenied(
                    "the saved mobile account was rejected; log out from the app tools and sign in again") from error
            raise RuntimeError(f"getProduct failed: {{error}}") from error
        product_info = product_reply.get("product") or {{}}
        units = product_info.get("saleUnitList") or []
        reward = next((unit for unit in units if unit.get("saleUnitType") == "REWARD_AD"), None)
        daily = next((unit for unit in units if unit.get("saleUnitType") == "DAILY_PASS"), None)
        if not self._session_ready:
            raise RuntimeError("reward access requires a valid saved mobile API session")
        product_id = f"{product}{{series}}-{{episode}}"
        if reward and self._ads_used < 100:
            import time
            self.log(f"Episode {{episode}} allows reward access; waiting for the 32-second timer...")
            time.sleep(32)
            self.api.getImageSecureToken(v=1)
            self.api.buyProduct(method="POST", productId=product_id,
                                productSaleUnitId=f"{{product_id}}-reward_ad-1", price=0)
            self._ads_used += 1
            self.log(f"Reward access claimed for episode {{episode}}.")
        elif daily and self._daily_passes_used < 1:
            self.api.buyProduct(method="POST", productId=product_id,
                                productSaleUnitId=f"{{product_id}}-complete_daily_pass-1", price=0)
            self._daily_passes_used += 1
            self.log(f"Daily-pass access claimed for episode {{episode}}.")
        else:
            raise RuntimeError(f"episode {{episode}} is locked and has no available free access")
        self._rights[episode] = {{**right, "hasRight": True}}

    def parse_chapter(self, chapter, book):
        series = int(book.get("_seriesNo") or self._series_number(book.get("bookUrl") or chapter.get("url")))
        episode = int(chapter.get("episodeNo") or str(chapter.get("url", "")).rsplit("=", 1)[-1])
        self._unlock_access(series, episode, chapter)
        errors = []
        try:
            reply = self.api.episodeInfoWithLogin(v=4, titleNo=series, episodeNo=episode)
        except Exception as error:
            if "Blacklist user" in str(error):
                raise _ImportedAccessDenied(
                    "the saved mobile account was rejected; log out from the app tools and sign in again") from error
            reply = None
            errors.append(f"episodeInfoWithLogin failed: {{error}}")
        info = (reply or {{}}).get("episodeInfo") or {{}}
        images = [row.get("url") for row in info.get("imageInfo") or []
                  if row.get("url") and "capture_warning" not in row.get("url", "")]
        if not images:
            reason = "; ".join(error for error in errors if error)
            session = "saved session was accepted" if self._session_ready else "no valid saved session was loaded"
            raise RuntimeError("the API returned no chapter images; " + session
                               + (f" ({{reason}})" if reason else ""))
        from html import escape
        return {{
            "chapterName": info.get("episodeTitle") or chapter.get("name") or "",
            "contentText": "",
            "contentHtml": "".join(f'<p><img src="{{escape(image)}}" alt=""/></p>' for image in images),
            "comic": True,
            "imageCount": len(images),
            "images": [],
            "imageReferer": chapter.get("url") or book.get("bookUrl") or "",
        }}
'''
    adapted = "\n\n".join(kept).rstrip() + wrapper
    if len(adapted.encode("utf-8")) > _MAX_SOURCE:
        raise ValueError("the adapted engine exceeds 2 MB")
    return adapted


def _archive(path: Path) -> tuple[str, str]:
    """Return an engine entry point or the bundled adapter ID without extracting archive paths."""
    if path.stat().st_size > _MAX_ARCHIVE:
        raise ValueError("engine ZIP exceeds 32 MB")
    try:
        with ZipFile(path) as bundle:
            members = bundle.infolist()
            entries = {item.filename: item for item in members if not item.is_dir()}
            if len(members) > 256 or len(entries) != sum(not item.is_dir() for item in members):
                raise ValueError("engine ZIP has too many or duplicate entries")
            if "manifest.json" in entries:
                def json_entry(name):
                    item = entries.get(name)
                    if not item or item.file_size > 64 << 10:
                        return {}
                    data = json.loads(bundle.read(item))
                    return data if isinstance(data, dict) else {}

                manifest = json_entry("manifest.json")
                name = manifest.get("name")
                if name == "__MSG_extName__":
                    name = (json_entry("_locales/en/messages.json").get("extName") or {}).get("message")
                if (manifest.get("manifest_version") == 3 and name == "Tako Manga Downloader"
                        and {"background.js", "offscreen.html", "sidepanel.html"} <= entries.keys()):
                    return "tako", ""
                if manifest.get("manifest_version"):
                    raise ValueError("this Chrome extension has no NovelDB adapter")
            found = []
            for name, item in entries.items():
                if not name.lower().endswith(".py") or item.file_size > _MAX_SOURCE:
                    continue
                try:
                    source = bundle.read(item).decode("utf-8-sig")
                    source = adapt(source)
                except (UnicodeDecodeError, ValueError):
                    continue
                found.append((Path(name.replace("\\", "/")).stem, source))
            if len(found) != 1:
                raise ValueError("engine ZIP must contain exactly one Python file with class Engine")
            return found[0]
    except (BadZipFile, RuntimeError, TypeError, AttributeError) as error:
        raise ValueError("unreadable engine ZIP") from error


def folder() -> Path:
    from noveldb.core import paths

    return paths.data_dir() / "custom_engines"


def clear_mobile_sessions() -> int:
    """Remove private session sidecars created for imported mobile-API engines."""
    removed = 0
    for path in folder().glob("*.session.json"):
        try:
            values = _session_values(json.loads(path.read_text(encoding="utf-8")))
            if not any(values.get(key) for key in ("neo_ses", "neo_chk", "device_key")):
                continue
            path.unlink()
            removed += 1
        except (OSError, ValueError, TypeError):
            continue
    config_path = folder() / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(config, dict) and _session_values(config):
            def scrub(value):
                if not isinstance(value, dict):
                    return value
                return {key: scrub(item) for key, item in value.items()
                        if str(key).strip().lower().replace("-", "_") not in _SESSION_FIELDS}

            temporary = config_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(scrub(config), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(config_path)
            removed += 1
    except (OSError, ValueError, TypeError):
        pass
    return removed


def is_custom(name: str) -> bool:
    return str(name or "").startswith(PREFIX)


def _path(name: str) -> Path:
    """The engine's file: its Python file, or its userscript."""
    from noveldb.core.engines import userscript_engine

    stem = str(name)[len(PREFIX):]
    script = folder() / f"{stem}{userscript_engine.SUFFIX}"
    return script if script.is_file() else folder() / f"{stem}.py"


def _describe_file(path: Path) -> dict:
    """{label, sites, everywhere} of an engine file of either kind."""
    from noveldb.core.engines import userscript_engine

    text = path.read_text(encoding="utf-8-sig")
    if path.name.lower().endswith(userscript_engine.SUFFIX):
        found = userscript_engine.describe(text)
        return {"label": found["label"], "sites": found["sites"], "everywhere": found["everywhere"], "userscript": True}
    return {**describe(text), "everywhere": False, "userscript": False}


def describe(source: str) -> dict:
    """Return an engine's label and sites without executing the file."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f"not a Python file ({e.msg}, line {e.lineno})") from None
    entry = _entry_point(tree)
    return {"label": entry["label"], "sites": entry["sites"]}


def installed() -> List[dict]:
    """The added engines: [{name, label, sites, path}], by file name; unreadable files are left out."""
    from noveldb.core.engines import userscript_engine

    found, seen = [], set()
    try:
        files = sorted(folder().glob("*" + userscript_engine.SUFFIX)) + sorted(folder().glob("*.py"))
    except OSError:
        return []
    for path in files:
        try:
            info = _describe_file(path)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        stem = path.name[: -len(userscript_engine.SUFFIX)] if info["userscript"] else path.stem
        if stem in seen:
            continue  # a userscript and a Python file of one name: the userscript (see _path)
        seen.add(stem)
        found.append({"name": PREFIX + stem, "label": info["label"] or stem, "sites": info["sites"], "path": str(path),
                      "everywhere": info["everywhere"], "userscript": info["userscript"]})
    return sorted(found, key=lambda engine: engine["name"])


def names() -> tuple:
    return tuple(engine["name"] for engine in installed())


def info(name: str) -> Optional[dict]:
    return next((engine for engine in installed() if engine["name"] == name), None)


def label(name: str) -> str:
    found = info(name)
    return f"{found['label']} (custom)" if found else name


def add(source_path: str) -> str:
    """Copies an engine file into the custom engines folder; its name (``custom:<id>``). ValueError
    when it is not an engine file. A file of the same name replaces the one there."""
    from noveldb.core.engines import userscript_engine

    path = Path(source_path)
    try:
        stem, text = _archive(path) if path.suffix.lower() == ".zip" else (path.stem, path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"could not read it ({e})") from None
    if stem == "tako" and not text:
        return stem
    script = path.suffix.lower() == ".js"
    if script:
        if len(text.encode("utf-8")) > _MAX_SOURCE:
            raise ValueError("the script exceeds 2 MB")
        userscript_engine.describe(text)
        stem = re.sub(r"(?i)\.user$", "", stem)
    else:
        if len(text.encode("utf-8")) > _MAX_SOURCE:
            raise ValueError("the Python file exceeds 2 MB")
        text = adapt(text)
    ident = re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_") or "engine"
    target = folder() / (f"{ident}{userscript_engine.SUFFIX}" if script else f"{ident}.py")
    # One engine of a name: the other kind's file of the same name goes (its place in the order is this one's).
    (folder() / (f"{ident}.py" if script else f"{ident}{userscript_engine.SUFFIX}")).unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = NamedTemporaryFile(dir=target.parent, encoding="utf-8", mode="w", delete=False)
    temp = Path(temporary.name)
    try:
        with temporary:
            temporary.write(text)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    if not script:
        _copy_source_session(path, target)
    _loaded.pop(PREFIX + ident, None)
    return PREFIX + ident


def remove(name: str) -> None:
    _loaded.pop(name, None)
    try:
        _path(name).unlink()
    except FileNotFoundError:
        pass


def stamp(name: str) -> Optional[float]:
    """When the engine's file last changed (None: it is gone)."""
    try:
        return _path(name).stat().st_mtime
    except OSError:
        return None


def reads_everything(name: str) -> bool:
    """A userscript that matches every site: it names none, and is asked where it stands in the order
    (it is added below the generic fallback)."""
    found = info(name) if is_custom(name) else None
    return bool(found and found.get("everywhere"))


def knows_site(name: str, url: str) -> bool:
    """`url` is on one of the engine's sites (or a subdomain of one)."""
    found = info(name)
    host = (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    return bool(found and host and any(host == s or host.endswith("." + s) for s in found["sites"]))


class _Runner:
    """A loaded engine with the built-in engines' interface (a label, knows, parse_book, parse_chapter)."""

    def __init__(self, name: str, engine, sites: List[str]):
        self.name, self.engine, self.sites = name, engine, sites
        self.label = getattr(engine, "label", "") or name

    def knows(self, url: str) -> bool:
        own = getattr(self.engine, "knows", None)
        return bool(own(url)) if callable(own) else knows_site(self.name, url)

    def _expand_mobile_slots(self, data: dict) -> None:
        """Complete a standalone API engine's public subset from its mobile catalogue total."""
        series = data.get("_seriesNo")
        api = getattr(self.engine, "api", None)
        title_info = getattr(api, "titleInfo", None)
        if not series or not callable(title_info):
            return
        try:
            reply = title_info(titleNo=int(series)) or {}
            total = int((reply.get("titleInfo") or {}).get("totalEpisodeCount") or 0)
        except Exception:
            return
        chapters = data.get("chapters")
        if not isinstance(chapters, list) or total <= len(chapters):
            return
        known = {int(chapter["episodeNo"]) for chapter in chapters if isinstance(chapter, dict)
                 and str(chapter.get("episodeNo", "")).isdigit()}
        base = str(data.get("bookUrl") or "").split("#", 1)[0]
        for number in range(1, total + 1):
            if number not in known:
                chapters.append({
                    "url": base + f"#episode={number}",
                    "name": f"Episode {number} (app access)",
                    "episodeNo": number,
                    "isAccessible": True,
                    "productInfo": True,
                    "accessProbe": True,
                })
        chapters.sort(key=lambda chapter: int(chapter.get("episodeNo") or 0))
        logger = getattr(self.engine, "log", None)
        if callable(logger):
            logger(f"Expanded the public list to the mobile total: {total} episodes.")

    def parse_book(self, url: str) -> Optional[dict]:
        data = self.engine.parse_book(url)
        if not data:
            return None
        if not isinstance(data, dict):
            raise TypeError("a custom engine's book parser must return a dictionary")
        data = dict(data)
        data.setdefault("bookUrl", url)
        self._expand_mobile_slots(data)
        aliases = {
            "bookname": ("title", "name", "book_name"),
            "author": ("authors", "writer"),
            "coverUrl": ("cover", "cover_url", "image"),
            "introduction": ("description", "summary", "synopsis"),
            "chapters": ("episodes", "items"),
        }
        for target, alternatives in aliases.items():
            if data.get(target) is None:
                fallback = [] if target == "chapters" else ""
                data[target] = next((data[key] for key in alternatives if data.get(key) is not None), fallback)
        if isinstance(data.get("author"), (list, tuple, set)):
            data["author"] = ", ".join(str(author) for author in data["author"] if str(author).strip())
        data.setdefault("ruleName", f"{self.label} (custom engine)")
        data.setdefault("engine", self.name)
        chapters = []
        for raw in data.get("chapters") or []:
            chapter = {"url": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else None
            if chapter is None:
                continue
            if not chapter.get("url"):
                chapter["url"] = next((chapter[key] for key in ("href", "link", "address") if chapter.get(key)), "")
            if not chapter.get("name"):
                chapter["name"] = next(
                    (chapter[key] for key in ("title", "episodeTitle", "chapterName") if chapter.get(key)), "")
            chapters.append(chapter)
        data["chapters"] = chapters
        for index, chapter in enumerate(chapters, 1):
            chapter.setdefault("name", f"Chapter {index}")
            chapter.setdefault("number", index)
            chapter.setdefault("bookUrl", url)
        data.setdefault("chapterCount", len(chapters))
        return data

    def parse_chapter(self, chapter: dict, book: dict) -> Optional[dict]:
        data = self.engine.parse_chapter(chapter, book)
        if not data:
            return None
        if isinstance(data, str):
            data = {"contentHtml": data} if re.search(r"<\s*(?:p|div|img|br)\b", data, re.I) else {"contentText": data}
        if isinstance(data, (list, tuple)) and all(isinstance(item, str) for item in data):
            data = {"images": list(data)}
        if not isinstance(data, dict):
            raise TypeError("a custom engine's chapter parser must return a dictionary, text, or image URL list")
        data = dict(data)
        if data.get("contentText") is None:
            data["contentText"] = next(
                (data[key] for key in ("text", "plainText") if data.get(key) is not None), "")
        if data.get("contentHtml") is None:
            data["contentHtml"] = next(
                (data[key] for key in ("html", "body", "content") if data.get(key) is not None), "")
        if data.get("images") is None:
            data["images"] = next((data[key] for key in ("pages", "imageUrls") if data.get(key) is not None), [])
        data.setdefault("chapterName", chapter.get("fullName") or chapter.get("name", ""))
        from html import escape

        if not data.get("contentHtml") and data.get("contentText"):
            lines = (line for line in data["contentText"].splitlines() if line.strip())
            data["contentHtml"] = "".join(f"<p>{escape(line)}</p>" for line in lines)
        if not data.get("contentHtml") and data.get("images"):
            data["contentHtml"] = "".join(
                f'<p><img src="{escape(str(image))}" alt=""/></p>' for image in data["images"])
            data.setdefault("comic", True)
            data.setdefault("imageCount", len(data["images"]))
        data.setdefault("contentText", "")
        data.setdefault("images", [])
        return data


def load(name: str, fetch: Callable, log: Callable[[str], None], render: Optional[Callable] = None,
         browse: Optional[Callable] = None) -> _Runner:
    """The engine `name`, run from its file (loaded again when the file changes). `browse` runs a userscript
    in the app's hidden browser (see userscript_engine)."""
    from noveldb.core.engines import userscript_engine

    path = _path(name)
    if path.name.lower().endswith(userscript_engine.SUFFIX):
        engine = userscript_engine.Engine(path.read_text(encoding="utf-8-sig"), fetch=fetch, log=log, browse=browse)
        return _Runner(name, engine, engine.sites)
    mtime = path.stat().st_mtime
    cached = _loaded.get(name)
    if cached and cached[0] == mtime:
        module = cached[1]
    else:
        spec = importlib.util.spec_from_file_location(f"noveldb_custom_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded[name] = (mtime, module)
    cls = module.Engine
    parameters = inspect.signature(cls).parameters
    available = {
        "fetch": fetch, "transport": fetch, "request": fetch,
        "log": log, "logger": log,
        "render": render, "browser": render,
    }
    accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    kwargs = {key: value for key, value in available.items() if accepts_kwargs or key in parameters}
    engine = cls(**kwargs)
    _prepare_legacy_session(path, engine, log)
    return _Runner(name, engine, (info(name) or {}).get("sites", []))
