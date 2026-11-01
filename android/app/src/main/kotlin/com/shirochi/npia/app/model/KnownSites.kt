package com.shirochi.npia.app.model

import android.content.Context
import com.shirochi.npia.webengine.SiteCookies
import com.shirochi.npia.webengine.VisibleBrowser
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Hosts the user has opened in the browser, newest first. `CookieManager` cannot enumerate
 * domains, so this is how the Sites screen knows where the user may be signed in.
 */
class KnownSites(context: Context) {
    private val prefs = context.getSharedPreferences("known_sites", Context.MODE_PRIVATE)
    private val _hosts = MutableStateFlow(load())
    val hosts: StateFlow<List<String>> = _hosts.asStateFlow()

    init {
        VisibleBrowser.onVisit = ::record
    }

    fun record(host: String) {
        val next = (listOf(host) + _hosts.value.filter { it != host }).take(MAX)
        if (next == _hosts.value) return
        _hosts.value = next
        prefs.edit().putString(KEY, next.joinToString("\n")).apply()
    }

    fun forget(host: String) {
        _hosts.value = _hosts.value.filter { it != host }
        prefs.edit().putString(KEY, _hosts.value.joinToString("\n")).apply()
    }

    /** Sites that currently hold cookies, i.e. where a login may be saved. */
    fun withCookies(): List<String> = _hosts.value.filter(SiteCookies::hasCookies)

    private fun load(): List<String> = prefs.getString(KEY, null).orEmpty().lines().filter { it.isNotBlank() }

    private companion object {
        const val KEY = "hosts"
        const val MAX = 200
    }
}
