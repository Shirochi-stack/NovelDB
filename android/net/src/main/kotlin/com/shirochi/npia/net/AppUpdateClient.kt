package com.shirochi.npia.net

import com.shirochi.npia.core.AppBuild
import com.shirochi.npia.core.AppUpdateUrls
import com.shirochi.npia.core.AppUpdates
import com.shirochi.npia.core.UpdateAsset
import com.shirochi.npia.core.UpdateManifests
import com.shirochi.npia.core.UpdateOffer
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** The downloaded file is not the one the manifest describes (wrong size or checksum). The file has been deleted. */
class UpdateVerificationException(message: String = MISMATCH) : IOException(message) {
    companion object {
        const val MISMATCH = "The download did not match its checksum; nothing was installed."
    }
}

/** What looking for a newer build found. */
sealed interface UpdateCheck {
    /** A development build never offers updates. */
    data object Development : UpdateCheck

    /** The release has no manifest (older releases), a bad one, or no entry for this app. */
    data object NoInformation : UpdateCheck

    data object UpToDate : UpdateCheck

    data class Available(val offer: UpdateOffer) : UpdateCheck

    /** The release could not be reached, or answered with an error. */
    data class Failed(val message: String) : UpdateCheck
}

/**
 * Reads the release manifest and downloads the update (`AppUpdate.kt` holds the rules). Nothing
 * here installs anything: `download` returns a file only after its size and SHA-256 match the
 * manifest, and deletes it otherwise.
 */
class AppUpdateClient(private val http: OkHttpClient = defaultClient()) {
    /** Looks for a build newer than `current`. Never throws except on cancellation. */
    suspend fun check(current: AppBuild, urls: AppUpdateUrls = AppUpdateUrls()): UpdateCheck {
        if (current.isDevelopment) return UpdateCheck.Development
        val text: String?
        try {
            text = fetchManifest(urls.manifest())
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            return UpdateCheck.Failed(e.message ?: e.toString())
        }
        val manifest = text?.let(UpdateManifests::parse) ?: return UpdateCheck.NoInformation
        if (AppUpdates.entry(manifest) == null) return UpdateCheck.NoInformation
        val offer = AppUpdates.offer(manifest, current) ?: return UpdateCheck.UpToDate
        return UpdateCheck.Available(offer)
    }

    /** The manifest text, or null for a 404 (older releases have none) and for one that is too large to be a manifest. */
    private suspend fun fetchManifest(url: String): String? = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(url).header("Accept", "application/json").build()
        val call = http.newBuilder().callTimeout(60, TimeUnit.SECONDS).build().newCall(request)
        call.await().use { response ->
            if (response.code == 404) return@use null
            if (!response.isSuccessful) throw IOException("HTTP ${response.code}")
            val source = response.body?.source() ?: return@use null
            source.request(MAX_MANIFEST_BYTES + 1)
            if (source.buffer.size > MAX_MANIFEST_BYTES) null else source.readUtf8()
        }
    }

    /**
     * Downloads `offer` from `url` into `directory` and returns the verified file. `onProgress`
     * gets the bytes received and the size the manifest promises. On a size or checksum mismatch,
     * any other failure or cancellation, nothing is left in `directory` for this file.
     */
    suspend fun download(
        offer: UpdateOffer,
        url: String,
        directory: File,
        onProgress: (received: Long, total: Long) -> Unit = { _, _ -> },
    ): File = withContext(Dispatchers.IO) {
        val asset = offer.asset
        directory.mkdirs()
        val partial = File(directory, asset.name + ".part")
        val target = File(directory, asset.name)
        partial.delete()
        target.delete()
        try {
            coroutineScope {
                val call = http.newCall(Request.Builder().url(url).build())
                // A blocked read ends when the download is cancelled.
                val watcher = launch {
                    try {
                        awaitCancellation()
                    } finally {
                        call.cancel()
                    }
                }
                try {
                    call.execute().use { response -> receive(response, asset, partial, onProgress) }
                } catch (e: IOException) {
                    currentCoroutineContext().ensureActive()
                    throw e
                } finally {
                    watcher.cancel()
                }
            }
            verify(partial, asset)
            if (!partial.renameTo(target)) throw IOException("Could not save the update")
            target
        } catch (e: Throwable) {
            partial.delete()
            target.delete()
            throw e
        }
    }

    private suspend fun receive(response: Response, asset: UpdateAsset, file: File, onProgress: (Long, Long) -> Unit) {
        if (!response.isSuccessful) throw IOException("HTTP ${response.code}")
        val body = response.body ?: throw IOException("The server sent no file")
        // A file of another size cannot match the manifest, so do not download all of it.
        if (body.contentLength() >= 0 && body.contentLength() != asset.size) throw UpdateVerificationException()
        var received = 0L
        body.byteStream().use { input ->
            file.outputStream().use { out ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    currentCoroutineContext().ensureActive()
                    val n = input.read(buffer)
                    if (n < 0) break
                    received += n
                    if (received > asset.size) throw UpdateVerificationException()
                    out.write(buffer, 0, n)
                    onProgress(received, asset.size)
                }
            }
        }
    }

    companion object {
        const val MAX_MANIFEST_BYTES = 512L * 1024

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            // A release address never needs to leave https.
            .followSslRedirects(false)
            .build()

        /** True when `file` has the size and SHA-256 the manifest lists. */
        fun matches(file: File, asset: UpdateAsset): Boolean =
            file.isFile && file.length() == asset.size && sha256(file).equals(asset.sha256, ignoreCase = true)

        /** Checks `file` against the manifest entry; deletes it and throws `UpdateVerificationException` when it does not match. */
        fun verify(file: File, asset: UpdateAsset) {
            if (!matches(file, asset)) {
                file.delete()
                throw UpdateVerificationException()
            }
        }

        fun sha256(file: File): String {
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    digest.update(buffer, 0, n)
                }
            }
            return digest.digest().joinToString("") { "%02x".format(it) }
        }
    }
}

private suspend fun Call.await(): Response = suspendCancellableCoroutine { continuation ->
    enqueue(object : Callback {
        override fun onResponse(call: Call, response: Response) = continuation.resume(response)
        override fun onFailure(call: Call, e: IOException) {
            if (!continuation.isCancelled) continuation.resumeWithException(e)
        }
    })
    continuation.invokeOnCancellation { runCatching { cancel() } }
}
