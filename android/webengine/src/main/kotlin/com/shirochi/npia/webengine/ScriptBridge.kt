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
