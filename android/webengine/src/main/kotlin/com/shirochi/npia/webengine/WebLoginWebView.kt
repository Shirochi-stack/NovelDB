package com.shirochi.npia.webengine

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient

/** Builds the visible web view for a `WebLoginSession`; the UI wraps it (Compose `AndroidView`). */
object WebLoginWebView {
    @SuppressLint("SetJavaScriptEnabled")
    fun create(context: Context, session: WebLoginSession): WebView {
        val webView = WebView(context)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.javaScriptCanOpenWindowsAutomatically = true
        // Sign-in pages often open providers in a popup; without multiple windows they load here.
        webView.settings.setSupportMultipleWindows(false)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                session.update(url, view.title, loading = true)
            }

            override fun onPageFinished(view: WebView, url: String?) {
                session.update(url, view.title, loading = false)
                CookieManager.getInstance().flush()
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onReceivedTitle(view: WebView, title: String?) {
                session.update(view.url, title, session.isLoading.value)
            }
        }
        webView.loadUrl(session.url)
        return webView
    }
}
