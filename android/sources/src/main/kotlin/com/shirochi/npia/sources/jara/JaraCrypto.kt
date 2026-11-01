package com.shirochi.npia.sources.jara

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Chapter text cipher: CryptoJS AES-CBC with a UTF-8 key and IV, PKCS#7 padding. */
internal object JaraCrypto {
    class Failure(message: String) : Exception(message)

    fun decrypt(base64: String, key: String, iv: String): String {
        val data = runCatching { Base64.getMimeDecoder().decode(base64) }.getOrNull()
        if (data == null || data.isEmpty()) throw Failure("bad input")
        val plain = crypt(Cipher.DECRYPT_MODE, data, key, iv)
        return try {
            Charsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(plain))
                .toString()
        } catch (e: Exception) {
            throw Failure("not UTF-8")
        }
    }

    /** Used by tests to build fixtures. */
    fun encrypt(text: String, key: String, iv: String): String =
        Base64.getEncoder().encodeToString(crypt(Cipher.ENCRYPT_MODE, text.encodeToByteArray(), key, iv))

    private fun crypt(mode: Int, input: ByteArray, key: String, iv: String): ByteArray {
        val keyBytes = key.encodeToByteArray()
        val ivBytes = iv.encodeToByteArray()
        if (keyBytes.size !in setOf(16, 24, 32) || ivBytes.size != 16) throw Failure("bad key or IV")
        return try {
            Cipher.getInstance("AES/CBC/PKCS5Padding").run {
                init(mode, SecretKeySpec(keyBytes, "AES"), IvParameterSpec(ivBytes))
                doFinal(input)
            }
        } catch (e: Exception) {
            throw Failure("cipher: ${e.message}")
        }
    }
}
