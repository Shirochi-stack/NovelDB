package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.HttpResponse
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.net.URI

/** A response or record the search could not use; the Python `ValueError` the validation raises. */
internal class ValueError(message: String) : Exception(message)

/** `type(exc).__name__` in the log lines: the class name, `ValueError` for validation failures. */
internal fun errorName(error: Throwable): String = error::class.simpleName ?: "Error"

/** The text of a JSON scalar, or "" for null, missing, objects and arrays (`str(value or "")` for ids and ages). */
internal fun JsonElement?.scalarText(): String = if (this is JsonPrimitive && this !is JsonNull) content else ""

internal fun String.isAsciiDigits(): Boolean = isNotEmpty() && all { it in '0'..'9' }

/** `author_id`: the first of `writer_mem_no`, `mem_no`, `author_id` that is a positive number. */
internal fun authorId(item: JsonObject): String? {
    for (field in listOf("writer_mem_no", "mem_no", "author_id")) {
        val value = item[field].scalarText()
        if (value.isAsciiDigits() && value.any { it != '0' }) return value
    }
    return null
}

/**
 * `matches_age`: "19" keeps adults (age 19 or more), "15" keeps the rest, anything else keeps all.
 * A missing age counts as 0 and an unreadable one passes only without a filter.
 */
internal fun matchesAge(item: JsonObject, ageFilter: String = ""): Boolean {
    val age = ageOf(item["novel_age"], present = "novel_age" in item) ?: return ageFilter.isEmpty()
    return when (ageFilter) {
        "19" -> age >= 19
        "15" -> age < 19
        else -> true
    }
}

private fun ageOf(value: JsonElement?, present: Boolean): Int? {
    if (!present) return 0
    if (value !is JsonPrimitive || value is JsonNull) return null
    if (value.isString) return value.content.trim().toIntOrNull()
    return when (value.content) {
        "true" -> 1
        "false" -> 0
        else -> value.content.toDoubleOrNull()?.takeIf { it.isFinite() }?.toInt()
    }
}

private val SECRET_PATTERN = Regex(
    """(["']?(?:loginkey|access_token|refresh_token|api_key|authorization|password|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s<>&,;]+)""",
    RegexOption.IGNORE_CASE,
)

/** `response_details`: bounded body text without request URLs, cookies or auth headers, for log lines. */
internal fun responseDetails(response: HttpResponse): String {
    val text = response.text
    val body = SECRET_PATTERN.replace(text, "$1[redacted]")
    val preview = body.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").take(240)
    val contentType = response.headers["content-type"] ?: "unknown"
    return "HTTP ${response.status}; content-type=${pyRepr(contentType)}; body-chars=${text.length}; preview=${pyRepr(preview)}"
}

private fun pyRepr(value: String): String = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

/** True when the final URL of a response is not the one requested: the request was redirected (`allow_redirects=False`). */
internal fun HttpResponse.wasRedirected(requestedUrl: String): Boolean {
    fun place(url: String): String? = runCatching { URI(url).let { "${it.host}${it.rawPath.orEmpty().trimEnd('/')}" } }.getOrNull()
    val requested = place(requestedUrl) ?: return false
    val final = place(url) ?: return false
    return requested != final
}

/** A JSON object body, or a `ValueError` naming the status and a short excerpt when it is anything else. */
internal fun HttpResponse.jsonObjectOrFail(): JsonObject {
    val body = text
    val element = runCatching { Json.parseToJsonElement(body) }.getOrNull()
    return element as? JsonObject
        ?: throw ValueError("invalid JSON response (HTTP $status, ${body.length} chars): ${body.trim().take(120)}")
}

/** `data.get("status") != 200`: only a JSON number 200 counts as success here (the author catalog is laxer). */
internal fun JsonObject.statusIs200(): Boolean {
    val status = this["status"]
    return status is JsonPrimitive && !status.isString && status.content.toDoubleOrNull() == 200.0
}

/** Sleeps `seconds` in 100 ms steps, returning early when a stop is requested. */
internal suspend fun sleepUnlessStopped(seconds: Double, stopped: () -> Boolean) {
    var remaining = (seconds * 1000).toLong()
    while (remaining > 0 && !stopped()) {
        val step = minOf(remaining, 100L)
        delay(step)
        remaining -= step
    }
}

/**
 * Runs `block` and returns its result, or null as soon as a stop is requested (the request is
 * cancelled). The desktop waits for the request in flight; on a phone Stop must answer quickly.
 */
internal suspend fun <T : Any> abortable(stopped: () -> Boolean, block: suspend () -> T): T? = supervisorScope {
    val request = async { block() }
    while (true) {
        if (stopped()) {
            request.cancel()
            return@supervisorScope null
        }
        if (withTimeoutOrNull(100) { request.join() } != null) break
    }
    request.await()
}

/** Logs "    Ns elapsed..." every 3 seconds while `block` runs, until a stop is requested. */
internal suspend fun <T> withElapsedTicks(stopped: () -> Boolean, log: (String) -> Unit, block: suspend () -> T): T = coroutineScope {
    val ticker = launch {
        var seconds = 0
        while (true) {
            delay(3000)
            if (stopped()) break
            seconds += 3
            log("    ${seconds}s elapsed...")
        }
    }
    try {
        block()
    } finally {
        ticker.cancel()
    }
}
