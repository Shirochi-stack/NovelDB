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
    @SerialName("cbz") CBZ("cbz");

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

/** Settings both desktop downloaders had, under one name. Per-source differences go in `AppSettings.overrides`. */
@Serializable
data class SharedSettings(
    val threads: Int = 4,
    val intervalMin: Double = 2.0,
    val intervalMax: Double = 5.0,
    val maxRetries: Int = 10,
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
    val namingMode: NamingMode = NamingMode.TITLE,
    val appendRange: Boolean = false,
) {
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
    val compress: Boolean = true,
    val quality: Int = 90,
    val format: ImageFormat = ImageFormat.WEBP,
    @SerialName("convertGIFs") val convertGifs: Boolean = false,
    val staticOnly: Boolean = false,
    @SerialName("recompressTargetMB") val recompressTargetMb: Double? = null,
    val compressCover: Boolean = false,
    val coverQuality: Int = 90,
    val coverFormat: ImageFormat = ImageFormat.JPEG,
    val zipCompressImages: Boolean = false,
    val compressionWorkers: Int = 4,
    val longImageLayout: Boolean = false,
    @SerialName("saveImageURLsOnly") val saveImageUrlsOnly: Boolean = false,
)

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
)

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
