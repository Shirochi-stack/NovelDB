package com.shirochi.npia.sources.ntk

import kotlinx.serialization.Serializable

/** What `hard-sources.js` returns for ntk (see android/docs/hard-sources.md). */
@Serializable
internal data class NtkPageState(val state: String = "waiting")

@Serializable
internal data class NtkChapterState(
    val ready: Boolean = false,
    val blocked: Boolean = false,
    val verification: Boolean = false,
)

@Serializable
internal data class NtkBookInfo(
    val ok: Boolean = false,
    val blocked: Boolean = false,
    val error: String = "",
    val kind: String = "novel",
    val title: String = "",
    val author: String = "",
    val synopsis: String = "",
    val cover: String = "",
    val tags: List<String> = emptyList(),
)

@Serializable
internal data class NtkChapterEntry(
    val url: String = "",
    val episodeId: String = "",
    val number: Int = 0,
    val name: String = "",
)

@Serializable
internal data class NtkChapterListResult(
    val ok: Boolean = false,
    val error: String = "",
    val chapters: List<NtkChapterEntry> = emptyList(),
)

@Serializable
internal data class NtkImage(val url: String = "", val name: String = "")

@Serializable
internal data class NtkChapterResult(
    val ok: Boolean = false,
    val blocked: Boolean = false,
    val stage: String = "",
    val status: Int = 0,
    val error: String = "",
    val chapterName: String = "",
    val contentText: String = "",
    /** XHTML fragment already wrapped in `<div class="ntk-content">`; webtoon images point at the `name`s. */
    val contentHtml: String = "",
    val images: List<NtkImage> = emptyList(),
)
