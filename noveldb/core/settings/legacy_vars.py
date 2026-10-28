"""The `var_*` option objects the moved pipelines read, filled from a settings snapshot.

The npia and external pipelines were written against Tk variables (`.get()` / `.set()`). Queue
jobs give them these plain values instead, so the pipeline code stays unchanged: one snapshot of
`AppSettings` is taken when a job starts and edits made while it runs do not leak into it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from noveldb.core.settings.model import NPIA, AppSettings


class Value:
    """A Tk-variable stand-in: `get()` and `set()` on one value."""

    __slots__ = ("_value",)

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value

    def __repr__(self) -> str:
        return f"Value({self._value!r})"


def _output_target(settings: AppSettings, output_dir: Optional[str]) -> tuple[bool, str]:
    """(quick_enable, quick_path): a chosen folder means no save prompt."""
    folder = output_dir or settings.output.output_dir
    if output_dir or (folder and not settings.output.ask_each_time):
        return True, folder
    return False, folder or ""


def npia_values(settings: AppSettings, novel_input: str, output_dir: Optional[str] = None) -> dict:
    """Attributes for `NpiaPipelineMixin`, using npia's effective run settings."""
    run = settings.effective(NPIA)
    images, text, npia = settings.images, settings.text, settings.npia
    formats = [f for f in run.formats if f in ("epub", "txt", "pdf")] or ["epub"]
    quick, folder = _output_target(settings, output_dir)
    values = {
        "var_novel_id": novel_input,
        "var_threads": run.threads,
        "var_interval": run.interval_min,
        "var_interval_max": run.interval_max,
        "var_max_retries": run.max_retries,
        "var_from_enabled": run.range_from is not None,
        "var_from_num": run.range_from or 1,
        "var_to_enabled": run.range_to is not None,
        "var_to_num": run.range_to or 1,
        "var_save_epub": "epub" in formats,
        "var_save_txt": "txt" in formats,
        "var_save_pdf": "pdf" in formats,
        "var_save_format": formats[0],
        "var_separate_txt": run.separate_txt,
        "var_number_chapters": run.number_chapters,
        "var_use_cache": run.use_cache,
        "var_cache_images": run.cache_images,
        "var_save_html": text.save_html,
        "var_strip_leading_spaces": text.strip_leading_spaces,
        "var_remove_newlines": text.remove_newlines,
        "var_pdf_toc": text.pdf_toc,
        "var_pdf_page_numbers": text.pdf_page_numbers,
        "var_pdf_counter_layout": text.pdf_counter_layout,
        "var_compress_images": images.compress,
        "var_jpeg_quality": images.quality,
        "var_image_format": images.format,
        "var_convert_gifs": images.convert_gifs,
        "var_static_only": images.static_only,
        "var_save_image_urls_only": images.save_image_urls_only,
        "var_recompress_target_enable": images.recompress_target_enable,
        "var_recompress_target_mb": images.recompress_target_mb,
        "var_compress_cover": images.compress_cover,
        "var_cover_quality": images.cover_quality,
        "var_cover_format": images.cover_format,
        "var_zip_compress_images": images.zip_compress_images,
        "var_image_compression_workers": images.compression_workers,
        "var_include_notices": npia.include_notices,
        "var_quick_enable": quick,
        "var_quick_path": folder,
        "var_naming_mode": run.naming_mode,
        "var_append_range": run.append_range,
        # Shutdown is decided by the queue once every job is done.
        "var_shutdown_after_success": False,
    }
    return {name: Value(value) for name, value in values.items()}


def external_values(
    settings: AppSettings, url: str, source: Optional[str] = None, output_dir: Optional[str] = None
) -> dict:
    """Attributes for `ExternalPipelineMixin`: its `_var_*` options, `_parent_gui` and retries."""
    run = settings.effective(source)
    images, text, sources = settings.images, settings.text, settings.sources
    formats = [f for f in run.formats if f in ("epub", "txt", "pdf", "cbz")] or ["epub"]
    quick, folder = _output_target(settings, output_dir)
    values = {
        "_url_var": url,
        "_var_format": formats[0],
        "_var_format_epub": "epub" in formats,
        "_var_format_txt": "txt" in formats,
        "_var_format_pdf": "pdf" in formats,
        "_var_format_cbz": "cbz" in formats,
        "_var_separate_txt": run.separate_txt,
        "_var_ext_threads": run.threads,
        "_var_ext_image_workers": run.image_workers,
        "_var_interval": run.interval_min,
        "_var_interval_max": run.interval_max,
        "_var_from_enabled": run.range_from is not None,
        "_var_from": run.range_from or 1,
        "_var_to_enabled": run.range_to is not None,
        "_var_to": run.range_to or 1,
        "_var_skip_paid": run.skip_paid,
        "_var_number_chapters": run.number_chapters,
        "_var_generate_on_stop": run.generate_on_stop,
        "_var_use_cache": run.use_cache,
        "_var_cache_images": run.cache_images,
        "_var_blank_paragraph_lines": text.blank_paragraph_lines,
        "_var_long_image_layout": images.long_image_layout,
        "_var_kobo_horizontal_layout": sources.kobo_horizontal_layout,
        "_var_regular_browser": sources.regular_browser,
        "_var_ntk_npia_cover": sources.ntk_npia_cover,
        "_var_syosetu_amazon_cover": sources.nweb_store_cover,
        "_var_kpage_skip_last_page": sources.kpage.skip_last_page,
        "_var_kpage_keep_filler": sources.kpage.keep_filler,
        "_var_kpage_dedupe_images": sources.kpage.dedupe_images,
        "_var_kpage_dedupe_leading_images": sources.kpage.dedupe_leading_images,
    }
    out = {name: Value(value) for name, value in values.items()}
    out["_retry_variable"] = Value(run.max_retries)
    out["_parent_gui"] = SimpleNamespace(**{
        name: Value(value)
        for name, value in {
            "var_include_notices": settings.npia.include_notices,
            "var_quick_enable": quick,
            "var_quick_path": folder,
            "var_compress_images": images.compress,
            "var_jpeg_quality": images.quality,
            "var_image_format": images.format,
            "var_zip_compress_images": images.zip_compress_images,
            "var_compress_cover": images.compress_cover,
            "var_cover_quality": images.cover_quality,
            "var_cover_format": images.cover_format,
            "var_pdf_toc": text.pdf_toc,
            "var_pdf_page_numbers": text.pdf_page_numbers,
            "var_pdf_counter_layout": text.pdf_counter_layout,
        }.items()
    })
    return out
