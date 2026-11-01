package com.shirochi.npia.core

/**
 * Read/write access to the cookie store the web engine and `HttpFetching` share, the stand-in
 * for `HTTPCookieStorage.shared` on iOS. Adapters use it to check for login cookies or to seed
 * cookies an API expects.
 */
interface SharedCookieStore {
    /** Cookie name to value for requests to `url`. */
    fun cookies(url: String): Map<String, String>

    /** Stores a cookie for `url`; `domain` widens it to subdomains (`.example.com`). */
    fun setCookie(url: String, name: String, value: String, domain: String? = null)

    /**
     * Deletes the cookie `name` that `url` sees, on the host and on its parent domains. The default
     * does nothing, for stores that cannot delete.
     */
    fun remove(url: String, name: String) {}
}

/** Process-wide hook; `AndroidWebEngine` installs its `CookieManager` store here. Null in unit tests. */
object SharedCookies {
    @Volatile
    var store: SharedCookieStore? = null
}
