import SwiftUI

struct CampaignDetailView: View {
    let campaign: Campaign

    @State private var detail: Campaign?
    @State private var streets: [Street] = []
    @State private var selectedIds: Set<Int> = []
    @State private var isLoadingMap = true
    @State private var mapError: String?
    @State private var showTripSheet = false
    @State private var showInstructions = false

    private var activeCampaign: Campaign { detail ?? campaign }

    var body: some View {
        VStack(spacing: 0) {
            // Instructions banner (collapsible)
            if let instructions = activeCampaign.instructions, !instructions.isEmpty {
                InstructionsBanner(html: instructions, isExpanded: $showInstructions)
            }

            // Map
            ZStack(alignment: .bottomTrailing) {
                if isLoadingMap {
                    ProgressView("Loading map…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(Color(.systemGroupedBackground))
                } else if let error = mapError {
                    ContentUnavailableView("Map Error", systemImage: "map", description: Text(error))
                } else {
                    StreetMapView(
                        streets: streets,
                        selectedIds: $selectedIds,
                        bbox: activeCampaign.bbox
                    )
                    .ignoresSafeArea(edges: .bottom)
                }

                // Selection badge + Log Trip button
                VStack(alignment: .trailing, spacing: 12) {
                    if !selectedIds.isEmpty {
                        selectionBadge
                    }
                    logTripButton
                }
                .padding()
            }
        }
        .navigationTitle(campaign.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let info = activeCampaign.contactInfo, !info.isEmpty {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { showContactInfo(info) } label: {
                        Image(systemName: "person.circle")
                    }
                }
            }
        }
        .sheet(isPresented: $showTripSheet) {
            TripSubmitView(
                campaign: activeCampaign,
                selectedIds: Array(selectedIds)
            ) {
                selectedIds = []
                showTripSheet = false
            }
        }
        .task { await loadAll() }
    }

    // MARK: - Subviews

    private var selectionBadge: some View {
        Text("● \(selectedIds.count) block\(selectedIds.count == 1 ? "" : "s")")
            .font(.callout.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(Color(red: 0.1, green: 0.42, blue: 0.24))
            .clipShape(Capsule())
    }

    private var logTripButton: some View {
        Button {
            showTripSheet = true
        } label: {
            Label("Log a Trip", systemImage: "map.fill")
                .font(.headline)
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
        }
        .buttonStyle(.borderedProminent)
        .tint(Color(red: 0.1, green: 0.42, blue: 0.24))
        .disabled(selectedIds.isEmpty || isLoadingMap)
    }

    // MARK: - Load

    private func loadAll() async {
        async let detailTask = APIClient.shared.fetchCampaignDetail(slug: campaign.slug)
        async let streetsTask = APIClient.shared.fetchStreets(slug: campaign.slug)

        do {
            let (d, s) = try await (detailTask, streetsTask)
            detail = d
            streets = s
        } catch {
            mapError = error.localizedDescription
        }
        isLoadingMap = false
    }

    private func showContactInfo(_ info: String) {
        // Present as alert — keep it simple for v1
        guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              let root = scene.windows.first?.rootViewController else { return }
        let alert = UIAlertController(title: "Contact", message: info, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        root.present(alert, animated: true)
    }
}

// MARK: - Instructions banner

private struct InstructionsBanner: View {
    let html: String
    @Binding var isExpanded: Bool

    var plainText: String {
        // Strip HTML tags for a simple plain-text preview
        html.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation { isExpanded.toggle() }
            } label: {
                HStack {
                    Text("Instructions")
                        .font(.subheadline.bold())
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption)
                }
                .padding(.horizontal)
                .padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .background(Color(.secondarySystemBackground))

            if isExpanded {
                Text(plainText)
                    .font(.footnote)
                    .padding()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(.systemBackground))
            }
        }
        .overlay(alignment: .bottom) {
            Divider()
        }
    }
}
