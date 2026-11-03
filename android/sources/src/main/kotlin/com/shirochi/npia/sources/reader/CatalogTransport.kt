package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.WebEngineProviding

/**
 * The network of the Catalog's generic search page (`window.NovelDBTransport`): the same transport
 * the engines' page scripts use, so requests carry the web view's cookies and `{op: "window"}` runs
 * a script in a hidden page of the address (a site's bot check, HaruNeko's window scripts).
 */
object CatalogTransport {
    suspend fun handle(requestJson: String, http: HttpFetching, web: WebEngineProviding): String =
        WebToEpubTransport.handle(requestJson, http, web)
}
