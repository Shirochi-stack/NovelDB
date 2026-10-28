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
     *   engine, since WebToEpub also matches some sites by their page.
     */
    fun resolve(url: String, engines: EngineSettings = EngineSettings()): SourceAdapter? {
        val order = engines.enabled()
        val native = adapters.firstOrNull { it.id != SourceId.READER && it.matches(url) }
            ?.takeIf { SiteEngine.NATIVE in order }
        val steps = order.filter { engine ->
            when {
                engine == SiteEngine.NATIVE -> native != null
                native == null -> isWebUrl(url)
                else -> lists(engine, url)
            }
        }
        if (steps.isEmpty()) return null
        if (native != null && steps == listOf(SiteEngine.NATIVE)) return native
        return SiteEngineChain(native, steps)
    }

    /** The engine's current bundle lists the URL's site. */
    fun lists(engine: SiteEngine, url: String): Boolean {
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
}
