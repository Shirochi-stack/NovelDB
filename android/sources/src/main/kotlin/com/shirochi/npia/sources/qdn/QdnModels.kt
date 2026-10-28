package com.shirochi.npia.sources.qdn

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** What `hard-sources.js` returns for qdn (see android/docs/hard-sources.md). */
@Serializable
internal data class QdnPageState(
    val state: String = "waiting",
    val url: String = "",
    val title: String = "",
    val text: String = "",
    val catalogCount: Int = 0,
    /** The page's layout viewport in CSS pixels (the desktop reader is read at 1280 x 800). */
    val viewport: QdnViewport = QdnViewport(),
)

@Serializable
internal data class QdnViewport(val width: Int = 0, val height: Int = 0)

@Serializable
internal data class QdnCatalogEntry(val url: String = "", val name: String = "", val isVip: Boolean = false)

@Serializable
internal data class QdnBookPage(
    val title: String = "",
    val author: String = "",
    val coverUrl: String = "",
    val description: String = "",
    val tags: List<String> = emptyList(),
    val chapters: List<QdnCatalogEntry> = emptyList(),
    val bookUrl: String = "",
    /** The chapter count the page announces; the page itself lists only the first chapters of a long book. */
    val totalChapters: Int = 0,
    /** The page that lists every chapter. */
    val catalogUrl: String = "",
)

@Serializable
internal data class QdnCatalogState(val state: String = "waiting", val count: Int = 0)

@Serializable
internal data class QdnCatalog(val chapters: List<QdnCatalogEntry> = emptyList())

@Serializable
internal data class QdnChapterState(
    val ready: Boolean = false,
    val blocked: Boolean = false,
    val verification: Boolean = false,
    val encrypted: Boolean = false,
)

@Serializable
internal data class QdnEncryptionState(
    val encrypted: Boolean = false,
    val rendered: Boolean = false,
    val failed: Boolean = false,
    val blocked: Boolean = false,
    val words: Int = 0,
)

/** A plain chapter read: `lines`, or an `error` (`locked` with a `reason`, `verification`, ...). */
@Serializable
internal data class QdnRead(
    val lines: List<String> = emptyList(),
    val error: String? = null,
    val reason: String? = null,
    val chars: Int = 0,
    val expected: Int = 0,
)

@Serializable
internal data class QdnUserInfo(val live: Boolean? = null)

@Serializable
internal data class QdnDecodeStats(
    val decoded: Int = 0,
    val verifiedGlyphs: Int = 0,
    val shapeMatchedGlyphs: Int = 0,
    val lowConfidence: Int = 0,
    val fonts: List<String> = emptyList(),
    val missingFonts: List<JsonElement> = emptyList(),
    val suspiciousPassthrough: Int = 0,
    val unstableParagraphs: List<Int> = emptyList(),
)

@Serializable
internal data class QdnDecoded(val lines: List<String> = emptyList(), val stats: QdnDecodeStats = QdnDecodeStats()) {
    /** Capture problems worth a second try: unstable paragraphs or fonts that were not available. */
    val issues: Int get() = stats.unstableParagraphs.size + stats.missingFonts.size
}

@Serializable
internal data class QdnPrepareState(val done: Boolean = false, val built: Int = 0, val total: Int = 0)
