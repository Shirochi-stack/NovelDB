package com.shirochi.npia.sources.rbooks

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** What `hard-sources.js` returns for rbooks (see android/docs/hard-sources.md). */
@Serializable
internal data class RbooksProduct(
    val title: String = "",
    val author: String = "",
    val synopsis: String = "",
    val cover: String = "",
    val publisher: String = "",
    val tags: List<String> = emptyList(),
    val links: List<String> = emptyList(),
    val titleById: Map<String, String> = emptyMap(),
    val seriesId: String = "",
    val diagnostics: List<String> = emptyList(),
)

@Serializable
internal data class RbooksLibraryProbe(
    val product: String = "",
    val unavailable: Boolean = false,
    val adult: Boolean = false,
    val title: String = "",
    val viewer: Boolean = false,
)

@Serializable
internal data class RbooksApiState(
    val json: Boolean = false,
    val title: String = "",
    val url: String = "",
    val challenge: Boolean = false,
    val text: String = "",
)

@Serializable
internal data class RbooksDiscovery(
    val done: Boolean = false,
    val stalled: Boolean = false,
    /** Opaque; handed back to continue. */
    val state: JsonElement? = null,
    val links: List<String> = emptyList(),
    val titleById: Map<String, String> = emptyMap(),
    val diagnostics: List<String> = emptyList(),
    val isSerial: Boolean? = null,
    val error: String = "",
)

@Serializable
internal data class RbooksContentState(
    val complete: Boolean = false,
    val found: Boolean = false,
    val ready: Boolean = false,
    val length: Int = 0,
    val refused: Boolean = false,
    val url: String = "",
)

@Serializable
internal data class RbooksWall(
    val wall: Boolean = false,
    val offViewer: Boolean = false,
    val refused: Boolean = false,
    val hits: Int = 0,
    val url: String = "",
)

@Serializable
internal data class RbooksImage(val url: String = "", val name: String = "")

@Serializable
internal data class RbooksExtract(
    val title: String = "",
    /** XHTML fragment; images point at the `name`s below. */
    val content: String = "",
    val contentText: String = "",
    val images: List<RbooksImage> = emptyList(),
)

@Serializable
internal data class RbooksOwned(val owned: List<String>? = null)
