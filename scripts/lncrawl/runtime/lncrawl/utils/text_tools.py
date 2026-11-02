"""The text helpers lightnovel-crawler's crawler core imports."""

import hashlib
import html
import re
import unicodedata
from urllib.parse import quote, unquote

_RE_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).casefold()


def format_title(text: str) -> str:
    name = html.unescape(str(text or ""))
    name = unicodedata.normalize("NFKC", name)
    return _RE_SPACES.sub(" ", name).strip().title()


def generate_md5(*texts) -> str:
    md5 = hashlib.md5()
    for text in texts:
        md5.update(str(text or "").encode())
    return md5.hexdigest()


class HttpUrl:
    """pydantic's HttpUrl, as far as the crawler core uses it: a percent-encoded address."""

    def __init__(self, url: str) -> None:
        self._url = quote(unquote(str(url)), safe=":/?#[]@!$&'()*+,;=%~")

    def encoded_string(self) -> str:
        return self._url

    def __str__(self) -> str:
        return self._url
