import base64
import hashlib
import io
import os
import sqlite3
import zipfile
from types import SimpleNamespace

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper
from kobo_desktop_proxy import KoboDesktopReader


URL = 'https://books.rakuten.co.jp/rk/212e3238005438f28bbed55c432d9c7b/'
REVISION = '212e3238-0054-38f2-8bbe-d55c432d9c7b'


def test_rakuten_kobo_url_is_exact_official_product():
    assert ExternalScraper.is_kobo(URL)
    assert not ExternalScraper.is_kobo(
        'https://evil-books.rakuten.co.jp/rk/212e3238005438f28bbed55c432d9c7b/'
    )
    assert not ExternalScraper.is_kobo(
        'https://books.rakuten.co.jp/rk/212e3238005438f28bbed55c432d9c7b/other'
    )


def _encrypt(key, value):
    cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return cipher.update(value) + cipher.finalize()


def test_desktop_export_matches_revision_and_preserves_epub(tmp_path,
                                                           monkeypatch):
    desktop = tmp_path / 'Kobo Desktop Edition'
    kepub = desktop / 'kepub'
    kepub.mkdir(parents=True)
    database = sqlite3.connect(desktop / 'Kobo.sqlite')
    database.executescript('''
        CREATE TABLE user (UserID TEXT);
        CREATE TABLE content (
            ContentID TEXT, Title TEXT, Attribution TEXT, ISBN TEXT,
            IsDownloaded TEXT, ContentType INTEGER, CrossRevisionId TEXT
        );
        CREATE TABLE content_keys (
            volumeid TEXT, elementid TEXT, elementkey TEXT
        );
    ''')
    user_id = 'owned-user'
    address = 'AA:BB:CC:DD:EE:FF'
    device = hashlib.sha256(
        ('88b3a2e13' + address).encode('ascii')
    ).hexdigest()
    user_key = hashlib.sha256((device + user_id).encode('ascii')).digest()[16:]
    file_key = b'0123456789abcdef'
    name = 'item/chapter.xhtml'
    chapter = b'<html class="vrtl"><body><p>Owned text</p></body></html>'
    padding = 16 - len(chapter) % 16
    encrypted_chapter = _encrypt(file_key, chapter + bytes([padding]) * padding)
    database.execute('INSERT INTO user VALUES (?)', (user_id,))
    database.execute('INSERT INTO content VALUES (?,?,?,?,?,?,?)',
                     ('decoy', 'Same title', 'Other author', '111',
                      'true', 6, '00000000-0000-0000-0000-000000000000'))
    database.execute('INSERT INTO content VALUES (?,?,?,?,?,?,?)',
                     ('owned', 'Same title', 'Correct author', '222',
                      'true', 6, REVISION))
    database.execute('INSERT INTO content_keys VALUES (?,?,?)',
                     ('owned', name,
                      base64.b64encode(_encrypt(user_key, file_key)).decode()))
    database.commit()
    database.close()
    with zipfile.ZipFile(kepub / 'owned', 'w') as source:
        source.writestr('mimetype', 'application/epub+zip')
        source.writestr('META-INF/container.xml', '<container/>')
        source.writestr(name, encrypted_chapter)
        source.writestr('item/xhtml/pictures.xhtml',
                        b'<html><head></head><body><div class="main">'
                        b'<p><img src="../image/a.jpg" class="fit"/></p>'
                        b'<p><img src="../image/b.jpg" class="fit"/></p>'
                        b'</div></body></html>')
        source.writestr('item/content.opf',
                        b'<package xmlns="http://www.idpf.org/2007/opf">'
                        b'<manifest><item id="pictures" '
                        b'href="xhtml/pictures.xhtml" media-type="application/'
                        b'xhtml+xml"/><item id="chapter" '
                        b'href="chapter.xhtml" media-type="application/'
                        b'xhtml+xml"/></manifest><spine '
                        b'page-progression-direction="rtl"><itemref '
                        b'idref="pictures"/><itemref idref="chapter"/>'
                        b'</spine></package>')
    monkeypatch.setattr(KoboDesktopReader, '_mac_addresses',
                        staticmethod(lambda: [address]))

    reader = KoboDesktopReader(directory=str(desktop))
    book, payload = reader.export(URL)
    assert book['volume_id'] == 'owned'
    assert book['isbn'] == '222'
    with zipfile.ZipFile(io.BytesIO(payload)) as epub:
        assert epub.namelist()[0] == 'mimetype'
        assert epub.getinfo('mimetype').compress_type == zipfile.ZIP_STORED
        assert b'<html class="hltr" dir="ltr">' in epub.read(name)
        assert b'<p>Owned text</p>' in epub.read(name)
        assert epub.read('item/xhtml/pictures.xhtml').count(
            b'<img class="npia-page-image"') == 1
        assert epub.read('item/xhtml/pictures-npia-page-2.xhtml').count(
            b'<img class="npia-page-image"') == 1
        opf = epub.read('item/content.opf')
        assert b'href="xhtml/pictures-npia-page-2.xhtml"' in opf
        assert (opf.index(b'idref="pictures"') <
                opf.index(b'idref="pictures-npia-2"') <
                opf.index(b'idref="chapter"'))
        assert epub.testzip() is None
    _, original_layout = reader.export(URL, horizontal_layout=False)
    with zipfile.ZipFile(io.BytesIO(original_layout)) as epub:
        assert epub.read(name) == chapter
    output = tmp_path / 'output.epub'
    ExternalNovelDialog._write_native_epub(str(output), payload)
    assert output.read_bytes() == payload

    messages = []
    dialog = ExternalNovelDialog.__new__(ExternalNovelDialog)
    dialog._book_data = {'bookname': 'Same title', '_kobo_desktop': True}
    dialog._chapter_results = [{'_nativeEpubBytes': payload}]
    dialog._active_output_formats = ('epub',)
    dialog._var_format = SimpleNamespace(set=lambda value: None)
    dialog._var_long_image_layout = SimpleNamespace(get=lambda: False)
    dialog._get_output_dir = lambda: str(tmp_path)
    dialog._log = messages.append
    dialog._generate_output()
    assert (tmp_path / 'Same title.epub').read_bytes() == payload
    assert any('Saved:' in message for message in messages)


def test_desktop_export_rejects_missing_or_wrong_book(tmp_path):
    desktop = tmp_path / 'Kobo Desktop Edition'
    desktop.mkdir()
    database = sqlite3.connect(desktop / 'Kobo.sqlite')
    database.executescript('''
        CREATE TABLE user (UserID TEXT);
        CREATE TABLE content (
            ContentID TEXT, Title TEXT, Attribution TEXT, ISBN TEXT,
            IsDownloaded TEXT, ContentType INTEGER, CrossRevisionId TEXT
        );
    ''')
    database.execute('INSERT INTO user VALUES (?)', ('signed-in',))
    database.execute('INSERT INTO content VALUES (?,?,?,?,?,?,?)',
                     ('other', 'Same title', '', '', 'true', 6,
                      '00000000-0000-0000-0000-000000000000'))
    database.commit()
    database.close()
    assert KoboDesktopReader(directory=str(desktop)).lookup(URL) is None


def test_full_page_illustration_becomes_responsive_without_touching_text():
    page = (b'<html><head><meta name="viewport" content="width=1443, '
            b'height=2048"/></head><body><div class="main">'
            b'<svg width="100%" height="100%" viewBox="0 0 1443 2048">'
            b'<image width="1443" height="2048" '
            b'xlink:href="../image/illustration.jpg"/></svg>'
            b'</div></body></html>')
    changed, is_image = KoboDesktopReader._responsive_image_page(page)
    assert is_image
    assert b'img class="npia-page-image"' in changed
    assert b'src="../image/illustration.jpg"' in changed
    assert b'max-height:95vh' in changed
    assert b'name="viewport"' not in changed

    text_page = page.replace(b'</div>', b'<p>Keep this text</p></div>')
    assert KoboDesktopReader._responsive_image_page(text_page) == (
        text_page, False
    )

    vertical_page = (b'<html class="vrtl"><head></head><body><div class="main">'
                     b'<p><img src="../image/a.jpg" class="fit"/></p>'
                     b'<p><img src="../image/b.jpg" class="fit"/></p>'
                     b'</div></body></html>')
    vertical_updated, is_image = KoboDesktopReader._responsive_image_page(
        vertical_page
    )
    assert is_image
    assert vertical_updated.count(b'class="npia-page-image"') == 2
    assert b'writing-mode:horizontal-tb' in vertical_updated
    assert b'class="fit"' not in vertical_updated

    opf = (b'<package xmlns="http://www.idpf.org/2007/opf">'
           b'<manifest><item id="picture" href="xhtml/picture.xhtml"/>'
           b'<item id="chapter" href="xhtml/chapter.xhtml"/></manifest>'
           b'<spine><itemref idref="picture" properties="rendition:'
           b'layout-pre-paginated rendition:spread-none"/>'
           b'<itemref idref="chapter" properties="page-spread-right"/>'
           b'</spine></package>')
    updated = KoboDesktopReader._reflow_image_spine(
        opf, {'item/xhtml/picture.xhtml'}, 'item/content.opf'
    )
    assert b'<itemref idref="picture"/>' in updated
    assert b'<itemref idref="chapter" properties="page-spread-right"/>' in updated

    horizontal = KoboDesktopReader._horizontal_spine(updated)
    assert b'page-progression-direction="ltr"' in horizontal
    assert b'<itemref idref="chapter"/>' in horizontal
