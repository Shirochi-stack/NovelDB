package com.shirochi.npia.pipeline

import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.SourceException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

class DownloadPipelineTest {
    private val chapters = (1..6).map { ChapterRef("$it", it, "C$it", isPaid = it > 4) }

    @Test
    fun selectAppliesRangeAndPaidFilter() {
        assertEquals(listOf(2, 3, 4, 5), DownloadPipeline.select(chapters, SharedSettings(rangeFrom = 2, rangeTo = 5)).map { it.index })
        assertEquals(listOf(1, 2, 3, 4), DownloadPipeline.select(chapters, SharedSettings(skipPaid = true)).map { it.index })
    }

    @Test
    fun unknownUrlFailsFast() = runTest {
        val pipeline = DownloadPipeline(InMemorySecretStore(), File("build/tmp"))
        val error = runCatching { pipeline.run("https://example.com/none", AppSettings()) }.exceptionOrNull()
        assertEquals(SourceException.UnsupportedUrl::class, error!!::class)
    }
}
