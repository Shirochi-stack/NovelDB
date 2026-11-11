"""Read immutable local catalogue pieces without network requests."""
import gzip
import json
import re
from functools import lru_cache
from zipfile import ZipFile

from .local_data import folder


@lru_cache(maxsize=4)
def archive(directory, part):
    return ZipFile(directory / f"instant-catalog-{part}.zip")


def read_piece(key, token=None, prefix=False, rows=None, version=None, directory=None):
    if not re.fullmatch(r"manifest|top|[bc]/[0-9]{1,6}", key):
        raise ValueError("Invalid local catalogue key")
    part = int(key.split("/")[1]) % 2 if "/" in key else 0
    data = json.loads(gzip.decompress(archive(directory or folder(version), part).read(key + ".json.gz")))
    if key.startswith("b/") and token:
        return {word: numbers for word, numbers in data.items() if word == token or (prefix and word.startswith(token))}
    if key.startswith("c/") and rows is not None:
        return {str(number): data[number] for number in rows if 0 <= number < len(data)}
    return data
