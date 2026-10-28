#!/usr/bin/env python3
"""A pretend NovelDB release site for the iOS app-update check in CI.

  update_fixture.py write <root> <scenario> <app_build>   write the files of one scenario
  update_fixture.py serve <root> <port>                   serve them on 127.0.0.1

The server answers like GitHub does: `releases/latest/download/<file>` and
`releases/download/<tag>/<file>` redirect (302) to the stored file, and a file that does not exist
is a 404. `write` prints "<sha256> <size>" of the IPA it made, so the workflow can compare it with
what the app reports.

Scenarios (the app under test has build <app_build>):
  good      a newer IPA whose size and SHA-256 match its manifest
  badsum    a newer IPA whose SHA-256 differs from the manifest's
  badsize   a newer IPA whose size differs from the manifest's
  current   the manifest's build equals the app's
  schema2   a manifest with another schema
  missing   no manifest at all (a 404)
"""
import hashlib
import http.server
import json
import os
import shutil
import socketserver
import sys

TAG = "v2"
NAME = "NovelDB-unsigned.ipa"
SCENARIOS = ("good", "badsum", "badsize", "current", "schema2", "missing")


def write(root, scenario, app_build):
    if scenario not in SCENARIOS:
        raise SystemExit("unknown scenario: " + scenario)
    files = os.path.join(root, "files")
    shutil.rmtree(files, ignore_errors=True)
    os.makedirs(files)

    # A few megabytes, so the download takes more than one chunk.
    payload = os.urandom(3 * 1024 * 1024 + 123)
    with open(os.path.join(files, NAME), "wb") as handle:
        handle.write(payload)
    sha = hashlib.sha256(payload).hexdigest()
    size = len(payload)

    build = int(app_build) if scenario == "current" else int(app_build) + 1000
    entry = {
        "name": NAME, "variant": "universal", "arch": "universal", "kind": "ipa",
        "build": build, "label": "hotfix3", "commit": "0" * 40,
        "size": size + 1 if scenario == "badsize" else size,
        "sha256": "0" * 64 if scenario == "badsum" else sha,
        "field_from_the_future": {"kept": False},
    }
    manifest = {
        "schema": 2 if scenario == "schema2" else 1, "platform": "ios", "tag": TAG,
        "release_url": "https://github.com/Shirochi-stack/NovelDB/releases/tag/" + TAG,
        "assets": [entry],
    }
    if scenario != "missing":
        with open(os.path.join(files, "update-ios.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
    print(sha, size)


class Handler(http.server.BaseHTTPRequestHandler):
    root = "."

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def reply(self, status, headers=None, body=b""):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def stored(self, name):
        path = os.path.join(self.root, "files", os.path.basename(name))
        return path if os.path.isfile(path) else None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/ping":
            return self.reply(200, body=b"ok")
        for prefix in ("/releases/latest/download/", "/releases/download/%s/" % TAG):
            if path.startswith(prefix):
                name = path[len(prefix):]
                if self.stored(name):
                    return self.reply(302, {"Location": "/files/" + name})
                return self.reply(404, body=b"not found")
        if path.startswith("/files/"):
            full = self.stored(path[len("/files/"):])
            if full:
                with open(full, "rb") as handle:
                    return self.reply(200, {"Content-Type": "application/octet-stream"}, handle.read())
        return self.reply(404, body=b"not found")


def serve(root, port):
    Handler.root = root
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", int(port)), Handler) as server:
        server.daemon_threads = True
        server.serve_forever()


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "write":
        write(args[1], args[2], args[3])
    elif len(args) == 3 and args[0] == "serve":
        serve(args[1], args[2])
    else:
        raise SystemExit(__doc__)
