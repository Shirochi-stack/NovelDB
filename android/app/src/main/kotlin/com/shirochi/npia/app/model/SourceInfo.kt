package com.shirochi.npia.app.model

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
    fun urls(text: String): List<String> = text.lines().mapNotNull(::parseUrl)

    /** URLs inside shared text (share sheet), which often wraps the link in a title or message. */
    fun extractUrls(text: String): List<String> =
        urlPattern.findAll(text).map { it.value.trimEnd('.', ',', ')', ']', '!', '?') }.distinct().toList()

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
