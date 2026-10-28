package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.put
import java.util.Base64

/**
 * The native side of `window.__npia_gm_xmlhttp_request`, the transport WebToEpub's `fetch` falls
 * back to when the web view refuses a request (CORS). Requests go through the app's HTTP client,
 * which shares the web view's cookies (`WebViewCookieJar`), so a site's login and bot-check cookies
 * apply to them as they do to the page.
 *
 * In: `{url, method, headers, data | dataBase64, responseType, timeout, cookie?}`.
 * Out: `{status, statusText, responseHeaders, finalUrl, responseBase64}` or `{error}`.
 */
internal object WebToEpubTransport {
    suspend fun handle(requestJson: String, http: HttpFetching): String {
        val reply = try {
            val details = Json.parseToJsonElement(requestJson) as? JsonObject ?: return error("the request is not an object")
            val request = request(details) ?: return error("invalid URL ${details.string("url").orEmpty()}")
            val response = http.send(request)
            buildJsonObject {
                put("status", response.status)
                put("statusText", reasonPhrase(response.status))
                put("responseHeaders", response.headers.entries.joinToString("") { (name, value) -> "$name: $value\r\n" })
                put("finalUrl", response.url)
                put("responseBase64", Base64.getEncoder().encodeToString(response.body))
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            buildJsonObject { put("error", e.message ?: e.toString()) }
        }
        return reply.toString()
    }

    private fun error(message: String) = buildJsonObject { put("error", message) }.toString()

    /** The HTTP request a transport call describes, or null when it is not an http(s) URL. */
    internal fun request(details: JsonObject): HttpRequest? {
        val url = details.string("url") ?: return null
        if (!(url.startsWith("http://", true) || url.startsWith("https://", true))) return null
        val headers = LinkedHashMap<String, String>()
        (details["headers"] as? JsonObject)?.forEach { (name, value) ->
            (value as? JsonPrimitive)?.contentOrNull?.let { headers[name] = it }
        }
        // The page's own cookie string, for callers that pass one instead of a header.
        details.string("cookie")?.takeIf { it.isNotEmpty() && headers.keys.none { name -> name.equals("cookie", true) } }
            ?.let { headers["Cookie"] = it }
        val body = details.string("dataBase64")?.takeIf { it.isNotEmpty() }?.let { runCatching { Base64.getDecoder().decode(it) }.getOrNull() }
            ?: details.string("data")?.takeIf { it.isNotEmpty() }?.encodeToByteArray()
        val timeoutMs = (details["timeout"] as? JsonPrimitive)?.doubleOrNull ?: 30_000.0
        return HttpRequest(
            url = url,
            method = details.string("method")?.uppercase()?.ifEmpty { null } ?: "GET",
            headers = headers,
            body = body,
            timeoutSeconds = kotlin.math.ceil(timeoutMs / 1000).toLong().coerceAtLeast(1),
        )
    }

    private fun reasonPhrase(status: Int): String = when (status) {
        200 -> "OK"
        201 -> "Created"
        204 -> "No Content"
        301 -> "Moved Permanently"
        302 -> "Found"
        304 -> "Not Modified"
        400 -> "Bad Request"
        401 -> "Unauthorized"
        403 -> "Forbidden"
        404 -> "Not Found"
        429 -> "Too Many Requests"
        500 -> "Internal Server Error"
        502 -> "Bad Gateway"
        503 -> "Service Unavailable"
        504 -> "Gateway Timeout"
        else -> ""
    }
}
