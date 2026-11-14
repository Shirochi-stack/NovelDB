"""Userscript engines: a .user.js added as a custom engine reads a link in a hidden page (local pages only)."""

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from noveldb.core.engines import custom_engines, userscript_engine

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "noveldb" / "core" / "engines" / "userscript-host.js"

# A capturing script as they are written: it watches the page's canvases, lists them in a panel of its own (a
# closed shadow root) and saves what is ticked, as one ZIP, when its "download" is pressed.
SCRIPT = r"""// ==UserScript==
// @name        Test capture
// @match       *://reader.test/*
// @match       https://*.other.example/read/*
// @version     1.0
// @grant       GM_getValue
// @grant       GM_setValue
// ==/UserScript==
(function () {
    GM_setValue('ran', (GM_getValue('ran', 0) || 0) + 1);
    const host = document.createElement('div');
    const panel = host.attachShadow({mode: 'closed'});
    panel.innerHTML = '<button>download</button><button>All</button><table></table>';
    const [download, all] = panel.querySelectorAll('button');
    const table = panel.querySelector('table');
    const ticked = () => Array.from(table.querySelectorAll('input')).filter(box => box.checked);
    all.addEventListener('click', () => {
        table.innerHTML = '';
        document.querySelectorAll('canvas').forEach((canvas, index) => {
            const row = table.insertRow();
            row.innerHTML = '<td><input type="checkbox" checked></td>';
            row.firstChild.firstChild.canvas = canvas;
            row.firstChild.firstChild.page = index + 1;
        });
    });
    download.addEventListener('click', async () => {
        const files = [];
        for (const box of ticked()) {
            const blob = await new Promise(done => box.canvas.toBlob(done, 'image/png'));
            files.push({name: 'c' + box.page + '.png', data: new Uint8Array(await blob.arrayBuffer())});
        }
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([window.__testZip(files)]));
        link.download = 'pages.zip';
        link.click();
    });
    document.documentElement.appendChild(host);
})();
"""
# A stored ZIP, written the way a page script writes one.
ZIP_WRITER = r"""
window.__testZip = function (files) {
    const crcTable = Array.from({length: 256}, (_, n) => { for (let k = 0; k < 8; k++) n = n & 1 ? 0xEDB88320 ^ (n >>> 1) : n >>> 1; return n >>> 0; });
    const crc = bytes => { let c = ~0; for (const b of bytes) c = crcTable[(c ^ b) & 255] ^ (c >>> 8); return ~c >>> 0; };
    const parts = [], directory = []; let offset = 0;
    for (const file of files) {
        const name = new TextEncoder().encode(file.name), sum = crc(file.data);
        const local = new DataView(new ArrayBuffer(30));
        local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint32(14, sum, true);
        local.setUint32(18, file.data.length, true); local.setUint32(22, file.data.length, true); local.setUint16(26, name.length, true);
        const entry = new DataView(new ArrayBuffer(46));
        entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true); entry.setUint32(16, sum, true);
        entry.setUint32(20, file.data.length, true); entry.setUint32(24, file.data.length, true); entry.setUint16(28, name.length, true);
        entry.setUint32(42, offset, true);
        parts.push(new Uint8Array(local.buffer), name, file.data);
        directory.push(new Uint8Array(entry.buffer), name);
        offset += 30 + name.length + file.data.length;
    }
    const size = directory.reduce((total, part) => total + part.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
    end.setUint32(12, size, true); end.setUint32(16, offset, true);
    return new Blob(parts.concat(directory, [new Uint8Array(end.buffer)]));
};
"""
COLORS = ["red", "green", "blue", "orange", "purple", "teal", "navy", "gray", "black", "white", "pink", "brown"]


def png(color, size=(120, 160)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def reader_page(count):
    """A page that draws each picture onto a canvas as it is scrolled to: nothing to find without reading it."""
    return ("""<!doctype html><html><head><title>Reader &amp; chapter 3</title></head><body><div id="pages"></div><script>
const list = %s; let at = 0;
function next() { if (at >= list.length) return; const img = new Image(); img.onload = () => {
  const c = document.createElement('canvas'); c.width = img.width; c.height = img.height; c.style.display = 'block'; c.style.marginBottom = '700px';
  c.getContext('2d').drawImage(img, 0, 0); document.getElementById('pages').appendChild(c); }; img.src = list[at++]; }
next(); addEventListener('scroll', () => { if (innerHeight + scrollY > document.body.scrollHeight - 900) next(); });
</script></body></html>""" % json.dumps([f"/p{i}.png" for i in range(count)]))


def test_the_header_gives_the_name_and_the_sites():
    found = userscript_engine.describe(SCRIPT)
    assert found["label"] == "Test capture"
    assert found["sites"] == ["reader.test", "other.example"] and not found["everywhere"]
    everywhere = userscript_engine.describe("// ==UserScript==\n// @name Any\n// @match *://*/*\n// ==/UserScript==\n")
    assert everywhere["everywhere"] and everywhere["sites"] == []
    # No @match at all is every site too, as in a userscript manager.
    assert userscript_engine.describe("// ==UserScript==\n// @name Bare\n// ==/UserScript==\n")["everywhere"]
    for text, why in (("alert(1)", "header"), ("// ==UserScript==\n// @match *://a.test/*\n// ==/UserScript==", "@name")):
        with pytest.raises(ValueError, match=why):
            userscript_engine.describe(text)


def test_a_userscript_is_added_listed_and_removed_like_a_custom_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_engines, "folder", lambda: tmp_path / "engines")
    source = tmp_path / "Test Capture-1.0.user.js"
    source.write_text(SCRIPT, encoding="utf-8")
    name = custom_engines.add(str(source))
    assert name == "custom:test_capture_1_0"
    info = custom_engines.info(name)
    assert info["label"] == "Test capture" and info["userscript"] and not info["everywhere"]
    assert custom_engines.knows_site(name, "https://www.reader.test/book/1")
    assert custom_engines.knows_site(name, "https://jp.other.example/read/2")
    assert not custom_engines.knows_site(name, "https://example.com/")
    assert not custom_engines.reads_everything(name)
    assert custom_engines.stamp(name) is not None

    anywhere = tmp_path / "any.js"
    anywhere.write_text("// ==UserScript==\n// @name Any site\n// @match *://*/*\n// ==/UserScript==\n", encoding="utf-8")
    other = custom_engines.add(str(anywhere))
    assert custom_engines.reads_everything(other) and not custom_engines.knows_site(other, "https://example.com/")
    assert custom_engines.names() == (other, name)

    not_a_script = tmp_path / "plain.js"
    not_a_script.write_text("console.log(1)", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        custom_engines.add(str(not_a_script))

    custom_engines.remove(name)
    assert custom_engines.info(name) is None and custom_engines.names() == (other,)


def test_the_engine_makes_a_one_chapter_book_and_its_images_from_what_the_script_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_engines, "folder", lambda: tmp_path)
    (tmp_path / "capture.user.js").write_text(SCRIPT, encoding="utf-8")
    asked = []

    def browse(url, source, meta):
        asked.append((url, meta["name"]))
        return {"title": "Reader", "pressed": ["All", "download"], "failed": "",
                "files": [{"name": "c1.png", "mime": "image/png", "base64": base64.b64encode(png("red")).decode()},
                          {"name": "c2.png", "mime": "image/jpeg", "base64": base64.b64encode(b"jpeg").decode()}]}

    fetch = lambda url, **_kwargs: {"text": "<html><head><title> Reader &amp;\n chapter 3 </title></head></html>", "status": 200}
    logs = []
    engine = custom_engines.load("custom:capture", fetch=fetch, log=logs.append, browse=browse)
    assert engine.knows("https://reader.test/c/3") and not engine.knows("https://example.com/")
    book = engine.parse_book("https://reader.test/c/3")
    assert book["bookname"] == "Reader & chapter 3" and book["engine"] == "custom:capture"
    assert [c["url"] for c in book["chapters"]] == ["https://reader.test/c/3"]
    chapter = engine.parse_chapter(book["chapters"][0], book)
    assert asked == [("https://reader.test/c/3", "Test capture")]
    assert chapter["comic"] and chapter["imageCount"] == 2
    assert [image["name"] for image in chapter["images"]] == ["0001.png", "0002.jpg"]
    assert base64.b64decode(chapter["images"][0]["data"].split(",", 1)[1]) == png("red")
    assert any("pressed All, download" in line for line in logs)

    empty = custom_engines.load("custom:capture", fetch=fetch, log=logs.append, browse=lambda *_args: {"files": []})
    with pytest.raises(ValueError, match="saved no images"):
        empty.parse_chapter(book["chapters"][0], book)
    with pytest.raises(ValueError, match="browser"):
        custom_engines.load("custom:capture", fetch=fetch, log=logs.append).parse_chapter(book["chapters"][0], book)


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


def run_script(chromium, script, html, pictures, options="{settle: 300, patience: 4000}"):
    page = chromium.new_context(viewport={"width": 800, "height": 600}).new_page()
    files, errors = [], []
    page.expose_function("__ndbUserscriptFile", lambda text: files.append(json.loads(text)))
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        if path in pictures:
            route.fulfill(body=pictures[path], content_type="image/png" if path.endswith(".png") else "application/zip")
        elif path == "/":
            route.fulfill(body=html, content_type="text/html; charset=utf-8")
        else:
            route.fulfill(status=404, body="")

    page.route("http://reader.test/**", serve)
    page.add_init_script(ZIP_WRITER + userscript_engine.page_script(script, userscript_engine.describe(script)["meta"]))
    page.goto("http://reader.test/")
    result = page.evaluate(f"window.__NDB_USERSCRIPT.run({options})")
    values = page.evaluate("window.__NDB_USERSCRIPT.values")
    page.context.close()
    assert not errors, errors
    return result, sorted(files, key=lambda item: item["index"]), values


def colors(files):
    found = []
    for item in files:
        with Image.open(io.BytesIO(base64.b64decode(item["base64"]))) as image:
            found.append(image.convert("RGB").getpixel((3, 3)))
    return found


def test_the_page_is_read_to_its_end_and_the_scripts_own_buttons_are_pressed(chromium):
    pictures = {f"/p{i}.png": png(color) for i, color in enumerate(COLORS)}
    result, files, values = run_script(chromium, SCRIPT, reader_page(len(COLORS)), pictures)
    assert result["pressed"] == ["All", "download"] and result["title"] == "Reader & chapter 3" and not result["failed"]
    assert values == {"ran": 1}
    # Every page the reader drew, once it was scrolled to: in page order (c2 before c10), out of the script's ZIP.
    assert [item["name"] for item in files] == [f"c{i}.png" for i in range(1, len(COLORS) + 1)]
    assert colors(files) == [Image.new("RGB", (1, 1), color).getpixel((0, 0)) for color in COLORS]
    assert {item["mime"] for item in files} == {"image/png"}


def test_a_page_the_script_catches_nothing_on_gives_its_own_pictures(chromium):
    script = "// ==UserScript==\n// @name Idle\n// @match *://*/*\n// ==/UserScript==\n"
    pictures = {f"/p{i}.png": png(color, (240, 320)) for i, color in enumerate(COLORS[:3])}
    pictures["/icon.png"] = png("black", (16, 16))
    html = "<html><head><title>Plain</title></head><body>" + "".join(
        f'<img src="{path}" style="display:block;margin-bottom:500px">' for path in pictures) + "</body></html>"
    result, files, _values = run_script(chromium, script, html, pictures)
    assert result["pressed"] == []
    assert [item["name"] for item in files] == ["page_0001", "page_0002", "page_0003"]  # the icon is left out
    assert colors(files) == [Image.new("RGB", (1, 1), color).getpixel((0, 0)) for color in COLORS[:3]]


def test_a_script_that_saves_by_itself_is_kept_too_compressed_zip_included(chromium):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("10.png", png("blue"))
        bundle.writestr("2.png", png("red"))
        bundle.writestr("notes.txt", "not a picture")
    script = ("// ==UserScript==\n// @name Saver\n// @match *://*/*\n// @grant GM_download\n// ==/UserScript==\n"
              "addEventListener('load', () => GM_download({url: '/bundle.zip', name: 'bundle.zip'}));\n")
    result, files, _values = run_script(chromium, script, "<html><head><title>Auto</title></head><body>hi</body></html>",
                                        {"/bundle.zip": archive.getvalue()})
    assert result["pressed"] == []
    assert [item["name"] for item in files] == ["2.png", "10.png"]
    assert colors(files) == [(255, 0, 0), (0, 0, 255)]
