package com.shirochi.npia.app

import android.app.Application
import com.shirochi.npia.app.pdf.AndroidPdfWriter
import com.shirochi.npia.output.Writers

class NpiaApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        Writers.pdfWriter = AndroidPdfWriter()
    }
}
