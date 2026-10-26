package com.shirochi.npia.core

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/** One fetch/XHR response recorded by `NetworkCapture.initScript`. */
@Serializable
data class CapturedResponse(
    val url: String,
    val method: String = "GET",
    val status: Int,
    val body: String,
) {
    inline fun <reified T> json(format: Json = NetworkCapture.json): T = format.decodeFromString(body)
}

/**
 * Records page fetch/XHR responses from inside the page, replacing Playwright's
 * `page.on("response")` interception. Install with `startCapture` before `load`.
 */
object NetworkCapture {
    /** Page global holding the captured responses. */
    const val STORE_NAME = "__npiaCaptured"

    val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    /**
     * Wraps `fetch` and `XMLHttpRequest` so responses whose URL contains one of `patterns`
     * (all responses when empty) are copied into `window.__npiaCaptured`. Bodies longer than
     * `maxBody` characters are truncated.
     */
    fun initScript(patterns: List<String>, maxBody: Int = 8_000_000): String {
        val encoded = Json.encodeToString(ListSerializer(String.serializer()), patterns)
        return """
        (() => {
          if (window.$STORE_NAME) return;
          const store = [];
          Object.defineProperty(window, '$STORE_NAME', { value: store, enumerable: false });
          const patterns = $encoded;
          const wanted = (url) => !patterns.length || patterns.some((p) => String(url).includes(p));
          const keep = (url, method, status, body) => {
            try {
              store.push({ url: String(url), method: String(method || 'GET').toUpperCase(),
                           status: Number(status) || 0, body: String(body ?? '').slice(0, $maxBody) });
            } catch (e) {}
          };
          const originalFetch = window.fetch;
          if (originalFetch) {
            window.fetch = function (input, init) {
              const url = (input && input.url) || input;
              const method = (init && init.method) || (input && input.method) || 'GET';
              return originalFetch.apply(this, arguments).then((response) => {
                if (wanted(response.url || url)) {
                  response.clone().text().then((text) => keep(response.url || url, method, response.status, text), () => {});
                }
                return response;
              });
            };
          }
          const open = XMLHttpRequest.prototype.open;
          const send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.open = function (method, url) {
            this.__npiaMethod = method;
            this.__npiaURL = url;
            return open.apply(this, arguments);
          };
          XMLHttpRequest.prototype.send = function () {
            this.addEventListener('load', () => {
              const url = this.responseURL || this.__npiaURL;
              if (!wanted(url)) return;
              let body = '';
              try {
                if (this.responseType === '' || this.responseType === 'text') body = this.responseText;
                else if (this.responseType === 'json') body = JSON.stringify(this.response);
              } catch (e) {}
              keep(url, this.__npiaMethod, this.status, body);
            });
            return send.apply(this, arguments);
          };
        })();
        """.trimIndent()
    }

    fun decode(raw: String): List<CapturedResponse> =
        json.decodeFromString(ListSerializer(CapturedResponse.serializer()), raw)
}

/** Starts recording fetch/XHR responses for pages loaded after this call. */
suspend fun WebPageDriving.startCapture(patterns: List<String> = emptyList()) =
    addInitScript(NetworkCapture.initScript(patterns))

/** Responses recorded so far in the current document. */
suspend fun WebPageDriving.capturedResponses(): List<CapturedResponse> =
    NetworkCapture.decode(evaluateJson("return window.${NetworkCapture.STORE_NAME} || [];"))

/** Polls until a captured response's URL contains `pattern`, then returns the newest one. */
suspend fun WebPageDriving.waitForResponse(pattern: String, timeoutSeconds: Double = 30.0): CapturedResponse? {
    val deadline = System.currentTimeMillis() + (timeoutSeconds * 1000).toLong()
    do {
        capturedResponses().lastOrNull { pattern in it.url }?.let { return it }
        delay(250)
    } while (System.currentTimeMillis() < deadline)
    return null
}

/** Runs a JavaScript function body and decodes its JSON result. */
suspend inline fun <reified T> WebPageDriving.evaluate(functionBody: String): T =
    NetworkCapture.json.decodeFromString(evaluateJson(functionBody))

/** Polls `condition` (a JavaScript expression) until it is truthy. */
suspend fun WebPageDriving.waitFor(condition: String, timeoutSeconds: Double = 30.0, intervalSeconds: Double = 0.25): Boolean {
    val deadline = System.currentTimeMillis() + (timeoutSeconds * 1000).toLong()
    do {
        val truthy = try {
            evaluateJson("return !!($condition);").trim() == "true"
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            false
        }
        if (truthy) return true
        delay((intervalSeconds * 1000).toLong())
    } while (System.currentTimeMillis() < deadline)
    return false
}

/** Opens a page, runs `body`, and always closes the page. */
suspend fun <T> WebEngineProviding.withPage(profile: String, body: suspend (WebPageDriving) -> T): T {
    val page = newPage(profile)
    try {
        return body(page)
    } finally {
        withContext(NonCancellable) { page.close() }
    }
}

/**
 * Web engine profile names. The desktop external downloader used one browser profile for every
 * source that needs a login. Android WebView keeps one cookie store per process, so every
 * profile shares it on Android; the names stay for parity with iOS.
 */
object WebProfile {
    const val EXTERNAL = "external"
    const val NPIA = "npia"
}
