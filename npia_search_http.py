"""Shared npia search response diagnostics and partial-result failures."""

import re
import time

from npia_search_terms import RETRYABLE_STATUS_CODES


class PartialSearchError(RuntimeError):
    def __init__(self, items, page, error):
        self.items = items
        super().__init__(f"page {page}: {error}")


def response_details(response):
    """Log bounded body text without request URLs, cookies, or auth headers."""
    body = response.text
    body = re.sub(
        r'''(?ix)(["']?(?:loginkey|access_token|refresh_token|api_key|authorization|password|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s<>&,;]+)''',
        r'\1[redacted]', body,
    )
    preview = " ".join(body.split())[:240]
    content_type = response.headers.get("Content-Type", "unknown")
    return (f"HTTP {response.status_code}; content-type={content_type!r}; "
            f"body-chars={len(response.text)}; preview={preview!r}")


def get_json_with_retry(session, params, headers, retries, timeout=120):
    last_error = None
    for attempt in range(1, max(1, retries) + 1):
        response = None
        try:
            response = session.get(
                "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/proc/novel",
                params=params, headers=headers, timeout=timeout,
            )
            if response.status_code in RETRYABLE_STATUS_CODES or response.status_code >= 400:
                raise ValueError(f"HTTP {response.status_code}")
            if not response.text.strip():
                raise ValueError("empty response body")
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise ValueError("expected a JSON object with a list field")
            return data
        except Exception as exc:
            # Transport exception messages can contain credential-bearing URLs.
            reason = str(exc) if response is not None else type(exc).__name__
            details = response_details(response) if response is not None else "no response received"
            last_error = RuntimeError(f"{reason}; {details}")
            print(f"Search {params.get('search_val')!r} page {params.get('page')} "
                  f"attempt {attempt}/{max(1, retries)} failed: {last_error}", flush=True)
            if attempt < max(1, retries):
                time.sleep(min(2 ** (attempt - 1), 8))
    raise last_error
