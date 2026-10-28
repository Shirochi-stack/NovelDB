package com.shirochi.npia.app

import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.sources.SourceCatalog
import java.net.URI

/** The kinds of page the Catalog tab can show; the app decides it for the page on screen. */
enum class CatalogKind {
    /** NovelDB or NovelDB Next: outside links open in the Browser tab, cards keep their cart buttons. */
    NOVELDB,

    /** A site one of our scrapers or rules handles: outside links open in the Browser tab; downloadable pages get a button. */
    SUPPORTED,

    /** Any other site: outside links are renamed and sent to the cart when the setting is on. */
    OTHER,
}

/**
 * What the Catalog tab does for one page.
 * - `cartLinks`: outside links are renamed "Download" and go to the cart (only on [CatalogKind.OTHER], setting on).
 * - `download`: the page can be downloaded, so the floating Download button adds it to the cart.
 */
data class CatalogPageMode(val kind: CatalogKind, val cartLinks: Boolean, val download: Boolean)

fun catalogKind(url: String, engines: EngineSettings = EngineSettings()): CatalogKind = when {
    isNovelDbSite(url) -> CatalogKind.NOVELDB
    SourceCatalog.supportedSite(url, engines) -> CatalogKind.SUPPORTED
    else -> CatalogKind.OTHER
}

fun catalogPageMode(url: String, engines: EngineSettings, cartLinksSetting: Boolean): CatalogPageMode {
    val kind = catalogKind(url, engines)
    return CatalogPageMode(
        kind = kind,
        cartLinks = kind == CatalogKind.OTHER && cartLinksSetting,
        download = kind == CatalogKind.SUPPORTED && SourceCatalog.downloadablePage(url, engines),
    )
}

private val emptyFragment = Regex("#$")
private val trailingSlashes = Regex("/+(?=$|[?#])")
private val whitespace = Regex("\\s+")
private val titleSeparators = Regex("\\s+[-\u2013\u2014|\u00b7\u2022]\\s+|\\s*\\|\\s*")

/** Same page, ignoring a trailing slash before the query or fragment and an empty fragment (as the bridge does). */
fun sameAddress(a: String, b: String): Boolean {
    fun plain(url: String) = url.replace(emptyFragment, "").replace(trailingSlashes, "")
    return plain(a) == plain(b)
}

/**
 * A page title for the link cart: the title without the site's name, so "Solo Leveling - Site" is
 * saved as "Solo Leveling". Segments split by " - ", " | " and the like that name the site (or are
 * only "Home") are dropped; a title made of nothing else is kept as it is.
 */
fun cleanPageTitle(title: String, url: String): String {
    val text = title.replace(whitespace, " ").trim()
    if (text.isEmpty()) return ""
    val label = siteLabel(url)
    val parts = text.split(titleSeparators).map { it.trim() }.filter { it.isNotEmpty() }
    val kept = parts.filterNot { part ->
        (label.length >= 3 && part.contains(label, ignoreCase = true)) ||
            part.equals("home", ignoreCase = true) || part.equals("official site", ignoreCase = true)
    }
    return (if (kept.isEmpty()) text else kept.joinToString(" - ")).take(200)
}

/** `global.example.co.uk` gives `example`: the name people use for the site, for spotting it in a title. */
fun siteLabel(url: String): String {
    val host = runCatching { URI(url.trim()).host }.getOrNull()?.lowercase()?.removePrefix("www.") ?: return ""
    val labels = host.split('.').filter { it.isNotEmpty() }
    if (labels.size < 2) return ""
    val secondLevel = setOf("co", "com", "ne", "net", "or", "org", "go", "ac", "ed", "gov", "edu")
    val drop = if (labels.last().length == 2 && labels.size >= 3 && labels[labels.size - 2] in secondLevel) 2 else 1
    return labels[labels.size - 1 - drop]
}
