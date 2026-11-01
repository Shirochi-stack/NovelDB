package com.shirochi.npia.sources.http

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.UnavailableWebEngine
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.Collections

/** Canned HTTP replies keyed by a URL substring; the longest matching key wins. */
class RoutedHttp : HttpFetching {
    class Reply(val status: Int = 200, val body: ByteArray, val headers: Map<String, String> = emptyMap(), val finalUrl: String? = null)

    private val routes = Collections.synchronizedMap(mutableMapOf<String, Reply>())
    val requests: MutableList<HttpRequest> = Collections.synchronizedList(mutableListOf())

    fun on(fragment: String, text: String, status: Int = 200, headers: Map<String, String> = emptyMap(), finalUrl: String? = null) {
        routes[fragment] = Reply(status, text.encodeToByteArray(), headers, finalUrl)
    }

    fun onJson(fragment: String, value: Any?) {
        routes[fragment] = Reply(body = toJson(value).toString().encodeToByteArray())
    }

    fun onData(fragment: String, data: ByteArray) {
        routes[fragment] = Reply(body = data)
    }

    fun requested(fragment: String): List<HttpRequest> = synchronized(requests) { requests.filter { it.url.contains(fragment) } }

    override suspend fun send(request: HttpRequest): HttpResponse {
        requests += request
        val reply = synchronized(routes) {
            routes.keys.filter { request.url.contains(it) }.maxByOrNull { it.length }?.let { routes[it] }
        } ?: return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
        return HttpResponse(reply.finalUrl ?: request.url, reply.status, reply.headers, reply.body)
    }
}

/** Plain Kotlin maps, lists, strings, numbers and booleans to JSON. */
fun toJson(value: Any?): JsonElement = when (value) {
    null -> JsonNull
    is JsonElement -> value
    is String -> JsonPrimitive(value)
    is Number -> JsonPrimitive(value)
    is Boolean -> JsonPrimitive(value)
    is Map<*, *> -> JsonObject(value.entries.associate { it.key.toString() to toJson(it.value) })
    is List<*> -> JsonArray(value.map(::toJson))
    else -> error("unsupported JSON value $value")
}

class CollectingLogger : JobLogging {
    val lines: MutableList<String> = Collections.synchronizedList(mutableListOf())
    override fun log(level: LogLevel, message: String) {
        lines += message
    }
}

fun makeContext(http: HttpFetching, secrets: InMemorySecretStore = InMemorySecretStore(), settings: AppSettings = AppSettings()) =
    SourceContext(http, UnavailableWebEngine, secrets, settings)

fun content(result: ChapterResult): ChapterContent? = (result as? ChapterResult.Content)?.content

fun isRefused(result: ChapterResult): Boolean = result is ChapterResult.Refused
