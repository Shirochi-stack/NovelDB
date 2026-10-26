package com.shirochi.npia.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Source codenames. Serial names match the desktop scraper's method prefixes and the iOS app. */
@Serializable
enum class SourceId(val raw: String) {
    @SerialName("npia") NPIA("npia"),
    @SerialName("global_npia") GLOBAL_NPIA("global_npia"),
    @SerialName("mpia") MPIA("mpia"),
    @SerialName("kpage") KPAGE("kpage"),
    @SerialName("sfc") SFC("sfc"),
    @SerialName("nweb") NWEB("nweb"),
    @SerialName("jara") JARA("jara"),
    @SerialName("rbooks") RBOOKS("rbooks"),
    @SerialName("ntk") NTK("ntk"),
    @SerialName("floo") FLOO("floo"),
    @SerialName("qdn") QDN("qdn"),
    @SerialName("fenrir") FENRIR("fenrir"),
    @SerialName("xiyuwx") XIYUWX("xiyuwx"),
    @SerialName("yeduji") YEDUJI("yeduji"),
    @SerialName("maru") MARU("maru"),
    @SerialName("kobo") KOBO("kobo"),
    /** Generic sites driven by `rules-lib.js`. */
    @SerialName("reader") READER("reader");

    companion object {
        fun fromRaw(raw: String): SourceId? = entries.firstOrNull { it.raw == raw }
    }
}

/** How much browser automation a source needs. */
enum class SourceTier { HTTP, LIGHT_BROWSER, HEAVY_BROWSER }

@Serializable
data class ChapterRef(
    val id: String,
    /** 1-based position in reading order; used for ranges and numbering. */
    val index: Int,
    val title: String,
    val url: String? = null,
    val isPaid: Boolean = false,
    val isNotice: Boolean = false,
)

@Serializable
data class BookInfo(
    val source: SourceId,
    val id: String,
    val url: String,
    val title: String,
    val author: String? = null,
    val synopsis: String? = null,
    val coverUrl: String? = null,
    val tags: List<String> = emptyList(),
    val status: String? = null,
    val language: String = "ko",
    val chapters: List<ChapterRef> = emptyList(),
    val notices: List<ChapterRef> = emptyList(),
)

@Serializable
data class ImageRef(
    val url: String,
    /** File name inside the output (e.g. `images/0001_03.webp`). */
    val filename: String,
    /** Bytes when the source already holds them (decrypted or rendered images). */
    @kotlinx.serialization.Transient val data: ByteArray? = null,
) {
    override fun equals(other: Any?): Boolean =
        other is ImageRef && other.url == url && other.filename == filename && other.data.contentEqualsNullable(data)

    override fun hashCode(): Int = 31 * url.hashCode() + filename.hashCode()
}

private fun ByteArray?.contentEqualsNullable(other: ByteArray?): Boolean =
    if (this == null || other == null) this === other else contentEquals(other)

@Serializable
data class ChapterContent(
    val ref: ChapterRef,
    /** XHTML body fragment; images reference `ImageRef.filename`. */
    val html: String,
    val images: List<ImageRef> = emptyList(),
)

sealed class ChapterResult {
    abstract val ref: ChapterRef

    data class Content(val content: ChapterContent) : ChapterResult() {
        override val ref: ChapterRef get() = content.ref
    }

    /** The account cannot read this chapter (unpaid, age gate, region). */
    data class Refused(override val ref: ChapterRef, val reason: String) : ChapterResult()

    data class Failed(override val ref: ChapterRef, val message: String) : ChapterResult()
}

sealed class SourceException(message: String) : Exception(message) {
    class NotImplemented(val source: SourceId) : SourceException("${source.raw} is not available in this build yet.")
    class UnsupportedUrl(val url: String) : SourceException("No source recognises $url.")
    class LoginRequired(val source: SourceId) : SourceException("Sign in to ${source.raw} first.")
    class Blocked(detail: String) : SourceException("Blocked: $detail")
    class Parse(detail: String) : SourceException("Could not read the page: $detail")
}
