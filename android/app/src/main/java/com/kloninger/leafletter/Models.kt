package com.kloninger.leafletter

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * A single campaign returned by /api/campaigns/.
 *
 * Detail-only fields (instructions, contactInfo, bbox) are absent from the
 * list endpoint and will be null when loaded from there.
 */
@Serializable
data class Campaign(
    val id: Int,
    val name: String,
    val slug: String,
    @SerialName("start_date") val startDate: String? = null,
    @SerialName("end_date") val endDate: String? = null,
    @SerialName("hero_image_url") val heroImageUrl: String? = null,
    @SerialName("map_status") val mapStatus: String = "pending",
    val instructions: String? = null,
    @SerialName("contact_info") val contactInfo: String? = null,
    val bbox: List<List<Double>>? = null,
) {
    /** True when the map is usable (may have minor data warnings). */
    val isReady: Boolean
        get() = mapStatus == "ready" || mapStatus == "warning"

    /** Human-readable date range, or null if no start date is available. */
    val dateRangeText: String?
        get() {
            val start = startDate ?: return null
            return if (endDate != null) "$start \u2013 $endDate" else "Starting $start"
        }
}
