import Foundation
import CoreLocation

enum APIError: LocalizedError {
    case badResponse(Int)
    case noData

    var errorDescription: String? {
        switch self {
        case .badResponse(let code): return "Server returned \(code)"
        case .noData: return "No data received"
        }
    }
}

actor APIClient {
    static let shared = APIClient()

    private let baseURL: String
    private let session: URLSession

    init(baseURL: String = Config.baseURL) {
        self.baseURL = baseURL
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    // MARK: - Campaign list

    func fetchCampaigns() async throws -> [Campaign] {
        let url = try makeURL("/api/campaigns/")
        let (data, response) = try await session.data(from: url)
        try checkResponse(response)
        return try JSONDecoder().decode([Campaign].self, from: data)
    }

    // MARK: - Campaign detail

    func fetchCampaignDetail(slug: String) async throws -> Campaign {
        let url = try makeURL("/api/campaigns/\(slug)/")
        let (data, response) = try await session.data(from: url)
        try checkResponse(response)
        return try JSONDecoder().decode(Campaign.self, from: data)
    }

    // MARK: - Streets GeoJSON

    func fetchStreets(slug: String) async throws -> [Street] {
        let url = try makeURL("/c/\(slug)/streets.geojson")
        let (data, response) = try await session.data(from: url)
        try checkResponse(response)
        let collection = try JSONDecoder().decode(GeoJSONFeatureCollection.self, from: data)
        return collection.features.compactMap { feature in
            guard let id = feature.id else { return nil }
            let coords = feature.geometry.coordinates.map {
                CLLocationCoordinate2D(latitude: $0[1], longitude: $0[0])
            }
            guard !coords.isEmpty else { return nil }
            return Street(id: id, name: feature.properties?.name ?? "", coordinates: coords)
        }
    }

    // MARK: - Submit trip

    func submitTrip(slug: String, segmentIds: [Int], workerName: String, notes: String) async throws -> TripResponse {
        let url = try makeURL("/c/\(slug)/trip/")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body = TripSubmission(segmentIds: segmentIds, workerName: workerName, notes: notes)
        request.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: request)
        try checkResponse(response)
        return try JSONDecoder().decode(TripResponse.self, from: data)
    }

    // MARK: - Helpers

    private func makeURL(_ path: String) throws -> URL {
        guard let url = URL(string: baseURL + path) else {
            throw URLError(.badURL)
        }
        return url
    }

    private func checkResponse(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.badResponse(http.statusCode)
        }
    }
}
