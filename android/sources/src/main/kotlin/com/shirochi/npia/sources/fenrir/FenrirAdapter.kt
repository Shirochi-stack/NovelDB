package com.shirochi.npia.sources.fenrir

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.core.WebProfile
import com.shirochi.npia.core.evaluate
import com.shirochi.npia.core.withPage
import com.shirochi.npia.sources.websupport.fetchOk
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.coroutines.coroutineContext
import kotlin.random.Random

/**
 * fenrir: series and chapter lists come from the site's JSON API over plain HTTP. Chapter bodies
 * need the reader's read token, so they are fetched from inside a web page
 * (`WebProfile.EXTERNAL`) exactly like the desktop browser path. Paid chapters the account does
 * not own are refused; nothing is ever bought or unlocked with coins.
 */
class FenrirAdapter : SourceAdapter {
    override val id = SourceId.FENRIR
    override val tier = SourceTier.LIGHT_BROWSER

    override fun matches(url: String): Boolean = FenrirParsing.isFenrirUrl(url)

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val slug = FenrirParsing.urlParts(url).first
        if (slug.isEmpty()) throw SourceException.UnsupportedUrl(url)
        val encoded = FenrirParsing.encodePath(slug)
        val api = "${FenrirParsing.ORIGIN}/api/new/v2/series/$encoded"
        val listApi = "${FenrirParsing.ORIGIN}/api/new/v2/series/$encoded/chapters"
        val headers = mapOf(
            "User-Agent" to FenrirParsing.USER_AGENT,
            "Accept" to "application/json",
            "Referer" to FenrirParsing.ORIGIN + "/",
        )
        context.logger.log(LogLevel.INFO, "[fenrir] Fetching series $slug...")
        val infoResponse = context.http.fetchOk(api, headers)
        val listResponse = context.http.fetchOk(listApi, headers)
        val info = parseJson(infoResponse.text) as? JsonObject
            ?: throw SourceException.Parse("Unexpected series response.")
        val rowObjects: List<JsonElement> = when (val listObject = parseJson(listResponse.text)) {
            is JsonArray -> listObject
            is JsonObject -> {
                val data = listObject["data"] as? JsonArray
                val chapters = listObject["chapters"] as? JsonArray
                when {
                    data != null && data.isNotEmpty() -> data
                    chapters != null -> chapters
                    else -> emptyList()
                }
            }
            else -> throw SourceException.Parse("Unexpected series response.")
        }
        val rows = rowObjects.mapNotNull { it as? JsonObject }
        val book = FenrirParsing.makeBook(info, rows, slug) ?: throw SourceException.Parse("No chapters found.")
        val paidCount = book.chapters.count { it.isPaid }
        context.logger.log(LogLevel.INFO, "[fenrir] Book: ${book.title} - ${book.chapters.size} chapters ($paidCount locked paid)")
        return book
    }

    // Chapters

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        parseChapters(listOf(ref), book, context).firstOrNull() ?: ChapterResult.Failed(ref, "fenrir returned no result")

    /**
     * One web page for the whole batch, one chapter at a time: the reader binds its read token to
     * the chapter it registered last, so parallel requests would lock each other out.
     */
    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> {
        if (refs.isEmpty()) return emptyList()
        val state = BatchState(refs, context.onChapter)
        refs.forEachIndexed { offset, ref ->
            when {
                ref.isPaid -> state.results[offset] = ChapterResult.Refused(ref, PAID_REASON)
                ref.url == null -> state.results[offset] = ChapterResult.Failed(ref, "Chapter has no URL")
                else -> state.pending += offset
            }
        }
        if (state.pending.isNotEmpty()) {
            try {
                context.web.withPage(WebProfile.EXTERNAL) { page -> runBatch(page, book.id, state, context.logger) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                for (offset in state.pending) {
                    if (state.results[offset] == null) {
                        state.results[offset] = ChapterResult.Failed(refs[offset], e.message ?: e.toString())
                    }
                }
            }
        }
        val shared = context.shared(id)
        if (shared.intervalMax > 0) {
            val low = maxOf(0.0, minOf(shared.intervalMin, shared.intervalMax))
            val high = maxOf(low, shared.intervalMax)
            val pause = if (high > low) Random.nextDouble(low, high) else low
            if (pause > 0) delay((pause * 1000).toLong())
        }
        return refs.mapIndexed { offset, ref ->
            state.results[offset]
                ?: state.refusals[offset]?.let { ChapterResult.Refused(ref, it) }
                ?: ChapterResult.Failed(ref, "fenrir returned no chapter content")
        }
    }

    private class BatchState(val refs: List<ChapterRef>, notify: (ChapterResult) -> Unit) {
        val results = Results(refs.size, notify)
        val refusals = mutableMapOf<Int, String>()
        var pending = mutableListOf<Int>()
    }

    private suspend fun runBatch(page: WebPageDriving, slug: String, state: BatchState, logger: JobLogging) {
        val refs = state.refs
        val firstUrl = refs[state.pending[0]].url ?: return
        page.load(firstUrl, WebLoadState.DOM_CONTENT_LOADED, 45)
        var unlocked = false
        repeat(3) {
            if (state.pending.isEmpty()) return
            coroutineContext.ensureActive()
            if (!tokenActive(page)) {
                if (unlocked) return
                val url = refs[state.pending[0]].url ?: return
                if (!unlockReader(page, url, logger)) {
                    throw SourceException.Blocked("fenrir reader human check was not completed")
                }
                unlocked = true
            }
            val items = buildJsonArray {
                for (offset in state.pending) {
                    val (refSlug, chapterId) = FenrirParsing.splitRefId(refs[offset].id)
                    val chapterSlug = refs[offset].url?.let { FenrirParsing.urlParts(it).second } ?: refSlug
                    add(
                        buildJsonObject {
                            put("api", "/api/new/v2/series/$slug/${FenrirParsing.encodePath(chapterSlug)}")
                            put("chapterId", chapterId)
                        },
                    )
                }
            }
            val replies: List<JsonObject> = try {
                fetchReplies(page, items)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                logger.log(LogLevel.WARNING, "[fenrir] Chapter request failed: ${e.message ?: e}")
                return
            }
            val retry = mutableListOf<Int>()
            val retryDelays = mutableListOf<Double>()
            for ((position, offset) in state.pending.withIndex()) {
                if (position >= replies.size) break
                val reply = replies[position]
                val ref = refs[offset]
                val status = FenrirParsing.integer(reply["status"]) ?: 0
                if (status == 429) {
                    val retryAfter = (reply["retryAfter"] as? JsonPrimitive)?.takeIf { it.isString }?.content
                    retryDelays += FenrirParsing.retryDelay(retryAfter)
                    retry += offset
                    continue
                }
                val payload = reply["payload"] as? JsonObject
                if (payload == null) {
                    logger.log(LogLevel.WARNING, "[fenrir] ${ref.title}: HTTP $status")
                    if (status == 401 || status == 403) retry += offset
                    continue
                }
                if (FenrirParsing.paidLocked(payload)) {
                    state.results[offset] = ChapterResult.Refused(ref, PAID_REASON)
                    continue
                }
                val format = FenrirParsing.string(payload["content_format"])
                if (format == "locked" || !FenrirParsing.isTruthy(payload["content"])) {
                    state.refusals[offset] = "The reader did not release this chapter (locked or not published yet)"
                    retry += offset
                    continue
                }
                val content = FenrirParsing.buildChapter(payload, ref)
                if (content != null) {
                    state.results[offset] = ChapterResult.Content(content)
                } else {
                    logger.log(LogLevel.WARNING, "[fenrir] Empty chapter: ${ref.title}")
                    state.results[offset] = ChapterResult.Failed(ref, "Chapter content was empty")
                }
            }
            state.pending = retry
            if (state.pending.isNotEmpty()) {
                if (retryDelays.isNotEmpty()) {
                    val wait = retryDelays.maxOrNull() ?: 30.0
                    logger.log(LogLevel.INFO, "[fenrir] Rate limited; waiting $wait s...")
                    delay((minOf(wait, 600.0) * 1000).toLong())
                } else if (tokenActive(page)) {
                    // The token is fine, so these may be timed releases.
                    return
                }
            }
        }
    }

    // Web page steps

    private suspend fun tokenActive(page: WebPageDriving): Boolean = try {
        page.evaluate<Boolean>(TOKEN_CHECK_JS)
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        false
    }

    private suspend fun unlockReader(page: WebPageDriving, chapterUrl: String, logger: JobLogging): Boolean {
        try {
            page.load(chapterUrl, WebLoadState.DOM_CONTENT_LOADED, 45)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            logger.log(LogLevel.WARNING, "[fenrir] Reader page did not load: ${e.message ?: e}")
            return false
        }
        // Counted polls rather than a wall-clock deadline, so virtual-time tests cannot spin.
        val polls = (TOKEN_TIMEOUT_SECONDS / 1.5).toInt()
        repeat(polls) {
            if (tokenActive(page)) return true
            val clicked: List<String> = try {
                page.evaluate<List<String>>("return await ($DISMISS_JS)();")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                emptyList()
            }
            if (clicked.isNotEmpty()) logger.log(LogLevel.INFO, "[fenrir] Closed reader pop-up: " + clicked.joinToString(", "))
            delay(1500)
        }
        return tokenActive(page)
    }

    private suspend fun fetchReplies(page: WebPageDriving, items: JsonArray): List<JsonObject> {
        val raw = page.evaluateJson("const __fn = $FETCH_JS; return await __fn($items);")
        val array = parseJson(raw) as? JsonArray ?: return emptyList()
        return array.map { it as? JsonObject ?: JsonObject(emptyMap()) }
    }

    private fun parseJson(text: String): JsonElement? = runCatching { Json.parseToJsonElement(text) }.getOrNull()

    internal companion object {
        const val PAID_REASON = "Paid chapter that this account does not own"

        /**
         * How long to wait for the reader to mint a read token (the desktop waits up to 300 s for
         * a person to tick the human check; an offscreen web view cannot show it, so wait less).
         */
        const val TOKEN_TIMEOUT_SECONDS = 60.0

        val TOKEN_CHECK_JS = """
            const r = await fetch('/api/read-token', {credentials: 'include'});
            if (!r.ok) return false;
            const j = await r.json();
            return !!(j && j.active);
        """.trimIndent()

        /**
         * Runs in the reader tab. Fetches chapters with the page's read-token cookie; encrypted
         * replies are decoded the same way the reader does.
         */
        val FETCH_JS = """
            async (items) => {
              const fragShuffle = async (token, id, n) => {
                if (n < 2) return n === 1 ? [0] : [];
                const d = await crypto.subtle.digest('SHA-256',
                  new TextEncoder().encode(`${'$'}{token}:${'$'}{id}:fr-frag-v1`));
                const v = new DataView(d);
                const s = Uint32Array.of(v.getUint32(0, true), v.getUint32(4, true),
                                         v.getUint32(8, true), v.getUint32(12, true));
                const next = () => {
                  const t = s[0];
                  let r = (t ^ (t << 11)) >>> 0; r = (r ^ (r >>> 8)) >>> 0;
                  const c = s[3]; s[0] = s[1]; s[1] = s[2]; s[2] = c;
                  const f = (c ^ (c >>> 19) ^ r) >>> 0; s[3] = f; return f;
                };
                const order = Array.from({length: n}, (_, i) => i);
                for (let b = n - 1; b > 0; b--) {
                  const w = next() % (b + 1); [order[b], order[w]] = [order[w], order[b]];
                }
                return order;
              };
              const decrypt = async (payload) => {
                const tokenResp = await fetch('/api/read-token', {credentials: 'include'});
                if (!tokenResp.ok) return null;
                const tokenData = await tokenResp.json();
                const token = tokenData && tokenData.active ? tokenData.token : null;
                if (!token) return null;
                let content = payload.content;
                if (Array.isArray(content)) {
                  const order = await fragShuffle(token, payload.id, content.length);
                  const out = new Array(content.length);
                  for (let i = 0; i < content.length; i++) out[order[i]] = content[i];
                  content = out.join('');
                }
                const keyBytes = await crypto.subtle.digest('SHA-256',
                  new TextEncoder().encode(`${'$'}{token}:${'$'}{payload.id}:fr-content-v1`));
                const key = await crypto.subtle.importKey('raw', keyBytes,
                  {name: 'AES-GCM'}, false, ['decrypt']);
                const raw = Uint8Array.from(atob(content), c => c.charCodeAt(0));
                if (raw.length <= 28) return null;
                const plain = await crypto.subtle.decrypt(
                  {name: 'AES-GCM', iv: raw.slice(0, 12)}, key, raw.slice(12));
                return new TextDecoder().decode(plain);
              };
              const fetchOne = async (item) => {
                try {
                  if (item.chapterId) {
                    await fetch('/api/read-token', {
                      method: 'POST', credentials: 'include',
                      headers: {'Content-Type': 'application/json',
                                Accept: 'application/json'},
                      body: JSON.stringify({chapter_id: String(item.chapterId)}),
                    }).catch(() => null);
                  }
                  const resp = await fetch(item.api, {credentials: 'include',
                                                      headers: {Accept: 'application/json'}});
                  if (!resp.ok) return {status: resp.status,
                    retryAfter: resp.headers.get('Retry-After')};
                  const payload = await resp.json();
                  if (payload.content_format === 'encrypted') {
                    const plain = await decrypt(payload).catch(() => null);
                    if (plain === null) return {status: resp.status, payload: {
                      ...payload, content: '', content_format: 'locked'}};
                    payload.content = plain;
                    payload.content_format = 'html';
                  }
                  return {status: resp.status, payload};
                } catch (e) {
                  return {status: 0, error: String(e)};
                }
              };
              const out = [];
              for (const item of items) out.push(await fetchOne(item));
              return out;
            }
        """.trimIndent()

        /** Clears the reader's own pop-ups (cookie banner, notices, content-warning dialogs). */
        val DISMISS_JS = """
            () => {
              const visible = (el) => {
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && st.visibility !== 'hidden'
                  && st.display !== 'none';
              };
              const label = (el) => ((el.innerText || '') + ' '
                + (el.getAttribute('aria-label') || '') + ' '
                + (el.getAttribute('title') || '')).trim().toLowerCase();
              const clicked = [];
              const press = (el) => { el.click(); clicked.push(label(el).slice(0, 40)); };
              const buttons = (root) => [...root.querySelectorAll(
                'button, [role=button], input[type=button], input[type=submit]')]
                .filter(visible);
              for (const b of buttons(document)) {
                const t = label(b);
                if (t === 'essential only' || t.startsWith('dismiss notice')
                    || t === 'dismiss' || t === 'got it') press(b);
              }
              const modals = [...document.querySelectorAll(
                '[role=dialog], [role=alertdialog], dialog[open], [aria-modal=true]')]
                .filter(visible);
              for (const modal of modals) {
                for (const box of modal.querySelectorAll(
                    'input[type=checkbox], [role=checkbox]')) {
                  const on = box.checked || box.getAttribute('aria-checked') === 'true';
                  if (!on && visible(box)) { box.click(); clicked.push('checkbox'); }
                }
                const next = buttons(modal).find((b) => /^(continue|proceed|confirm|i understand|i agree|agree|accept|ok|okay|yes|read|start reading|continue reading)\b/
                  .test(label(b)) && !b.disabled);
                const close = buttons(modal).find((b) => /^(close|dismiss|\u00D7|x)${'$'}/
                  .test(label(b)));
                if (next) press(next); else if (close) press(close);
              }
              return clicked;
            }
        """.trimIndent()
    }
}

/** Chapter slots that report each result to the progress hook the first time it is set. */
private class Results(size: Int, private val notify: (ChapterResult) -> Unit) {
    private val items = arrayOfNulls<ChapterResult>(size)

    operator fun get(index: Int): ChapterResult? = items[index]

    operator fun set(index: Int, value: ChapterResult) {
        val first = items[index] == null
        items[index] = value
        if (first) notify(value)
    }
}
