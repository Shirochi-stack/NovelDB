package com.shirochi.npia.app

import android.app.Application
import com.shirochi.npia.app.model.KnownSites
import com.shirochi.npia.app.pdf.AndroidPdfWriter
import com.shirochi.npia.output.Writers
import com.shirochi.npia.sources.npia.NpiaFontMapping

class NpiaApplication : Application() {
    lateinit var downloads: DownloadManager
        private set
    lateinit var sites: KnownSites
        private set

    override fun onCreate() {
        super.onCreate()
        Writers.pdfWriter = AndroidPdfWriter()
        NpiaFontMapping.attach(java.io.File(filesDir, "font-mapping.json"))
        Notifications.createChannels(this)
        sites = KnownSites(this)
        downloads = DownloadManager(this)
    }
}
