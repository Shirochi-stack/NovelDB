package com.shirochi.npia.upload

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * Persisted list of finished uploads, newest first, for the Uploads screen. Expired entries stay
 * until `pruneExpired` so the UI can show them greyed out. `entries` emits on every change.
 */
class UploadHistoryStore(val file: File, val limit: Int = 500) {
    private val lock = Any()
    private val state = MutableStateFlow(load())

    val entries: StateFlow<List<UploadResult>> = state.asStateFlow()

    val all: List<UploadResult> get() = state.value

    /** Entries whose links should still work. */
    fun active(at: Long = System.currentTimeMillis()): List<UploadResult> = all.filter { !it.isExpired(at) }

    fun add(result: UploadResult) = mutate { entries ->
        (listOf(result) + entries.filter { it.link != result.link }).take(limit)
    }

    fun remove(result: UploadResult) = mutate { entries -> entries.filter { it.link != result.link } }

    fun pruneExpired(at: Long = System.currentTimeMillis()) = mutate { entries -> entries.filter { !it.isExpired(at) } }

    fun clear() = mutate { emptyList() }

    private fun load(): List<UploadResult> =
        runCatching { json.decodeFromString(serializer, file.readText()) }.getOrDefault(emptyList())

    private fun mutate(change: (List<UploadResult>) -> List<UploadResult>) {
        synchronized(lock) {
            val next = change(state.value)
            try {
                file.absoluteFile.parentFile?.mkdirs()
                val staged = File(file.path + ".tmp")
                staged.writeText(json.encodeToString(serializer, next))
                Files.move(staged.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } catch (_: Exception) {
                // History is a convenience; a failed write must not fail the upload.
            }
            state.value = next
        }
    }

    companion object {
        /** Set by the app at startup (for example `filesDir/upload-history.json`). */
        @Volatile
        var shared: UploadHistoryStore? = null

        private val serializer = ListSerializer(UploadResult.serializer())
        private val json = Json {
            ignoreUnknownKeys = true
            prettyPrint = true
        }
    }
}
