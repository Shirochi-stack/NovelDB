"""Small same-server GitLab API client; credentials never enter argv or logs."""
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ApiError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ApiError(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ApiError("GitLab API redirected; credentials were not forwarded.")


class GitLab:
    def __init__(self, env=None, opener=None):
        self.env = dict(os.environ if env is None else env)
        self.base = self.env.get("CI_API_V4_URL", "").rstrip("/")
        api, server = urlparse(self.base), urlparse(self.env.get("CI_SERVER_URL", ""))
        require(api.scheme == server.scheme == "https" and api.netloc == server.netloc and api.netloc and not api.username and not api.password, "Use this GitLab server's HTTPS API.")
        require(re.fullmatch(r"[0-9]+", self.env.get("CI_PROJECT_ID", "")), "CI_PROJECT_ID is missing or invalid.")
        require(self.env.get("GITLAB_AUTOMATION_TOKEN"), "Owner-provisioned protected/masked GITLAB_AUTOMATION_TOKEN is required.")
        self.project = "/projects/" + self.env["CI_PROJECT_ID"]
        self.opener = opener or build_opener(NoRedirect())

    def request(self, method, path, data=None):
        require(path.startswith("/") and not path.startswith("//") and "://" not in path, "Invalid relative API path.")
        if method != "GET":
            require(self.env.get("CI_COMMIT_REF_PROTECTED") == "true" and self.env.get("AUTOMATION_WRITE_APPROVED") == "true", "Writes require a protected ref and AUTOMATION_WRITE_APPROVED=true.")
        body = json.dumps(data).encode() if data is not None else None
        request = Request(self.base + path, data=body, method=method, headers={"PRIVATE-TOKEN": self.env["GITLAB_AUTOMATION_TOKEN"], "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            raise ApiError(f"GitLab {method} failed (HTTP {error.code}); response body withheld.") from None

    def pages(self, path, limit=1000):
        items = []
        for page in range(1, 1 + (limit + 99) // 100):
            batch = self.request("GET", path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            require(isinstance(batch, list), "Expected a paginated API list.")
            items.extend(batch)
            if len(batch) < 100:
                return items[:limit]
        raise ApiError("Pagination bound reached; narrow the scope instead of silently dropping items.")
