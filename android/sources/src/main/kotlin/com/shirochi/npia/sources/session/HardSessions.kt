package com.shirochi.npia.sources.session

import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.qdn.QdnSite
import com.shirochi.npia.sources.rbooks.RbooksSite

/** The sources whose sign-in is a browser session (qdn and rbooks); sfc signs in through its app API. */
object HardSessions {
    val qdn: SessionSite get() = QdnSite.session
    val rbooks: SessionSite get() = RbooksSite.session

    val all: List<SessionSite> get() = listOf(qdn, rbooks)

    fun of(source: SourceId): SessionSite? = all.firstOrNull { it.source == source }

    /** The sites whose login cookies live on `host` (or a parent of it), for the Browser tab's sign-out. */
    fun forHost(host: String): List<SessionSite> {
        val name = host.lowercase().removePrefix("www.")
        return all.filter { name == it.domain || name.endsWith("." + it.domain) || it.domain.endsWith("." + name) }
    }
}
