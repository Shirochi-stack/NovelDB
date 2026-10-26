package com.shirochi.npia.webengine

import android.content.Context
import android.webkit.WebView

/** Builds the visible web view for a `WebLoginSession`; the UI wraps it (Compose `AndroidView`). */
object WebLoginWebView {
    fun create(context: Context, session: WebLoginSession): WebView {
        val webView = WebView(context)
        VisibleBrowser.configure(webView, object : VisibleBrowserListener {
            override fun onPage(url: String?, title: String?, loading: Boolean, progress: Int) {
                session.update(url, title, loading)
            }
        })
        webView.loadUrl(session.url)
        return webView
    }
}
