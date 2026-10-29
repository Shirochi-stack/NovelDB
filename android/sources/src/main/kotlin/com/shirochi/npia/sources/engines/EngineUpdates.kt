package com.shirochi.npia.sources.engines

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import java.io.File

/** Downloads a file for an update check: the bytes of `url`, or an exception (including for an HTTP error status). */
fun interface EngineFetch {
    suspend fun get(url: String, timeoutSeconds: Long): ByteArray
}

data class UpdateResult(val engine: RuleEngine, val status: Status, val message: String, val bundle: EngineBundle? = null) {
    enum class Status { UPDATED, CURRENT, FAILED }
}

/**
 * Opt-in updates of the rule engines, from the `engines` release of this project's repository.
 * A daily workflow there rebuilds each engine from its official upstream repository after checking
 * that it loads. A port of the desktop's `engine_updates.check_for_update` and `check_if_due`.
 *
 * An update is accepted only when its manifest names this engine, speaks this app's API, was not
 * rejected before, is not a rebuild of the bundle in use (same upstream commit and build patch),
 * is newer than the bundle in use, names a plain file, and the downloaded file matches the
 * manifest's size and SHA-256.
 */
class EngineUpdates(
    private val store: EngineStore,
    private val releaseUrl: String = RELEASE_URL,
    /** Milliseconds since the epoch. */
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val mutex = Mutex()

    /** Downloads the release's build of `engine` when it is newer than the one in use. Never throws except on cancellation. */
    suspend fun check(engine: RuleEngine, fetch: EngineFetch, timeoutSeconds: Long = 0): UpdateResult = mutex.withLock {
        val title = engine.title
        val state = store.state(engine).toMutableMap()
        state["checked"] = JsonPrimitive(clock() / 1000.0)

        fun done(status: UpdateResult.Status, message: String, bundle: EngineBundle? = null): UpdateResult {
            store.saveState(engine, JsonObject(state))
            return UpdateResult(engine, status, message, bundle)
        }

        val remote = try {
            Json.parseToJsonElement(fetch.get(releaseUrl + engine.releaseManifest, timeoutSeconds).decodeToString()) as? JsonObject
                ?: throw IllegalArgumentException("the manifest is not an object")
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return done(UpdateResult.Status.FAILED, "Could not check for $title updates: ${e.message ?: e}")
        }
        val inUse = store.current(engine)
        if (remote["engine"] != null && remote.text("engine") != engine.name) {
            return done(UpdateResult.Status.FAILED, "The $title update manifest is for another engine.")
        }
        if ((remote["api"] as? JsonPrimitive)?.doubleOrNull != engine.api.toDouble()) {
            return done(UpdateResult.Status.CURRENT, "The newest $title need a newer NovelDB.", inUse)
        }
        if (EngineStore.releaseKey(remote) in store.rejectedReleases(engine)) {
            return done(UpdateResult.Status.CURRENT, "The newest $title failed to load before; keeping these.", inUse)
        }
        if (inUse != null && inUse.manifest.text("commit").isNotEmpty() && EngineStore.releaseKey(remote) == EngineStore.releaseKey(inUse.manifest)) {
            // The same upstream commit and build patch: a rebuild of what is in use, only the build time differs.
            return done(UpdateResult.Status.CURRENT, "The $title are up to date (${inUse.label}).", inUse)
        }
        if (inUse != null && EngineKey.of(remote) <= inUse.key) {
            return done(UpdateResult.Status.CURRENT, "The $title are up to date (${inUse.label}).", inUse)
        }
        val name = remote.text("file")
        if (name.isEmpty() || '/' in name || '\\' in name || ".." in name) {
            return done(UpdateResult.Status.FAILED, "The $title update manifest names no valid file.")
        }
        val data = try {
            fetch.get(releaseUrl + name, maxOf(timeoutSeconds, 60))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return done(UpdateResult.Status.FAILED, "Could not download the $title update: ${e.message ?: e}")
        }
        val expectedSize = (remote["size"] as? JsonPrimitive)?.longOrNull
        if (!EngineStore.sha256(data).equals(remote.text("sha256"), ignoreCase = true) || data.size.toLong() != expectedSize) {
            return done(UpdateResult.Status.FAILED, "The downloaded $title update did not match its checksum.")
        }
        val folder = store.directory(engine)
            ?: return done(UpdateResult.Status.FAILED, "This app has nowhere to keep $title updates.")
        val bundle = try {
            folder.mkdirs()
            EngineStore.writeAtomically(File(folder, engine.lib), data)
            // The file is renamed to the lib name; the manifest follows it so a half-written pair never validates.
            val manifest = JsonObject(remote + ("file" to JsonPrimitive(engine.lib)))
            EngineStore.writeAtomically(File(folder, EngineStore.MANIFEST), pretty.encodeToString(JsonObject.serializer(), manifest).toByteArray())
            store.downloaded(engine)
        } catch (e: Exception) {
            return done(UpdateResult.Status.FAILED, "Could not save the $title update: ${e.message ?: e}")
        }
        if (bundle == null) return done(UpdateResult.Status.FAILED, "The saved $title update did not validate.")
        done(UpdateResult.Status.UPDATED, "Updated the $title to ${bundle.label}.", bundle)
    }

    /**
     * The automatic check: at most once per `everyMillis`. Logs only news and failures. Returns
     * null when no check was due.
     */
    suspend fun checkIfDue(
        engine: RuleEngine,
        fetch: EngineFetch,
        log: (String) -> Unit = {},
        everyMillis: Long = CHECK_EVERY_MILLIS,
    ): UpdateResult? {
        if (clock() - (store.lastChecked(engine) * 1000).toLong() < everyMillis) return null
        val result = check(engine, fetch)
        if (result.status != UpdateResult.Status.CURRENT) log(result.message)
        return result
    }

    companion object {
        const val RELEASE_URL = "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/"
        const val CHECK_EVERY_MILLIS = 24L * 3600 * 1000
        private val pretty = Json { prettyPrint = true }
    }
}
