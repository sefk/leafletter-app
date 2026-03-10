import SwiftUI

struct CampaignListView: View {
    @State private var campaigns: [Campaign] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

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
                    List(campaigns) { campaign in
                        NavigationLink(destination: CampaignDetailView(campaign: campaign)) {
                            CampaignRow(campaign: campaign)
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
