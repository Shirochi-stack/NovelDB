"""Optional per-chapter clocks shared by the desktop scraper engines."""

from functools import wraps
from time import perf_counter


def timed_chapter(function):
    """Measure one worker, preserving a more precise nested engine measurement."""
    @wraps(function)
    def run(self, *args, **kwargs):
        if not getattr(self, "chapter_timing", False):
            return function(self, *args, **kwargs)
        started = perf_counter()
        result = function(self, *args, **kwargs)
        if isinstance(result, dict):
            result.setdefault("_scrape_seconds", perf_counter() - started)
        return result
    return run


def timing_suffix(data):
    seconds = (data or {}).get("_scrape_seconds")
    return f" ({seconds:.2f} s)" if isinstance(seconds, (int, float)) and seconds >= 0 else ""


def browser_chapter_timing(page, function, enabled):
    """Time each exported JS chapter function, including concurrent Promise.all calls.

    Leave batch scheduling and the engine's string/object return contract intact.
    The shared page is switched off again for subsequent jobs with timing disabled.
    """
    if not enabled and getattr(page, "_noveldb_timing_installed", False) is not True:
        return
    page.evaluate(_BROWSER_TIMER, [function, bool(enabled)])
    page._noveldb_timing_installed = True


_BROWSER_TIMER = """([name, enabled]) => {
  window.__noveldbChapterTiming = enabled;
  const original = window[name];
  if (!enabled || !original || original.__noveldbTimed) return;
  const timed = async function (...args) {
    if (!window.__noveldbChapterTiming) return original.apply(this, args);
    const started = performance.now();
    const raw = await original.apply(this, args);
    const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (value && typeof value === 'object')
      value._scrape_seconds = (performance.now() - started) / 1000;
    return typeof raw === 'string' ? JSON.stringify(value) : value;
  };
  timed.__noveldbTimed = true;
  window[name] = timed;
}"""
