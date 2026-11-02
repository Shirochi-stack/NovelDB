"""The browser errors lightnovel-crawler's exceptions module imports."""

from .exceptions import ScraperError


class SolveError(ScraperError):
    pass


class RenderError(ScraperError):
    """The page could not be rendered in the browser."""
