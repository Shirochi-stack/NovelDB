package com.shirochi.npia.sources

import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.engines.RuleEngine
import com.shirochi.npia.sources.engines.RuleEngines
import com.shirochi.npia.sources.fenrir.FenrirAdapter
import com.shirochi.npia.sources.floo.FlooAdapter
import com.shirochi.npia.sources.globalnpia.GlobalNpiaAdapter
import com.shirochi.npia.sources.jara.JaraAdapter
import com.shirochi.npia.sources.kobo.KoboAdapter
import com.shirochi.npia.sources.kpage.KpageAdapter
import com.shirochi.npia.sources.maru.MaruAdapter
import com.shirochi.npia.sources.mpia.MpiaAdapter
import com.shirochi.npia.sources.npia.NpiaAdapter
import com.shirochi.npia.sources.ntk.NtkAdapter
import com.shirochi.npia.sources.nweb.NwebAdapter
import com.shirochi.npia.sources.qdn.QdnAdapter
import com.shirochi.npia.sources.rbooks.RbooksAdapter
import com.shirochi.npia.sources.reader.CustomEngines
import com.shirochi.npia.sources.reader.PythonEngines
import com.shirochi.npia.sources.reader.BuiltInComicAdapter
import com.shirochi.npia.sources.reader.ReaderAdapter
import com.shirochi.npia.sources.reader.SiteEngineChain
import com.shirochi.npia.sources.sfc.SfcAdapter
import com.shirochi.npia.sources.xiyuwx.XiyuwxAdapter
import com.shirochi.npia.sources.yeduji.YedujiAdapter
import java.net.URI

object SourceCatalog {
    /** Checked in order; more specific matchers first (`reader` last). */
    val adapters: List<SourceAdapter> = listOf(
        NpiaAdapter(),
        GlobalNpiaAdapter(),
        MpiaAdapter(),
        KpageAdapter(),
        SfcAdapter(),
        NwebAdapter(),
        JaraAdapter(),
        RbooksAdapter(),
        NtkAdapter(),
        FlooAdapter(),
        QdnAdapter(),
        FenrirAdapter(),
        XiyuwxAdapter(),
        YedujiAdapter(),
        MaruAdapter(),
        KoboAdapter(),
        BuiltInComicAdapter(SourceId.COMIX),
        BuiltInComicAdapter(SourceId.MANGAK),
        ReaderAdapter(),
    )

    /** The adapter for a URL, for telling supported links from unsupported ones; see `resolve` for downloads. */
    fun adapterFor(url: String): SourceAdapter? = adapters.firstOrNull { it.matches(url) }

    fun require(url: String, engines: EngineSettings = EngineSettings()): SourceAdapter =
        resolve(url, engines) ?: throw SourceException.UnsupportedUrl(url)

    /**
     * The adapter that downloads `url` with the site engines of Settings > Site engines, tried in the
     * user's order until one returns chapters (the desktop's `parse_book`). Null when nothing can try it.
     *
     * - A built-in scraper that owns the URL goes first or later as the order says. The script engines
     *   (novel-downloader rules, WebToEpub) only follow it for sites they list, so a clear failure such
     *   as a sign-in is not followed by browser attempts that cannot work.
     * - A URL no built-in scraper owns (or with the scrapers switched off) tries every enabled script
     *   engine, since WebToEpub also matches some sites by their page; with dynamic priority on (the
     *   default), only the engines that list its site, then the generic fallback.
     */
    fun resolve(url: String, engines: EngineSettings = EngineSettings()): SourceAdapter? {
        val order = engines.enabled()
        val native = adapters.firstOrNull { it.id != SourceId.READER && it.matches(url) }
            ?.takeIf { SiteEngine.NATIVE in order }
        val steps = order.filter { engine ->
            when {
                engine == SiteEngine.NATIVE -> native != null
                engine == SiteEngine.CUSTOM && !CustomEngines.any() -> false  // none added
                native == null && !engines.dynamicPriority -> isWebUrl(url)
                native == null -> isWebUrl(url) && (engine == SiteEngine.GENERIC || lists(engine, url))
                else -> (engine == SiteEngine.GENERIC && native is BuiltInComicAdapter) || lists(engine, url)
            }
        }
        if (steps.isEmpty()) return null
        if (native != null && steps == listOf(SiteEngine.NATIVE)) return native
        // The Settings' order as it is, as on the desktop (gallery-dl was put before HaruNeko on manga sites
        // here only, and books it lists without pages, such as fanfox's One Piece, could not be read), except
        // on the sites the generic fallback reads best (`genericFirst`).
        return SiteEngineChain(native, if (genericFirst(url)) genericFirst(steps) else steps)
    }

    /**
     * fanfox and mangahere (one platform): a phone's web view is sent from their pages to the mobile site,
     * whose chapters say "licensed and not available". HaruNeko's chapter window follows it there and
     * gallery-dl reads those pages, so their books came without pages; the generic fallback reads the
     * desktop pages (their chapterfun.ashx answers), so it goes before them. webtoons.com likewise sends a phone
     * to m.webtoons.com; the generic fallback reads its episode list and both sites' episode pages.
     */
    // rawkuma.net: its book page lists every chapter and its chapter pages every picture (HaruNeko took one-chapter
    // books for chapters).
    private val GENERIC_FIRST = listOf("fanfox.net", "mangahere.cc", "mangahere.com", "webtoons.com", "rawkuma.net",
        "\u0062\u006f\u006f\u006b\u006b\u006f\u0072.com")

    fun genericFirst(url: String): Boolean {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return false
        val host = uri.host?.lowercase() ?: return false
        // Asura's comics (its novels are lightnovel-crawler's, which read a comic as a novel).
        val path = uri.path.orEmpty()
        if (host.removePrefix("www.") in ASURA_COMICS && (path.startsWith("/comics/") || path.startsWith("/series/"))) return true
        return GENERIC_FIRST.any { host == it || host.endsWith(".$it") }
    }
    private val ASURA_COMICS = setOf("asurascans.com", "asuracomic.net")

    private fun genericFirst(steps: List<SiteEngine>): List<SiteEngine> =
        if (SiteEngine.GENERIC !in steps) steps
        else steps.filter { it == SiteEngine.NATIVE } + SiteEngine.GENERIC + steps.filter { it != SiteEngine.NATIVE && it != SiteEngine.GENERIC }

    /** The engine's current bundle lists the URL's site. */
    fun lists(engine: SiteEngine, url: String): Boolean {
        if (engine == SiteEngine.CUSTOM) return CustomEngines.listsSite(url)
        if (engine in PythonEngines.bundled) return PythonEngines.listsBundled(engine, url)
        val rule = RuleEngine.of(engine) ?: return false
        return RuleEngines.store.current(rule)?.knows(url) == true
    }

    /** The engine's current bundle lists the URL's page, not only its site (see `EngineBundle.listsPage`). */
    fun listsPage(engine: SiteEngine, url: String): Boolean {
        val rule = RuleEngine.of(engine) ?: return false
        return RuleEngines.store.current(rule)?.listsPage(url) == true
    }

    /**
     * True when a built-in scraper or one of the switched-on script engines handles the URL's site:
     * the Catalog tab then leaves its links alone and offers a Download button on pages it can take.
     */
    fun supportedSite(url: String, engines: EngineSettings = EngineSettings()): Boolean {
        val host = runCatching { URI(url.trim()).host }.getOrNull()?.lowercase()?.takeIf { it.isNotEmpty() } ?: return false
        if (!isWebUrl(url)) return false
        if (adapters.any { it.id != SourceId.READER && it.ownsSite(host) }) return true
        return engines.enabled().any { it.runsScripts && lists(it, url) }
    }

    /**
     * True when this page, not just its site, can be downloaded, so the Catalog and Browser tabs show
     * their buttons only where a download makes sense:
     * - a built-in scraper takes the page (`matches` looks at the page) while the built-in scrapers are on;
     * - or a switched-on script engine lists the page: the novel-downloader rules by the page patterns
     *   of their header, WebToEpub by host with any path but the home page.
     * The Add screen still accepts any web link (`resolve`); this decides where to offer the shortcut.
     */
    fun downloadablePage(url: String, engines: EngineSettings = EngineSettings()): Boolean {
        if (!isWebUrl(url)) return false
        val order = engines.enabled()
        if (SiteEngine.NATIVE in order && adapters.any { it.id != SourceId.READER && it.matches(url) }) return true
        return order.any { it.runsScripts && listsPage(it, url) }
    }

    private fun isWebUrl(url: String): Boolean {
        val scheme = runCatching { URI(url.trim()).scheme }.getOrNull()?.lowercase()
        return scheme == "http" || scheme == "https"
    }

    fun adapterFor(id: SourceId): SourceAdapter = adapters.first { it.id == id }

    /** `url` in the form its source reads it under (see `SourceAdapter.canonicalUrl`); unchanged when no source owns it. */
    fun canonicalUrl(url: String): String = adapterFor(url)?.canonicalUrl(url) ?: url
}
