package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.jsoup.Jsoup
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.nodes.TextNode
import java.net.URI

/**
 * Bounded discovery from the public intro catalog, including anonymous R19 authors, ported from
 * `npia_intro_discovery.py`. It reads the default list and the first catalog page, then pages
 * through the catalog's own "exclude what you have seen" API while the request budget lasts. The
 * adult entries it finds carry their author when the novel page names one.
 */
internal class NpiaIntroDiscovery(
    private val http: HttpFetching,
    private val loginKey: String?,
    private val stopped: () -> Boolean = { false },
    private val log: (String) -> Unit = {},
) {
    private val found = LinkedHashMap<String, JsonObject>()
    /** Novel id to its author, from novel pages already opened (the desktop keeps this in its checkpoint). */
    private val resolvedAuthors = HashMap<String, String>()
    private var requestsMade = 0

    /** Returns the R19 novels found, as rows with `novel_no`, `novel_age` and, when known, `mem_no`. */
    suspend fun discover(maxRequests: Int = 6, delay: Double = 2.0): List<JsonObject> {
        var failures = 0
        // The default list has different seeds and no working pagination, so it goes first,
        // before the campaign pagination can use up the shared request budget.
        try {
            readCards("/intro", maxRequests, delay)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failures++
            log("Public intro default PARTIAL; discoveries retained: ${errorName(e)}: ${e.message}")
        }

        try {
            val initialIds = readCards("/intro/megabox", maxRequests, delay)
            if (initialIds.isEmpty()) return found.values.toList()
            var excluded = LinkedHashSet(initialIds)
            var page = 2
            while (requestsMade < maxRequests && !stopped()) {
                val fields = buildList {
                    add("cmd" to "ajax_intro_list")
                    add("page" to page.toString())
                    add("cate" to "megabox")
                    add("csrf" to "")
                    excluded.forEach { add("novel_no_arr[]" to it) }
                }
                val response = request("POST", "/proc/intro", maxRequests, delay, form = fields) ?: break
                val payload = parse(response)
                if (payload["result"].scalarText() != "success") throw ValueError("public intro API error")
                val rows = (payload["data"] as? JsonObject)?.get("list") as? JsonArray ?: throw ValueError("invalid public intro response")
                if (rows.isEmpty()) break
                val newIds = mutableListOf<String>()
                for (element in rows) {
                    val row = element as? JsonObject
                    if (row == null || !row["novel_no"].scalarText().isAsciiDigits()) throw ValueError("invalid public intro record")
                    val id = row["novel_no"].scalarText()
                    if (id !in excluded) newIds.add(id)
                    if (row["novel_age"].scalarText() == "19") found[id] = row
                }
                if (newIds.isEmpty()) throw ValueError("public intro repeated an excluded page")
                excluded = LinkedHashSet(excluded + newIds)
                page++
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failures++
            log("Public intro PARTIAL; discoveries retained: ${errorName(e)}: ${e.message}")
        } finally {
            log("Public intro: ${found.size} R19 novels discovered in $requestsMade requests")
        }
        return found.values.toList()
    }

    /** One request after `delay`, or null once stopped or the budget is spent; anything but HTTP 200 is an error. */
    private suspend fun request(
        method: String,
        path: String,
        maxRequests: Int,
        delay: Double,
        form: List<Pair<String, String>>? = null,
    ): HttpResponse? {
        sleepUnlessStopped(maxOf(0.0, delay), stopped)
        if (stopped() || requestsMade >= maxRequests) return null
        requestsMade++
        val url = ORIGIN + path
        val headers = NpiaAuth.headers(loginKey, form = form != null)
        val request = HttpRequest(url, method, headers, form?.let(NpiaAuth::formBody), timeoutSeconds = 30)
        val response = abortable(stopped) { http.send(request) } ?: return null
        if (response.status != 200 || response.wasRedirected(url)) throw ValueError("public intro HTTP ${response.status}")
        return response
    }

    private fun parse(response: HttpResponse): JsonObject {
        val element: JsonElement = Json.parseToJsonElement(response.text)
        return element as? JsonObject ?: throw ValueError("invalid public intro response")
    }

    private suspend fun readCards(path: String, maxRequests: Int, delay: Double): List<String> {
        val response = request("GET", path, maxRequests, delay) ?: return emptyList()
        val cards = Jsoup.parse(response.text).select(".intro-item[data-novel_no][data-novel_age]")
        if (cards.isEmpty()) throw ValueError("public intro cards unavailable")
        val initialIds = mutableListOf<String>()
        for (card in cards) {
            val id = card.attr("data-novel_no")
            if (!id.isAsciiDigits()) continue
            initialIds.add(id)
            if (card.attr("data-novel_age") != "19") continue
            val writer = card.selectFirst(".intro-novel-info p")
            val title = card.selectFirst(".novel-title")
            val base = mapOf<String, JsonElement>(
                "novel_no" to JsonPrimitive(id.toLong()),
                "novel_age" to JsonPrimitive(19),
                "writer_nick" to JsonPrimitive(writer?.let { strippedText(it) }.orEmpty()),
                "novel_name" to JsonPrimitive(title?.let { strippedText(it) }.orEmpty()),
            )
            found[id] = JsonObject(base)
            var owner = resolvedAuthors[id]
            if (owner == null) {
                val detailResponse = request("GET", "/novel/$id", maxRequests, delay) ?: continue
                val detail = Jsoup.parse(detailResponse.text)
                val canonical = detail.selectFirst("meta[property=og:url]")
                if (canonical != null && pathOf(canonical.attr("content")).trimEnd('/') != "/novel/$id") {
                    throw ValueError("intro detail identity mismatch")
                }
                val link = detail.selectFirst(".epnew-novel-info .in-writer .writer-name[href]")
                val match = link?.let { USER_PATH.matchEntire(it.attr("href")) }
                if (match != null) {
                    owner = match.groupValues[1]
                    resolvedAuthors[id] = owner
                }
            }
            if (owner != null) found[id] = JsonObject(base + ("mem_no" to JsonPrimitive(owner)))
        }
        return initialIds
    }

    /** BeautifulSoup's `get_text(strip=True)`: every text piece stripped and joined without separators. */
    private fun strippedText(element: Element): String = buildString { appendStripped(element) }

    private fun StringBuilder.appendStripped(node: Node) {
        when (node) {
            is TextNode -> append(node.wholeText.trim())
            is Element -> node.childNodes().forEach { appendStripped(it) }
            else -> Unit
        }
    }

    private fun pathOf(url: String): String = runCatching { URI(url).path }.getOrNull().orEmpty()

    companion object {
        val ORIGIN = "https://${NpiaParser.HOST}"
        private val USER_PATH = Regex("/user/(\\d+)/?")
    }
}
