package com.shirochi.npia.core

import kotlinx.serialization.Serializable

/**
 * The ways a book page can be read, tried in the user's order until one returns chapters (the
 * desktop's `SITE_ENGINES`, in its order): the app's own scrapers, the novel-downloader rules, the
 * WebToEpub parsers, lightnovel-crawler's sources, the LNReader plugins, FanFicFare's adapters, the
 * HaruNeko websites, gallery-dl's extractors, the user's custom engines, then the generic fallback
 * (any site, without site rules). The three Python engines and the custom engines run in Pyodide
 * (`PythonEngines`). `raw` is the name the desktop settings file uses (the desktop lists each custom
 * engine on its own, as `custom:<id>`; here they are one place in the order).
 */
enum class SiteEngine(val raw: String, val label: String) {
    NATIVE("native", "Built-in scrapers"),
    NOVEL_DOWNLOADER("novel_downloader", "novel-downloader rules"),
    WEBTOEPUB("webtoepub", "WebToEpub parsers"),
    LNCRAWL("lncrawl", "lightnovel-crawler sources"),
    LNREADER("lnreader", "LNReader plugins"),
    FANFICFARE("fanficfare", "FanFicFare adapters"),
    HARUNEKO("haruneko", "HaruNeko websites"),
    GALLERYDL("gallerydl", "gallery-dl extractors"),
    CUSTOM("custom", "Custom engines"),
    GENERIC("generic", "Generic fallback");

    /** True for the engines that run a JavaScript bundle (everything but the built-in scrapers). */
    val runsScripts: Boolean get() = this != NATIVE

    companion object {
        val DEFAULT_ORDER: List<SiteEngine> = entries.toList()

        fun fromRaw(raw: String): SiteEngine? = entries.firstOrNull { it.raw == raw }

        /**
         * Known engines in the given order (missing ones appended; the custom engines, when the order
         * predates them, just before the generic fallback), minus the switched-off ones.
         */
        fun order(order: Iterable<String>?, disabled: Iterable<String>? = null): List<SiteEngine> {
            val off = disabled?.toSet().orEmpty()
            val given = (order ?: emptyList()).mapNotNull(::fromRaw)
            val all = (given + entries).distinct().toMutableList()
            if (CUSTOM !in given) {
                all.remove(CUSTOM)
                all.add(all.indexOf(GENERIC).takeIf { it >= 0 } ?: all.size, CUSTOM)
            }
            return all.filter { it.raw !in off }
        }
    }
}

/** Which site engines run and in what order, and which rule engines update themselves (opt in). */
@Serializable
data class EngineSettings(
    /** Engine names (`SiteEngine.raw`), first tried first. Unknown names are ignored and missing ones come last. */
    val order: List<String> = SiteEngine.DEFAULT_ORDER.map { it.raw },
    /** Engines switched off. */
    val disabled: List<String> = emptyList(),
    val novelDownloaderAutoUpdate: Boolean = true,
    val webtoepubAutoUpdate: Boolean = true,
    val lnreaderAutoUpdate: Boolean = true,
    val harunekoAutoUpdate: Boolean = true,
    val lncrawlAutoUpdate: Boolean = true,
    val fanficfareAutoUpdate: Boolean = true,
    val gallerydlAutoUpdate: Boolean = true,
    /**
     * Dynamic priority (the desktop's): a link no built-in scraper owns goes only to the engines that
     * list its site, then the generic fallback, instead of to every engine that is on.
     */
    val dynamicPriority: Boolean = true,
) {
    /** Every engine in the user's order, switched off or not. */
    fun fullOrder(): List<SiteEngine> = SiteEngine.order(order)

    /** The engines to try, in order. Never empty: if every engine is switched off, none are. */
    fun enabled(): List<SiteEngine> = SiteEngine.order(order, disabled).ifEmpty { fullOrder() }

    fun isEnabled(engine: SiteEngine): Boolean = engine in enabled()

    fun autoUpdate(engine: SiteEngine): Boolean = when (engine) {
        SiteEngine.NOVEL_DOWNLOADER -> novelDownloaderAutoUpdate
        SiteEngine.WEBTOEPUB -> webtoepubAutoUpdate
        SiteEngine.LNREADER -> lnreaderAutoUpdate
        SiteEngine.HARUNEKO -> harunekoAutoUpdate
        SiteEngine.LNCRAWL -> lncrawlAutoUpdate
        SiteEngine.FANFICFARE -> fanficfareAutoUpdate
        SiteEngine.GALLERYDL -> gallerydlAutoUpdate
        SiteEngine.NATIVE, SiteEngine.CUSTOM, SiteEngine.GENERIC -> false
    }

    fun withAutoUpdate(engine: SiteEngine, on: Boolean): EngineSettings = when (engine) {
        SiteEngine.NOVEL_DOWNLOADER -> copy(novelDownloaderAutoUpdate = on)
        SiteEngine.WEBTOEPUB -> copy(webtoepubAutoUpdate = on)
        SiteEngine.LNREADER -> copy(lnreaderAutoUpdate = on)
        SiteEngine.HARUNEKO -> copy(harunekoAutoUpdate = on)
        SiteEngine.LNCRAWL -> copy(lncrawlAutoUpdate = on)
        SiteEngine.FANFICFARE -> copy(fanficfareAutoUpdate = on)
        SiteEngine.GALLERYDL -> copy(gallerydlAutoUpdate = on)
        SiteEngine.NATIVE, SiteEngine.CUSTOM, SiteEngine.GENERIC -> this
    }

    /** Switches `engine` on or off. The last engine that is on cannot be switched off. */
    fun withEnabled(engine: SiteEngine, on: Boolean): EngineSettings {
        val off = if (on) disabled.filter { it != engine.raw } else (disabled + engine.raw).distinct()
        val next = copy(order = fullOrder().map { it.raw }, disabled = off.filter { SiteEngine.fromRaw(it) != null })
        return if (SiteEngine.order(next.order, next.disabled).isEmpty()) this else next
    }

    /** `engine` moved `by` places (negative is earlier) in the full order. */
    fun moved(engine: SiteEngine, by: Int): EngineSettings {
        val list = fullOrder().toMutableList()
        val from = list.indexOf(engine)
        val to = (from + by).coerceIn(0, list.lastIndex)
        if (from == to) return this
        list.add(to, list.removeAt(from))
        return copy(order = list.map { it.raw })
    }

    val isDefaultOrder: Boolean get() = fullOrder() == SiteEngine.DEFAULT_ORDER

    /** The default order with every engine on; the update choices stay as they are. */
    fun resetOrder(): EngineSettings = copy(order = SiteEngine.DEFAULT_ORDER.map { it.raw }, disabled = emptyList())
}
