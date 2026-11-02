"""The scraper's exception names the sources and lightnovel-crawler's core catch."""


class ScraperError(Exception):
    pass


class Aborted(ScraperError):
    """The work was stopped."""


class Blocked(ScraperError):
    """The site refused the request."""


class Poisoned(ScraperError):
    """The site answered with something that is not the page."""
