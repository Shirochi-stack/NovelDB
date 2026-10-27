package com.shirochi.npia.app

import android.app.Application
import com.shirochi.npia.app.model.KnownSites
import com.shirochi.npia.app.model.LinkCart
import com.shirochi.npia.app.pdf.AndroidPdfWriter
import com.shirochi.npia.output.Writers
import com.shirochi.npia.sources.npia.NpiaFontMapping

class NpiaApplication : Application() {
    lateinit var downloads: DownloadManager
        private set
    lateinit var sites: KnownSites
        private set
    lateinit var cart: LinkCart
        private set

    override fun onCreate() {
        super.onCreate()
        Writers.pdfWriter = AndroidPdfWriter()
        NpiaFontMapping.attach(java.io.File(filesDir, "font-mapping.json"))
        Notifications.createChannels(this)
        sites = KnownSites(this)
        cart = LinkCart(java.io.File(filesDir, "link-cart.json"))
        downloads = DownloadManager(this)
    }
}
