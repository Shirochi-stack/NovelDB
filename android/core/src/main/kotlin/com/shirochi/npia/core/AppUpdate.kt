package com.shirochi.npia.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.net.URLEncoder

// In-app updates: the logic shared by every platform's contract, free of Android and HTTP so it can
// be tested on a plain JVM. `:net` fetches and downloads, the app installs.

/**
 * The build the running app was made from. `build` is the Unix time in seconds at which the release
 * workflow planned it; only CI sets it, and a larger number is newer. A build without one (local,
 * debug, pull request) is a development build that never offers an update.
 */
data class AppBuild(val build: Long = 0, val tag: String = "", val label: String = "") {
    val isDevelopment: Boolean get() = build <= 0

    /** `v98` or `v98 hotfix3`, for display only. */
    val version: String get() = UpdateVersion.display(tag, label, build)

    /** The line Settings shows: the version in use, or why updates are off. */
    val statusLine: String
        get() = when {
            isDevelopment -> DEVELOPMENT_STATUS
            tag.isBlank() -> "build $build"
            else -> "$version, build $build"
        }

    companion object {
        const val DEVELOPMENT_STATUS = "Development build: app updates are off"
    }
}

object UpdateVersion {
    /** `v98 hotfix3`; the label `none` (or none at all) is left out, and a missing tag falls back to the build number. */
    fun display(tag: String, label: String, build: Long): String {
        val name = tag.trim().ifEmpty { return "build $build" }
        val extra = label.trim().takeUnless { it.isEmpty() || it.equals("none", ignoreCase = true) }
        return if (extra == null) name else "$name $extra"
    }
}

/** One file of a release, as the platform manifest lists it. */
data class UpdateAsset(
    val name: String,
    val variant: String,
    val arch: String,
    val kind: String,
    val build: Long,
    val label: String,
    val commit: String,
    val size: Long,
    /** 64 lowercase hex characters. */
    val sha256: String,
)

/** `update-<platform>.json` of a release (schema 1). */
data class UpdateManifest(val tag: String, val releaseUrl: String, val assets: List<UpdateAsset>)

object UpdateManifests {
    const val SCHEMA = 1

    private val safeTag = Regex("[A-Za-z0-9._-]{1,64}")
    private val sha256 = Regex("[0-9a-f]{64}")

    /**
     * Reads a manifest. Unknown fields are ignored. A manifest that is not JSON, not an object, or
     * whose `schema` is not 1 gives null ("no update information"), and so does one without a usable
     * `tag`. An entry that is incomplete or names a path instead of a file is skipped.
     */
    fun parse(text: String): UpdateManifest? {
        val root = parseObject(text) ?: return null
        if (root.long("schema") != SCHEMA.toLong()) return null
        val tag = root.string("tag")?.takeIf { safeTag.matches(it) } ?: return null
        val releaseUrl = root.string("release_url")?.takeIf(::isWebUrl) ?: AppUpdateUrls.releasePage(tag)
        val assets = ((root["assets"] as? JsonArray) ?: emptyList<JsonElement>()).mapNotNull { (it as? JsonObject)?.let(::asset) }
        return UpdateManifest(tag, releaseUrl, assets)
    }

    private fun asset(o: JsonObject): UpdateAsset? {
        val name = o.string("name")?.takeIf(::isPlainFileName) ?: return null
        val kind = o.string("kind")?.takeIf { it.isNotEmpty() } ?: return null
        val build = o.long("build")?.takeIf { it > 0 } ?: return null
        val size = o.long("size")?.takeIf { it > 0 } ?: return null
        val digest = o.string("sha256")?.lowercase()?.takeIf { sha256.matches(it) } ?: return null
        return UpdateAsset(
            name = name,
            variant = o.string("variant").orEmpty(),
            arch = o.string("arch").orEmpty(),
            kind = kind,
            build = build,
            label = o.string("label").orEmpty(),
            commit = o.string("commit").orEmpty(),
            size = size,
            sha256 = digest,
        )
    }

    /** A file name that cannot leave the folder it is saved in or change the download address. */
    fun isPlainFileName(name: String): Boolean =
        name.isNotEmpty() && name.length <= 200 && name != "." && ".." !in name &&
            name.none { it == '/' || it == '\\' || it == '?' || it == '#' || it.isISOControl() }

    private fun isWebUrl(url: String): Boolean {
        val lower = url.lowercase()
        return (lower.startsWith("https://") || lower.startsWith("http://")) && url.none { it.isWhitespace() }
    }

    private fun JsonObject.string(key: String): String? = (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content

    private fun JsonObject.long(key: String): Long? =
        (this[key] as? JsonPrimitive)?.takeUnless { it is JsonNull || it.isString }?.longOrNull

    private fun parseObject(text: String): JsonObject? = try {
        Json.parseToJsonElement(text) as? JsonObject
    } catch (e: Exception) {
        null
    }
}

/** A newer build of the app, as found in the manifest. */
data class UpdateOffer(val tag: String, val releaseUrl: String, val asset: UpdateAsset) {
    val build: Long get() = asset.build

    /** `v98 hotfix3`. */
    val version: String get() = UpdateVersion.display(tag, asset.label, asset.build)

    /** The title of the update notice. */
    val title: String get() = "NovelDB $version is available"
}

object AppUpdates {
    const val KIND = "apk"
    const val VARIANT = "universal"
    const val ARCH = "universal"

    /** The app's own file in the manifest: its kind, variant and architecture; the newest build if there are several. */
    fun entry(manifest: UpdateManifest, kind: String = KIND, variant: String = VARIANT, arch: String = ARCH): UpdateAsset? =
        manifest.assets.filter { it.kind == kind && it.variant == variant && it.arch == arch }.maxByOrNull { it.build }

    /** An update exists when the app's entry has a larger build than `current`. A development build is never offered one. */
    fun offer(manifest: UpdateManifest, current: AppBuild): UpdateOffer? {
        if (current.isDevelopment) return null
        val entry = entry(manifest) ?: return null
        return if (entry.build > current.build) UpdateOffer(manifest.tag, manifest.releaseUrl, entry) else null
    }
}

/** Where releases are read from. `base` is the releases page of the repository; debug builds can point it at a local server. */
class AppUpdateUrls(base: String? = null) {
    val base: String = base?.trim()?.trimEnd('/')?.takeIf { it.isNotEmpty() } ?: DEFAULT_BASE

    /** GitHub redirects `latest/download` to the newest published release that is not a draft or a pre-release. */
    fun manifest(platform: String = "android"): String = "$base/latest/download/update-$platform.json"

    fun asset(tag: String, name: String): String = "$base/download/${encode(tag)}/${encode(name)}"

    private fun encode(part: String) = URLEncoder.encode(part, "UTF-8").replace("+", "%20")

    companion object {
        const val DEFAULT_BASE = "https://github.com/Shirochi-stack/NovelDB/releases"

        fun releasePage(tag: String) = "$DEFAULT_BASE/tag/$tag"
    }
}

/** When the automatic check runs: at most once per 24 hours, and never for a development build. */
object UpdateSchedule {
    const val EVERY_MILLIS = 24L * 3600 * 1000

    /** True when no check was made yet, a day has passed, or the clock went back past the last check. */
    fun due(lastCheckedMillis: Long, nowMillis: Long): Boolean =
        lastCheckedMillis <= 0 || nowMillis < lastCheckedMillis || nowMillis - lastCheckedMillis >= EVERY_MILLIS

    fun automaticCheckDue(enabled: Boolean, build: AppBuild, lastCheckedMillis: Long, nowMillis: Long): Boolean =
        enabled && !build.isDevelopment && due(lastCheckedMillis, nowMillis)
}
