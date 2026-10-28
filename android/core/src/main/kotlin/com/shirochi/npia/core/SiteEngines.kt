package com.shirochi.npia.core

import kotlinx.serialization.Serializable

/**
 * The ways a book page can be read, tried in the user's order until one returns chapters (the
 * desktop's `SITE_ENGINES`): the app's own scrapers, the novel-downloader rules, then the
 * WebToEpub parsers. `raw` is the name the desktop settings file uses.
 */
enum class SiteEngine(val raw: String, val label: String) {
    NATIVE("native", "Built-in scrapers"),
    NOVEL_DOWNLOADER("novel_downloader", "novel-downloader rules"),
    WEBTOEPUB("webtoepub", "WebToEpub parsers");

    /** True for the engines that run a JavaScript bundle (everything but the built-in scrapers). */
    val runsScripts: Boolean get() = this != NATIVE

    companion object {
        val DEFAULT_ORDER: List<SiteEngine> = entries.toList()

        fun fromRaw(raw: String): SiteEngine? = entries.firstOrNull { it.raw == raw }

        /** Known engines in the given order (missing ones appended), minus the switched-off ones. */
        fun order(order: Iterable<String>?, disabled: Iterable<String>? = null): List<SiteEngine> {
            val off = disabled?.toSet().orEmpty()
            return ((order ?: emptyList()).mapNotNull(::fromRaw) + entries).distinct().filter { it.raw !in off }
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
) {
    /** Every engine in the user's order, switched off or not. */
    fun fullOrder(): List<SiteEngine> = SiteEngine.order(order)

    /** The engines to try, in order. Never empty: if every engine is switched off, none are. */
    fun enabled(): List<SiteEngine> = SiteEngine.order(order, disabled).ifEmpty { fullOrder() }

    fun isEnabled(engine: SiteEngine): Boolean = engine in enabled()

    fun autoUpdate(engine: SiteEngine): Boolean = when (engine) {
        SiteEngine.NOVEL_DOWNLOADER -> novelDownloaderAutoUpdate
        SiteEngine.WEBTOEPUB -> webtoepubAutoUpdate
        SiteEngine.NATIVE -> false
    }

    fun withAutoUpdate(engine: SiteEngine, on: Boolean): EngineSettings = when (engine) {
        SiteEngine.NOVEL_DOWNLOADER -> copy(novelDownloaderAutoUpdate = on)
        SiteEngine.WEBTOEPUB -> copy(webtoepubAutoUpdate = on)
        SiteEngine.NATIVE -> this
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
