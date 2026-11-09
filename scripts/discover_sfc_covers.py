"""Find larger author-posted artwork that matches the current SFC cover."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import cv2
import numpy as np
import requests
from bs4 import BeautifulSoup

try:
    from . import source_names
    from .scrape_sfc import HEADERS
except ImportError:
    import source_names
    from scrape_sfc import HEADERS

HOST = '\u0073\u0066\u0061\u0063\u0067.com'
COVER_PREFIX = f'https://rss.{HOST}/web/novel/images/NovelCover/Big/'
ART_TITLES = re.compile(r'封面|封皮|人[设設]|[设設]定|插[图圖画畫]|[画畫]廊|立[绘繪]|彩插|[图圖]集|cover', re.I)
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'


def image_url(value, kind):
    value = str(value)
    url = urlsplit(('https:' + value if value.startswith('//') else value).replace('http:', 'https:', 1))
    if (url.scheme != 'https' or url.hostname not in (f'rs.{HOST}', f'rss.{HOST}')
            or url.username or url.password or url.port not in (None, 443)):
        return ''
    prefix = '/web/novel/images/' + kind + '/'
    return url.geturl() if url.path.startswith(prefix) else ''


def standard_cover(value):
    value = str(value or '')
    if value.startswith('//'):
        value = 'https:' + value
    if value and not value.startswith(('https:', 'http:')):
        value = COVER_PREFIX + value
    value = image_url(value, 'NovelCover').replace('/NovelCover/Small/', '/NovelCover/Big/')
    return urlsplit(value)._replace(netloc=f'rss.{HOST}').geturl() if value else ''


def chapter_candidates(volumes):
    candidates = []
    for volume in volumes:
        related = bool(ART_TITLES.search(volume.get('title') or ''))
        for chapter in volume.get('chapterList') or []:
            title = chapter.get('title') or chapter.get('ntitle') or ''
            if chapter.get('isVip') or (chapter.get('needFireMoney') or 0) > 0:
                continue
            if not (related or ART_TITLES.search(title)):
                continue
            if not str(chapter.get('chapId', '')).isdigit() or not str(volume.get('volumeId', '')).isdigit():
                continue
            priority = 0 if re.search('封面|封皮|cover', title, re.I) else 1
            candidates.append((priority, -int(chapter['chapId']), volume['volumeId'], chapter['chapId']))
    return [(volume, chapter) for _, _, volume, chapter in sorted(candidates)[:16]]


def load_image(session, url):
    # Inspect public image responses without sending the app API authorization.
    with session.get(url, timeout=(10, 20), stream=True) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > 16 * 1024 * 1024:
                raise ValueError('Cover image exceeds 16 MB')
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Cover response is not an image')
    height, width = image.shape
    scale = min(1, 1600 / max(width, height))
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return image, width, height, hashlib.sha256(content).hexdigest()


def larger_match(standard, candidate):
    """Require matching features spread across the cover and a larger source crop."""
    image, width, height, _ = standard
    other, other_width, other_height, _ = candidate
    if other_width * other_height <= width * height:
        return 0
    sift = cv2.SIFT_create(nfeatures=1600)
    points, descriptors = sift.detectAndCompute(image, None)
    other_points, other_descriptors = sift.detectAndCompute(other, None)
    if descriptors is None or other_descriptors is None or len(other_descriptors) < 2:
        return 0
    pairs = cv2.BFMatcher().knnMatch(descriptors, other_descriptors, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .7 * pair[1].distance]
    if len(good) < 12:
        return 0
    source = np.float32([points[match.queryIdx].pt for match in good])
    target = np.float32([other_points[match.trainIdx].pt for match in good])
    transform, mask = cv2.findHomography(source, target, cv2.RANSAC, 4)
    if transform is None or mask.sum() < max(12, .6 * len(good)):
        return 0
    matched = source[mask.ravel().astype(bool)]
    spread = np.ptp(matched, axis=0)
    h, w = image.shape
    if spread.prod() < .2 * w * h:
        return 0
    corners = np.float32([[[0, 0], [w, 0], [w, h], [0, h]]])
    projected = cv2.perspectiveTransform(corners, transform)[0]
    projected *= np.array([other_width / other.shape[1], other_height / other.shape[0]])
    if not np.isfinite(projected).all() or not cv2.isContourConvex(projected):
        return 0
    if ((projected < [-.03 * other_width, -.03 * other_height]).any()
            or (projected > [1.03 * other_width, 1.03 * other_height]).any()):
        return 0
    horizontal = min(np.linalg.norm(projected[1] - projected[0]), np.linalg.norm(projected[2] - projected[3])) / width
    vertical = min(np.linalg.norm(projected[3] - projected[0]), np.linalg.norm(projected[2] - projected[1])) / height
    return float(min(horizontal, vertical)) if min(horizontal, vertical) >= 1.05 else 0


def discover(ident, cover, session):
    response = session.get(f'https://api.{HOST}/novels/{ident}/dirs', headers=HEADERS, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if payload.get('status', {}).get('httpCode') != 200:
        raise ValueError('SFC chapter catalog unavailable')
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('volumeList'), list):
        raise ValueError('SFC chapter catalog is incomplete')
    chapters = chapter_candidates(data['volumeList'])
    if not chapters:
        return None
    standard = load_image(session, cover)
    best, best_score, seen, image_error = None, 0, set(), None
    for volume, chapter in chapters:
        page = f'https://book.{HOST}/Novel/{ident}/{volume}/{chapter}/'
        response = session.get(page, timeout=25)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        body = BeautifulSoup(response.content, 'html.parser').select_one('#ChapterBody, .yuedu.Content_Frame')
        if body is None:
            raise ValueError('SFC public chapter body unavailable')
        for element in body.select('img[src], img[data-original]'):
            if len(seen) >= 32:
                break
            url = image_url(element.get('data-original') or element.get('src'), 'UploadPic')
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                candidate = load_image(session, url)
            except requests.HTTPError as exc:
                if exc.response.status_code in (404, 410):
                    continue
                raise
            except (ValueError, cv2.error) as exc:
                image_error = exc
                continue
            score = larger_match(standard, candidate)
            if score > best_score:
                best_score = score
                best = {'url': url, 'width': candidate[1], 'height': candidate[2],
                        'chapter': page, 'cover_sha256': standard[3], 'scale': round(score, 3)}
        if len(seen) >= 32:
            break
        time.sleep(.1)
    if best is None and image_error is not None:
        raise ValueError('Author image could not be inspected') from image_error
    return best


def read_state(path):
    if not path.exists():
        return {}
    return json.loads(gzip.decompress(path.read_bytes()))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = source_names.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_bytes(gzip.compress(raw, mtime=0))
    os.replace(temporary, path)


def save(state, covers, state_path, output):
    write_json(state_path, state)
    published = {}
    for ident, record in state.items():
        if record.get('cover') == covers.get(ident) and record.get('match'):
            published[urlsplit(record['cover']).path] = record['match']
    write_json(output, {'schema': 1, 'covers': published})
    return len(published)


def check_book(ident, cover, previous):
    record = {'cover': cover, 'checked': time.time()}
    try:
        with requests.Session() as session:
            session.headers['User-Agent'] = USER_AGENT
            record['match'] = discover(ident, cover, session)
    except (requests.RequestException, ValueError, cv2.error) as exc:
        record.update(error=str(exc), retry_at=time.time() + 3600)
        if previous.get('cover') == cover:
            record['match'] = previous.get('match')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('docs/data/sfc_novels.json'))
    parser.add_argument('--state', type=Path, default=Path('metadata/state/sfc_author_covers.json.gz'))
    parser.add_argument('--output', type=Path, default=Path('docs/data/sfc_author_covers.json.gz'))
    parser.add_argument('--max-books', type=int, default=5000)
    parser.add_argument('--max-seconds', type=int, default=1800)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--ids', help='Restrict a diagnostic run to comma-separated book IDs')
    args = parser.parse_args()
    if args.max_books < 1 or args.max_seconds < 1 or args.workers < 1:
        parser.error('Discovery limits must be positive')
    cv2.setNumThreads(1)
    rows = json.loads(args.input.read_text(encoding='utf-8'))
    covers = {str(row[0]): standard_cover(row[3]) for row in rows if len(row) > 3}
    state = read_state(args.state)
    now = time.time()
    selected = set(args.ids.split(',')) if args.ids else None
    due = []
    for row in rows:
        ident = str(row[0])
        previous = state.get(ident, {})
        if not covers.get(ident) or (selected is not None and ident not in selected):
            continue
        stale = previous.get('cover') != covers[ident]
        if not selected and not stale and now < previous.get('retry_at', previous.get('checked', 0) + 7 * 86400):
            continue
        ranked = any(row[index] for index in range(11, min(17, len(row))))
        priority = -1 if previous and stale else previous.get('checked', 0)
        due.append((not ranked, priority, -(row[5] or 0), ident))
    due.sort()
    errors, checked, last_save = 0, 0, 0
    selected_books = [item[3] for item in due[:args.max_books]]
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for offset in range(0, len(selected_books), args.workers):
                if time.time() - now >= args.max_seconds:
                    break
                batch = selected_books[offset:offset + args.workers]
                results = pool.map(check_book, batch, [covers[ident] for ident in batch], [state.get(ident, {}) for ident in batch])
                for ident, record in zip(batch, results):
                    state[ident] = record
                    checked += 1
                    errors = errors + 1 if record.get('error') else 0
                    if record.get('match'):
                        match = record['match']
                        print(f'SFC {ident}: matched larger cover ({match["width"]}x{match["height"]})', flush=True)
                if checked - last_save >= 25:
                    count = save(state, covers, args.state, args.output)
                    last_save = checked
                    print(f'Checked {checked} books; {count} author covers available', flush=True)
                if errors >= 3:
                    print('SFC discovery stopped after consecutive source errors', flush=True)
                    break
                time.sleep(.2)
    finally:
        count = save(state, covers, args.state, args.output)
    print(f'Published lookup contains {count} matching author covers', flush=True)


if __name__ == '__main__':
    main()
