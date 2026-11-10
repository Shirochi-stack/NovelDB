"""Read immutable local catalogue pieces without network requests."""
import gzip
import json
import re
from functools import lru_cache
from zipfile import ZipFile

from noveldb.core import paths


@lru_cache(maxsize=2)
def archive(part):
    return ZipFile(paths.bundle_dir() / f"assets/instant-catalog-{part}.zip")


def read_piece(key, token=None, prefix=False, rows=None):
    if not re.fullmatch(r"manifest|top|starter|[bc]/[0-9]{1,6}", key):
        raise ValueError("Invalid local catalogue key")
    if key == "starter":
        return json.loads(gzip.decompress((paths.bundle_dir() / "assets/instant-starter.bin").read_bytes()))
    part = int(key.split("/")[1]) % 2 if "/" in key else 0
    data = json.loads(gzip.decompress(archive(part).read(key + ".json.gz")))
    if key.startswith("b/") and token:
        return {word: numbers for word, numbers in data.items() if word == token or (prefix and word.startswith(token))}
    if key.startswith("c/") and rows is not None:
        return {str(number): data[number] for number in rows if 0 <= number < len(data)}
    return data
