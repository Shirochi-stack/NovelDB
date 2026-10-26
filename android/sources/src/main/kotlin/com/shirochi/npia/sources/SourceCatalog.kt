package com.shirochi.npia.sources

import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.fenrir.FenrirAdapter
import com.shirochi.npia.sources.floo.FlooAdapter
import com.shirochi.npia.sources.globalnpia.GlobalNpiaAdapter
import com.shirochi.npia.sources.jara.JaraAdapter
import com.shirochi.npia.sources.kobo.KoboAdapter
import com.shirochi.npia.sources.kpage.KpageAdapter
import com.shirochi.npia.sources.maru.MaruAdapter
import com.shirochi.npia.sources.mpia.MpiaAdapter
import com.shirochi.npia.sources.npia.NpiaAdapter
import com.shirochi.npia.sources.ntk.NtkAdapter
import com.shirochi.npia.sources.nweb.NwebAdapter
import com.shirochi.npia.sources.qdn.QdnAdapter
import com.shirochi.npia.sources.rbooks.RbooksAdapter
import com.shirochi.npia.sources.reader.ReaderAdapter
import com.shirochi.npia.sources.sfc.SfcAdapter
import com.shirochi.npia.sources.xiyuwx.XiyuwxAdapter
import com.shirochi.npia.sources.yeduji.YedujiAdapter

object SourceCatalog {
    /** Checked in order; more specific matchers first (`reader` last). */
    val adapters: List<SourceAdapter> = listOf(
        NpiaAdapter(),
        GlobalNpiaAdapter(),
        MpiaAdapter(),
        KpageAdapter(),
        SfcAdapter(),
        NwebAdapter(),
        JaraAdapter(),
        RbooksAdapter(),
        NtkAdapter(),
        FlooAdapter(),
        QdnAdapter(),
        FenrirAdapter(),
        XiyuwxAdapter(),
        YedujiAdapter(),
        MaruAdapter(),
        KoboAdapter(),
        ReaderAdapter(),
    )

    fun adapterFor(url: String): SourceAdapter? = adapters.firstOrNull { it.matches(url) }

    fun require(url: String): SourceAdapter = adapterFor(url) ?: throw SourceException.UnsupportedUrl(url)

    fun adapterFor(id: SourceId): SourceAdapter = adapters.first { it.id == id }
}
