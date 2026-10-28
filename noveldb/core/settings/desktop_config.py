"""Converts between the flat `config.json` and `AppSettings`.

Same mapping as the mobile `DesktopConfig`: the external downloader's `ext_*` keys are the shared
set and npia's legacy keys (`thread_num`, `interval_num`, `save_formats`, ...) hold its effective
values. Writing always emits every legacy key, so the mobile apps, older desktop builds and the
bot keep reading the file. Unknown keys are preserved.

Desktop additions: `source_overrides` stores every override (camelCase, as mobile names them),
`settings_schema` marks files written by the merged app, and the old main window's credentials,
output folder and appearance keys round-trip.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from noveldb.core.settings.model import (
    ACCENTS,
    DEFAULT_CATALOG_URL,
    IMAGE_FORMATS,
    NAMING_MODES,
    NPIA,
    NPIA_ENGINES,
    OUTPUT_FORMATS,
    THEMES,
    AppSettings,
    LastInput,
    Secrets,
    SharedOverrides,
    SharedSettings,
)

SCHEMA = 2

# Legacy desktop keys that spell provider names are kept as escapes.
LEGACY_KPAGE = "\u006b\u0061\u006b\u0061\u006f"
LEGACY_NPIA = "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061"

try:
    from npia_search_terms import DEFAULT_SEARCH_QUERY_COUNT
except ImportError:  # pragma: no cover - the module ships with the app
    DEFAULT_SEARCH_QUERY_COUNT = 165

# camelCase names used for overrides in `source_overrides` (mobile `SharedOverrides`).
_OVERRIDE_KEYS = {
    "threads": "threads",
    "interval_min": "intervalMin",
    "interval_max": "intervalMax",
    "formats": "formats",
    "range_from": "rangeFrom",
    "range_to": "rangeTo",
    "use_cache": "useCache",
    "cache_images": "cacheImages",
    "number_chapters": "numberChapters",
    "separate_txt": "separateTXT",
    "range_override": "rangeOverride",
}


# ---------------------------------------------------------------------------
# Value coercion: the file mixes ints, floats, strings and booleans.
# ---------------------------------------------------------------------------

def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            try:
                return int(float(value.strip()))
            except ValueError:
                return None
    return None


def _float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _strings(value: Any) -> Optional[tuple[str, ...]]:
    if not isinstance(value, list):
        return None
    return tuple(str(item) for item in value if item is not None)


def _formats(values: Any, fallback: Any) -> Optional[tuple[str, ...]]:
    if isinstance(values, list):
        picked = tuple(dict.fromkeys(str(v).lower() for v in values if str(v).lower() in OUTPUT_FORMATS))
        if picked:
            return picked
    if isinstance(fallback, str) and fallback.lower() in OUTPUT_FORMATS:
        return (fallback.lower(),)
    return None


def _choice(value: Any, allowed: tuple[str, ...], default: str, upper: bool = False) -> str:
    if not isinstance(value, str):
        return default
    value = value.upper() if upper else value.lower()
    return value if value in allowed else default


def _clamp(value: Optional[float], low: float, high: float, default: float) -> float:
    if value is None:
        return default
    return min(max(value, low), high)


def _first(c: dict, *keys: str) -> Any:
    for key in keys:
        if key in c and c[key] is not None:
            return c[key]
    return None


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _decode_override(data: Any) -> SharedOverrides:
    if not isinstance(data, dict):
        return SharedOverrides()
    get = {name: data.get(key) for name, key in _OVERRIDE_KEYS.items()}
    return SharedOverrides(
        threads=_int(get["threads"]),
        interval_min=_float(get["interval_min"]),
        interval_max=_float(get["interval_max"]),
        formats=_formats(get["formats"], None),
        range_from=_int(get["range_from"]),
        range_to=_int(get["range_to"]),
        use_cache=_bool(get["use_cache"]),
        cache_images=_bool(get["cache_images"]),
        number_chapters=_bool(get["number_chapters"]),
        separate_txt=_bool(get["separate_txt"]),
        range_override=bool(_bool(get["range_override"])),
    )


def _encode_override(o: SharedOverrides) -> dict:
    out: dict[str, Any] = {}
    for name, key in _OVERRIDE_KEYS.items():
        value = getattr(o, name)
        if name == "range_override":
            if value:
                out[key] = True
            continue
        if value is not None:
            out[key] = list(value) if isinstance(value, tuple) else value
    return out


def _legacy_npia_values(c: dict, shared: SharedSettings) -> dict:
    """npia's run settings as the old main window stored them (None where the key is missing)."""
    interval_min = _float(c.get("interval_num"))
    interval_max = _float(c.get("interval_max_num"))
    if interval_max is None:
        interval_max = interval_min
    has_range = "from_enabled" in c or "to_enabled" in c
    return {
        "threads": _int(c.get("thread_num")),
        "interval_min": interval_min,
        "interval_max": interval_max,
        "formats": _formats(c.get("save_formats"), c.get("save_format")),
        "range": (
            _int(c.get("from_num")) if _bool(c.get("from_enabled")) else None,
            _int(c.get("to_num")) if _bool(c.get("to_enabled")) else None,
        ) if has_range else None,
        "use_cache": _bool(c.get("use_cache")),
        "cache_images": _bool(c.get("cache_images")),
        "number_chapters": _bool(c.get("number_chapters")),
        "separate_txt": _bool(c.get("separate_txt")),
    }


def import_config(c: dict, base: Optional[AppSettings] = None) -> AppSettings:
    """`AppSettings` from a raw config dict; missing keys keep `base` (defaults)."""
    base = base or AppSettings()
    c = c if isinstance(c, dict) else {}
    b = base.shared

    shared_min = _float(c.get("ext_interval"))
    shared_min = b.interval_min if shared_min is None else shared_min
    shared_max = _float(c.get("ext_interval_max"))
    if shared_max is None:
        shared_max = shared_min if "ext_interval" in c else max(shared_min, b.interval_max)
    retries = _int(c.get("max_retries"))
    shared = replace(
        b,
        threads=_int(c.get("ext_threads")) or b.threads,
        interval_min=shared_min,
        interval_max=shared_max,
        formats=_formats(c.get("ext_formats"), c.get("ext_format")) or b.formats,
        range_from=_int(c.get("ext_from")) if _bool(c.get("ext_from_enabled")) else None,
        range_to=_int(c.get("ext_to")) if _bool(c.get("ext_to_enabled")) else None,
        use_cache=_first_bool(c, "ext_use_cache", b.use_cache),
        cache_images=_first_bool(c, "ext_cache_images", b.cache_images),
        number_chapters=_first_bool(c, "ext_number_chapters", b.number_chapters),
        separate_txt=_first_bool(c, "ext_separate_txt", b.separate_txt),
        skip_paid=_first_bool(c, "ext_skip_paid", b.skip_paid),
        image_workers=_int(c.get("ext_image_workers")) or b.image_workers,
        generate_on_stop=_first_bool(c, "ext_generate_on_stop", b.generate_on_stop),
        max_retries=retries if retries is not None and retries >= 1 else b.max_retries,
        naming_mode=_choice(c.get("naming_mode"), NAMING_MODES, b.naming_mode),
        append_range=_first_bool(c, "append_range", b.append_range),
    )

    overrides = {
        source: _decode_override(data)
        for source, data in (c.get("source_overrides") or {}).items()
        if isinstance(source, str)
    } if isinstance(c.get("source_overrides"), dict) else dict(base.overrides)

    npia = _merge_legacy_npia(c, shared, overrides.get(NPIA, SharedOverrides()), legacy=_int(c.get("settings_schema")) is None)
    if npia.is_empty:
        overrides.pop(NPIA, None)
    else:
        overrides[NPIA] = npia
    overrides = {k: v for k, v in overrides.items() if not v.is_empty}

    i = base.images
    target_mb = _float(c.get("recompress_target_mb"))
    images = replace(
        i,
        compress=_first_bool(c, "compress_images", i.compress),
        quality=_int(c.get("jpeg_quality")) or i.quality,
        format=_choice(c.get("image_format"), IMAGE_FORMATS, i.format, upper=True),
        convert_gifs=_first_bool(c, "convert_gifs", i.convert_gifs),
        static_only=_first_bool(c, "static_only", i.static_only),
        recompress_target_enable=_first_bool(c, "recompress_target_enable", i.recompress_target_enable),
        recompress_target_mb=target_mb if target_mb is not None and target_mb > 0 else i.recompress_target_mb,
        compress_cover=_first_bool(c, "compress_cover", i.compress_cover),
        cover_quality=_int(c.get("cover_quality")) or i.cover_quality,
        cover_format=_choice(c.get("cover_format"), IMAGE_FORMATS, i.cover_format, upper=True),
        zip_compress_images=_first_bool(c, "zip_compress_images", i.zip_compress_images),
        compression_workers=_int(c.get("image_compression_workers")) or i.compression_workers,
        long_image_layout=_first_bool(c, "ext_long_image_layout", i.long_image_layout),
        save_image_urls_only=_first_bool(c, "save_image_urls_only", i.save_image_urls_only),
    )

    t = base.text
    text = replace(
        t,
        strip_leading_spaces=_first_bool(c, "strip_leading_spaces", t.strip_leading_spaces),
        remove_newlines=_first_bool(c, "remove_newlines", t.remove_newlines),
        blank_paragraph_lines=_first_bool(c, "ext_blank_paragraph_lines", t.blank_paragraph_lines),
        save_html=_first_bool(c, "save_html", t.save_html),
        pdf_toc=_first_bool(c, "pdf_toc", t.pdf_toc),
        pdf_page_numbers=_first_bool(c, "pdf_page_numbers", t.pdf_page_numbers),
        pdf_counter_layout=_first_bool(c, "pdf_counter_layout", t.pdf_counter_layout),
    )

    n = base.npia
    queries = _int(c.get("scrape_max_queries"))
    if queries == 100:  # the old default, raised when more search terms were added
        queries = DEFAULT_SEARCH_QUERY_COUNT
    groups = c.get("tag_query_groups")
    npia_settings = replace(
        n,
        auto_login=_first_bool(c, "auto_login", n.auto_login),
        include_notices=_first_bool(c, "include_notices", n.include_notices),
        use_browser_fallback=_first_bool(c, "use_browser_fallback", n.use_browser_fallback),
        engine=_choice(c.get("npia_engine"), NPIA_ENGINES, n.engine),
        mapping_path=_str(c.get("mapping_path")) if _str(c.get("mapping_path")) is not None else n.mapping_path,
        tag_age_filter=_str(c.get("tag_age_filter")) if _str(c.get("tag_age_filter")) is not None else n.tag_age_filter,
        tag_selected_tags=_strings(c.get("tag_selected_tags")) or n.tag_selected_tags,
        tag_custom_tags=_str(c.get("tag_custom_tags")) if _str(c.get("tag_custom_tags")) is not None else n.tag_custom_tags,
        tag_scrape_all=_first_bool(c, "tag_scrape_all", n.tag_scrape_all),
        tag_query_groups=tuple(
            tuple(str(tag) for tag in group) for group in groups if isinstance(group, list)
        ) if isinstance(groups, list) else n.tag_query_groups,
        scrape_max_queries=queries if queries and queries > 0 else n.scrape_max_queries,
    )

    s = base.sources
    k = s.kpage

    def kpage(suffix: str) -> Any:
        return _first(c, f"ext_kpage_{suffix}", f"ext_{LEGACY_KPAGE}_{suffix}")

    leading = _int(kpage("dedupe_leading_images"))
    sources = replace(
        s,
        kpage=replace(
            k,
            skip_last_page=_bool(kpage("skip_last_page")) if _bool(kpage("skip_last_page")) is not None else k.skip_last_page,
            keep_filler=_bool(kpage("keep_filler")) if _bool(kpage("keep_filler")) is not None else k.keep_filler,
            dedupe_images=_bool(kpage("dedupe_images")) if _bool(kpage("dedupe_images")) is not None else k.dedupe_images,
            dedupe_leading_images=int(_clamp(leading, 1, 10, k.dedupe_leading_images)),
        ),
        ntk_npia_cover=_bool(_first(c, "ext_ntk_npia_cover", f"ext_ntk_{LEGACY_NPIA}_cover"))
        if _bool(_first(c, "ext_ntk_npia_cover", f"ext_ntk_{LEGACY_NPIA}_cover")) is not None else s.ntk_npia_cover,
        nweb_store_cover=_first_bool(c, "ext_syosetu_amazon_cover", s.nweb_store_cover),
        kobo_horizontal_layout=_first_bool(c, "ext_kobo_horizontal_layout", s.kobo_horizontal_layout),
        regular_browser=_first_bool(c, "ext_regular_browser", s.regular_browser),
    )

    o = base.output
    quick = _bool(c.get("quick_enable"))
    output = replace(
        o,
        ask_each_time=(not quick) if quick is not None else o.ask_each_time,
        output_dir=_str(c.get("quick_path")) if _str(c.get("quick_path")) is not None else o.output_dir,
        shutdown_after_success=_first_bool(c, "shutdown_after_success", o.shutdown_after_success),
    )

    u = base.ui
    ui = replace(
        u,
        theme=_choice(c.get("ui_theme"), THEMES, u.theme),
        accent=_choice(c.get("ui_accent"), ACCENTS, u.accent),
        auto_dpi_scale=_first_bool(c, "auto_dpi_scale", u.auto_dpi_scale),
        gui_scale_factor=_clamp(_float(c.get("gui_scale_factor")), 0.5, 3.0, u.gui_scale_factor),
        window_geometry=_str(c.get("window_geometry")) or u.window_geometry,
    )

    catalog = replace(
        base.catalog,
        url=_valid_url(c.get("catalog_url")) or base.catalog.url,
        cart_links=_first_bool(c, "catalog_cart_links", base.catalog.cart_links),
        zoom=_clamp(_float(c.get("catalog_zoom")), 0.5, 3.0, base.catalog.zoom),
    )
    concurrent = _int(c.get("max_concurrent_jobs"))
    queue = replace(base.queue, max_concurrent_jobs=int(_clamp(concurrent, 1, 3, base.queue.max_concurrent_jobs)))

    up = base.upload
    hosts = _strings(c.get("upload_hosts"))
    upload = replace(
        up,
        auto_upload=_first_bool(c, "upload_auto", up.auto_upload),
        hosts=tuple(h for h in hosts if h) if hosts else up.hosts,
        copy_link=_first_bool(c, "upload_copy_link", up.copy_link),
        agreement_accepted=_first_bool(c, "share_agreement_accepted", up.agreement_accepted),
    )

    sec = base.secrets
    secrets = Secrets(
        email=_str(c.get("email")) if _str(c.get("email")) is not None else sec.email,
        password=_str(c.get("wd")) if _str(c.get("wd")) is not None else sec.password,
        loginkey=_str(c.get("loginkey")) if _str(c.get("loginkey")) is not None else sec.loginkey,
        sfc_cookie=_str(c.get("sfc_cookie")) if _str(c.get("sfc_cookie")) is not None else sec.sfc_cookie,
        sfc_user=_str(c.get("sfc_user")) if _str(c.get("sfc_user")) is not None else sec.sfc_user,
        sfc_pass=_str(c.get("sfc_pass")) if _str(c.get("sfc_pass")) is not None else sec.sfc_pass,
        pixeldrain_key=_str(c.get("upload_pixeldrain_key")) or sec.pixeldrain_key,
        gofile_token=_str(c.get("upload_gofile_token")) or sec.gofile_token,
        catbox_userhash=_str(c.get("upload_catbox_userhash")) or sec.catbox_userhash,
    )
    last = LastInput(
        novel_id=_str(c.get("novel_id")) if _str(c.get("novel_id")) is not None else base.last_input.novel_id,
        ext_url=_str(c.get("ext_url")) if _str(c.get("ext_url")) is not None else base.last_input.ext_url,
    )

    return AppSettings(
        shared=shared, overrides=overrides, images=images, text=text, npia=npia_settings,
        sources=sources, output=output, ui=ui, catalog=catalog, queue=queue, upload=upload, secrets=secrets,
        last_input=last,
    )


def _first_bool(c: dict, key: str, default: bool) -> bool:
    value = _bool(c.get(key))
    return default if value is None else value


def _valid_url(value: Any) -> Optional[str]:
    from urllib.parse import urlsplit

    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return text


def _merge_legacy_npia(c: dict, shared: SharedSettings, current: SharedOverrides, legacy: bool) -> SharedOverrides:
    """npia's override, reconciled with the legacy keys.

    Old desktop files (no `settings_schema`) get no npia override: npia uses the shared settings
    like every other source, and no override is chosen for the user. Files written by the merged
    app take their override from `source_overrides`, updated by any legacy key another app (an
    older build, the mobile apps) changed since.
    """
    if legacy:
        return current
    values = _legacy_npia_values(c, shared)
    effective = current.apply_to(shared)
    o = current
    for name in ("threads", "interval_min", "interval_max", "formats", "use_cache",
                 "cache_images", "number_chapters", "separate_txt"):
        value = values[name]
        if value is not None and value != getattr(effective, name):
            o = replace(o, **{name: value})
    if values["range"] is not None:
        frm, to = values["range"]
        if (frm, to) != (effective.range_from, effective.range_to):
            o = replace(o, range_from=frm, range_to=to, range_override=True)
    return o


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_config(s: AppSettings, existing: Optional[dict] = None, include_secrets: bool = True) -> dict:
    """Write `s` onto a copy of `existing`, keeping keys this app does not know."""
    c = dict(existing or {})
    shared = s.shared
    npia = s.effective(NPIA)

    c["ext_threads"] = shared.threads
    c["ext_interval"] = shared.interval_min
    c["ext_interval_max"] = shared.interval_max
    c["ext_formats"] = list(shared.formats)
    c["ext_format"] = shared.formats[0] if shared.formats else "epub"
    c["ext_from_enabled"] = shared.range_from is not None
    if shared.range_from is not None:
        c["ext_from"] = shared.range_from
    c["ext_to_enabled"] = shared.range_to is not None
    if shared.range_to is not None:
        c["ext_to"] = shared.range_to
    c["ext_use_cache"] = shared.use_cache
    c["ext_cache_images"] = shared.cache_images
    c["ext_number_chapters"] = shared.number_chapters
    c["ext_separate_txt"] = shared.separate_txt
    c["ext_skip_paid"] = shared.skip_paid
    c["ext_image_workers"] = shared.image_workers
    c["ext_generate_on_stop"] = shared.generate_on_stop
    c["max_retries"] = shared.max_retries
    c["naming_mode"] = shared.naming_mode
    c["append_range"] = shared.append_range

    c["thread_num"] = npia.threads
    c["interval_num"] = npia.interval_min
    c["interval_max_num"] = npia.interval_max
    npia_formats = [f for f in npia.formats if f != "cbz"] or ["epub"]
    c["save_formats"] = npia_formats
    c["save_format"] = npia_formats[0]
    c["from_enabled"] = npia.range_from is not None
    if npia.range_from is not None:
        c["from_num"] = npia.range_from
    c["to_enabled"] = npia.range_to is not None
    if npia.range_to is not None:
        c["to_num"] = npia.range_to
    c["use_cache"] = npia.use_cache
    c["cache_images"] = npia.cache_images
    c["number_chapters"] = npia.number_chapters
    c["separate_txt"] = npia.separate_txt

    i = s.images
    c["compress_images"] = i.compress
    c["jpeg_quality"] = i.quality
    c["image_format"] = i.format
    c["convert_gifs"] = i.convert_gifs
    c["static_only"] = i.static_only
    c["recompress_target_enable"] = i.recompress_target_enable
    c["recompress_target_mb"] = i.recompress_target_mb
    c["compress_cover"] = i.compress_cover
    c["cover_quality"] = i.cover_quality
    c["cover_format"] = i.cover_format
    c["zip_compress_images"] = i.zip_compress_images
    c["image_compression_workers"] = i.compression_workers
    c["ext_long_image_layout"] = i.long_image_layout
    c["save_image_urls_only"] = i.save_image_urls_only

    t = s.text
    c["strip_leading_spaces"] = t.strip_leading_spaces
    c["remove_newlines"] = t.remove_newlines
    c["ext_blank_paragraph_lines"] = t.blank_paragraph_lines
    c["save_html"] = t.save_html
    c["pdf_toc"] = t.pdf_toc
    c["pdf_page_numbers"] = t.pdf_page_numbers
    c["pdf_counter_layout"] = t.pdf_counter_layout

    n = s.npia
    c["auto_login"] = n.auto_login
    c["include_notices"] = n.include_notices
    c["use_browser_fallback"] = n.use_browser_fallback
    c["npia_engine"] = n.engine
    c["mapping_path"] = n.mapping_path
    c["tag_age_filter"] = n.tag_age_filter
    c["tag_selected_tags"] = list(n.tag_selected_tags)
    c["tag_custom_tags"] = n.tag_custom_tags
    c["tag_scrape_all"] = n.tag_scrape_all
    c["tag_query_groups"] = [list(group) for group in n.tag_query_groups]
    c["scrape_max_queries"] = n.scrape_max_queries

    src = s.sources
    c["ext_kpage_skip_last_page"] = src.kpage.skip_last_page
    c["ext_kpage_keep_filler"] = src.kpage.keep_filler
    c["ext_kpage_dedupe_images"] = src.kpage.dedupe_images
    c["ext_kpage_dedupe_leading_images"] = max(1, min(10, src.kpage.dedupe_leading_images))
    c["ext_ntk_npia_cover"] = src.ntk_npia_cover
    c["ext_syosetu_amazon_cover"] = src.nweb_store_cover
    c["ext_kobo_horizontal_layout"] = src.kobo_horizontal_layout
    c["ext_regular_browser"] = src.regular_browser

    c["quick_enable"] = not s.output.ask_each_time
    c["quick_path"] = s.output.output_dir
    c["shutdown_after_success"] = s.output.shutdown_after_success

    c["ui_theme"] = s.ui.theme
    c["ui_accent"] = s.ui.accent
    c["auto_dpi_scale"] = s.ui.auto_dpi_scale
    c["gui_scale_factor"] = s.ui.gui_scale_factor
    if s.ui.window_geometry:
        c["window_geometry"] = s.ui.window_geometry

    c["catalog_url"] = s.catalog.url or DEFAULT_CATALOG_URL
    c["catalog_cart_links"] = s.catalog.cart_links
    c["catalog_zoom"] = s.catalog.zoom
    c["max_concurrent_jobs"] = s.queue.max_concurrent_jobs

    c["upload_auto"] = s.upload.auto_upload
    c["upload_hosts"] = list(s.upload.hosts)
    c["upload_copy_link"] = s.upload.copy_link
    c["share_agreement_accepted"] = s.upload.agreement_accepted

    c["novel_id"] = s.last_input.novel_id
    c["ext_url"] = s.last_input.ext_url

    if include_secrets:
        c["email"] = s.secrets.email
        c["wd"] = s.secrets.password
        c["loginkey"] = s.secrets.loginkey
        for key in ("sfc_cookie", "sfc_user", "sfc_pass"):
            value = getattr(s.secrets, key)
            if value or key in c:
                c[key] = value
        for key, attr in (("upload_pixeldrain_key", "pixeldrain_key"), ("upload_gofile_token", "gofile_token"),
                          ("upload_catbox_userhash", "catbox_userhash")):
            value = getattr(s.secrets, attr)
            if value or key in c:
                c[key] = value
    else:
        for key in ("email", "wd", "loginkey", "sfc_cookie", "sfc_user", "sfc_pass",
                    "upload_pixeldrain_key", "upload_gofile_token", "upload_catbox_userhash"):
            c.pop(key, None)

    c["source_overrides"] = {source: _encode_override(o) for source, o in sorted(s.overrides.items()) if not o.is_empty}
    c["settings_schema"] = SCHEMA
    return c
