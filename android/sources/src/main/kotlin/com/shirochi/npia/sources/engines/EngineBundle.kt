package com.shirochi.npia.sources.engines

import com.shirochi.npia.core.SiteEngine
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.math.BigInteger
import java.net.URI

/**
 * A rule engine that runs inside a web page: the novel-downloader rules (`rules-lib.js`) or the
 * WebToEpub parsers (`webtoepub-lib.js`). Mirrors the desktop's `engine_updates.Engine`.
 */
class RuleEngine(
    val engine: SiteEngine,
    val title: String,
    /** The `window.*` contract the app's bridge speaks; an update must declare the same one. */
    val api: Int,
    /** The script's file name, in the shipped resources and in the downloads folder. */
    val lib: String,
    internal val shippedPath: String,
    /** Resource path of the shipped manifest; null when the version comes from the script's own header. */
    internal val shippedManifestPath: String?,
    /** The manifest asset in the `engines` release. */
    val releaseManifest: String,
) {
    /** The settings name, also the folder under `engines/`. */
    val name: String get() = engine.raw

    companion object {
        private const val ROOT = "/com/shirochi/npia/sources/reader/"

        val NOVEL_DOWNLOADER = RuleEngine(
            SiteEngine.NOVEL_DOWNLOADER, "novel-downloader rules", 1, "rules-lib.js",
            ROOT + "rules-lib.js", null, "novel-downloader-manifest.json",
        )
        val WEBTOEPUB = RuleEngine(
            SiteEngine.WEBTOEPUB, "WebToEpub parsers", 1, "webtoepub-lib.js",
            ROOT + "webtoepub/webtoepub-lib.js", ROOT + "webtoepub/manifest.json", "webtoepub-manifest.json",
        )
        val ALL = listOf(NOVEL_DOWNLOADER, WEBTOEPUB)

        fun of(engine: SiteEngine): RuleEngine? = ALL.firstOrNull { it.engine == engine }
    }
}

/**
 * How new a bundle is: the upstream version (its digits as numbers), then the commit date, then
 * the build time. Newer bundles sort later.
 */
data class EngineKey(val version: List<BigInteger>, val date: String, val built: String) : Comparable<EngineKey> {
    override fun compareTo(other: EngineKey): Int {
        for (i in 0 until minOf(version.size, other.version.size)) {
            val order = version[i].compareTo(other.version[i])
            if (order != 0) return order
        }
        if (version.size != other.version.size) return version.size.compareTo(other.version.size)
        val byDate = date.compareTo(other.date)
        return if (byDate != 0) byDate else built.compareTo(other.built)
    }

    companion object {
        private val digits = Regex("""\d+""")

        fun versionOf(value: String?): List<BigInteger> = digits.findAll(value.orEmpty()).map { BigInteger(it.value) }.toList()

        fun of(version: String?, date: String? = "", built: String? = "") =
            EngineKey(versionOf(version), date.orEmpty(), built.orEmpty())

        fun of(manifest: JsonObject) = of(manifest.text("version"), manifest.text("date"), manifest.text("built"))
    }
}

/** A manifest value as text: strings as they are, numbers and booleans as written, anything else empty. */
internal fun JsonObject.text(key: String): String = (this[key] as? JsonPrimitive)?.contentOrNull.orEmpty()

/** One copy of an engine's script: the one shipped with the app, or a downloaded update. */
class EngineBundle internal constructor(
    val engine: RuleEngine,
    val manifest: JsonObject,
    /** True for a downloaded update, false for the copy shipped with the app. */
    val updated: Boolean,
    private val bytes: () -> ByteArray,
) {
    /** Newer bundles sort later. */
    val key: EngineKey = EngineKey.of(manifest)

    /** Names the bundle in logs and settings, for example `2026-10-27, cc1ef36, built in`. */
    val label: String
        get() = buildList {
            if (manifest.text("version").isNotEmpty() && engine === RuleEngine.NOVEL_DOWNLOADER) add(manifest.text("version"))
            if (manifest.text("date").isNotEmpty()) add(manifest.text("date").take(10))
            if (manifest.text("commit").isNotEmpty()) add(manifest.text("commit").take(7))
            add(if (updated) "downloaded update" else "built in")
        }.joinToString(", ")

    private val text: String by lazy { bytes().decodeToString() }
    private val index: SiteIndex by lazy { SiteIndex.parse(engine, text) }

    /** The script. */
    fun read(): String = text

    private val header: UserScriptPatterns by lazy { UserScriptPatterns.parse(text) }

    /** The engine lists the URL's site (page-based WebToEpub matches need the page itself). */
    fun knows(url: String): Boolean = index.knows(engine, url)

    /**
     * The engine lists this page, not just its site. The novel-downloader rules name their pages in
     * the script's header (`@match` and `@include` patterns without any `@exclude`); WebToEpub lists
     * only hosts, so any page of a listed site counts except the site's home page.
     */
    fun listsPage(url: String): Boolean =
        if (engine === RuleEngine.WEBTOEPUB) index.knows(engine, url) && !isHomePage(url) else header.accepts(url)

    /** Hosts the engine lists, for diagnostics and tests. */
    val hosts: Set<String> get() = index.hosts
}

/**
 * The sites a bundle lists, read from its script text the way the desktop does:
 * - WebToEpub registers its parsers as `parserFactory.register("host"`. A leading `www.` counts for
 *   nothing on either side.
 * - novel-downloader's `getRule()` is one `switch` over `location.host` (the host with its port),
 *   followed by a few host patterns, just before it throws "Not Found Rule!".
 */
internal class SiteIndex(val hosts: Set<String>, private val patterns: List<Regex>) {
    fun knows(engine: RuleEngine, url: String): Boolean {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return false
        val host = uri.host?.lowercase()?.takeIf { it.isNotEmpty() } ?: return false
        if (engine === RuleEngine.WEBTOEPUB) return host.removePrefix("www.") in hosts
        // novel-downloader compares location.host, which includes a non-default port.
        val withPort = if (uri.port != -1) "$host:${uri.port}" else host
        return withPort in hosts || patterns.any { it.containsMatchIn(withPort) }
    }

    companion object {
        private val webToEpubHost = Regex("""parserFactory\.register\("([^"]+)"""")
        private val ruleHost = Regex("""case"([^"]+)":""")
        private val rulePattern = Regex("""if\((/.+?/)\.test\(e\)""")
        private const val NO_RULE = "throw new Error(\"Not Found Rule!\")"

        fun parse(engine: RuleEngine, text: String): SiteIndex {
            if (engine === RuleEngine.WEBTOEPUB) {
                val hosts = webToEpubHost.findAll(text).map { it.groupValues[1].lowercase().removePrefix("www.") }.toSet()
                return SiteIndex(hosts, emptyList())
            }
            val end = text.indexOf(NO_RULE)
            val start = if (end < 0) -1 else text.lastIndexOf("switch(", end)
            if (end < 0 || start < 0) return SiteIndex(emptySet(), emptyList())
            val region = text.substring(start, end)
            val hosts = ruleHost.findAll(region).map { unescape(it.groupValues[1]).lowercase() }.toSet()
            val patterns = rulePattern.findAll(region).mapNotNull { match ->
                val raw = match.groupValues[1]
                runCatching { Regex(raw.substring(1, raw.length - 1)) }.getOrNull()
            }.toList()
            return SiteIndex(hosts, patterns)
        }

        /** The bundle writes some hosts with `\uXXXX` escapes. */
        private fun unescape(raw: String): String {
            if ('\\' !in raw) return raw
            return runCatching { Json.parseToJsonElement("\"$raw\"").let { (it as JsonPrimitive).content } }.getOrDefault(raw)
        }
    }
}

/** True for the front page of a site: an empty path or `/`, whatever the query. */
internal fun isHomePage(url: String): Boolean {
    val path = runCatching { URI(url.trim()).rawPath }.getOrNull() ?: return false
    return path.isEmpty() || path == "/"
}
