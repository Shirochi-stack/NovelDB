package com.shirochi.npia.sources.npia

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.File

/**
 * The desktop "Font Mapping" file (`mapping_path`, `font_mapper.py`): a JSON object mapping
 * each obfuscated character to the real one. The app keeps the imported copy in `file`.
 */
object NpiaFontMapping {
    @Volatile var mapping: Map<String, String> = emptyMap()
        private set

    @Volatile private var file: File? = null

    /** Loads the stored copy, if any. */
    fun attach(storage: File) {
        file = storage
        mapping = runCatching { parse(storage.readText()) }.getOrDefault(emptyMap())
    }

    /** Validates and stores a mapping file; returns the number of mapped characters. */
    fun import(text: String): Int {
        val parsed = parse(text)
        require(parsed.isNotEmpty()) { "The file has no character mappings." }
        file?.writeText(text)
        mapping = parsed
        return parsed.size
    }

    fun clear() {
        file?.delete()
        mapping = emptyMap()
    }

    internal fun parse(text: String): Map<String, String> {
        val root = Json.parseToJsonElement(text) as? JsonObject ?: throw IllegalArgumentException("Expected a JSON object.")
        return root.mapNotNull { (key, value) -> (value as? JsonPrimitive)?.takeIf { it.isString }?.let { key to it.content } }.toMap()
    }

    /** `FontMapper.decode`: replaces each mapped character. */
    fun decode(text: String, map: Map<String, String> = mapping): String {
        if (map.isEmpty()) return text
        val out = StringBuilder(text.length)
        var i = 0
        while (i < text.length) {
            val end = i + Character.charCount(text.codePointAt(i))
            val ch = text.substring(i, end)
            out.append(map[ch] ?: ch)
            i = end
        }
        return out.toString()
    }
}
