package com.kloninger.leafletter

/**
 * App-wide configuration.
 *
 * URL selection mirrors the iOS approach: debug builds point at the local dev
 * server; release builds use production.  Android uses BuildConfig.DEBUG
 * instead of the P_TRACED sysctl check used on iOS.
 *
 * Adjust DEV_BASE_URL to your machine's LAN IP when testing on a real device.
 */
object Config {
    private const val PROD_BASE_URL = "https://leafletter.app"
    private const val DEV_BASE_URL = "http://10.10.0.200:8000"

    val baseUrl: String
        get() = if (BuildConfig.DEBUG) DEV_BASE_URL else PROD_BASE_URL
}
