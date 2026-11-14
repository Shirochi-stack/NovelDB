"""Custom site engines: Python files the user adds (Settings > Site engines > Add custom engine).

A custom engine is one ``.py`` file with a class ``Engine`` shaped like the built-in Python engines::

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

A userscript (``.user.js``, or a ``.js`` with a ``==UserScript==`` header) is a custom engine too: its name and
sites come from its header, and it reads a link in the app's hidden browser (``userscript_engine`` has how).
"""

from __future__ import annotations

import ast
import importlib.util
import json
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
                    describe(source)
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
    """{label, sites} of an engine file's ``Engine`` class, read without running it; ValueError when
    the file has no such class (or its label or sites are not plain values)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f"not a Python file ({e.msg}, line {e.lineno})") from None
    engine = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine"), None)
    if engine is None:
        raise ValueError("it has no class Engine")
    found = {"label": "", "sites": []}
    for node in engine.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            if key in found:
                try:
                    found[key] = ast.literal_eval(node.value)
                except ValueError:
                    raise ValueError(f"Engine.{key} must be a plain value") from None
    methods = {n.name for n in engine.body if isinstance(n, ast.FunctionDef)}
    missing = [m for m in ("parse_book", "parse_chapter") if m not in methods]
    if missing:
        raise ValueError("Engine has no " + " or ".join(missing))
    label = str(found["label"] or "").strip()
    sites = found["sites"]
    if isinstance(sites, str):
        sites = [sites]
    sites = [str(s).strip().lower().removeprefix("www.") for s in (sites or []) if str(s).strip()]
    return {"label": label, "sites": sites}


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
        describe(text)
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
    """A userscript that matches every site: it names none, and is asked after the generic fallback."""
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

    def parse_book(self, url: str) -> Optional[dict]:
        data = self.engine.parse_book(url)
        if not data:
            return None
        data.setdefault("bookUrl", url)
        data.setdefault("ruleName", f"{self.label} (custom engine)")
        data.setdefault("engine", self.name)
        chapters = data.get("chapters") or []
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
        data.setdefault("chapterName", chapter.get("fullName") or chapter.get("name", ""))
        if not data.get("contentHtml") and data.get("contentText"):
            from html import escape

            data["contentHtml"] = "".join(f"<p>{escape(line)}</p>" for line in data["contentText"].splitlines() if line.strip())
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
    try:
        engine = cls(fetch=fetch, log=log, render=render)
    except TypeError:
        engine = cls(fetch=fetch, log=log)
    return _Runner(name, engine, (info(name) or {}).get("sites", []))
