"""Apply the repository codename policy to release text and asset metadata."""
import json
import os
import urllib.request

try:
    from .source_names import to_codenames
except ImportError:
    from source_names import to_codenames


def sanitize_releases(repository, token):
    base = 'https://api.github.com/repos/' + repository
    headers = {'Authorization': 'Bearer ' + token,
               'Accept': 'application/vnd.github+json', 'User-Agent': 'codename-policy'}

    def request(path, method='GET', body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, headers=headers, data=data, method=method)
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    count = 0
    page = 1
    while True:
        releases = request(f'/releases?per_page=100&page={page}')
        for release in releases:
            patch = {}
            for key in ('name', 'body'):
                old = release.get(key) or ''
                value = to_codenames(old)
                if value != old:
                    patch[key] = value
            if patch:
                request('/releases/' + str(release['id']), 'PATCH', patch)
                count += 1
            for asset in release['assets']:
                patch = {}
                for key in ('name', 'label'):
                    old = asset.get(key) or ''
                    value = to_codenames(old, preserve_case=True)
                    if value != old:
                        patch[key] = value
                if patch:
                    request('/releases/assets/' + str(asset['id']), 'PATCH', patch)
                    count += 1
        if len(releases) < 100:
            break
        page += 1
    print(f'Release policy applied: {count} metadata updates')
    return count


if __name__ == '__main__':
    sanitize_releases(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'])
