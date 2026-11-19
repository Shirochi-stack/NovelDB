package com.shirochi.npia.sources.kpage

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** What the ticket script did for one episode (desktop `kpage_auto_purchase`). */
internal class KpageTicketAnswer(
    /** Codes of the free tickets the book handed over on this visit (a gift received, tickets that were waiting). */
    val claimed: List<String>,
    /** Code of the ticket that opened the episode, or "" when none did. */
    val used: String,
    val signedOut: Boolean,
    val error: String,
    /** Why the page's gift ticket was not received, when it was offered and refused. */
    val giftError: String,
)

/**
 * Auto purchase for kpage: opens one paid episode with a ticket the account already holds. The
 * requests are the content page's own, made from inside it so they carry its login: the free
 * tickets waiting for the book are collected and a gift ticket is received (the page's "Receive"
 * button), then a ticket is used. Gift tickets go first, then the ticket the site itself would use
 * (a wait-for-free or rental ticket before one that keeps the episode). The site's cash endpoints
 * are never called, so nothing is bought.
 */
internal object KpageTickets {
    /** What each ticket code is, for the log. RT: a rental ticket, TT: one that keeps the episode. */
    private val names = mapOf(
        "RT00" to "a new member gift ticket", "RT05" to "a wait-for-free ticket", "RT06" to "a gift ticket",
        "RT07" to "a gift ticket", "RT10" to "a wait-for-free ticket", "TT05" to "a wait-for-free ticket",
        "TT06" to "a gift ticket", "TT07" to "a gift ticket",
    )

    fun describe(code: String): String = names[code] ?: if (code.startsWith("RT")) "a rental ticket" else "a ticket"

    /** The function body for `WebPageDriving.evaluateJson`, run on the book's content page. */
    fun script(series: String, product: String, claim: Boolean, preferred: List<String>): String {
        val prefer = preferred.filter { CODE.matches(it) }.joinToString(",") { "'$it'" }
        return "const seriesId = '${digits(series)}', productId = '${digits(product)}', claim = $claim, preferred = [$prefer];\n$BODY"
    }

    fun parse(raw: String): KpageTicketAnswer {
        val root = runCatching { Json.parseToJsonElement(raw) }.getOrNull() as? JsonObject
            ?: return KpageTicketAnswer(emptyList(), "", false, "unexpected answer", "")
        fun text(key: String) = (root[key] as? JsonPrimitive)?.content.orEmpty()
        return KpageTicketAnswer(
            claimed = (root["claimed"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.content },
            used = text("used"),
            signedOut = text("login") == "true",
            error = text("error"),
            giftError = text("giftError"),
        )
    }

    private val CODE = Regex("^(RT|TT)\\d+$")

    private fun digits(text: String) = text.filter(Char::isDigit)

    private val BODY = """
        // The gateway sits on the page's own host with a "bff-" prefix.
        const api = 'https://bff-' + location.host.replace(/^bff-/, '') + '/api/gateway/api/v1';
        const out = { claimed: [], tried: [], used: '', login: false, error: '', giftError: '' };
        const call = async (path, form) => {
            const options = { credentials: 'include' };
            if (form) {
                options.method = 'POST';
                options.headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
                options.body = new URLSearchParams(form).toString();
            }
            try {
                const response = await fetch(api + path, options);
                let data = null;
                try { data = await response.json(); } catch (e) {}
                return { status: response.status, data: data || {} };
            } catch (e) {
                return { status: 0, data: { message: String(e) } };
            }
        };
        const code = (answer) => { const value = answer.data.result_code; return Number(value === undefined || value === null ? -1 : value); };
        const why = (answer) => String(answer.data.message || answer.data.message_key || ('HTTP ' + answer.status));
        const signedOut = (answer) => answer.status === 401 || /authorize|login/i.test(String(answer.data.message_key || ''));

        if (claim) {
            // Opening a book's page hands over the free tickets waiting for it; this is that request.
            const waiting = await call('/ticket/free_check?series_id=' + seriesId);
            if (signedOut(waiting)) { out.login = true; return out; }
            for (const item of ((waiting.data.result || {}).list || [])) {
                if (item && item.ticket_type) out.claimed.push(String(item.ticket_type));
            }
            // The page's "Receive" button for a gift ticket.
            const overview = await call('/content/overview?series_id=' + seriesId);
            const seen = new Set();
            const find = (value) => {
                if (!value || typeof value !== 'object' || seen.has(value)) return null;
                seen.add(value);
                if (value.today_gift && typeof value.today_gift === 'object') return value.today_gift;
                for (const child of Object.values(value)) {
                    const found = find(child);
                    if (found) return found;
                }
                return null;
            };
            const gift = find(overview.data);
            const uid = gift && (gift.today_gift_uid || gift.uid);
            if (uid && gift.is_received !== true && gift.received !== true) {
                const received = await call('/ticket/free', { uid: String(uid), series_id: String(seriesId), type: 'TODAY_GIFT' });
                if (code(received) === 0) out.claimed.push(String(gift.ticket_type || 'RT06'));
                else out.giftError = why(received);
            }
        }

        const ready = await call('/ticket/ready_to_use?product_id=' + productId + '&include_series=true');
        if (signedOut(ready)) { out.login = true; return out; }
        const available = (ready.data.result || {}).available || {};
        // Gift tickets first, then what the site itself would use: a rental ticket before one that keeps the episode.
        const order = [];
        for (const type of [...preferred, ...out.claimed, available.ticket_rental_type, available.ticket_own_type]) {
            if (type && /^(RT|TT)\d+${'$'}/.test(String(type)) && !order.includes(String(type))) order.push(String(type));
        }
        if (!order.length) {
            out.error = code(ready) === 0 ? 'no ticket available' : why(ready);
            return out;
        }
        for (const type of order) {
            const used = await call('/ticket/use', { product_id: String(productId), ticket_type: type });
            out.tried.push(type);
            if (code(used) === 0) { out.used = type; return out; }
            out.error = why(used);
        }
        return out;
    """.trimIndent()
}
