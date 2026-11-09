import gzip
import json

import pytest
import requests

cv2 = pytest.importorskip('cv2')
np = pytest.importorskip('numpy')

from scripts import discover_sfc_covers as covers


def artwork(seed):
    random = np.random.default_rng(seed)
    image = np.full((1500, 1200), 220, dtype=np.uint8)
    for index in range(240):
        point = tuple(int(value) for value in random.integers([50, 50], [1150, 1450]))
        cv2.circle(image, point, int(random.integers(8, 35)), int(random.integers(0, 200)), 3)
        cv2.putText(image, str(index), point, cv2.FONT_HERSHEY_SIMPLEX, .6, 30, 1)
    return image


def image_record(image):
    return image, image.shape[1], image.shape[0], 'test-hash'


def test_larger_matching_artwork_accepts_a_crop_and_rejects_unrelated_or_smaller_art():
    original = artwork(1)
    standard = cv2.resize(original[150:1300, 100:1020], (368, 460))
    assert covers.larger_match(image_record(standard), image_record(original)) > 2
    assert covers.larger_match(image_record(standard), image_record(artwork(2))) == 0
    assert covers.larger_match(image_record(standard), image_record(standard)) == 0
    collage = np.full((1500, 1200), 220, dtype=np.uint8)
    collage[500:730, 500:684] = cv2.resize(standard, (184, 230))
    assert covers.larger_match(image_record(standard), image_record(collage)) == 0


def test_chapter_discovery_prioritizes_cover_posts_and_skips_paid_chapters():
    volumes = [{'volumeId': 10, 'title': '人设', 'chapterList': [
        {'chapId': 3, 'title': '人物图', 'isVip': False},
        {'chapId': 4, 'title': '封面原图', 'isVip': True},
        {'chapId': 5, 'title': '封面', 'needFireMoney': 10},
        {'chapId': 6, 'title': '封面原图', 'isVip': False},
    ]}, {'volumeId': 20, 'title': '正文', 'chapterList': [{'chapId': 7, 'title': 'First chapter'}]}]
    assert covers.chapter_candidates(volumes) == [(10, 6), (10, 3)]


@pytest.mark.parametrize('value', [
    'https://evil.test/web/novel/images/UploadPic/id.jpg',
    f'https://user:password@rs.{covers.HOST}/web/novel/images/UploadPic/id.jpg',
    f'https://rs.{covers.HOST}/web/account/avatar.jpg',
])
def test_artwork_urls_must_be_public_source_uploads(value):
    assert covers.image_url(value, 'UploadPic') == ''


@pytest.mark.parametrize('broken_image', [False, True])
def test_discover_matches_the_right_image_in_a_multi_image_post(broken_image):
    original = artwork(1)
    standard = cv2.resize(original[150:1300, 100:1020], (368, 460))
    native = covers.COVER_PREFIX + 'id.jpg'
    first = f'https://rs.{covers.HOST}/web/novel/images/UploadPic/wrong.jpg'
    second = f'https://rs.{covers.HOST}/web/novel/images/UploadPic/right.jpg'
    chapter = f'https://book.{covers.HOST}/Novel/123/10/6/'
    payload = {'status': {'httpCode': 200}, 'data': {'volumeList': [
        {'volumeId': 10, 'title': 'Art', 'chapterList': [{'chapId': 6, 'title': '封面', 'isVip': False}]}]}}
    responses = {f'https://api.{covers.HOST}/novels/123/dirs': json.dumps(payload).encode(),
                 native: cv2.imencode('.png', standard)[1].tobytes(),
                 first: b'not an image' if broken_image else cv2.imencode('.png', artwork(2))[1].tobytes(),
                 second: cv2.imencode('.png', original)[1].tobytes(),
                 chapter: f'<div id="ChapterBody"><img src="{first}"><img src="{second}"></div>'.encode()}
    class Session:
        def get(self, url, **kwargs):
            response = requests.Response()
            response.status_code = 200
            response._content = responses[url]
            response._content_consumed = True
            return response
    match = covers.discover('123', native, Session())
    assert match['url'] == second
    assert (match['width'], match['height']) == (1200, 1500)
    assert match['chapter'] == chapter


def test_published_matches_expire_when_the_standard_cover_changes(tmp_path):
    old = covers.COVER_PREFIX + 'old.jpg'
    new = covers.COVER_PREFIX + 'new.jpg'
    match = {'url': f'https://rs.{covers.HOST}/web/novel/images/UploadPic/right.jpg'}
    state = {'123': {'cover': old, 'match': match}, '456': {'cover': new, 'match': match}}
    output = tmp_path / 'index.json.gz'
    assert covers.save(state, {'123': new, '456': new}, tmp_path / 'state.json.gz', output) == 1
    assert json.loads(gzip.decompress(output.read_bytes()))['covers'] == {
        '/web/novel/images/NovelCover/Big/new.jpg': match}


def test_transient_discovery_failure_keeps_a_match_for_the_same_standard_cover(monkeypatch):
    def fail(*args):
        raise requests.Timeout('temporary source failure')
    monkeypatch.setattr(covers, 'discover', fail)
    native = covers.COVER_PREFIX + 'id.jpg'
    previous = {'cover': native, 'match': {'url': 'existing match'}}
    result = covers.check_book('123', native, previous)
    assert result['match'] == previous['match']
    assert result['retry_at'] > result['checked']
    assert 'match' not in covers.check_book('123', covers.COVER_PREFIX + 'new.jpg', previous)
