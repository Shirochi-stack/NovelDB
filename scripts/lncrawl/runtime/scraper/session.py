"""`Scraper`: the request methods lightnovel-crawler's sources call, over NovelDB's transport."""

from __future__ import annotations

import json as json_module
import threading
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from requests.cookies import RequestsCookieJar
from requests.structures import CaseInsensitiveDict

from .browser import RenderError
from .exceptions import Aborted
from .soup import PageSoup


def extract_base(url: str) -> str:
    """The scheme and host of `url`, with a trailing slash."""
    text = str(url or "")
    if "//" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    return f"{parts.scheme or 'http'}://{parts.netloc}/"


class Diagnosis:  # only named in type hints
    pass


def _response(result: dict, url: str) -> requests.Response:
    response = requests.Response()
    response.status_code = int(result.get("status") or 0)
    content = result.get("content")
    response._content = content if content is not None else (result.get("text") or "").encode("utf-8")
    headers = CaseInsensitiveDict()
    for line in str(result.get("headers") or "").splitlines():
        name, _, value = line.partition(":")
        if name.strip():
            headers[name.strip()] = value.strip()
    response.headers = headers
    response.url = result.get("url") or url
    response.encoding = requests.utils.get_encoding_from_headers(headers)
    response.reason = ""
    return response


class Scraper:
    # Set by the app: fetcher(url, method=, data=, headers=, binary=) -> {status, url, content, text,
    # headers} or None; renderer(url) -> the page's HTML from the browser, or None.
    fetcher: Optional[Callable[..., Optional[dict]]] = None
    renderer: Optional[Callable[[str], Optional[str]]] = None

    def __init__(self, origin: str = "", parser: Optional[str] = None, rate_limit: Optional[float] = None,
                 **_kwargs: Any) -> None:
        self.origin = extract_base(origin) if origin else ""
        self.parser = parser or "lxml"
        self.rate_limit = rate_limit
        self.headers: Dict[str, str] = {}
        self.cookies = RequestsCookieJar()
        self.last_url = self.origin
        self.signal = threading.Event()
        self.check_response = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self) -> None:
        self.signal.set()

    def fetch(self, method: str, url: str, *, headers: Optional[Mapping[str, str]] = None, data: Any = None,
              json: Any = None, params: Any = None, signal: Any = None, **_kwargs: Any) -> requests.Response:
        if (signal or self.signal).is_set():
            raise Aborted("aborted by signal")
        fetcher = self.__dict__.get("fetcher") or Scraper.fetcher  # a crawler's own, else the app's
        if fetcher is None:
            raise RuntimeError("no transport for lightnovel-crawler")
        url = urljoin(self.last_url or self.origin, str(url))
        if params:
            query = params if isinstance(params, str) else urlencode(params, doseq=True)
            url += ("&" if "?" in url else "?") + query
        sent = {**self.headers, **{str(k): str(v) for k, v in (headers or {}).items()}}
        sent = {k: v for k, v in sent.items() if k.lower() not in ("user-agent", "accept-encoding", "host")}
        body = None
        if json is not None:
            body = json_module.dumps(json)
            sent.setdefault("Content-Type", "application/json")
        elif isinstance(data, (dict, list, tuple)):
            body = urlencode(data, doseq=True)
        elif data is not None:
            body = data if isinstance(data, (str, bytes)) else str(data)
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        accept = next((v for k, v in sent.items() if k.lower() == "accept"), "")
        result = fetcher(url, method=method.upper(), data=body, headers=sent, binary=accept.startswith("image/"))
        if result is None:
            raise requests.ConnectionError(f"Could not fetch {url}")
        response = _response(result, url)
        self.last_url = response.url
        if response.status_code >= 400:
            raise requests.HTTPError(f"{response.status_code} for {url}", response=response)
        return response

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.fetch("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.fetch("POST", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        return self.fetch("GET", url, **kwargs)

    def submit_form(self, url: str, data: Any = None, *, json: Any = None, multipart: bool = False,
                    headers: Optional[Mapping[str, str]] = None, **kwargs: Any) -> requests.Response:
        merged = {k.lower(): v for k, v in (headers or {}).items()}
        if json is None:
            merged.setdefault("content-type", "application/x-www-form-urlencoded; charset=UTF-8")
        return self.post(url, data=data, json=json, headers=merged, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        headers = {"accept": "application/json, text/plain, */*"}
        headers.update({k.lower(): v for k, v in (kwargs.pop("headers", None) or {}).items()})
        return self.fetch("GET", url, headers=headers, **kwargs).json()

    def post_json(self, url: str, data: Any = None, **kwargs: Any) -> Any:
        headers = {"content-type": "application/json", "accept": "application/json, text/plain, */*"}
        headers.update({k.lower(): v for k, v in (kwargs.pop("headers", None) or {}).items()})
        return self.post(url, data=data, headers=headers, **kwargs).json()

    def make_soup(self, data: Any, encoding: Optional[str] = None) -> PageSoup:
        return PageSoup.create(data, encoding, self.parser)

    def get_soup(self, url: str, encoding: Optional[str] = None, **kwargs: Any) -> PageSoup:
        headers = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        headers.update({k.lower(): v for k, v in (kwargs.pop("headers", None) or {}).items()})
        return self.make_soup(self.fetch("GET", url, headers=headers, **kwargs), encoding)

    def post_soup(self, url: str, data: Any = None, encoding: Optional[str] = None, **kwargs: Any) -> PageSoup:
        return self.make_soup(self.post(url, data=data, **kwargs), encoding)

    def render(self, url: str, **_kwargs: Any) -> str:
        renderer = self.__dict__.get("renderer") or Scraper.renderer
        html = renderer(url) if renderer is not None else None
        if not html:
            raise RenderError(f"Could not render {url}")
        self.last_url = url
        return html

    def render_soup(self, url: str, **kwargs: Any) -> PageSoup:
        return self.make_soup(self.render(url, **kwargs))

    def get_file(self, url: str, output_file: Any, **kwargs: Any):
        from pathlib import Path

        target = Path(output_file)
        target.write_bytes(self.fetch("GET", url, **kwargs).content)
        return target

    def get_image(self, url: str, **kwargs: Any) -> Any:
        import base64
        from io import BytesIO

        from PIL import Image

        if url.startswith("data:"):
            return Image.open(BytesIO(base64.b64decode(url.split("base64,")[-1])))
        headers = {"accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
        return Image.open(BytesIO(self.fetch("GET", url, headers=headers, **kwargs).content))
