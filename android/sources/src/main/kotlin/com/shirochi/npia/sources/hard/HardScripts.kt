package com.shirochi.npia.sources.hard

import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.core.evaluate
import kotlinx.coroutines.delay
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.util.Base64

/**
 * The shared page script (`hard-sources.js`) and its resources. The same file runs in the iOS app's
 * web view; the contract is `android/docs/hard-sources.md`. Kotlin only loads it, calls its entry
 * points and reads the JSON they return.
 */
internal object HardScripts {
    private const val ROOT = "/com/shirochi/npia/sources/hard/"

    private const val LINE_SEPARATOR = 0x2028.toChar()
    private const val PARAGRAPH_SEPARATOR = 0x2029.toChar()

    const val SCRIPT = "hard-sources.js"
    const val QDN_FONT = "qdn/NotoSansCJKsc-Regular-hanzi.otf"
    const val QDN_FREQUENCY = "qdn/zh_char_frequency.txt"
    const val QDN_VERIFIED = "qdn/qdn_verified_maps.json"

    /** The script's source text. */
    val source: String by lazy { resource(SCRIPT).decodeToString() }

    fun resource(name: String): ByteArray {
        val stream = HardScripts::class.java.getResourceAsStream(ROOT + name)
            ?: throw WebEngineException("missing resource $name")
        return stream.use { it.readBytes() }
    }

    /** A JavaScript string literal (JSON is one, except for the two line separators older engines reject). */
    fun literal(value: String): String =
        Json.encodeToString(String.serializer(), value)
            .replace(LINE_SEPARATOR.toString(), "\\" + "u2028")
            .replace(PARAGRAPH_SEPARATOR.toString(), "\\" + "u2029")

    fun base64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)
}

/** Reads JSON the script returned. Unknown keys are ignored so the script can grow. */
internal val hardJson = Json { ignoreUnknownKeys = true; coerceInputValues = true; isLenient = true }

/**
 * Defines `window.__npiaHard` when the page does not have it yet. It normally does: native code
 * installs the script as a document-start script, which also puts the font hook in place before the
 * reader runs. This covers a web view without document-start scripts and blank decoder pages.
 */
internal suspend fun WebPageDriving.ensureHard() {
    val present = evaluateJson("return !!(window.__npiaHard && window.__npiaHard.version);").trim() == "true"
    if (!present) evaluateJson("(0, eval)(${HardScripts.literal(HardScripts.source)}); return true;")
}

/** Calls an entry point, for example `qdn.pageState()`, and decodes its JSON result. */
internal suspend inline fun <reified T> WebPageDriving.hard(call: String): T {
    ensureHard()
    return evaluate("return await window.__npiaHard.$call;")
}

/** Same, returning the JSON text. */
internal suspend fun WebPageDriving.hardText(call: String): String {
    ensureHard()
    return evaluateJson("return await window.__npiaHard.$call;")
}

/**
 * Runs a long entry point as a task and polls it (see `tasks` in the script), so no single script call
 * outlives the web view's script timeout. Returns the result's JSON text. `maxPolls` bounds the wait.
 */
internal suspend fun WebPageDriving.hardTask(
    path: String,
    argsJson: String,
    pollMillis: Long = 250,
    maxPolls: Int = 480,
): String {
    ensureHard()
    val started = evaluateJson("return window.__npiaHard.tasks.start(${HardScripts.literal(path)}, $argsJson);")
    val ticket = hardJson.parseToJsonElement(started).jsonObject["ticket"]?.jsonPrimitive?.content
        ?: throw WebEngineException("$path did not start")
    repeat(maxPolls) {
        val polled: JsonObject = hardJson.parseToJsonElement(
            evaluateJson("return window.__npiaHard.tasks.poll(${HardScripts.literal(ticket)});"),
        ).jsonObject
        when (polled["state"]?.jsonPrimitive?.content) {
            "done" -> return polled["json"]?.jsonPrimitive?.content ?: "null"
            "error" -> throw WebEngineException(polled["error"]?.jsonPrimitive?.content ?: "$path failed")
        }
        delay(pollMillis)
    }
    throw WebEngineException("$path timed out")
}
