"""The merged settings model: one shared set for every source plus optional per-source overrides.

Field for field this mirrors the mobile apps' `AppSettings` (android `Settings.kt`, iOS
`AppSettings`), so a config moves between desktop and mobile. Defaults are the desktop's own
(4 threads and 0.5 s for external sources; npia keeps 1 thread through its override), and the
desktop-only groups (output folder, appearance, catalog, queue, credentials) sit alongside.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Callable, Optional

OUTPUT_FORMATS = ("epub", "txt", "pdf", "cbz")
IMAGE_FORMATS = ("WEBP", "JPEG", "PNG", "AVIF")
NAMING_MODES = ("title", "id")
THEMES = ("system", "light", "dark", "amoled")
ACCENTS = ("halgakos_rose", "desktop_blue", "library_violet")
NPIA_ENGINES = ("native", "browser")

DEFAULT_CATALOG_URL = "https://shirochi-stack.github.io/NovelDB/"

# Source whose run settings live in its override by default (mobile `editRunSettings`).
NPIA = "npia"


def clamp_interval_range(low: float, high: float) -> tuple[float, float]:
    """`_normalize_interval_range`: each end clamped to 0..300 s, swapped when max < min."""
    low = min(max(float(low), 0.0), 300.0)
    high = min(max(float(high), 0.0), 300.0)
    return (high, low) if high < low else (low, high)


@dataclass(frozen=True)
class SharedSettings:
    """Settings both desktop downloaders had, under one name."""

    threads: int = 4
    interval_min: float = 2.0
    interval_max: float = 5.0
    max_retries: int = 5
    formats: tuple[str, ...] = ("epub",)
    range_from: Optional[int] = None
    range_to: Optional[int] = None
    use_cache: bool = True
    cache_images: bool = False
    number_chapters: bool = False
    separate_txt: bool = False
    skip_paid: bool = False
    image_workers: int = 8
    generate_on_stop: bool = False
    naming_mode: str = "title"
    append_range: bool = False

    @property
    def interval_range(self) -> tuple[float, float]:
        return clamp_interval_range(self.interval_min, self.interval_max)


# Fields a source may override. `range_override` (desktop only) makes the override's range apply
# even when it is "off" (both ends None); mobile treats a None range as "use the shared one".
OVERRIDABLE = (
    "threads", "interval_min", "interval_max", "formats", "range_from", "range_to",
    "use_cache", "cache_images", "number_chapters", "separate_txt",
)


@dataclass(frozen=True)
class SharedOverrides:
    threads: Optional[int] = None
    interval_min: Optional[float] = None
    interval_max: Optional[float] = None
    formats: Optional[tuple[str, ...]] = None
    range_from: Optional[int] = None
    range_to: Optional[int] = None
    use_cache: Optional[bool] = None
    cache_images: Optional[bool] = None
    number_chapters: Optional[bool] = None
    separate_txt: Optional[bool] = None
    range_override: bool = False

    @property
    def is_empty(self) -> bool:
        return self == SharedOverrides()

    @property
    def has_range(self) -> bool:
        return self.range_override or self.range_from is not None or self.range_to is not None

    def overridden(self) -> list[str]:
        """Names of the fields this override replaces (range counts once, as "range")."""
        names = [name for name in OVERRIDABLE if not name.startswith("range_") and getattr(self, name) is not None]
        if self.has_range:
            names.append("range")
        return names

    def apply_to(self, base: SharedSettings) -> SharedSettings:
        changes = {
            name: getattr(self, name)
            for name in OVERRIDABLE
            if not name.startswith("range_") and getattr(self, name) is not None
        }
        if self.range_override:
            changes.update(range_from=self.range_from, range_to=self.range_to)
        else:
            if self.range_from is not None:
                changes["range_from"] = self.range_from
            if self.range_to is not None:
                changes["range_to"] = self.range_to
        return replace(base, **changes)


@dataclass(frozen=True)
class ImageSettings:
    compress: bool = True
    quality: int = 50
    format: str = "WEBP"
    convert_gifs: bool = False
    static_only: bool = False
    recompress_target_enable: bool = False
    recompress_target_mb: float = 20.0
    compress_cover: bool = False
    cover_quality: int = 90
    cover_format: str = "JPEG"
    zip_compress_images: bool = False
    compression_workers: int = 1
    long_image_layout: bool = False
    save_image_urls_only: bool = False


@dataclass(frozen=True)
class TextSettings:
    strip_leading_spaces: bool = False
    remove_newlines: bool = False
    blank_paragraph_lines: bool = False
    save_html: bool = False
    pdf_toc: bool = False
    pdf_page_numbers: bool = False
    pdf_counter_layout: bool = False


@dataclass(frozen=True)
class NpiaSettings:
    auto_login: bool = True
    include_notices: bool = True
    use_browser_fallback: bool = True
    engine: str = "native"
    mapping_path: str = ""
    tag_age_filter: str = ""
    tag_selected_tags: tuple[str, ...] = ()
    tag_custom_tags: str = ""
    tag_scrape_all: bool = False
    tag_query_groups: tuple[tuple[str, ...], ...] = ()
    scrape_max_queries: int = 165


@dataclass(frozen=True)
class KpageSettings:
    skip_last_page: bool = False
    keep_filler: bool = False
    dedupe_images: bool = True
    dedupe_leading_images: int = 2


@dataclass(frozen=True)
class SourceOptions:
    kpage: KpageSettings = field(default_factory=KpageSettings)
    ntk_npia_cover: bool = False
    nweb_store_cover: bool = False
    kobo_horizontal_layout: bool = True
    regular_browser: bool = False


@dataclass(frozen=True)
class OutputSettings:
    """Where files go. `ask_each_time` is the inverse of the old "Quick Download" switch."""

    ask_each_time: bool = True
    output_dir: str = ""
    shutdown_after_success: bool = False


@dataclass(frozen=True)
class UiSettings:
    theme: str = "system"
    accent: str = "halgakos_rose"
    auto_dpi_scale: bool = True
    gui_scale_factor: float = 1.0
    window_geometry: str = ""


@dataclass(frozen=True)
class CatalogSettings:
    url: str = DEFAULT_CATALOG_URL
    cart_links: bool = True


@dataclass(frozen=True)
class QueueSettings:
    max_concurrent_jobs: int = 1


@dataclass(frozen=True)
class Secrets:
    """Credentials. Desktop keeps them in config.json as before; settings exports leave them out."""

    email: str = ""
    password: str = ""
    loginkey: str = ""
    sfc_cookie: str = ""
    sfc_user: str = ""
    sfc_pass: str = ""


@dataclass(frozen=True)
class LastInput:
    novel_id: str = ""
    ext_url: str = ""


@dataclass(frozen=True)
class AppSettings:
    shared: SharedSettings = field(default_factory=SharedSettings)
    # Keyed by source codename. npia's threads/interval/range are pinned here on first migration.
    overrides: dict = field(default_factory=dict)
    images: ImageSettings = field(default_factory=ImageSettings)
    text: TextSettings = field(default_factory=TextSettings)
    npia: NpiaSettings = field(default_factory=NpiaSettings)
    sources: SourceOptions = field(default_factory=SourceOptions)
    output: OutputSettings = field(default_factory=OutputSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    catalog: CatalogSettings = field(default_factory=CatalogSettings)
    queue: QueueSettings = field(default_factory=QueueSettings)
    secrets: Secrets = field(default_factory=Secrets)
    last_input: LastInput = field(default_factory=LastInput)

    def override(self, source: Optional[str]) -> SharedOverrides:
        return self.overrides.get(source, SharedOverrides()) if source else SharedOverrides()

    def effective(self, source: Optional[str]) -> SharedSettings:
        """The run settings a download from `source` uses (None: the shared set)."""
        if not source or source not in self.overrides:
            return self.shared
        return self.overrides[source].apply_to(self.shared)

    def with_override(self, source: str, value: SharedOverrides) -> "AppSettings":
        overrides = dict(self.overrides)
        if value.is_empty:
            overrides.pop(source, None)
        else:
            overrides[source] = value
        return replace(self, overrides=overrides)

    def edit_run_settings(
        self, source: Optional[str], change: Callable[[SharedSettings], SharedSettings]
    ) -> "AppSettings":
        """Apply an edit made on the Download page, routed like the mobile `editRunSettings`.

        npia edits always go to its override (it keeps its own values, as the desktop main window
        did). For other sources an edit goes to the override only when that field is already
        overridden; otherwise it changes the shared set. Skip paid, image workers and generate on
        stop are always shared.
        """
        current = self.effective(source)
        nxt = change(current)
        if nxt == current:
            return self
        existing = self.override(source)

        def to_override(has: bool) -> bool:
            return source is not None and (source == NPIA or has)

        base = replace(
            self.shared,
            skip_paid=nxt.skip_paid,
            image_workers=nxt.image_workers,
            generate_on_stop=nxt.generate_on_stop,
            max_retries=nxt.max_retries,
            naming_mode=nxt.naming_mode,
            append_range=nxt.append_range,
        )
        o = existing
        for name in OVERRIDABLE:
            if name.startswith("range_"):
                continue
            if getattr(nxt, name) == getattr(current, name):
                continue
            if to_override(getattr(existing, name) is not None):
                o = replace(o, **{name: getattr(nxt, name)})
            else:
                base = replace(base, **{name: getattr(nxt, name)})
        if (nxt.range_from, nxt.range_to) != (current.range_from, current.range_to):
            if to_override(existing.has_range):
                o = replace(o, range_from=nxt.range_from, range_to=nxt.range_to, range_override=True)
            else:
                base = replace(base, range_from=nxt.range_from, range_to=nxt.range_to)
        result = replace(self, shared=base)
        return result.with_override(source, o) if source else result


def dataclass_to_dict(value) -> dict:
    """Plain dict of a settings dataclass (tuples become lists), for logging and tests."""
    out = {}
    for item in fields(value):
        v = getattr(value, item.name)
        if hasattr(v, "__dataclass_fields__"):
            v = dataclass_to_dict(v)
        elif isinstance(v, tuple):
            v = [list(x) if isinstance(x, tuple) else x for x in v]
        elif isinstance(v, dict):
            v = {k: dataclass_to_dict(x) if hasattr(x, "__dataclass_fields__") else x for k, x in v.items()}
        out[item.name] = v
    return out
