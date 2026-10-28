"""The merged settings model and its config.json mapping (ported from the mobile SettingsTest)."""

import json
import os
from dataclasses import replace

import pytest

from noveldb.core.settings import desktop_config as dc
from noveldb.core.settings.model import (
    NPIA,
    AppSettings,
    SharedOverrides,
    SharedSettings,
    clamp_interval_range,
)
from noveldb.core.settings.store import ConfigStore, read_raw, write_raw

LEGACY_KPAGE = "".join(map(chr, (0x6B, 0x61, 0x6B, 0x61, 0x6F)))


def test_defaults_are_the_desktop_ones():
    s = AppSettings()
    assert s.shared.threads == 4
    assert (s.shared.interval_min, s.shared.interval_max) == (2.0, 5.0)
    assert s.shared.max_retries == 5
    assert s.shared.formats == ("epub",)
    assert s.images.quality == 50
    assert s.sources.kpage.dedupe_images is True
    assert s.ui.accent == "halgakos_rose"


def test_interval_range_matches_desktop_normalization():
    assert clamp_interval_range(2.0, 0.5) == (0.5, 2.0)
    assert clamp_interval_range(-1.0, 999.0) == (0.0, 300.0)
    assert SharedSettings(interval_min=1.5, interval_max=1.5).interval_range == (1.5, 1.5)


def test_overrides_apply_per_source():
    s = AppSettings(overrides={NPIA: SharedOverrides(threads=1, formats=("txt",))})
    assert s.effective(NPIA).threads == 1
    assert s.effective(NPIA).formats == ("txt",)
    assert s.effective("kpage").threads == 4
    assert s.effective(None).threads == 4


def test_old_desktop_config_gets_no_npia_override():
    config = {
        "email": "reader@example.com", "wd": "pw", "loginkey": "",
        "thread_num": 4, "interval_num": 2.5, "interval_max_num": 4.0,
        "save_formats": ["epub", "txt"], "from_enabled": False, "to_enabled": True, "to_num": 10,
        "ext_threads": 4, "ext_interval": 1.0, "ext_interval_max": 2.0,
        "ext_formats": ["epub", "txt"], "ext_from_enabled": False, "ext_to_enabled": True,
        "ext_from": 89, "ext_to": 92,
        "image_format": "WEBP", "jpeg_quality": 90, "recompress_target_enable": False,
        "recompress_target_mb": 20.0,
        f"ext_{LEGACY_KPAGE}_dedupe_leading_images": 3, "ext_kpage_keep_filler": True,
    }
    s = dc.import_config(config)
    assert s.shared.threads == 4
    assert (s.shared.interval_min, s.shared.interval_max) == (1.0, 2.0)
    assert (s.shared.range_from, s.shared.range_to) == (None, 92)

    # No override is chosen for the user: npia uses the shared settings like every source.
    assert s.overrides == {}
    assert s.effective(NPIA) == s.shared

    assert s.images.format == "WEBP"
    assert s.images.recompress_target_enable is False
    assert s.sources.kpage.dedupe_leading_images == 3
    assert s.sources.kpage.keep_filler is True
    assert s.secrets.email == "reader@example.com"
    assert s.secrets.password == "pw"
    assert s.secrets.loginkey == ""


def test_npia_range_off_does_not_inherit_the_external_range():
    # A file from the merged app whose npia range is off while the shared range is set.
    s = dc.import_config({"settings_schema": 2, "from_enabled": False, "to_enabled": False,
                          "ext_to_enabled": True, "ext_to": 50,
                          "source_overrides": {NPIA: {"range_override": True}}})
    assert s.shared.range_to == 50
    assert s.effective(NPIA).range_to is None


def test_export_keeps_unknown_keys_and_writes_legacy_keys():
    s = AppSettings(shared=SharedSettings(threads=6), overrides={NPIA: SharedOverrides(threads=3)})
    out = dc.export_config(s, {"gui_scale_factor": 1.7, "custom_tool_key": "keep"})
    assert out["ext_threads"] == 6
    assert out["thread_num"] == 3
    assert out["custom_tool_key"] == "keep"
    assert out["settings_schema"] == dc.SCHEMA
    assert out["source_overrides"] == {NPIA: {"threads": 3}}
    again = dc.import_config(out)
    assert again.effective(NPIA).threads == 3
    assert again.shared.threads == 6
    assert again == dc.import_config(dc.export_config(again, out))


def test_export_without_secrets_drops_credentials():
    s = replace(AppSettings(), secrets=replace(AppSettings().secrets, email="me@example.com", password="pw"))
    assert dc.export_config(s)["wd"] == "pw"
    out = dc.export_config(s, {"wd": "old", "sfc_pass": "x"}, include_secrets=False)
    assert "wd" not in out and "email" not in out and "sfc_pass" not in out


def test_settings_written_by_the_merged_app_merge_equal_npia_values():
    out = dc.export_config(AppSettings())
    s = dc.import_config(out)
    assert NPIA not in s.overrides


def test_a_legacy_key_changed_by_another_app_updates_the_override():
    out = dc.export_config(AppSettings(overrides={NPIA: SharedOverrides(threads=1)}))
    out["thread_num"] = 2  # an older desktop build or the mobile export wrote this
    assert dc.import_config(out).effective(NPIA).threads == 2


def test_cbz_round_trips_and_npia_formats_drop_cbz():
    s = dc.import_config({"ext_formats": ["epub", "cbz"]})
    assert s.shared.formats == ("epub", "cbz")
    out = dc.export_config(s)
    assert out["ext_formats"] == ["epub", "cbz"]
    assert out["save_formats"] == ["epub"]


def test_legacy_migrations():
    s = dc.import_config({
        "save_format": "txt", "interval_num": 1.5, "max_retries": 0, "scrape_max_queries": 100,
        "gui_scale_factor": 9, "ext_kpage_dedupe_leading_images": 40, "recompress_target_mb": "bad",
        "quick_enable": True, "quick_path": "D:/books",
    })
    assert s.overrides == {}
    assert s.effective(NPIA).threads == 4
    assert s.effective(NPIA).interval_range == (2.0, 5.0)
    assert s.shared.max_retries == 5
    assert s.npia.scrape_max_queries == dc.DEFAULT_SEARCH_QUERY_COUNT
    assert s.ui.gui_scale_factor == 3.0
    assert s.sources.kpage.dedupe_leading_images == 10
    assert s.images.recompress_target_mb == 20.0
    assert s.output.ask_each_time is False
    assert s.output.output_dir == "D:/books"


def test_type_coercion_ignores_strings_for_booleans():
    s = dc.import_config({"ext_use_cache": "false", "ext_threads": "6", "ext_interval": "0.7"})
    assert s.shared.use_cache is True  # a string is not a boolean
    assert s.shared.threads == 6
    assert s.shared.interval_min == 0.7


def test_invalid_catalog_url_falls_back_to_default():
    assert dc.import_config({"catalog_url": "https://"}).catalog.url == AppSettings().catalog.url
    assert dc.import_config({"catalog_url": "https://example.com/a"}).catalog.url == "https://example.com/a"


def test_edits_follow_desktop_storage():
    base = AppSettings()
    shared = base.edit_run_settings("nweb", lambda r: replace(r, threads=7, interval_min=0.2, interval_max=0.9, skip_paid=True))
    assert shared.shared.threads == 7
    assert (shared.shared.interval_min, shared.shared.interval_max) == (0.2, 0.9)
    assert shared.shared.skip_paid is True
    assert shared.overrides == {}

    npia = base.edit_run_settings(NPIA, lambda r: replace(r, threads=2, image_workers=3))
    assert npia.shared.threads == 4
    assert npia.overrides[NPIA].threads == 2
    assert npia.shared.image_workers == 3

    overridden = replace(base, overrides={"nweb": SharedOverrides(interval_max=5.0)}).edit_run_settings(
        "nweb", lambda r: replace(r, interval_max=6.0, interval_min=1.5)
    )
    assert overridden.overrides["nweb"].interval_max == 6.0
    assert overridden.shared.interval_min == 1.5

    assert base.edit_run_settings(None, lambda r: replace(r, threads=9)).shared.threads == 9


def test_npia_range_edits_pin_the_range():
    s = AppSettings().edit_run_settings(NPIA, lambda r: replace(r, range_to=20))
    assert s.overrides[NPIA].range_override is True
    s = s.edit_run_settings(NPIA, lambda r: replace(r, range_to=None))
    assert s.effective(NPIA).range_to is None
    assert s.overrides[NPIA].range_override is True


def test_store_round_trip_is_atomic_and_merges(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"bot_only_key": 1, "ext_threads": 2}), encoding="utf-8")
    store = ConfigStore(path)
    s = store.load()
    assert s.shared.threads == 2
    store.save(replace(s, shared=replace(s.shared, threads=5)))
    raw = read_raw(path)
    assert raw["ext_threads"] == 5
    assert raw["bot_only_key"] == 1
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


def test_store_escapes_provider_names(tmp_path):
    path = tmp_path / "config.json"
    write_raw({f"ext_{LEGACY_KPAGE}_keep_filler": True}, path)
    assert LEGACY_KPAGE not in path.read_text(encoding="utf-8")
    assert read_raw(path)[f"ext_{LEGACY_KPAGE}_keep_filler"] is True


def test_store_creates_config_on_first_launch(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    assert not store.exists()
    store.ensure_exists()
    assert store.exists()
    assert read_raw(store.path)["settings_schema"] == dc.SCHEMA


def test_unreadable_config_loads_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert ConfigStore(path).load() == AppSettings()


@pytest.mark.parametrize("value", [None, [], "text", 3])
def test_non_dict_config_loads_defaults(value):
    assert dc.import_config(value) == AppSettings()


def test_new_install_has_no_overrides_and_mobile_defaults(tmp_path):
    from noveldb.core.settings.store import ConfigStore

    store = ConfigStore(tmp_path / "config.json")
    store.ensure_exists()
    settings = store.load()
    assert settings.overrides == {}
    assert settings.effective("npia").threads == settings.shared.threads == 4
    assert settings.effective("npia").interval_range == (2.0, 5.0)
