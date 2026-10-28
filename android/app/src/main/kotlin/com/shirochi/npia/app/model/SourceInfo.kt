package com.shirochi.npia.app.model

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.SharedOverrides
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.sources.SourceCatalog
import java.net.URI

/** Source-specific option groups the Add screen and settings show. */
enum class SourceOptionGroup { NPIA, KPAGE, NTK_COVER, NWEB_COVER, KOBO_LAYOUT, LONG_IMAGES }

object SourceInfo {
    private val urlPattern = Regex("""https?://[^\s<>"']+""", RegexOption.IGNORE_CASE)

    fun detect(text: String): SourceId? = parseUrl(text)?.let { SourceCatalog.adapterFor(it)?.id }

    /** Adds `https://` when the scheme is missing; null unless the result has a host. */
    fun parseUrl(text: String): String? {
        val trimmed = text.trim()
        if (trimmed.isEmpty() || trimmed.any { it.isWhitespace() }) return null
        val candidate = if ("://" in trimmed) trimmed else "https://$trimmed"
        val host = runCatching { URI(candidate).host }.getOrNull()
        return if (host.isNullOrEmpty() || '.' !in host) null else candidate
    }

    /** Every non-empty line that parses as a URL. */
    fun urls(text: String): List<String> = text.lines().mapNotNull(::parseUrl).map(SourceCatalog::canonicalUrl)

    /** URLs inside shared text (share sheet), which often wraps the link in a title or message. */
    fun extractUrls(text: String): List<String> =
        urlPattern.findAll(text).map { it.value.trimEnd('.', ',', ')', ']', '!', '?') }.map(SourceCatalog::canonicalUrl).distinct().toList()

    fun groups(source: SourceId): List<SourceOptionGroup> = when (source) {
        SourceId.NPIA -> listOf(SourceOptionGroup.NPIA)
        SourceId.KPAGE -> listOf(SourceOptionGroup.KPAGE, SourceOptionGroup.LONG_IMAGES)
        SourceId.NTK -> listOf(SourceOptionGroup.NTK_COVER, SourceOptionGroup.LONG_IMAGES)
        SourceId.NWEB -> listOf(SourceOptionGroup.NWEB_COVER)
        SourceId.KOBO -> listOf(SourceOptionGroup.KOBO_LAYOUT)
        SourceId.FLOO, SourceId.RBOOKS, SourceId.QDN -> listOf(SourceOptionGroup.LONG_IMAGES)
        else -> emptyList()
    }

    fun tierLabel(source: SourceId): String = when (SourceCatalog.adapterFor(source).tier) {
        SourceTier.HTTP -> "Direct download"
        SourceTier.LIGHT_BROWSER -> "Uses a web view for sign-in"
        SourceTier.HEAVY_BROWSER -> "Renders pages in a web view (slower)"
    }
}

/** The overridable fields where `value` differs from `base`. */
fun SharedOverrides.Companion.difference(value: SharedSettings, base: SharedSettings) = SharedOverrides(
    threads = value.threads.takeIf { it != base.threads },
    intervalMin = value.intervalMin.takeIf { it != base.intervalMin },
    intervalMax = value.intervalMax.takeIf { it != base.intervalMax },
    formats = value.formats.takeIf { it != base.formats },
    rangeFrom = value.rangeFrom.takeIf { it != base.rangeFrom },
    rangeTo = value.rangeTo.takeIf { it != base.rangeTo },
    useCache = value.useCache.takeIf { it != base.useCache },
    cacheImages = value.cacheImages.takeIf { it != base.cacheImages },
    numberChapters = value.numberChapters.takeIf { it != base.numberChapters },
    separateTxt = value.separateTxt.takeIf { it != base.separateTxt },
)

/** Number of fields a source overrides. */
val SharedOverrides.count: Int
    get() = listOf(threads, intervalMin, intervalMax, formats, rangeFrom, rangeTo, useCache, cacheImages, numberChapters, separateTxt)
        .count { it != null }

/**
 * Applies an Add-tab edit of the run settings the way the desktop stores them: npia's
 * overridable fields go to `overrides["npia"]` (the main window's `thread_num`, `interval_num`...),
 * other sources write a field they already override, and everything else goes to `shared`
 * (the external downloader's `ext_*` keys). Fields with no override slot always go to `shared`.
 */
fun AppSettings.editRunSettings(source: SourceId?, change: (SharedSettings) -> SharedSettings): AppSettings {
    val current = source?.let(::effective) ?: shared
    val next = change(current)
    if (next == current) return this
    val existing = source?.let { overrides[it.raw] } ?: SharedOverrides()
    val toOverride = { has: Boolean -> source != null && (source == SourceId.NPIA || has) }
    var base = shared.copy(skipPaid = next.skipPaid, imageWorkers = next.imageWorkers, generateOnStop = next.generateOnStop)
    var o = existing
    fun route(changed: Boolean, has: Boolean, toO: () -> Unit, toShared: () -> Unit) {
        if (!changed) return
        if (toOverride(has)) toO() else toShared()
    }
    route(next.threads != current.threads, existing.threads != null, { o = o.copy(threads = next.threads) }, { base = base.copy(threads = next.threads) })
    route(next.intervalMin != current.intervalMin, existing.intervalMin != null, { o = o.copy(intervalMin = next.intervalMin) }, { base = base.copy(intervalMin = next.intervalMin) })
    route(next.intervalMax != current.intervalMax, existing.intervalMax != null, { o = o.copy(intervalMax = next.intervalMax) }, { base = base.copy(intervalMax = next.intervalMax) })
    route(next.formats != current.formats, existing.formats != null, { o = o.copy(formats = next.formats) }, { base = base.copy(formats = next.formats) })
    route(next.useCache != current.useCache, existing.useCache != null, { o = o.copy(useCache = next.useCache) }, { base = base.copy(useCache = next.useCache) })
    route(next.cacheImages != current.cacheImages, existing.cacheImages != null, { o = o.copy(cacheImages = next.cacheImages) }, { base = base.copy(cacheImages = next.cacheImages) })
    route(next.numberChapters != current.numberChapters, existing.numberChapters != null, { o = o.copy(numberChapters = next.numberChapters) }, { base = base.copy(numberChapters = next.numberChapters) })
    route(next.separateTxt != current.separateTxt, existing.separateTxt != null, { o = o.copy(separateTxt = next.separateTxt) }, { base = base.copy(separateTxt = next.separateTxt) })
    // A null range means "off", so ranges are routed by source rather than by an existing value.
    if (next.rangeFrom != current.rangeFrom || next.rangeTo != current.rangeTo) {
        if (toOverride(existing.rangeFrom != null || existing.rangeTo != null)) {
            o = o.copy(rangeFrom = next.rangeFrom, rangeTo = next.rangeTo)
        } else {
            base = base.copy(rangeFrom = next.rangeFrom, rangeTo = next.rangeTo)
        }
    }
    val nextOverrides = if (source == null) overrides else overrides.toMutableMap().apply {
        if (o.isEmpty) remove(source.raw) else put(source.raw, o)
    }
    return copy(shared = base, overrides = nextOverrides)
}
