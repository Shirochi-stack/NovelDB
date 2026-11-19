package com.shirochi.npia.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

// The JSON shape matches the iOS `AppSettings`, so a settings file moves between the apps.

@Serializable
enum class OutputFormat(val raw: String) {
    @SerialName("epub") EPUB("epub"),
    @SerialName("txt") TXT("txt"),
    @SerialName("pdf") PDF("pdf"),
    @SerialName("html") HTML("html"),
    @SerialName("cbz") CBZ("cbz"),
    @SerialName("folder") FOLDER("folder"),
    @SerialName("zip") ZIP("zip");

    val label: String get() = if (this == FOLDER) "Folder" else raw.uppercase()

    companion object {
        fun fromRaw(raw: String): OutputFormat? = entries.firstOrNull { it.raw == raw.lowercase() }
    }
}

@Serializable
enum class ImageFormat(val raw: String) {
    @SerialName("WEBP") WEBP("WEBP"),
    @SerialName("JPEG") JPEG("JPEG"),
    @SerialName("PNG") PNG("PNG"),
    @SerialName("ORIGINAL") ORIGINAL("ORIGINAL");

    companion object {
        fun fromRaw(raw: String): ImageFormat? = entries.firstOrNull { it.raw == raw.uppercase() }
    }
}

@Serializable
enum class NamingMode { @SerialName("title") TITLE, @SerialName("id") ID }

/** The request timeout and page load wait the app starts with, in seconds (Settings > Network). */
const val DEFAULT_TIMEOUT_SECONDS = 60

/** Settings both desktop downloaders had, under one name. Per-source differences go in `AppSettings.overrides`. */
@Serializable
data class SharedSettings(
    val threads: Int = 4,
    val intervalMin: Double = 2.0,
    val intervalMax: Double = 5.0,
    val maxRetries: Int = 10,
    /** How long a request waits for the server, at least (sources that need longer keep their own); 10-600 s. */
    val requestTimeoutSeconds: Int = DEFAULT_TIMEOUT_SECONDS,
    /** How long a hidden browser page may take to load, and waits for its content may last, at least; 10-600 s. */
    val pageLoadTimeoutSeconds: Int = DEFAULT_TIMEOUT_SECONDS,
    val formats: List<OutputFormat> = listOf(OutputFormat.EPUB),
    val rangeFrom: Int? = null,
    val rangeTo: Int? = null,
    val useCache: Boolean = true,
    val cacheImages: Boolean = true,
    val numberChapters: Boolean = false,
    @SerialName("separateTXT") val separateTxt: Boolean = false,
    val skipPaid: Boolean = false,
    val imageWorkers: Int = 6,
    val generateOnStop: Boolean = false,
    val chapterTiming: Boolean = true,
    val namingMode: NamingMode = NamingMode.TITLE,
    val appendRange: Boolean = false,
    /**
     * Auto purchase: paid chapters inside `purchaseFrom..purchaseTo` are unlocked with the account's
     * free tickets (a gift, a wait-for-free ticket) before they are downloaded. Never spends cash;
     * without a range nothing is purchased.
     */
    val autoPurchase: Boolean = false,
    val purchaseFrom: Int? = null,
    val purchaseTo: Int? = null,
    /**
     * A comma list typed in a range field ("4,7", "1-3,9"), kept beside the range it spans
     * (`rangeFrom..rangeTo`, `purchaseFrom..purchaseTo`); "" for a plain range.
     */
    val rangeList: String = "",
    val purchaseList: String = "",
) {
    /** True when the chapter range (and its comma list) keeps chapter `index`. */
    fun keepsChapter(index: Int): Boolean = index in chapterRange && ChapterRangeText.inList(rangeFrom, rangeTo, rangeList, index)

    /** True when auto purchase may unlock chapter `index`. */
    fun purchasesChapter(index: Int): Boolean {
        val range = purchaseRange ?: return false
        return index in range && ChapterRangeText.inList(purchaseFrom, purchaseTo, purchaseList, index)
    }

    /** The chapters auto purchase may unlock, or null when it is off or has no range. */
    val purchaseRange: IntRange?
        get() {
            if (!autoPurchase || (purchaseFrom == null && purchaseTo == null)) return null
            val from = purchaseFrom ?: 1
            return from..maxOf(purchaseTo ?: Int.MAX_VALUE, from)
        }

    /**
     * The chapters the range keeps, as `first..last` (unset ends open). A "From" after "To" is a typo,
     * so it keeps just the "From" chapter rather than nothing.
     */
    val chapterRange: IntRange
        get() {
            val from = rangeFrom ?: Int.MIN_VALUE
            val to = rangeTo ?: Int.MAX_VALUE
            return if (rangeFrom != null && rangeTo != null && rangeFrom > rangeTo) from..from else from..to
        }

    /** `_normalize_interval_range`: each end clamped to 0..300 s, swapped when max < min. */
    val intervalRange: Pair<Double, Double>
        get() {
            val low = intervalMin.coerceIn(0.0, 300.0)
            val high = intervalMax.coerceIn(0.0, 300.0)
            return if (high < low) high to low else low to high
        }

    /** One `_random_interval_delay` draw in seconds: the pause a worker takes after each chapter. */
    fun chapterDelay(): Double {
        val (low, high) = intervalRange
        if (high <= 0) return 0.0
        return if (high > low) kotlin.random.Random.nextDouble(low, high) else low
    }

    /** Sleeps one `chapterDelay`, like the desktop `_sleep_interval`. */
    suspend fun pauseBetweenChapters() {
        val seconds = chapterDelay()
        if (seconds > 0) kotlinx.coroutines.delay((seconds * 1000).toLong())
    }
}

/**
 * A chapter range typed in one field, the way a print dialog takes pages: `4` or `4-7` (desktop
 * `noveldb.core.chapter_range`, iOS `ChapterRangeText`). Text that is not a range keeps its first
 * number: `7-3` is chapter 7 and `3-` chapter 3. No number at all is no range.
 */
object ChapterRangeText {
    private val first = Regex("\\d+")
    private val second = Regex("\\s*[-\u2013\u2014~]\\s*(\\d+)")

    /** `(first, last)` of the chapters `text` names, or `(null, null)` when it names none. */
    fun parse(text: String): Pair<Int?, Int?> {
        val match = first.find(text) ?: return null to null
        val low = match.value.toIntOrNull()?.takeIf { it >= 1 } ?: return null to null
        val high = second.matchAt(text, match.range.last + 1)?.groupValues?.get(1)?.toIntOrNull() ?: low
        return low to maxOf(high, low)
    }

    /** The field's text for a stored range. An open end (older settings) shows as `4-` or `1-7`. */
    fun text(from: Int?, to: Int?): String = when {
        from == null && to == null -> ""
        from == null -> "1-$to"
        to == null -> "$from-"
        to <= from -> "$from"
        else -> "$from-$to"
    }

    /** The range as file names and logs show it: `4`, `4-7`, `4-end`; "" for no range. */
    fun label(from: Int?, to: Int?): String = when {
        from == null && to == null -> ""
        to == null -> "$from-end"
        to <= (from ?: 1) -> "${from ?: 1}"
        else -> "${from ?: 1}-$to"
    }

    /** What a typed field means: the span it names and, for separate ranges (`4,7`), their comma list (else ""). */
    data class Typed(val from: Int?, val to: Int?, val list: String)

    private val separators = Regex("[,;，、]")

    /**
     * Every range in a comma list such as `4,7` or `1-3,9`, in order, with overlapping and touching
     * ones joined. Each part is read like a single range.
     */
    fun parseList(text: String): List<IntRange> {
        val ranges = text.split(separators).mapNotNull { part ->
            val (low, high) = parse(part)
            if (low != null && high != null) low..high else null
        }.sortedWith(compareBy({ it.first }, { it.last }))
        val joined = mutableListOf<IntRange>()
        for (range in ranges) {
            val last = joined.lastOrNull()
            if (last != null && range.first <= last.last + 1) joined[joined.size - 1] = last.first..maxOf(last.last, range.last)
            else joined += range
        }
        return joined
    }

    /** `4,7` or `1-3,9` for two or more ranges; "" for one or none (a plain range needs no list). */
    fun listText(ranges: List<IntRange>): String =
        if (ranges.size < 2) "" else ranges.joinToString(",") { text(it.first, it.last) }

    fun read(text: String): Typed {
        val ranges = parseList(text)
        if (ranges.isEmpty()) return Typed(null, null, "")
        return Typed(ranges.first().first, ranges.last().last, listText(ranges))
    }

    /**
     * The ranges of `list` when it belongs to `from..to`. It is stored beside the range and is
     * stale once the range was changed without it; then it counts for nothing.
     */
    private fun listed(from: Int?, to: Int?, list: String): List<IntRange> {
        val ranges = if (list.isEmpty()) emptyList() else parseList(list)
        return if (ranges.size > 1 && ranges.first().first == from && ranges.last().last == to) ranges else emptyList()
    }

    /** True when chapter `index` is named by the comma list; a range without a list names all of its span. */
    fun inList(from: Int?, to: Int?, list: String, index: Int): Boolean {
        val ranges = listed(from, to, list)
        return ranges.isEmpty() || ranges.any { index in it }
    }

    /** The field's text for a stored range and list. */
    fun shown(from: Int?, to: Int?, list: String): String = listed(from, to, list).let { if (it.isEmpty()) text(from, to) else listText(it) }

    /** The range and list as file names and logs show them: `4`, `4-7`, `4,7`, `4-end`. */
    fun named(from: Int?, to: Int?, list: String): String = listed(from, to, list).let { if (it.isEmpty()) label(from, to) else listText(it) }
}

/** Optional replacements for `SharedSettings` fields on one source; null keeps the shared value. */
@Serializable
data class SharedOverrides(
    val threads: Int? = null,
    val intervalMin: Double? = null,
    val intervalMax: Double? = null,
    val formats: List<OutputFormat>? = null,
    val rangeFrom: Int? = null,
    val rangeTo: Int? = null,
    val useCache: Boolean? = null,
    val cacheImages: Boolean? = null,
    val numberChapters: Boolean? = null,
    @SerialName("separateTXT") val separateTxt: Boolean? = null,
) {
    val isEmpty: Boolean get() = this == SharedOverrides()

    fun applyTo(base: SharedSettings) = base.copy(
        threads = threads ?: base.threads,
        intervalMin = intervalMin ?: base.intervalMin,
        intervalMax = intervalMax ?: base.intervalMax,
        formats = formats ?: base.formats,
        rangeFrom = rangeFrom ?: base.rangeFrom,
        rangeTo = rangeTo ?: base.rangeTo,
        useCache = useCache ?: base.useCache,
        cacheImages = cacheImages ?: base.cacheImages,
        numberChapters = numberChapters ?: base.numberChapters,
        separateTxt = separateTxt ?: base.separateTxt,
    )
}

@Serializable
data class ImageSettings(
    val webtoonExport: Boolean = false,
    val stitchImages: Boolean = true,
    val stitchHeight: Int = 16_000, // Zero means Unlimited.
    val stitchChapterFolders: Boolean = false,
    val compress: Boolean = true,
    val quality: Int = 90,
    val format: ImageFormat = ImageFormat.WEBP,
    @SerialName("convertGIFs") val convertGifs: Boolean = false,
    val staticOnly: Boolean = false,
    @SerialName("recompressTargetMB") val recompressTargetMb: Double? = null,
    val compressCover: Boolean = false,
    val coverQuality: Int = 90,
    val coverFormat: ImageFormat = ImageFormat.JPEG,
    val compressionWorkers: Int = 4,
    @SerialName("saveImageURLsOnly") val saveImageUrlsOnly: Boolean = false,
) {
    val compressAfterStitch get() = webtoonExport && stitchImages && compress && !saveImageUrlsOnly
    val exportStitchHeight: Int get() {
        val height = stitchHeight.coerceIn(0, 32_000)
        val limit = if (compressAfterStitch) when (format) {
            ImageFormat.WEBP -> 16_383
            ImageFormat.JPEG -> 65_500
            else -> return height
        } else return height
        return minOf(if (height == 0) limit else height, limit)
    }
}

@Serializable
data class TextSettings(
    val stripLeadingSpaces: Boolean = false,
    val removeNewlines: Boolean = false,
    val blankParagraphLines: Boolean = false,
    @SerialName("saveHTML") val saveHtml: Boolean = false,
    @SerialName("pdfTOC") val pdfToc: Boolean = false,
    val pdfPageNumbers: Boolean = false,
    val pdfCounterLayout: Boolean = false,
)

@Serializable
data class NpiaSettings(
    val autoLogin: Boolean = false,
    val includeNotices: Boolean = true,
    val useBrowserFallback: Boolean = true,
    val tagAgeFilter: String = "15",
    val tagSelectedTags: List<String> = emptyList(),
    val tagCustomTags: String = "",
    val tagScrapeAll: Boolean = false,
    /** Query Builder groups: tags within a group are OR'd and the groups are AND'd. */
    val tagQueryGroups: List<List<String>> = emptyList(),
    /** "Search queries" of Scrape all novels, 1 to `MAX_QUERIES`. */
    val scrapeMaxQueries: Int = MAX_QUERIES,
) {
    /** `scrapeMaxQueries` limited to the number of search terms, whatever an older file stored. */
    val queryCount: Int get() = scrapeMaxQueries.coerceIn(1, MAX_QUERIES)

    companion object {
        /** The number of search terms (`NpiaSearchTerms.DEFAULT_SEARCH_QUERY_COUNT`; a test keeps them equal). */
        const val MAX_QUERIES = 165
    }
}

@Serializable
data class KpageSettings(
    val skipLastPage: Boolean = false,
    val keepFiller: Boolean = false,
    val dedupeImages: Boolean = false,
    val dedupeLeadingImages: Int = 2,
)

@Serializable
data class SourceOptions(
    val kpage: KpageSettings = KpageSettings(),
    /** ntk: use the npia cover when the novel exists there. */
    val ntkNpiaCover: Boolean = false,
    /** nweb: prefer the store cover image. */
    val nwebStoreCover: Boolean = false,
    val koboHorizontalLayout: Boolean = false,
)

@Serializable
data class UploadSettings(
    val autoUpload: Boolean = false,
    /** `Uploader.id` values, tried in order. */
    val hosts: List<String> = listOf("tempsh", "gofile"),
    @SerialName("googleDriveFolderID") val googleDriveFolderId: String? = null,
    val copyLinkToClipboard: Boolean = true,
    /** Several files in one upload are packed into ZIPs no larger than `zipMaxMb` (desktop `upload_zip_bundle`). */
    val zipBundle: Boolean = true,
    /** Largest ZIP in MB (desktop `upload_zip_max_mb`), 50 to 4096. */
    @SerialName("zipMaxMB") val zipMaxMb: Int = DEFAULT_ZIP_MAX_MB,
) {
    /** `zipMaxMb` in bytes, limited to the allowed range whatever an imported file said. */
    val zipLimitBytes: Long get() = zipMaxMb.coerceIn(MIN_ZIP_MAX_MB, MAX_ZIP_MAX_MB) * 1024L * 1024L

    companion object {
        const val DEFAULT_ZIP_MAX_MB = 1024
        const val MIN_ZIP_MAX_MB = 50
        const val MAX_ZIP_MAX_MB = 4096
    }
}

@Serializable
data class AppSettings(
    val schemaVersion: Int = 1,
    val shared: SharedSettings = SharedSettings(),
    /** Keyed by `SourceId.raw`. */
    val overrides: Map<String, SharedOverrides> = emptyMap(),
    val images: ImageSettings = ImageSettings(),
    val text: TextSettings = TextSettings(),
    val npia: NpiaSettings = NpiaSettings(),
    val sources: SourceOptions = SourceOptions(),
    /** Which site engines run, in what order, and which rule engines update themselves. */
    val engines: EngineSettings = EngineSettings(),
    val upload: UploadSettings = UploadSettings(),
    /** Checks for a newer build of the app once a day (desktop `app_update_check`); on by default. Nothing is installed without a tap. */
    val appUpdateCheck: Boolean = true,
) {
    fun effective(source: SourceId): SharedSettings = overrides[source.raw]?.applyTo(shared) ?: shared
}

/** Reads and writes `AppSettings`. Missing keys fall back to defaults, so fields can be added freely. */
object SettingsCoding {
    val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        explicitNulls = false
        prettyPrint = true
        coerceInputValues = true
    }

    fun decode(text: String): AppSettings = json.decodeFromString(AppSettings.serializer(), text)

    fun encode(settings: AppSettings): String = json.encodeToString(AppSettings.serializer(), settings)
}
