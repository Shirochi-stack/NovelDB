package com.shirochi.npia.sources.reader

import java.io.File
import java.io.InputStream
import java.net.URI
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.zip.ZipFile
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

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

    data class Info(val id: String, val label: String, val sites: List<String>, val source: String, val bundled: Boolean = false)

    fun readImport(input: InputStream): ByteArray = readBounded(input, 32 shl 20)

    private fun readBounded(input: InputStream, limit: Int): ByteArray {
        val output = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            require(output.size() <= limit - count) { "engine file exceeds its size limit" }
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }

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
        dir.mkdirs()
        val found = if (fileName.endsWith(".zip", ignoreCase = true)) {
            require(bytes.size <= (32 shl 20)) { "engine ZIP exceeds 32 MB" }
            val file = File.createTempFile("engine-", ".zip", dir)
            try {
                file.writeBytes(bytes)
                readArchive(file)
            } finally {
                file.delete()
            }
        } else {
            require(fileName.endsWith(".py", ignoreCase = true)) { "choose a Python engine or a ZIP" }
            require(bytes.size <= (2 shl 20)) { "Python engine exceeds 2 MB" }
            info(identifier(fileName), bytes.decodeToString(throwOnInvalidSequence = true).removePrefix("\uFEFF"))
        }
        if (found.bundled) return found
        val temporary = File.createTempFile("engine-", ".tmp", dir)
        try {
            temporary.writeText(found.source)
            Files.move(temporary.toPath(), File(dir, found.id + ".py").toPath(),
                StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally {
            temporary.delete()
        }
        return found
    }

    private fun identifier(name: String): String = name.replace('\\', '/').substringAfterLast('/').substringBeforeLast('.')
        .lowercase().replace(Regex("[^a-z0-9_]+"), "_").trim('_').ifEmpty { "engine" }

    private fun readArchive(file: File): Info = ZipFile(file).use { zip ->
        val members = zip.entries().asSequence().take(257).toList()
        val files = members.filterNot { it.isDirectory }
        require(members.size <= 256 && files.map { it.name }.distinct().size == files.size) {
            "engine ZIP has too many or duplicate entries"
        }
        fun entry(name: String, limit: Int): String? {
            val member = zip.getEntry(name) ?: return null
            require(member.size in 0..limit.toLong()) { "engine ZIP entry exceeds its size limit" }
            return zip.getInputStream(member).use { readBounded(it, limit).decodeToString(throwOnInvalidSequence = true) }
        }
        entry("manifest.json", 64 shl 10)?.let { source ->
            val manifest = Json.parseToJsonElement(source).jsonObject
            var name = manifest["name"]?.jsonPrimitive?.content
            if (name == "__MSG_extName__") {
                name = entry("_locales/en/messages.json", 64 shl 10)?.let {
                    Json.parseToJsonElement(it).jsonObject["extName"]?.jsonObject?.get("message")?.jsonPrimitive?.content
                }
            }
            if (manifest["manifest_version"]?.jsonPrimitive?.content == "3" && name == "Tako Manga Downloader" &&
                listOf("background.js", "offscreen.html", "sidepanel.html").all { zip.getEntry(it)?.isDirectory == false }
            ) return@use Info("tako", "Tako readers", emptyList(), "", bundled = true)
            require(manifest["manifest_version"] == null) { "this Chrome extension has no NovelDB adapter" }
        }
        val candidates = files.filter { it.name.endsWith(".py", ignoreCase = true) && it.size in 0..(2L shl 20) }
            .mapNotNull { runCatching { info(identifier(it.name), entry(it.name, 2 shl 20).orEmpty().removePrefix("\uFEFF")) }.getOrNull() }
        require(candidates.size == 1) { "engine ZIP must contain exactly one Python file with class Engine" }
        candidates.single()
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
