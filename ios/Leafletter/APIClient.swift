import Foundation

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

    private var baseURL: String { Config.baseURL }
    private let session: URLSession

    init() {
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
