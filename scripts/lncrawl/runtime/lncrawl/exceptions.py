"""lightnovel-crawler's exception names (without its server's)."""

from requests.exceptions import RequestException
from scraper.browser import RenderError, SolveError
from scraper.exceptions import Aborted as AbortedException, Blocked, Poisoned

try:
    from PIL import UnidentifiedImageError
except ImportError:  # pragma: no cover
    UnidentifiedImageError = OSError


class LNException(Exception):
    pass


ScraperErrorGroup = (Blocked, Poisoned, RenderError, SolveError, RequestException, UnidentifiedImageError)
