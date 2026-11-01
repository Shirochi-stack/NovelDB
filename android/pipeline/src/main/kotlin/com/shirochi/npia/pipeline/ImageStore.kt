package com.shirochi.npia.pipeline

import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.ImageRef
import java.io.Closeable
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * One job's image bytes, kept in files instead of memory until the book is written. A book of
 * picture chapters (sfc's bought VIP chapters are one image each) held in memory ran Android's
 * 256 MB limit out after about 550 chapters. Deleted when the job ends.
 */
class ImageStore private constructor(private val dir: File) : Closeable {
    private val files = ConcurrentHashMap<String, File>()
    private val counter = AtomicInteger()

    fun put(key: String, data: ByteArray) {
        val file = File(dir, "${counter.incrementAndGet()}.bin")
        file.writeBytes(data)
        files.put(key, file)?.delete()
    }

    fun get(key: String): ByteArray? = files[key]?.takeIf { it.isFile }?.readBytes()

    fun drop(key: String) {
        files.remove(key)?.delete()
    }

    /** Takes a finished chapter's image bytes (`SourceContext.keep`): they wait here until the image stage. */
    fun keep(result: ChapterResult): ChapterResult {
        if (result !is ChapterResult.Content || result.content.images.none { it.data != null }) return result
        val images = result.content.images.map { ref ->
            val data = ref.data ?: return@map ref
            put(HELD + ref.filename, data)
            ImageRef(ref.url, ref.filename)
        }
        return ChapterResult.Content(result.content.copy(images = images))
    }

    /** Bytes a source already handed over for `filename` (see `keep`). */
    fun held(filename: String): ByteArray? = get(HELD + filename)

    fun dropHeld(filename: String) = drop(HELD + filename)

    /** The book's images by file name, read from disk one at a time; `names` maps each file name to its key here. */
    fun view(names: Map<String, String>): Map<String, ByteArray> = Stored(LinkedHashMap(names))

    /** Re-encodes every image into this store under `tag`; the returned map reads the new bytes back. */
    fun transform(images: Map<String, ByteArray>, tag: String, change: (String, ByteArray) -> ByteArray): Map<String, ByteArray> {
        val names = LinkedHashMap<String, String>()
        for (name in images.keys) {
            val data = images[name] ?: continue
            put("$tag:$name", change(name, data))
            names[name] = "$tag:$name"
        }
        return view(names)
    }

    override fun close() {
        files.clear()
        dir.deleteRecursively()
    }

    private inner class Stored(private val names: Map<String, String>) : AbstractMap<String, ByteArray>() {
        override val keys: Set<String> get() = names.keys
        override val size: Int get() = names.size
        override fun containsKey(key: String): Boolean = key in names
        override fun get(key: String): ByteArray? = names[key]?.let { this@ImageStore.get(it) }

        override val entries: Set<Map.Entry<String, ByteArray>> = object : AbstractSet<Map.Entry<String, ByteArray>>() {
            override val size: Int get() = names.size
            override fun iterator(): Iterator<Map.Entry<String, ByteArray>> {
                val keys = names.keys.iterator()
                return object : Iterator<Map.Entry<String, ByteArray>> {
                    override fun hasNext() = keys.hasNext()
                    override fun next(): Map.Entry<String, ByteArray> {
                        val name = keys.next()
                        return object : Map.Entry<String, ByteArray> {
                            override val key = name
                            override val value: ByteArray get() = this@Stored[name] ?: ByteArray(0)
                        }
                    }
                }
            }
        }
    }

    companion object {
        private const val HELD = "held:"
        private const val PREFIX = "noveldb-images-"

        /** A new store under `parent` (the app's cache folder by default); folders a killed job left behind go. */
        fun create(parent: File = File(System.getProperty("java.io.tmpdir") ?: ".")): ImageStore {
            val dayAgo = System.currentTimeMillis() - 24 * 60 * 60 * 1000L
            parent.listFiles { file -> file.isDirectory && file.name.startsWith(PREFIX) && file.lastModified() < dayAgo }
                ?.forEach { it.deleteRecursively() }
            val dir = File(parent, PREFIX + UUID.randomUUID())
            check(dir.mkdirs()) { "Could not create $dir" }
            return ImageStore(dir)
        }
    }
}
