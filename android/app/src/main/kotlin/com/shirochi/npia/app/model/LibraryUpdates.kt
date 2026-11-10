package com.shirochi.npia.app.model

import kotlinx.serialization.Serializable
import com.shirochi.npia.core.SourceId
import java.io.File
import java.net.URI
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.zip.ZipFile

@Serializable
data class LibraryUpdateEntry(val url: String = "", val ranged: Boolean = true, val autoUpdate: Boolean? = null) {
    val enabled: Boolean get() = url.isNotBlank() && (autoUpdate ?: !ranged)
}

@Serializable
data class LibraryUpdateState(
    val enabled: Boolean = true,
    val background: Boolean = false,
    val uploadDrive: Boolean = false,
    val books: Map<String, LibraryUpdateEntry> = emptyMap(),
)

fun librarySourceKey(url: String, source: SourceId? = null): String {
    if (source != null && source != SourceId.READER) return source.raw
    return runCatching {
        URI(url).host.orEmpty().lowercase().split('.').takeLast(2).joinToString(".")
    }.getOrDefault(url)
}

fun replaceLibraryEpub(candidate: File, target: File) {
    fun chapters(file: File): Int = ZipFile(file).use { zip ->
        val container = zip.getInputStream(zip.getEntry("META-INF/container.xml")).reader().readText()
        val opf = Regex("full-path=[\"']([^\"']+)").find(container)?.groupValues?.get(1) ?: error("Invalid EPUB container")
        val text = zip.getInputStream(zip.getEntry(opf)).reader().readText()
        Regex("<(?:\\w+:)?itemref\\b").findAll(text).count()
    }
    val count = chapters(candidate)
    require(count > 0 && count >= chapters(target)) { "The refreshed EPUB has fewer chapters than the saved book" }
    Files.move(candidate.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
}
