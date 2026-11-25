package com.shirochi.npia.sources.session

import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SharedCookieStore

/**
 * A login whose cookies live in the shared cookie store the generic reader engine reads, with a copy
 * kept in the secret store and put back when the app starts. Unlike [SiteSessions], it keeps the login
 * by value as a whole cookie header — for sites that are not `SourceId` adapters (tapas, webtoons),
 * which sign in through their own HTTP endpoints and then download through the generic engine.
 *
 * The desktop keeps these the same way (`tapas_app_cookie.txt` / `webtoons_cookie.txt` plus a per-request
 * injection in `external_scraper.py`); here the cookie goes straight into the store every request shares.
 * Pure over [SharedCookieStore] and [SecretStore], so it is unit tested.
 */
class GenericCookieSession(
    private val key: SecretKey,
    /** Every host the login cookies are valid on; the cookie is written for each. */
    private val cookieUrls: List<String>,
    /** The cookie domain, without the leading dot; the write widens the cookie to its subdomains. */
    private val domain: String,
) {
    /** The saved cookie header, or null when nothing is saved. */
    fun savedHeader(secrets: SecretStore): String? = secrets.get(key)?.trim()?.takeIf { it.isNotEmpty() }

    fun hasSession(secrets: SecretStore): Boolean = savedHeader(secrets) != null

    /** Writes `header`'s cookies into the shared store and keeps a copy. False when it has no `name=value`. */
    fun save(header: String, secrets: SecretStore, cookies: SharedCookieStore?): Boolean {
        val pairs = pairs(header)
        if (pairs.isEmpty()) return false
        inject(pairs, cookies)
        secrets.set(key, pairs.joinToString("; ") { (name, value) -> "$name=$value" })
        return true
    }

    /** Puts the saved cookies back into the store (a web view may drop session cookies on restart). */
    fun restore(secrets: SecretStore, cookies: SharedCookieStore?): Int {
        val pairs = pairs(savedHeader(secrets) ?: return 0)
        inject(pairs, cookies)
        return pairs.size
    }

    /** Removes the login cookies from the store and drops the saved copy. */
    fun signOut(secrets: SecretStore, cookies: SharedCookieStore?) {
        val store = cookies
        if (store != null) {
            for (url in cookieUrls) for ((name, _) in pairs(savedHeader(secrets) ?: "")) store.remove(url, name)
        }
        secrets.set(key, null)
    }

    private fun inject(pairs: List<Pair<String, String>>, cookies: SharedCookieStore?) {
        val store = cookies ?: return
        for (url in cookieUrls) for ((name, value) in pairs) store.setCookie(url, name, value, ".$domain")
    }

    private fun pairs(header: String): List<Pair<String, String>> =
        header.split(';').mapNotNull { item ->
            val trimmed = item.trim()
            val eq = trimmed.indexOf('=')
            if (eq <= 0) null else trimmed.substring(0, eq) to trimmed.substring(eq + 1)
        }
}
