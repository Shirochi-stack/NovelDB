package com.shirochi.npia.sources.reader

import java.io.File
import java.net.URI

/**
 * The user's custom engines: Python files with a class `Engine` (the desktop's format, see
 * noveldb/core/engines/custom_engines.py), added in Settings > Site engines and run by the Python
 * engines' page (`PythonEngines`, scripts/python_host/noveldb_mobile.py). In the engine order they are
 * one place, [com.shirochi.npia.core.SiteEngine.CUSTOM]: a link goes to each of them that reads it.
 *
 * Their label and sites are read from the file's text (the page runs it, not this).
 */
object CustomEngines {
    /** Where the files are kept; set by the app at start (its private files folder). */
    @Volatile var folder: File? = null

    data class Info(val id: String, val label: String, val sites: List<String>, val source: String)

    /** The added engines, by file name; a file that is not an engine is left out. */
    fun list(): List<Info> {
        val dir = folder ?: return emptyList()
        return (dir.listFiles { f -> f.isFile && f.name.endsWith(".py") } ?: emptyArray())
            .sortedBy { it.name }
            .mapNotNull { file -> runCatching { info(file.nameWithoutExtension, file.readText()) }.getOrNull() }
    }

    /** True when an engine was added. */
    fun any(): Boolean = list().isNotEmpty()

    /** `url` is on one of an engine's sites (or a subdomain): dynamic priority offers it to them. */
    fun listsSite(url: String): Boolean {
        val host = runCatching { URI(url).host }.getOrNull()?.lowercase()?.removePrefix("www.") ?: return false
        return list().any { engine -> engine.sites.any { host == it || host.endsWith(".$it") } }
    }

    /** Keeps `bytes` (a file named `fileName`) as an engine; IllegalArgumentException when it is not one. */
    fun add(fileName: String, bytes: ByteArray): Info {
        val dir = folder ?: throw IllegalStateException("custom engines have no folder")
        val source = bytes.decodeToString()
        val id = fileName.substringBeforeLast('.').lowercase().replace(Regex("[^a-z0-9_]+"), "_").trim('_').ifEmpty { "engine" }
        val found = info(id, source)
        dir.mkdirs()
        File(dir, "$id.py").writeText(source)
        return found
    }

    fun remove(id: String) {
        val dir = folder ?: return
        File(dir, "$id.py").delete()
    }

    internal fun info(id: String, source: String): Info {
        val engine = Regex("""(?m)^class\s+Engine\b[^:]*:""").find(source)
            ?: throw IllegalArgumentException("it has no class Engine")
        val body = source.substring(engine.range.last + 1)
        val classBody = Regex("""(?m)^\S""").find(body)?.let { body.substring(0, it.range.first) } ?: body
        for (method in listOf("parse_book", "parse_chapter")) {
            if (!Regex("""(?m)^\s+def\s+$method\s*\(""").containsMatchIn(classBody)) {
                throw IllegalArgumentException("Engine has no $method")
            }
        }
        val label = Regex("""(?m)^\s+label\s*=\s*(['"])(.*?)\1""").find(classBody)?.groupValues?.get(2)?.trim().orEmpty()
        val sitesText = Regex("""(?ms)^\s+sites\s*=\s*[\[(](.*?)[\])]""").find(classBody)?.groupValues?.get(1)
            ?: Regex("""(?m)^\s+sites\s*=\s*(['"].*?['"])""").find(classBody)?.groupValues?.get(1).orEmpty()
        val sites = Regex("""(['"])(.*?)\1""").findAll(sitesText).map { it.groupValues[2].trim().lowercase().removePrefix("www.") }
            .filter { it.isNotEmpty() }.toList()
        return Info(id, label.ifEmpty { id }, sites, source)
    }
}
