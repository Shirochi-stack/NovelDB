package com.shirochi.npia.sources.engines

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import java.io.File
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

/** Where the copies shipped with the app live (the classpath resources of `:sources`; a map in tests). */
interface ShippedFiles {
    fun read(path: String): ByteArray?

    fun exists(path: String): Boolean = read(path) != null
}

internal object ClasspathFiles : ShippedFiles {
    override fun read(path: String): ByteArray? = ClasspathFiles::class.java.getResourceAsStream(path)?.use { it.readBytes() }

    override fun exists(path: String): Boolean = ClasspathFiles::class.java.getResource(path) != null
}

/**
 * The engine bundles: the copies shipped with the app, and the updates downloaded into
 * `<root>/<engine>/` (the app's files folder, `engines/`). A port of the desktop's
 * `engine_updates` module without the network part, which is `EngineUpdates`.
 *
 * A downloaded update is used only when its manifest matches the engine's API, its checksum
 * matches the manifest and it is newer than the shipped copy. If it then fails to load in a page,
 * `reject` sets it aside; that build is never downloaded again.
 *
 * With no `root`, nothing is downloaded and the shipped copies are always used.
 */
class EngineStore(private val root: File? = null, private val shippedFiles: ShippedFiles = ClasspathFiles) {
    private val lock = Any()
    private val shippedCache = HashMap<String, EngineBundle>()
    private val downloadedCache = HashMap<String, Pair<List<Long>, EngineBundle?>>()

    /** The folder an engine's download and state live in, or null when this store does not download. */
    fun directory(engine: RuleEngine): File? = root?.let { File(it, engine.name) }

    /** The copy shipped with the app, or null when this build lacks it. */
    fun shipped(engine: RuleEngine): EngineBundle? = synchronized(lock) {
        shippedCache[engine.name]?.let { return it }
        if (!shippedFiles.exists(engine.shippedPath)) return null
        val bundle = if (engine.shippedManifestPath != null) {
            val manifest = shippedFiles.read(engine.shippedManifestPath)?.let(::parseObject)
            if (manifest == null || !hasApi(manifest, engine)) return null
            EngineBundle(engine, manifest, updated = false) { shippedFiles.read(engine.shippedPath) ?: ByteArray(0) }
        } else {
            // novel-downloader's version is the @version line of the script's own header.
            val data = shippedFiles.read(engine.shippedPath) ?: return null
            val manifest = JsonObject(mapOf("api" to JsonPrimitive(engine.api), "version" to JsonPrimitive(headerVersion(data))))
            EngineBundle(engine, manifest, updated = false) { data }
        }
        shippedCache[engine.name] = bundle
        bundle
    }

    /** A downloaded update whose files are intact, or null. */
    fun downloaded(engine: RuleEngine): EngineBundle? = synchronized(lock) {
        val folder = directory(engine) ?: return null
        val manifestFile = File(folder, MANIFEST)
        val libFile = File(folder, engine.lib)
        if (!manifestFile.isFile || !libFile.isFile) return null
        val stamp = listOf(manifestFile.lastModified(), manifestFile.length(), libFile.lastModified(), libFile.length())
        downloadedCache[engine.name]?.takeIf { it.first == stamp }?.let { return it.second }
        val bundle = runCatching {
            val manifest = parseObject(manifestFile.readBytes())
            val data = libFile.readBytes()
            if (manifest == null || !hasApi(manifest, engine) || sha256(data) != manifest.text("sha256")) {
                null
            } else {
                EngineBundle(engine, manifest, updated = true, bytes = { data })
            }
        }.getOrNull()
        downloadedCache[engine.name] = stamp to bundle
        bundle
    }

    /** The bundle to run: a valid download when it is newer than the shipped copy. */
    fun current(engine: RuleEngine): EngineBundle? {
        val base = shipped(engine)
        val update = downloaded(engine)
        return if (update != null && (base == null || update.key > base.key)) update else base
    }

    /** Sets a downloaded bundle aside (it failed to load) so it is not used or fetched again. */
    fun reject(bundle: EngineBundle, reason: String = "") {
        if (!bundle.updated) return
        val engine = bundle.engine
        synchronized(lock) {
            val state = state(engine).toMutableMap()
            val rejected = rejectedReleases(engine) + releaseKey(bundle.manifest)
            state["rejected"] = JsonArray(rejected.sorted().map(::JsonPrimitive))
            state["rejected_reason"] = JsonPrimitive(reason.take(300))
            saveState(engine, JsonObject(state))
            val folder = directory(engine)
            if (folder != null) for (name in listOf(engine.lib, MANIFEST)) File(folder, name).delete()
            downloadedCache.remove(engine.name)
        }
    }

    // State: when the engine was last checked and which releases failed to load.

    fun state(engine: RuleEngine): JsonObject = synchronized(lock) {
        val file = directory(engine)?.let { File(it, STATE) } ?: return JsonObject(emptyMap())
        runCatching { parseObject(file.readBytes()) }.getOrNull() ?: JsonObject(emptyMap())
    }

    fun saveState(engine: RuleEngine, state: JsonObject) = synchronized(lock) {
        val folder = directory(engine) ?: return
        folder.mkdirs()
        writeAtomically(File(folder, STATE), pretty.encodeToString(JsonObject.serializer(), state).toByteArray())
    }

    /** When the engine was last checked, in seconds since the epoch (0 when never). */
    fun lastChecked(engine: RuleEngine): Double = (state(engine)["checked"] as? JsonPrimitive)?.doubleOrNull ?: 0.0

    fun rejectedReleases(engine: RuleEngine): Set<String> =
        (state(engine)["rejected"] as? JsonArray).orEmpty().map { (it as? JsonPrimitive)?.content.orEmpty() }.toSet()

    companion object {
        const val MANIFEST = "manifest.json"
        private const val STATE = "state.json"
        private val pretty = Json { prettyPrint = true }

        /** What identifies a published build: upstream commit plus the shim around it. */
        fun releaseKey(manifest: JsonObject) = "${manifest.text("commit")}-${manifest.text("shim")}"

        fun sha256(data: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

        internal fun parseObject(bytes: ByteArray): JsonObject? =
            runCatching { Json.parseToJsonElement(bytes.decodeToString()) as? JsonObject }.getOrNull()

        private fun hasApi(manifest: JsonObject, engine: RuleEngine): Boolean =
            (manifest["api"] as? JsonPrimitive)?.doubleOrNull == engine.api.toDouble()

        private val header = Regex("""@version\s+(\S+)""")

        /** The `@version` line of a userscript header, or "". */
        internal fun headerVersion(script: ByteArray): String =
            header.find(script.copyOf(minOf(script.size, 4096)).decodeToString())?.groupValues?.get(1).orEmpty()

        /** Writes `data` to a temporary file next to `target` and renames it over `target`. */
        internal fun writeAtomically(target: File, data: ByteArray) {
            val temporary = File(target.parentFile, target.name + ".tmp")
            temporary.writeBytes(data)
            try {
                Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } catch (e: AtomicMoveNotSupportedException) {
                Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
        }
    }
}

/**
 * The app-wide store. `configure` points it at the app's files folder at start; until then (and in
 * unit tests) only the shipped copies are used.
 */
object RuleEngines {
    @Volatile
    var store: EngineStore = EngineStore()
        private set

    @Volatile
    var updates: EngineUpdates = EngineUpdates(store)
        private set

    fun configure(directory: File?) {
        store = EngineStore(directory)
        updates = EngineUpdates(store)
    }
}
