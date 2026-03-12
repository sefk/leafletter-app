import SwiftUI
import WebKit

struct CampaignListView: View {
    @State private var campaigns: [Campaign] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showAbout = false

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("Loading campaigns…")
                } else if let error = errorMessage {
                    ContentUnavailableView("Unable to Load", systemImage: "exclamationmark.triangle", description: Text(error))
                        .overlay(alignment: .bottom) {
                            Button("Retry") { Task { await load() } }
                                .buttonStyle(.borderedProminent)
                                .padding()
                        }
                } else if campaigns.isEmpty {
                    ContentUnavailableView("No Active Campaigns", systemImage: "mappin.slash")
                } else {
                    List {
                        Section {
                            ForEach(campaigns) { campaign in
                                NavigationLink(destination: CampaignDetailView(campaign: campaign)) {
                                    CampaignRow(campaign: campaign)
                                }
                            }
                        } header: {
                            BannerView(onAbout: { showAbout = true })
                                .textCase(nil)
                                .listRowInsets(EdgeInsets())
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Leafletter")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { Task { await load() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(isLoading)
                }
            }
            .sheet(isPresented: $showAbout) {
                AboutWebView()
            }
        }
        .task { await load() }
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        do {
            campaigns = try await APIClient.shared.fetchCampaigns()
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

// MARK: - Banner

private struct BannerView: View {
    let onAbout: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Track where leaflets have been distributed by feet-on-the-street. Select a campaign below to browse coverage or log your own trip.")
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)
            Button(action: onAbout) {
                Text("About this app")
                    .font(.footnote.bold())
                    .foregroundStyle(.white)
                    .underline()
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(red: 0.10, green: 0.42, blue: 0.24))
    }
}

// MARK: - About sheet

private struct AboutWebView: UIViewRepresentable {
    private let url = URL(string: Config.baseURL + "/about/")!

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}
}

// MARK: - Row

private struct CampaignRow: View {
    let campaign: Campaign

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(campaign.name)
                .font(.headline)
            if let dates = campaign.dateRangeText {
                Text(dates)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if !campaign.isReady {
                Label("Map generating…", systemImage: "clock")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 4)
    }
}
