package com.shirochi.npia.sources.mpia

import kotlinx.serialization.Serializable

/** What `hard-sources.js` returns for mpia (see android/docs/hard-sources.md). */
@Serializable
internal data class MpiaDetail(
    val ok: Boolean = false,
    val status: Int = 0,
    val error: String = "",
    val login: Boolean = false,
    val id: String = "",
    val title: String = "",
    val author: String = "",
    val synopsis: String = "",
    val cover: String = "",
    val tags: List<String> = emptyList(),
    val finished: Boolean = false,
    val total: Int = 0,
)

@Serializable
internal data class MpiaChapterRow(
    val id: String = "",
    val order: Int = 0,
    val title: String = "",
    val url: String = "",
    val isPaid: Boolean = false,
    val accessible: Boolean = false,
    val date: String = "",
)

@Serializable
internal data class MpiaChapterList(
    val ok: Boolean = false,
    val status: Int = 0,
    val error: String = "",
    val total: Int = 0,
    val list: List<MpiaChapterRow> = emptyList(),
)

@Serializable
internal data class MpiaImage(val url: String = "", val name: String = "")

@Serializable
internal data class MpiaExtract(
    val chapterName: String = "",
    val contentText: String = "",
    /** XHTML fragment already wrapped in `<div class="mpia-content">`; images point at the `name`s below. */
    val contentHtml: String = "",
    val images: List<MpiaImage> = emptyList(),
    val locked: Boolean = false,
    val error: String = "",
)

@Serializable
internal data class MpiaReaderState(
    val ready: Boolean = false,
    val denied: Boolean = false,
    val found: Boolean = false,
)
