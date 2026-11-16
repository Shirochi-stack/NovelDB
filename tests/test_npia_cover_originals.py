"""Each npia work's full-size cover is recorded beside the catalog, which keeps the small card cover."""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import npia_cover_originals as originals  # noqa: E402

CARD = "//images.example.test/imagebox/cover/2ab8c9d2_229071_ori.wimg"
ITEM = {"novel_no": 419368, "cover_url": CARD, "novel_thumb_all": "/imagebox/cover/2ab8c9d2_229071_ori.file",
        "novel_img_all": "/imagebox/cover/89d8cac4_3972599_q_ori.file"}


def test_the_original_is_the_upload_and_never_the_card_cover_again():
    assert originals.original_path(ITEM, "https:" + CARD) == "/imagebox/cover/89d8cac4_3972599_q_ori.file"
    # The upload named in any address form the API uses.
    for value in ("https://example.test/imagebox/cover/a_1_q_ori.file", "//images.example.test/imagebox/cover/a_1_q_ori.file"):
        assert originals.original_path({"novel_img_all": value}, CARD) == "/imagebox/cover/a_1_q_ori.file"
    # The same stored image as the card cover (its .file resource) adds nothing.
    assert originals.original_path({"novel_img_all": "/imagebox/cover/2ab8c9d2_229071_ori.file"}, CARD) == ""
    # Placeholders, empty values and anything off the image folder are not covers.
    for value in (None, "", "None", "null", "/img/layout/readycover4.png", "/imagebox/../secret.file",
                  "/imagebox/cover/a b.file", "https://example.test/other/a.file", "/imagebox/adult_cover_img.file"):
        assert originals.original_path({"novel_img_all": value}, CARD) == "", value


def test_scrapers_record_originals_in_small_shards_and_rewrite_only_what_changed(tmp_path):
    store = originals.Store(tmp_path)
    assert store.observe(ITEM, "https:" + CARD)
    assert store.observe({"novel_no": 7, "novel_img_all": "/imagebox/cover/seven_q_ori.file"}, CARD)
    assert store.observe({"novel_no": 71, "novel_img_all": "/imagebox/cover/seventy_one_q_ori.file"}, CARD)  # 71 % 64 == 7
    # An item that names no upload leaves the recorded one alone.
    assert not store.observe({"novel_no": 7, "novel_img_all": None}, CARD)
    assert not store.set("not-a-number", "/imagebox/cover/x.file") and not store.set(8, "https://example.test/x")
    assert store.save() == 2
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["npia_cover_originals_07.json.gz", f"npia_cover_originals_{419368 % 64:02d}.json.gz"]
    shard = json.loads(gzip.decompress((tmp_path / names[0]).read_bytes()))
    assert shard == {"version": 1, "covers": {"7": "/imagebox/cover/seven_q_ori.file",
                                              "71": "/imagebox/cover/seventy_one_q_ori.file"}}
    before = {name: (tmp_path / name).read_bytes() for name in names}
    again = originals.Store(tmp_path)
    assert again.get(419368) == "/imagebox/cover/89d8cac4_3972599_q_ori.file" and again.get(8) == ""
    assert not again.observe(ITEM, "https:" + CARD) and again.save() == 0
    assert again.set(7, "/imagebox/cover/seven_new_q_ori.file") and again.save() == 1
    assert (tmp_path / names[1]).read_bytes() == before[names[1]]
    assert json.loads(gzip.decompress((tmp_path / names[0]).read_bytes()))["covers"]["7"].endswith("seven_new_q_ori.file")


def test_every_npia_scraper_records_the_upload_and_the_workflows_publish_it():
    for name in ("rescrape_npia_noauth.py", "update_rankings_noauth.py", "scrape_npia.py", "update_rankings.py"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "cover_originals.original_path(item, " in text and "cover_originals.Store(" in text and ".save()" in text, name
        # The card cover stays the small one: the upload is only recorded beside the catalog.
        assert '("cover_url", "novel_thumb_all", "novel_thumb", "novel_img", "novel_img_all")' in text, name
    for name in ("rescrape-npia.yml", "update-rankings.yml"):
        assert "'docs/data/npia_cover_originals_*.json.gz'" in (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), name
