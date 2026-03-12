import Foundation
import CoreLocation

// MARK: - Campaign

struct Campaign: Codable, Identifiable {
    let id: Int
    let name: String
    let slug: String
    let startDate: String?
    let endDate: String?
    let heroImageUrl: String?
    let mapStatus: String
    // Detail fields (nil when loaded from list endpoint)
    let instructions: String?
    let contactInfo: String?
    let bbox: [[Double]]?

    enum CodingKeys: String, CodingKey {
        case id, name, slug
        case startDate = "start_date"
        case endDate = "end_date"
        case heroImageUrl = "hero_image_url"
        case mapStatus = "map_status"
        case instructions
        case contactInfo = "contact_info"
        case bbox
    }

    var isReady: Bool { mapStatus == "ready" || mapStatus == "warning" }

    var dateRangeText: String? {
        guard let start = startDate else { return nil }
        if let end = endDate { return "\(start) – \(end)" }
        return "Starting \(start)"
    }
}

// MARK: - Street (parsed from GeoJSON)

struct Street: Identifiable {
    let id: Int          // Street PK — used in trip submission as segment_id
    let name: String
    let coordinates: [CLLocationCoordinate2D]
}

// MARK: - GeoJSON decoding (streets)

struct GeoJSONFeatureCollection: Decodable {
    let features: [GeoJSONFeature]
}

struct GeoJSONFeature: Decodable {
    let id: Int?
    let geometry: GeoJSONGeometry
    let properties: StreetProperties?
}

struct GeoJSONGeometry: Decodable {
    let coordinates: [[Double]]
}

struct StreetProperties: Decodable {
    let name: String?
}

// MARK: - Coverage GeoJSON decoding

struct CoveredStreet {
    let coordinates: [CLLocationCoordinate2D]
    let tripId: String
}

struct CoverageFeatureCollection: Decodable {
    let features: [CoverageFeature]
}

struct CoverageFeature: Decodable {
    let geometry: GeoJSONGeometry
    let properties: CoverageProperties
}

struct CoverageProperties: Decodable {
    let tripId: String

    enum CodingKeys: String, CodingKey {
        case tripId = "trip_id"
    }
}

// MARK: - Trip

struct TripSubmission: Encodable {
    let segmentIds: [Int]
    let workerName: String
    let notes: String

    enum CodingKeys: String, CodingKey {
        case segmentIds = "segment_ids"
        case workerName = "worker_name"
        case notes
    }
}

struct TripResponse: Decodable {
    let status: String
    let tripId: String

    enum CodingKeys: String, CodingKey {
        case status
        case tripId = "trip_id"
    }
}
