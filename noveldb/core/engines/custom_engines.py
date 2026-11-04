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
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

PREFIX = "custom:"
_loaded: Dict[str, tuple] = {}  # name -> (file's mtime, module)


def folder() -> Path:
    from noveldb.core import paths

    return paths.data_dir() / "custom_engines"


def is_custom(name: str) -> bool:
    return str(name or "").startswith(PREFIX)


def _path(name: str) -> Path:
    return folder() / f"{str(name)[len(PREFIX):]}.py"


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
    found = []
    try:
        files = sorted(folder().glob("*.py"))
    except OSError:
        return []
    for path in files:
        try:
            info = describe(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        name = PREFIX + path.stem
        found.append({"name": name, "label": info["label"] or path.stem, "sites": info["sites"], "path": str(path)})
    return found


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
    path = Path(source_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"could not read it ({e})") from None
    describe(text)
    ident = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_") or "engine"
    target = folder() / f"{ident}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
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


def load(name: str, fetch: Callable, log: Callable[[str], None], render: Optional[Callable] = None) -> _Runner:
    """The engine `name`, run from its file (loaded again when the file changes)."""
    path = _path(name)
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
