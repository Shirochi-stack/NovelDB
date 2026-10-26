package com.shirochi.npia.app.model

import kotlinx.serialization.KSerializer
import java.io.File

/** A small serializable value persisted as JSON, written atomically through a temp file. */
class JsonFileStore<T>(private val file: File, private val serializer: KSerializer<T>) {
    fun load(): T? = runCatching { JobItem.json.decodeFromString(serializer, file.readText()) }.getOrNull()

    @Synchronized
    fun save(value: T) {
        runCatching {
            file.parentFile?.mkdirs()
            val temp = File(file.parentFile, file.name + ".tmp")
            temp.writeText(JobItem.json.encodeToString(serializer, value))
            if (!temp.renameTo(file)) {
                file.delete()
                temp.renameTo(file)
            }
        }
    }
}
