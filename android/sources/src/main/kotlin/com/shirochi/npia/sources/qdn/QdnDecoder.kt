package com.shirochi.npia.sources.qdn

import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.core.WebProfile
import com.shirochi.npia.sources.hard.HardScripts
import com.shirochi.npia.sources.hard.ensureHard
import com.shirochi.npia.sources.hard.hard
import com.shirochi.npia.sources.hard.hardJson
import com.shirochi.npia.sources.hard.hardTask
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * The glyph decoder's page. A cipher font is matched against a reference of ~9,800 hanzi that takes a
 * few seconds to build, so the decoder lives in its own blank page that outlives the chapter pages:
 * the reference is built once per download and the chapter pages only extract. Chapters hand it
 * the extraction payload as JSON text and get lines back (`hard-sources.js`, `qdn.decoder`).
 */
internal class QdnDecoder(
    private val web: WebEngineProviding,
    private val logger: JobLogging,
    private val timing: QdnTiming,
) {
    private val gate = Mutex()
    private var page: WebPageDriving? = null
    private var prepared = false

    /** Opens the page, pushes the resources and builds the reference. Safe to call again. */
    suspend fun prepare() = gate.withLock {
        if (prepared) return@withLock
        val started = System.currentTimeMillis()
        val decoderPage = page ?: web.newPage(WebProfile.EXTERNAL).also { page = it }
        decoderPage.load("about:blank", WebLoadState.DOM_CONTENT_LOADED, 20)
        decoderPage.ensureHard()
        push(decoderPage, "font", HardScripts.QDN_FONT)
        push(decoderPage, "frequency", HardScripts.QDN_FREQUENCY)
        push(decoderPage, "verified", HardScripts.QDN_VERIFIED)
        repeat(timing.referenceSlices) {
            val state = decoderPage.hard<QdnPrepareState>("qdn.decoder.prepare(${timing.referenceSliceMillis})")
            if (state.done) {
                prepared = true
                logger.log(LogLevel.INFO, "[qdn] Glyph reference ready (${state.built} characters, ${System.currentTimeMillis() - started} ms).")
                return@withLock
            }
        }
        throw WebEngineException("the glyph reference did not finish building")
    }

    private suspend fun push(decoderPage: WebPageDriving, name: String, resource: String) {
        val encoded = HardScripts.base64(HardScripts.resource(resource))
        val parts = encoded.chunked(CHUNK_CHARACTERS).ifEmpty { listOf("") }
        parts.forEachIndexed { index, part ->
            decoderPage.evaluateJson(
                "return window.__npiaHard.qdn.decoder.putResource(${HardScripts.literal(name)}, $index, ${parts.size}, ${HardScripts.literal(part)});",
            )
        }
    }

    /** SHA-256 digests of the cipher fonts held, so the next extraction can leave them out. */
    suspend fun knownFonts(): List<String> = gate.withLock {
        val decoderPage = page ?: return@withLock emptyList()
        decoderPage.hard<List<String>>("qdn.decoder.knownFonts()")
    }

    /** Decodes an extraction payload (JSON text, as `qdn.extract` returned it). */
    suspend fun decode(payloadJson: String): QdnDecoded {
        prepare()
        return gate.withLock {
            val decoderPage = page ?: throw WebEngineException("the glyph decoder is closed")
            val text = decoderPage.hardTask(
                "qdn.decoder.decodeJson", "[${HardScripts.literal(payloadJson)}]",
                pollMillis = timing.decodePollMillis, maxPolls = timing.decodePolls,
            )
            hardJson.decodeFromString(QdnDecoded.serializer(), text)
        }
    }

    suspend fun close() {
        val closing = gate.withLock { page.also { page = null; prepared = false } } ?: return
        withContext(NonCancellable) {
            try {
                closing.close()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Already gone.
            }
        }
    }

    private companion object {
        /** About 192 KB of binary per call keeps every script call small. */
        const val CHUNK_CHARACTERS = 262_144
    }
}
