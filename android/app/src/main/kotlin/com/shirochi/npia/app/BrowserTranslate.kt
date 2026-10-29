package com.shirochi.npia.app

import android.content.Context
import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder

/** A language the Browser tab can translate pages into (a Google Translate language code). */
data class TranslateLanguage(val code: String, val name: String)

/**
 * Page translation for the Browser tab, which has none of its own.
 *
 * The page is translated in place: `browser-translate.js` loads Google's website translator into
 * it (or uses the copy a site loads for itself), so the real site (sign-in, the app's buttons) stays.
 * Sites whose security policy refuses the translator get a translated copy instead, served by Google
 * from `<site>.translate.goog`; the helpers below build that address and map it back to the original
 * site, which downloads need.
 */
object BrowserTranslate {
    const val DEFAULT_LANGUAGE = "en"

    val languages: List<TranslateLanguage> = listOf(
        TranslateLanguage("en", "English"),
        TranslateLanguage("ko", "Korean"),
        TranslateLanguage("ja", "Japanese"),
        TranslateLanguage("zh-CN", "Chinese (Simplified)"),
        TranslateLanguage("zh-TW", "Chinese (Traditional)"),
        TranslateLanguage("es", "Spanish"),
        TranslateLanguage("fr", "French"),
        TranslateLanguage("de", "German"),
        TranslateLanguage("pt", "Portuguese"),
        TranslateLanguage("ru", "Russian"),
        TranslateLanguage("id", "Indonesian"),
        TranslateLanguage("vi", "Vietnamese"),
        TranslateLanguage("th", "Thai"),
        TranslateLanguage("ar", "Arabic"),
    )

    /** The language for a stored code; English when it is missing or unknown. */
    fun language(code: String?): TranslateLanguage =
        languages.firstOrNull { it.code.equals(code, ignoreCase = true) } ?: languages.first()

    private const val PROXY_SUFFIX = ".translate.goog"
    private const val TRANSLATE_HOST = "translate.google.com"

    /** The address of Google's translated copy of `pageUrl`. */
    fun copyUrl(pageUrl: String, language: String): String =
        "https://$TRANSLATE_HOST/translate?sl=auto&tl=${enc(language)}&u=${enc(pageUrl)}"

    /** True for a page Google serves as a translated copy (`<site>.translate.goog`, or its `translate` address). */
    fun isTranslatedCopy(url: String): Boolean = originalUrl(url) != null

    /**
     * The original site's address for a translated copy, or null when `url` is not one.
     * - `global-example-com.translate.goog/novel/1?_x_tr_sl=auto&_x_tr_tl=en` is `https://global.example.com/novel/1`:
     *   the host's dots are dashes and its own dashes are doubled; Google's `_x_tr_*` query parameters are dropped.
     * - `translate.google.com/translate?u=<address>` is the address in `u`.
     */
    fun originalUrl(url: String): String? {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return null
        val host = uri.host?.lowercase() ?: return null
        if (host == TRANSLATE_HOST) {
            if (uri.path != "/translate") return null
            val target = queryPairs(uri.rawQuery).firstOrNull { it.first == "u" }?.second ?: return null
            return target.takeIf { it.startsWith("http://") || it.startsWith("https://") }
        }
        if (!host.endsWith(PROXY_SUFFIX)) return null
        val label = host.removeSuffix(PROXY_SUFFIX)
        if (label.isEmpty() || label.contains('.')) return null
        val original = label.split("--").joinToString("-") { it.replace('-', '.') }
        val query = queryPairs(uri.rawQuery).filterNot { it.first.startsWith("_x_tr_") }
        return buildString {
            append("https://").append(original)
            append(uri.rawPath.orEmpty().ifEmpty { "/" })
            if (query.isNotEmpty()) append('?').append(query.joinToString("&") { it.third })
            uri.rawFragment?.let { append('#').append(it) }
        }
    }

    /** `(name, value, as written)` for each parameter of a raw query string. */
    private fun queryPairs(rawQuery: String?): List<Triple<String, String, String>> =
        rawQuery.orEmpty().split('&').filter { it.isNotEmpty() }.map { part ->
            val name = part.substringBefore('=')
            val value = part.substringAfter('=', "")
            Triple(dec(name), dec(value), part)
        }

    private fun enc(text: String) = URLEncoder.encode(text, "UTF-8")
    private fun dec(text: String) = runCatching { URLDecoder.decode(text, "UTF-8") }.getOrDefault(text)

    /** `browser-translate.js` followed by the call that starts translating into `language`. */
    fun injection(context: Context, language: String): String {
        val script = context.assets.open("browser-translate.js").bufferedReader(Charsets.UTF_8).use { it.readText() }
        return script + "\nwindow.NpiaTranslate && window.NpiaTranslate.start('" + language.filter { it.isLetterOrDigit() || it == '-' } + "');"
    }
}

/**
 * How the page translation is going; the Browser tab polls the page for it. UNCHANGED: the translator
 * changed nothing (the page may be in that language already). FAILED: nothing was translated in time,
 * with no sign the site refused it. BLOCKED: the site's security policy refused the translator.
 */
enum class TranslateStatus { OFF, WORKING, TRANSLATED, UNCHANGED, FAILED, BLOCKED }
