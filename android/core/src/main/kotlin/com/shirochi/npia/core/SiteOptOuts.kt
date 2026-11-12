package com.shirochi.npia.core

import java.security.MessageDigest

/**
 * Sites the app leaves alone because their owners asked.
 *
 * - The list holds SHA-256 digests of host names, not the names. A host also covers its subdomains.
 * - [matches] is asked wherever a link is about to be read: downloads, the Catalog's reader and its
 *   searches. The caller logs or shows [MESSAGE] and reads nothing.
 *
 * The desktop (`noveldb/core/site_optouts.py`) and iOS (`SiteOptOuts.swift`) keep the same list;
 * keep the three in step.
 */
object SiteOptOuts {
    const val MESSAGE = "Support for this site has been disabled at the request of the site owner."

    private val DIGESTS = setOf(
        "83aea3497826ee11580bef0f3b60a7fe6c7de15b927319f536433ea7e89b865b",
    )

    private val AUTHORITY = Regex("^(?:[A-Za-z][A-Za-z0-9+.-]*:)?//([^/?#\\\\]*)")

    /** The address (or bare host) belongs to a site whose owner opted out. `digests`: another list, for tests. */
    fun matches(url: String, digests: Set<String> = DIGESTS): Boolean {
        val labels = host(url)?.split('.')?.filter { it.isNotEmpty() } ?: return false
        // The host and each domain above it, down to two labels ("a.b.example.com", "b.example.com", "example.com").
        return (0 until maxOf(labels.size - 1, 1)).any { start -> sha256(labels.drop(start).joinToString(".")) in digests }
    }

    /** The host of an address, read without `URI` (which refuses addresses with spaces or odd host names). */
    private fun host(url: String): String? {
        val text = url.trim()
        if (text.isEmpty()) return null
        val authority = (AUTHORITY.find(text) ?: AUTHORITY.find("//$text"))?.groupValues?.get(1) ?: return null
        return authority.substringAfterLast('@').substringBefore(':').lowercase().trimEnd('.').takeIf { it.isNotEmpty() }
    }

    private fun sha256(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
}
