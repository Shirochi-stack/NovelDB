package com.shirochi.npia.webengine

import com.shirochi.npia.core.WebEngineException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/**
 * `evaluateJavascript` cannot await promises, so `evaluateJson` runs the body as an async function
 * and hands the result back through the `__npiaBridge` JavaScript interface. When the interface
 * is missing (a page loaded before it was registered) the result is parked in
 * `window.__npiaResults` and polled instead.
 */
internal object ScriptBridge {
    const val BRIDGE_NAME = "__npiaBridge"
    const val RESULTS_NAME = "__npiaResults"

    /** Script that starts `functionBody` and delivers `{ok, payload}` under `id`. */
    fun wrap(id: String, functionBody: String): String = """
        (function () {
          const id = ${Json.encodeToString(JsonPrimitive.serializer(), JsonPrimitive(id))};
          const deliver = (ok, payload) => {
            try {
              if (window.$BRIDGE_NAME && window.$BRIDGE_NAME.resolve) { window.$BRIDGE_NAME.resolve(id, ok, payload); return; }
            } catch (e) {}
            (window.$RESULTS_NAME = window.$RESULTS_NAME || {})[id] = { ok: ok, payload: payload };
          };
          (async () => { $functionBody })().then((value) => {
            let text;
            try { text = JSON.stringify(value === undefined ? null : value); } catch (e) { deliver(false, 'Result is not JSON: ' + e); return; }
            deliver(true, text === undefined ? 'null' : text);
          }, (error) => deliver(false, String((error && error.message) || error)));
          return true;
        })();
    """.trimIndent()

    /** The name of the function `transportScript` defines. */
    const val TRANSPORT_NAME = "__npia_gm_xmlhttp_request"

    private fun literal(value: String) = Json.encodeToString(JsonPrimitive.serializer(), JsonPrimitive(value))

    /** The name of the page function native code calls with a request's reply; unguessable, so pages cannot answer for the app. */
    fun replyName(token: String) = "__npia_reply_$token"

    /**
     * Script that defines `window.__npia_gm_xmlhttp_request(details)` in the top frame only: an async
     * function that hands the JSON of `details` to `__npiaBridge.request(token, id, json)` and resolves
     * to the JSON the app answers with. Frames of other sites (ads) run the script too but stay out,
     * and without `token` the bridge ignores a request, so only the page itself can reach the app's
     * HTTP client this way. Safe to run more than once.
     */
    fun transportScript(token: String): String = """
        (function () {
          if (window.top !== window || window.$TRANSPORT_NAME) return;
          const token = ${literal(token)};
          const pending = new Map();
          let next = 0;
          window[${literal(replyName(token))}] = function (id, text) {
            const resolve = pending.get(id);
            if (!resolve) return;
            pending.delete(id);
            let value;
            try { value = JSON.parse(text); } catch (e) { value = { error: 'The app sent an unreadable reply' }; }
            resolve(value);
          };
          window.$TRANSPORT_NAME = function (details) {
            return new Promise(function (resolve) {
              const id = String(++next);
              pending.set(id, resolve);
              try {
                window.$BRIDGE_NAME.request(token, id, JSON.stringify(details || {}));
              } catch (e) {
                pending.delete(id);
                resolve({ error: String((e && e.message) || e) });
              }
            });
          };
        })();
    """.trimIndent()

    /** The script that hands `id`'s reply to the page. */
    fun replyScript(token: String, id: String, reply: String): String =
        "(function () { const f = window[${literal(replyName(token))}]; if (f) f(${literal(id)}, ${literal(reply)}); })();"

    /** Expression that returns and clears a parked result, or null. */
    fun pollScript(id: String): String {
        val key = Json.encodeToString(JsonPrimitive.serializer(), JsonPrimitive(id))
        return "(function () { const r = window.$RESULTS_NAME && window.$RESULTS_NAME[$key]; " +
            "if (!r) return null; delete window.$RESULTS_NAME[$key]; return r; })();"
    }

    /** Decodes the JSON value `evaluateJavascript` hands back for `pollScript`. */
    fun decodePolled(raw: String?): Result<String>? {
        val element = runCatching { Json.parseToJsonElement(raw ?: "null") }.getOrNull() ?: return null
        if (element is JsonNull || element !is JsonObject) return null
        val ok = (element["ok"] as? JsonPrimitive)?.booleanOrNull ?: false
        val payload = (element["payload"] as? JsonPrimitive)?.contentOrNull ?: "null"
        return outcome(ok, payload)
    }

    fun outcome(ok: Boolean, payload: String): Result<String> =
        if (ok) Result.success(payload) else Result.failure(WebEngineException("Script failed: $payload"))

    /** The plain string inside an `evaluateJavascript` result (`"\"text\""` becomes `text`). */
    fun decodeString(raw: String?): String? {
        val element = runCatching { Json.parseToJsonElement(raw ?: "null") }.getOrNull() ?: return null
        return (element as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull
    }
}
