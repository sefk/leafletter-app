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
        let startFormatted = Self.formatDate(start) ?? start
        if let end = endDate {
            let endFormatted = Self.formatDate(end) ?? end
            return "\(startFormatted) – \(endFormatted)"
        }
        let prefix = start <= Self.isoToday() ? "Started" : "Starting"
        return "\(prefix) \(startFormatted)"
    }

    private static let isoParser: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    private static let display: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .long
        f.timeStyle = .none
        return f
    }()

    private static func formatDate(_ iso: String) -> String? {
        guard let d = isoParser.date(from: iso) else { return nil }
        return display.string(from: d)
    }

    private static func isoToday() -> String {
        return isoParser.string(from: Date())
    }
}
