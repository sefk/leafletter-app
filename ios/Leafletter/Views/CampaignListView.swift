import SwiftUI
import WebKit

struct CampaignListView: View {
    @State private var campaigns: [Campaign] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var navigateToAbout = false

    var body: some View {
        NavigationStack {
            let regular = campaigns.filter { !$0.isTest }
            let test = campaigns.filter { $0.isTest }
            List {
                Section {
                    ForEach(regular) { campaign in
                        ZStack(alignment: .leading) {
                            NavigationLink(destination: CampaignDetailView(campaign: campaign)) { EmptyView() }
                                .opacity(0)
                            CampaignRow(campaign: campaign)
                                .contentShape(Rectangle())
                        }
                        .listRowInsets(campaign.heroImageUrl != nil ? EdgeInsets() : nil)
                        .listRowSeparator(campaign.heroImageUrl != nil ? .hidden : .automatic)
                    }
                } header: {
                    BannerView(onAbout: { navigateToAbout = true })
                        .textCase(nil)
                        .listRowInsets(EdgeInsets())
                }

                if !test.isEmpty {
                    Section {
                        ForEach(test) { campaign in
                            ZStack(alignment: .leading) {
                                NavigationLink(destination: CampaignDetailView(campaign: campaign)) { EmptyView() }
                                    .opacity(0)
                                CampaignRow(campaign: campaign)
                                    .contentShape(Rectangle())
                            }
                            .listRowInsets(campaign.heroImageUrl != nil ? EdgeInsets() : nil)
                            .listRowSeparator(campaign.heroImageUrl != nil ? .hidden : .automatic)
                        }
                    } header: {
                        Text("Test Campaigns")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundStyle(.primary)
                            .textCase(nil)
                    }
                }
            }
            .listStyle(.plain)
            .refreshable { await load() }
            .overlay {
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
                }
            }
            .navigationTitle("Leafletter")
            .navigationBarTitleDisplayMode(.large)
            // Navigate to About as a full-screen push (not a modal sheet) for
            // consistency with the rest of the app.
            .navigationDestination(isPresented: $navigateToAbout) {
                AboutWebView(returnSlug: nil)
            }
            .safeAreaInset(edge: .bottom) {
                BetaBannerView()
            }
        }
        .task { await load() }
    }

    private func load() async {
        if campaigns.isEmpty {
            isLoading = true
        }
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

// MARK: - Beta banner

private struct BetaBannerView: View {
    var body: some View {
        Text("**Beta:** This app is in beta. If you have feedback or concerns, please [file a bug](https://github.com/sefk/leafletter-app/issues/) or [contact the owner](https://sef.kloninger.com/).")
            .font(.caption)
            .foregroundStyle(Color(red: 0.365, green: 0.251, blue: 0.216))
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color(red: 1.0, green: 0.973, blue: 0.882))
            .overlay(alignment: .top) {
                Rectangle()
                    .fill(Color(red: 0.976, green: 0.659, blue: 0.145))
                    .frame(height: 3)
            }
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
    @State private var tapCount = 0
    @State private var showingServerAlert = false

    var body: some View {
        AboutWKWebViewRepresentable(onNavigateBack: { dismiss() }, returnSlug: returnSlug)
            .ignoresSafeArea(edges: .bottom)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("About")
                        .font(.headline)
                        .onTapGesture {
                            tapCount += 1
                            if tapCount >= 3 {
                                tapCount = 0
                                showingServerAlert = true
                            }
                        }
                }
            }
            .alert(
                Config.isStaging ? "Using Staging Server" : "Using Production Server",
                isPresented: $showingServerAlert
            ) {
                Button(Config.isStaging ? "Switch to Production" : "Switch to Staging") {
                    Config.toggleStaging()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(Config.isStaging
                     ? "Currently pointing to \(Config.stagingURL). Switch back to production?"
                     : "Switch to \(Config.stagingURL)? Changes take effect on next app launch.")
            }
    }
}

struct AboutWKWebViewRepresentable: UIViewRepresentable {
    let onNavigateBack: () -> Void
    /// When non-nil, intercept `/c/<slug>/` links so tapping "Back to
    /// campaigns" in the HTML header pops back to the campaign rather
    /// than navigating the WebView to the home page.
    let returnSlug: String?

    private let url = URL(string: Config.baseURL + "/about/")!

    static var buildInfo: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "?"
        let build = info["CFBundleVersion"] as? String ?? "?"
        let dateStr: String
        if let execURL = Bundle.main.executableURL,
           let attrs = try? FileManager.default.attributesOfItem(atPath: execURL.path),
           let modDate = attrs[.modificationDate] as? Date {
            let fmt = DateFormatter()
            fmt.dateStyle = .medium
            fmt.timeStyle = .none
            dateStr = fmt.string(from: modDate)
        } else {
            dateStr = "unknown"
        }
        return "v\(version) (build \(build), \(dateStr))"
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onNavigateBack: onNavigateBack, returnSlug: returnSlug, baseURL: Config.baseURL)
    }

    func makeUIView(context: Context) -> WKWebView {
        let info = AboutWKWebViewRepresentable.buildInfo
        let config = WKWebViewConfiguration()
        // Allow window.open() / target="_blank" links to be handled by the
        // WKUIDelegate (which the Coordinator also implements) so we can
        // redirect them to Safari.
        config.preferences.javaScriptCanOpenWindowsAutomatically = false

        let script = WKUserScript(
            source: """
            (function() {
                var meta = document.querySelector('meta[name=viewport]');
                if (!meta) { meta = document.createElement('meta'); meta.name = 'viewport'; document.head.appendChild(meta); }
                meta.content = 'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no';

                // Signal to the page that we are running inside the iOS app so
                // the about page can set target="_blank" on external links.
                window.LEAFLETTER_IOS = true;

                var ver = document.createElement('div');
                ver.style.cssText = 'text-align:center;padding:12px;font-size:0.75rem;color:#888;font-family:-apple-system,sans-serif;';
                ver.textContent = '\(info)';
                document.body.appendChild(ver);
            })();
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(script)
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
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
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            // Open all external HTTP(S) link clicks in Safari — don't load them
            // inside the about-page WebView.
            if navigationAction.navigationType == .linkActivated,
               (url.scheme == "http" || url.scheme == "https"),
               !url.absoluteString.hasPrefix(baseURL) {
                decisionHandler(.cancel)
                UIApplication.shared.open(url)
                return
            }

            // For same-host links, intercept the "Back" navigation so the native
            // back button pops the correct screen rather than loading a new URL.
            if navigationAction.navigationType == .linkActivated,
               url.absoluteString.hasPrefix(baseURL) {
                let isBackLink: Bool
                if let slug = returnSlug {
                    isBackLink = url.path == "/c/\(slug)/" || url.path == "/"
                } else {
                    isBackLink = url.path == "/" || url.path.isEmpty
                }
                if isBackLink {
                    decisionHandler(.cancel)
                    onNavigateBack()
                    return
                }
            }

            decisionHandler(.allow)
        }

        // WKUIDelegate: handle window.open() calls and target="_blank" new
        // window requests by opening the URL in Safari.
        func webView(_ webView: WKWebView,
                     createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction,
                     windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = navigationAction.request.url {
                UIApplication.shared.open(url)
            }
            return nil
        }
    }
}

// MARK: - Row

private struct TestBadgeView: View {
    var body: some View {
        Text("TEST")
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(.white)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(Color(red: 0.10, green: 0.42, blue: 0.24))
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}

private struct CampaignRow: View {
    let campaign: Campaign

    var body: some View {
        if let urlString = campaign.heroImageUrl, let url = URL(string: urlString) {
            // Hero image card: edge-to-edge image with gradient fade and
            // text overlapping the bottom, matching the web campaign list style.
            ZStack(alignment: .bottomLeading) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image
                            .resizable()
                            .scaledToFill()
                    default:
                        Color(.systemGray5)
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 150, maxHeight: 150)
                .clipped()

                // Steep gradient fade from transparent to near-opaque
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0.3),
                        .init(color: Color(.systemBackground).opacity(0.97), location: 0.75)
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                // Text overlaid on top of the gradient
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(campaign.name)
                            .font(.headline)
                            .fontWeight(.bold)
                            .foregroundStyle(.primary)
                        if campaign.isTest { TestBadgeView() }
                    }
                    if let dates = campaign.dateRangeText {
                        Text(dates)
                            .font(.caption)
                            .fontWeight(.semibold)
                            .foregroundStyle(.secondary)
                    }
                    if !campaign.isReady {
                        Label("Map generating…", systemImage: "clock")
                            .font(.caption2)
                            .foregroundStyle(.orange)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 10)
            }
        } else {
            // No hero image — plain text row
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(campaign.name)
                        .font(.headline)
                    if campaign.isTest {
                        Text("TEST")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Color(red: 0.10, green: 0.42, blue: 0.24))
                            .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                }
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
}
