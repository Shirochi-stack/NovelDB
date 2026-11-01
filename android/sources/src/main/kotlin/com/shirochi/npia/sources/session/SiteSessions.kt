package com.shirochi.npia.sources.session

import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SharedCookieStore
import com.shirochi.npia.core.SharedCookies
import com.shirochi.npia.core.SourceId
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import java.util.concurrent.ConcurrentHashMap

/**
 * A site whose sign-in is a browser session: the user signs in in the app's browser and the login
 * lives in the cookie store that downloads share. `qdn` and `rbooks` are such sites.
 *
 * Some login cookies are session cookies, which the web view's cookie store may drop when the app
 * restarts (the desktop snapshots and restores them for the same reason), so `SiteSessions` keeps
 * a copy of the values in the secret store and puts them back.
 */
class SessionSite(
    val source: SourceId,
    /** The page the app's browser opens for signing in. */
    val signInUrl: String,
    /** URLs whose cookies are the login (every host the login cookies are valid on). */
    val cookieUrls: List<String>,
    /** Domain the login cookies belong to, without the leading dot. */
    val domain: String,
    /** The cookies that carry the login; only these are kept in the saved copy. */
    val cookieNames: List<String>,
    /** Plain-words description of what makes a login usable, for the UI ("ywkey and ywguid"). */
    val requirement: String,
    /**
     * Appended to the sign-in web view's user agent, so a site that serves a different session to its
     * own app can be told we are it (sfc appends its app token to try for an app session). Null keeps
     * the browser's user agent, which is what qdn and rbooks sign in with.
     */
    val appUserAgentSuffix: String? = null,
    private val usableWith: (Set<String>) -> Boolean,
) {
    fun isUsable(present: Set<String>): Boolean = usableWith(present)
}

data class SessionStatus(
    /** Enough login cookies are present for downloads to use them. */
    val usable: Boolean,
    /** Names of the login cookies currently in the cookie store. */
    val present: Set<String>,
    /** A saved copy exists in the secret store. */
    val hasSavedCopy: Boolean,
)

/**
 * Status, saved copy, restore and sign-out of browser-session logins. Pure Kotlin over
 * `SharedCookieStore` and `SecretStore`, so it is unit tested; the iOS app mirrors it over
 * `HTTPCookieStorage` and the Keychain.
 */
class SiteSessions(
    private val secrets: SecretStore,
    private val cookies: () -> SharedCookieStore? = { SharedCookies.store },
) {
    private fun key(site: SessionSite) = SecretKey.source(site.source, "sessionCookies")

    /** The login cookies the cookie store holds right now, name to value. */
    fun live(site: SessionSite): Map<String, String> {
        val store = cookies() ?: return emptyMap()
        val found = LinkedHashMap<String, String>()
        for (url in site.cookieUrls) {
            for ((name, value) in store.cookies(url)) {
                if (name in site.cookieNames && value.isNotEmpty() && name !in found) found[name] = value
            }
        }
        return found
    }

    private fun saved(site: SessionSite): Map<String, String> {
        val raw = secrets.get(key(site)) ?: return emptyMap()
        val parsed = runCatching { Json.parseToJsonElement(raw).jsonObject }.getOrNull() ?: return emptyMap()
        val values = (parsed["cookies"] as? JsonObject) ?: return emptyMap()
        return values.mapNotNull { (name, value) ->
            (value as? JsonPrimitive)?.contentOrNull?.takeIf { name in site.cookieNames && it.isNotEmpty() }?.let { name to it }
        }.toMap()
    }

    fun status(site: SessionSite): SessionStatus {
        val present = live(site).keys
        return SessionStatus(site.isUsable(present), present, saved(site).isNotEmpty())
    }

    /**
     * Saves the login cookies that are present. Nothing present keeps the earlier copy: a browser
     * that never opened the site says nothing about the login. Returns how many were saved.
     */
    fun snapshot(site: SessionSite): Int {
        val current = live(site)
        if (current.isEmpty()) return 0
        val value = buildJsonObject {
            put("saved", System.currentTimeMillis())
            put("cookies", buildJsonObject { current.forEach { (name, cookie) -> put(name, cookie) } })
        }
        secrets.set(key(site), Json.encodeToString(JsonObject.serializer(), value))
        return current.size
    }

    /** Puts saved login cookies the cookie store lacks back. Returns how many were restored. */
    fun restore(site: SessionSite): Int {
        val store = cookies() ?: return 0
        val present = live(site).keys
        val url = site.cookieUrls.firstOrNull() ?: return 0
        var restored = 0
        for ((name, value) in saved(site)) {
            if (name in present) continue
            store.setCookie(url, name, value, ".${site.domain}")
            restored += 1
        }
        return restored
    }

    /**
     * Restores at most once per process and site. The cookie store only loses session cookies when the
     * process restarts, so a later restore could only bring back a login the user ended in the browser.
     */
    fun restoreOnce(site: SessionSite): Int {
        if (!restored.add(site.source)) return 0
        return restore(site)
    }

    /** Removes the login cookies and the saved copy. */
    fun signOut(site: SessionSite) {
        val store = cookies()
        if (store != null) {
            for (url in site.cookieUrls) for (name in site.cookieNames) store.remove(url, name)
        }
        secrets.set(key(site), null)
    }

    /** Drops the saved copy only (the browser's own sign-out already removed the cookies). */
    fun forgetSavedCopy(site: SessionSite) {
        secrets.set(key(site), null)
    }

    companion object {
        private val restored: MutableSet<SourceId> = ConcurrentHashMap.newKeySet()

        /** Tests start from a fresh process. */
        internal fun resetProcessState() {
            restored.clear()
        }
    }
}
