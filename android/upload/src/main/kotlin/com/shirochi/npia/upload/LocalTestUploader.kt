package com.shirochi.npia.upload

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import java.io.File

/**
 * A host that posts to a server on the developer's machine, for checking uploads end to end
 * without a public host. Debug builds of the app install it (`UploaderCatalog.testHost`) when
 * they are given a base address; nothing else ever creates one. The server answers with the
 * link as the first line of the body, like temp.sh.
 */
class LocalTestUploader(base: String, override val maxBytes: Long? = null) : Uploader {
    override val id = ID
    override val displayName = "Local test server"
    override val retentionSeconds: Long? = null
    override val requiresAccount = false

    private val endpoint: HttpUrl = (base.trimEnd('/') + "/upload").toHttpUrl()

    override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
        val reply = context.postMultipart(endpoint, MultipartForm("file", file, name = context.nameFor(file)), progress = progress)
        reply.requireSuccess(displayName)
        val link = reply.text.lineSequence().firstOrNull().orEmpty().trim()
        if (!link.startsWith("http")) throw UploadException.Rejected(displayName, reply.snippet)
        return result(file, link, context)
    }

    companion object {
        const val ID = "localtest"
    }
}
