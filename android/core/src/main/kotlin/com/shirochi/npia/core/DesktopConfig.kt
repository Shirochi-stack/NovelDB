package com.shirochi.npia.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Converts between the desktop app's flat `config.json` and `AppSettings` (same mapping as the
 * iOS `DesktopConfig`). The external downloader's `ext_*` keys become `shared`; npia keys that
 * differ become `overrides["npia"]`. Credentials go to the secret store, never settings.
 */
object DesktopConfig {
    // Legacy desktop keys that spell provider names are kept as escapes.
    private const val LEGACY_KPAGE = "\u006b\u0061\u006b\u0061\u006f"
    private const val LEGACY_NPIA = "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061"

    data class Secrets(val email: String?, val password: String?, val loginKey: String?)

    fun import(text: String, base: AppSettings = AppSettings()): Pair<AppSettings, Secrets> =
        import(Json.parseToJsonElement(text).jsonObject, base)

    fun import(c: JsonObject, base: AppSettings = AppSettings()): Pair<AppSettings, Secrets> {
        val b = base.shared
        val sharedMin = c.double("ext_interval") ?: b.intervalMin
        val shared = b.copy(
            threads = c.int("ext_threads") ?: b.threads,
            intervalMin = sharedMin,
            intervalMax = c.double("ext_interval_max") ?: maxOf(sharedMin, b.intervalMax),
            formats = formats(c["ext_formats"], c["ext_format"]) ?: b.formats,
            rangeFrom = if (c.bool("ext_from_enabled") == true) c.int("ext_from") else null,
            rangeTo = if (c.bool("ext_to_enabled") == true) c.int("ext_to") else null,
            useCache = c.bool("ext_use_cache") ?: b.useCache,
            cacheImages = c.bool("ext_cache_images") ?: b.cacheImages,
            numberChapters = c.bool("ext_number_chapters") ?: b.numberChapters,
            separateTxt = c.bool("ext_separate_txt") ?: b.separateTxt,
            skipPaid = c.bool("ext_skip_paid") ?: b.skipPaid,
            imageWorkers = c.int("ext_image_workers") ?: b.imageWorkers,
            generateOnStop = c.bool("ext_generate_on_stop") ?: b.generateOnStop,
            maxRetries = c.int("max_retries") ?: b.maxRetries,
            namingMode = when (c.string("naming_mode")) { "id" -> NamingMode.ID; "title" -> NamingMode.TITLE; else -> b.namingMode },
            appendRange = c.bool("append_range") ?: b.appendRange,
        )

        val npia = SharedOverrides(
            threads = differing(c.int("thread_num"), shared.threads),
            intervalMin = differing(c.double("interval_num"), shared.intervalMin),
            intervalMax = differing(c.double("interval_max_num"), shared.intervalMax),
            formats = differing(formats(c["save_formats"], c["save_format"]), shared.formats),
            rangeFrom = if (c.bool("from_enabled") == true) c.int("from_num") else null,
            rangeTo = if (c.bool("to_enabled") == true) c.int("to_num") else null,
            useCache = differing(c.bool("use_cache"), shared.useCache),
            cacheImages = differing(c.bool("cache_images"), shared.cacheImages),
            numberChapters = differing(c.bool("number_chapters"), shared.numberChapters),
            separateTxt = differing(c.bool("separate_txt"), shared.separateTxt),
        )
        val overrides = base.overrides.toMutableMap().apply {
            if (npia.isEmpty) remove(SourceId.NPIA.raw) else put(SourceId.NPIA.raw, npia)
        }

        val i = base.images
        val images = i.copy(
            compress = c.bool("compress_images") ?: i.compress,
            quality = c.int("jpeg_quality") ?: i.quality,
            format = c.string("image_format")?.let(ImageFormat::fromRaw) ?: i.format,
            convertGifs = c.bool("convert_gifs") ?: i.convertGifs,
            staticOnly = c.bool("static_only") ?: i.staticOnly,
            recompressTargetMb = if (c.bool("recompress_target_enable") == true) c.double("recompress_target_mb") else null,
            compressCover = c.bool("compress_cover") ?: i.compressCover,
            coverQuality = c.int("cover_quality") ?: i.coverQuality,
            coverFormat = c.string("cover_format")?.let(ImageFormat::fromRaw) ?: i.coverFormat,
            zipCompressImages = c.bool("zip_compress_images") ?: i.zipCompressImages,
            compressionWorkers = c.int("image_compression_workers") ?: i.compressionWorkers,
            longImageLayout = c.bool("ext_long_image_layout") ?: i.longImageLayout,
            saveImageUrlsOnly = c.bool("save_image_urls_only") ?: i.saveImageUrlsOnly,
        )

        val t = base.text
        val text = t.copy(
            stripLeadingSpaces = c.bool("strip_leading_spaces") ?: t.stripLeadingSpaces,
            removeNewlines = c.bool("remove_newlines") ?: t.removeNewlines,
            blankParagraphLines = c.bool("ext_blank_paragraph_lines") ?: t.blankParagraphLines,
            saveHtml = c.bool("save_html") ?: t.saveHtml,
            pdfToc = c.bool("pdf_toc") ?: t.pdfToc,
            pdfPageNumbers = c.bool("pdf_page_numbers") ?: t.pdfPageNumbers,
            pdfCounterLayout = c.bool("pdf_counter_layout") ?: t.pdfCounterLayout,
        )

        val n = base.npia
        val npiaSettings = n.copy(
            autoLogin = c.bool("auto_login") ?: n.autoLogin,
            includeNotices = c.bool("include_notices") ?: n.includeNotices,
            useBrowserFallback = c.bool("use_browser_fallback") ?: n.useBrowserFallback,
            tagAgeFilter = c.string("tag_age_filter") ?: n.tagAgeFilter,
            tagSelectedTags = c.strings("tag_selected_tags") ?: n.tagSelectedTags,
            tagCustomTags = c.string("tag_custom_tags") ?: n.tagCustomTags,
            tagScrapeAll = c.bool("tag_scrape_all") ?: n.tagScrapeAll,
            scrapeMaxQueries = c.int("scrape_max_queries") ?: n.scrapeMaxQueries,
        )

        // The kpage-named keys win over legacy ones.
        fun kpage(suffix: String) = c["ext_kpage_$suffix"] ?: c["ext_${LEGACY_KPAGE}_$suffix"]
        val s = base.sources
        val k = s.kpage
        val sources = s.copy(
            kpage = k.copy(
                skipLastPage = kpage("skip_last_page").bool() ?: k.skipLastPage,
                keepFiller = kpage("keep_filler").bool() ?: k.keepFiller,
                dedupeImages = kpage("dedupe_images").bool() ?: k.dedupeImages,
                dedupeLeadingImages = kpage("dedupe_leading_images").int() ?: k.dedupeLeadingImages,
            ),
            ntkNpiaCover = (c["ext_ntk_npia_cover"] ?: c["ext_ntk_${LEGACY_NPIA}_cover"]).bool() ?: s.ntkNpiaCover,
            nwebStoreCover = c.bool("ext_syosetu_amazon_cover") ?: s.nwebStoreCover,
            koboHorizontalLayout = c.bool("ext_kobo_horizontal_layout") ?: s.koboHorizontalLayout,
        )

        val settings = base.copy(
            shared = shared, overrides = overrides, images = images, text = text,
            npia = npiaSettings, sources = sources,
        )
        val secrets = Secrets(
            email = c.string("email")?.ifEmpty { null },
            password = c.string("wd")?.ifEmpty { null },
            loginKey = c.string("loginkey")?.ifEmpty { null },
        )
        return settings to secrets
    }

    /** Writes settings onto an existing desktop config, keeping unknown keys. Credentials are never exported. */
    fun export(s: AppSettings, existing: JsonObject = JsonObject(emptyMap())): JsonObject {
        val c = existing.toMutableMap()
        fun put(key: String, value: Any?) {
            c[key] = when (value) {
                null -> JsonNull
                is Boolean -> JsonPrimitive(value)
                is Number -> JsonPrimitive(value)
                is String -> JsonPrimitive(value)
                is List<*> -> JsonArray(value.map { JsonPrimitive(it.toString()) })
                else -> error("unsupported $value")
            }
        }
        val shared = s.shared
        val npia = s.effective(SourceId.NPIA)

        put("ext_threads", shared.threads)
        put("ext_interval", shared.intervalMin)
        put("ext_interval_max", shared.intervalMax)
        put("ext_formats", shared.formats.map { it.raw })
        put("ext_format", shared.formats.firstOrNull()?.raw ?: "epub")
        put("ext_from_enabled", shared.rangeFrom != null)
        shared.rangeFrom?.let { put("ext_from", it) }
        put("ext_to_enabled", shared.rangeTo != null)
        shared.rangeTo?.let { put("ext_to", it) }
        put("ext_use_cache", shared.useCache)
        put("ext_cache_images", shared.cacheImages)
        put("ext_number_chapters", shared.numberChapters)
        put("ext_separate_txt", shared.separateTxt)
        put("ext_skip_paid", shared.skipPaid)
        put("ext_image_workers", shared.imageWorkers)
        put("ext_generate_on_stop", shared.generateOnStop)
        put("max_retries", shared.maxRetries)
        put("naming_mode", if (shared.namingMode == NamingMode.ID) "id" else "title")
        put("append_range", shared.appendRange)

        put("thread_num", npia.threads)
        put("interval_num", npia.intervalMin)
        put("interval_max_num", npia.intervalMax)
        put("save_formats", npia.formats.map { it.raw })
        put("save_format", npia.formats.firstOrNull()?.raw ?: "epub")
        put("from_enabled", npia.rangeFrom != null)
        npia.rangeFrom?.let { put("from_num", it) }
        put("to_enabled", npia.rangeTo != null)
        npia.rangeTo?.let { put("to_num", it) }
        put("use_cache", npia.useCache)
        put("cache_images", npia.cacheImages)
        put("number_chapters", npia.numberChapters)
        put("separate_txt", npia.separateTxt)

        val i = s.images
        put("compress_images", i.compress)
        put("jpeg_quality", i.quality)
        put("image_format", i.format.raw)
        put("convert_gifs", i.convertGifs)
        put("static_only", i.staticOnly)
        put("recompress_target_enable", i.recompressTargetMb != null)
        i.recompressTargetMb?.let { put("recompress_target_mb", it) }
        put("compress_cover", i.compressCover)
        put("cover_quality", i.coverQuality)
        put("cover_format", i.coverFormat.raw)
        put("zip_compress_images", i.zipCompressImages)
        put("image_compression_workers", i.compressionWorkers)
        put("ext_long_image_layout", i.longImageLayout)
        put("save_image_urls_only", i.saveImageUrlsOnly)

        val t = s.text
        put("strip_leading_spaces", t.stripLeadingSpaces)
        put("remove_newlines", t.removeNewlines)
        put("ext_blank_paragraph_lines", t.blankParagraphLines)
        put("save_html", t.saveHtml)
        put("pdf_toc", t.pdfToc)
        put("pdf_page_numbers", t.pdfPageNumbers)
        put("pdf_counter_layout", t.pdfCounterLayout)

        put("auto_login", s.npia.autoLogin)
        put("include_notices", s.npia.includeNotices)
        put("use_browser_fallback", s.npia.useBrowserFallback)
        put("tag_age_filter", s.npia.tagAgeFilter)
        put("tag_selected_tags", s.npia.tagSelectedTags)
        put("tag_custom_tags", s.npia.tagCustomTags)
        put("tag_scrape_all", s.npia.tagScrapeAll)
        put("scrape_max_queries", s.npia.scrapeMaxQueries)

        put("ext_kpage_skip_last_page", s.sources.kpage.skipLastPage)
        put("ext_kpage_keep_filler", s.sources.kpage.keepFiller)
        put("ext_kpage_dedupe_images", s.sources.kpage.dedupeImages)
        put("ext_kpage_dedupe_leading_images", s.sources.kpage.dedupeLeadingImages)
        put("ext_ntk_npia_cover", s.sources.ntkNpiaCover)
        put("ext_syosetu_amazon_cover", s.sources.nwebStoreCover)
        put("ext_kobo_horizontal_layout", s.sources.koboHorizontalLayout)
        return JsonObject(c)
    }

    // Value coercion: the desktop file mixes ints, floats, strings, and booleans.

    private fun JsonElement?.prim(): JsonPrimitive? = (this as? JsonPrimitive)?.takeUnless { it is JsonNull }
    private fun JsonElement?.int(): Int? = prim()?.let { it.content.toIntOrNull() ?: it.doubleOrNull?.toInt() }
    private fun JsonElement?.double(): Double? = prim()?.doubleOrNull
    private fun JsonElement?.bool(): Boolean? = prim()?.takeUnless { it.isString }?.booleanOrNull
    private fun JsonObject.int(key: String) = this[key].int()
    private fun JsonObject.double(key: String) = this[key].double()
    private fun JsonObject.bool(key: String) = this[key].bool()
    private fun JsonObject.string(key: String): String? = this[key].prim()?.takeIf { it.isString }?.content
    private fun JsonObject.strings(key: String): List<String>? =
        (this[key] as? JsonArray)?.mapNotNull { it.prim()?.content }

    private fun formats(list: JsonElement?, fallback: JsonElement?): List<OutputFormat>? {
        val values = (list as? JsonArray)?.mapNotNull { it.prim()?.content?.let(OutputFormat::fromRaw) }
        if (!values.isNullOrEmpty()) return values
        return fallback.prim()?.content?.let(OutputFormat::fromRaw)?.let(::listOf)
    }

    private fun <T> differing(value: T?, shared: T): T? = value?.takeIf { it != shared }
}
