import SwiftUI
import UIKit
import WebKit

struct CampaignListView: View {
    @State private var campaigns: [Campaign] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var navigateToAbout = false

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
                            BannerView(onAbout: { navigateToAbout = true })
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
            // Navigate to About as a full-screen push (not a modal sheet) for
            // consistency with the rest of the app.
            .navigationDestination(isPresented: $navigateToAbout) {
                AboutWebView(returnSlug: nil)
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
        Group {
            Text("Track where leaflets have been distributed by feet-on-the-street. Select a campaign below to browse coverage or log your own trip. ")
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.9))
            + Text("About this app")
                .font(.footnote.bold())
                .foregroundStyle(.white)
                .underline()
        }
        .onTapGesture { onAbout() }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(red: 0.10, green: 0.42, blue: 0.24))
    }
}

// MARK: - About view (full-screen push navigation)

/// Full-screen About page pushed onto the navigation stack.
///
/// - Parameter returnSlug: When non-nil the "Back to campaigns" HTML link
///   navigates back to `/c/<slug>/` rather than `/`, so the native back
///   button pops to the correct campaign detail screen.  When nil (launched
///   from the campaign list) the link leads back to `/` and the native back
///   button pops to the list.
struct AboutWebView: View {
    let returnSlug: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        AboutWKWebViewRepresentable(onNavigateBack: { dismiss() }, returnSlug: returnSlug)
            .ignoresSafeArea(edges: .bottom)
            .navigationTitle("About")
            .navigationBarTitleDisplayMode(.inline)
    }
}

struct AboutWKWebViewRepresentable: UIViewRepresentable {
    let onNavigateBack: () -> Void
    /// When non-nil, intercept `/c/<slug>/` links so tapping "Back to
    /// campaigns" in the HTML header pops back to the campaign rather
    /// than navigating the WebView to the home page.
    let returnSlug: String?

    private let url = URL(string: Config.baseURL + "/about/")!

    func makeCoordinator() -> Coordinator {
        Coordinator(onNavigateBack: onNavigateBack, returnSlug: returnSlug, baseURL: Config.baseURL)
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let script = WKUserScript(
            source: """
            (function() {
                var meta = document.querySelector('meta[name=viewport]');
                if (!meta) { meta = document.createElement('meta'); meta.name = 'viewport'; document.head.appendChild(meta); }
                meta.content = 'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no';
            })();
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(script)
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    class Coordinator: NSObject, WKNavigationDelegate {
        let onNavigateBack: () -> Void
        let returnSlug: String?
        let baseURL: String

        init(onNavigateBack: @escaping () -> Void, returnSlug: String?, baseURL: String) {
            self.onNavigateBack = onNavigateBack
            self.returnSlug = returnSlug
            self.baseURL = baseURL
        }

        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard navigationAction.navigationType == .linkActivated,
                  let url = navigationAction.request.url,
                  url.absoluteString.hasPrefix(baseURL) else {
                decisionHandler(.allow)
                return
            }

            // Intercept the "Back" link.  When we were launched from a campaign
            // detail view, the back link is `/c/<slug>/`; from the list it is `/`.
            let isBackLink: Bool
            if let slug = returnSlug {
                isBackLink = url.path == "/c/\(slug)/" || url.path == "/"
            } else {
                isBackLink = url.path == "/" || url.path.isEmpty
            }

            if isBackLink {
                decisionHandler(.cancel)
                onNavigateBack()
            } else {
                decisionHandler(.allow)
            }
        }
    }
}

// MARK: - Row

private struct CampaignRow: View {
    let campaign: Campaign

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let urlString = campaign.heroImageUrl, let url = URL(string: urlString) {
                AsyncImage(url: url) { image in
                    image
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: .infinity)
                } placeholder: {
                    Color(.systemGray5)
                        .frame(maxWidth: .infinity)
                        .aspectRatio(32/9, contentMode: .fit)
                }
            }
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
            .padding(.vertical, 8)
            .padding(.horizontal, campaign.heroImageUrl != nil ? 12 : 0)
        }
        .padding(.vertical, campaign.heroImageUrl != nil ? 0 : 4)
    }
}
