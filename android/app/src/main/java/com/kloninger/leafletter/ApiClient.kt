package com.kloninger.leafletter

import kotlinx.serialization.json.Json
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Thin HTTP client for the Leafletter backend.
 *
 * Uses only the standard library (HttpURLConnection) and kotlinx.serialization
 * to keep the dependency footprint small.  All methods are suspend functions
 * that run the blocking I/O on the caller's dispatcher — callers should use
 * [kotlinx.coroutines.Dispatchers.IO].
 */
object ApiClient {

    private val json = Json {
        ignoreUnknownKeys = true   // future-proof: tolerate new API fields
        coerceInputValues = true   // handle null where non-null default exists
    }

    private const val TIMEOUT_MS = 30_000

    // MARK: - Public API

    /** Fetch the list of published campaigns. */
    suspend fun fetchCampaigns(): List<Campaign> {
        val body = get("/api/campaigns/")
        return json.decodeFromString(body)
    }

    // MARK: - Helpers

    private fun get(path: String): String {
        val url = URL(Config.baseUrl + path)
        val conn = url.openConnection() as HttpURLConnection
        return try {
            conn.connectTimeout = TIMEOUT_MS
            conn.readTimeout = TIMEOUT_MS
            conn.requestMethod = "GET"
            conn.setRequestProperty("Accept", "application/json")

            val code = conn.responseCode
            if (code !in 200..299) {
                throw IOException("Server returned HTTP $code for $path")
            }
            conn.inputStream.bufferedReader().readText()
        } finally {
            conn.disconnect()
        }
    }
}
