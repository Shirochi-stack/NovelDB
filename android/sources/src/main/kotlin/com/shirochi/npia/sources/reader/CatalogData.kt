package com.shirochi.npia.sources.reader

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*
import java.io.File
import java.net.URL
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.Base64
import java.util.zip.ZipFile

/** Optional catalogue snapshots, installed atomically outside the application package. */
object CatalogData {
    @Volatile var directory: File? = null
    private const val base = "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var busy = false
    private var progress = 0.0
    private var failure = ""
    private val archives = mutableMapOf<String, ZipFile>()
    private val revisionPattern = Regex("[A-Za-z0-9_-]{1,64}")

    private fun installed(): JsonObject = runCatching {
        Json.parseToJsonElement(File(directory, "installed.json").readText()).jsonObject
    }.getOrDefault(buildJsonObject {})

    @Synchronized private fun status(): JsonObject {
        val saved = installed()
        return buildJsonObject {
            put("busy", busy)
            put("progress", progress)
            put("error", failure)
            put("version", saved["revision"]?.jsonPrimitive?.content.orEmpty())
            put("built", saved["built"]?.jsonPrimitive?.content.orEmpty())
            put("books", saved["books"]?.jsonPrimitive?.intOrNull ?: 0)
        }
    }

    @Synchronized fun handle(details: JsonObject): String {
        val root = directory ?: return buildJsonObject { put("error", "Catalogue storage unavailable") }.toString()
        when (details["action"]?.jsonPrimitive?.content) {
            "download" -> if (!busy) {
                busy = true
                progress = 0.0
                failure = ""
                scope.launch { download(root) }
            }
            "remove" -> if (!busy) {
                archives.values.forEach { it.close() }
                archives.clear()
                root.deleteRecursively()
                progress = 0.0
                failure = ""
            }
            "read" -> return runCatching {
                val revision = details["version"]?.jsonPrimitive?.content.orEmpty()
                val key = details["key"]?.jsonPrimitive?.content.orEmpty()
                require(revisionPattern.matches(revision) && Regex("manifest|top|[bc]/[0-9]{1,6}").matches(key))
                val part = key.substringAfter('/', "0").toInt() % 2
                val archive = File(File(root, revision), "instant-catalog-$part.zip")
                val zip = archives.getOrPut("$revision/$part") { ZipFile(archive) }
                val bytes = zip.getInputStream(zip.getEntry("$key.json.gz")).use { it.readBytes() }
                buildJsonObject { put("base64", Base64.getEncoder().encodeToString(bytes)) }.toString()
            }.getOrElse { "{}" }
        }
        return status().toString()
    }

    private fun connection(name: String) = URL(base + name).openConnection().apply {
        connectTimeout = 60_000
        readTimeout = 60_000
    }

    private fun download(root: File) {
        try {
            val manifest = connection("noveldb-local.json").getInputStream().bufferedReader().use {
                Json.parseToJsonElement(it.readText()).jsonObject
            }
            val revision = manifest.getValue("revision").jsonPrimitive.content
            val files = manifest.getValue("files").jsonArray
            require(revisionPattern.matches(revision) && files.size == 2) { "Invalid catalogue manifest" }
            val previous = installed()["revision"]?.jsonPrimitive?.content
            if (previous == revision) return
            val target = File(root, revision).apply { mkdirs() }
            files.forEachIndexed { part, value ->
                val entry = value.jsonObject
                val name = entry.getValue("name").jsonPrimitive.content
                require(Regex("noveldb-local-[A-Za-z0-9_-]+-[01]\\.zip").matches(name))
                val expected = entry.getValue("size").jsonPrimitive.long
                val pending = File(target, "part-$part.tmp")
                val digest = MessageDigest.getInstance("SHA-256")
                var size = 0L
                connection(name).getInputStream().use { input ->
                    pending.outputStream().use { output ->
                        val buffer = ByteArray(1024 * 1024)
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            output.write(buffer, 0, count)
                            digest.update(buffer, 0, count)
                            size += count
                            synchronized(this) { progress = (part + (size.toDouble() / expected.coerceAtLeast(1)).coerceAtMost(1.0)) / 2 }
                        }
                    }
                }
                val hash = digest.digest().joinToString("") { "%02x".format(it) }
                require(size == expected && hash == entry.getValue("sha256").jsonPrimitive.content) { "Catalogue download failed its checksum" }
                ZipFile(pending).use { require(it.size() > 0) }
                Files.move(pending.toPath(), File(target, "instant-catalog-$part.zip").toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
            val pending = File(root, "installed.tmp").apply { writeText(manifest.toString()) }
            Files.move(pending.toPath(), File(root, "installed.json").toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            synchronized(this) {
                val expired = archives.keys.filter { !it.startsWith("$revision/") && (previous == null || !it.startsWith("$previous/")) }
                expired.forEach { archives.remove(it)?.close() }
            }
            root.listFiles()?.filter { it.isDirectory && it.name != revision && it.name != previous && revisionPattern.matches(it.name) }
                ?.forEach { it.deleteRecursively() }
        } catch (error: Exception) {
            synchronized(this) { failure = error.message ?: "Catalogue download failed" }
        } finally {
            synchronized(this) { busy = false }
        }
    }
}
