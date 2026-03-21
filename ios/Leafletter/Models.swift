import Foundation

// MARK: - Campaign

struct Campaign: Codable, Identifiable {
    let id: Int
    let name: String
    let slug: String
    let startDate: String?
    let endDate: String?
    let heroImageUrl: String?
    let mapStatus: String
    let isTest: Bool
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
        case isTest = "is_test"
        case instructions
        case contactInfo = "contact_info"
        case bbox
    }

    var isReady: Bool { mapStatus == "ready" || mapStatus == "warning" }

    var dateRangeText: String? {
        guard let start = startDate else { return nil }
        if let end = endDate { return "\(start) – \(end)" }
        let prefix = start <= today() ? "Started" : "Starting"
        return "\(prefix) \(start)"
    }

    private func today() -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        return fmt.string(from: Date())
    }
}
