import Foundation
import Darwin

enum Config {
    static let stagingURL = "https://staging.leafletter.app"
    private static let productionURL = "https://leafletter.app"
    private static let userOverrideKey = "leafletter_base_url_override"

    // When a debugger is attached (i.e. running from Xcode), uses the local
    // dev server. Otherwise uses the production server.
    // Adjust the dev URL to your machine's LAN IP if running on a real device.
    // A UserDefaults override (set via the secret 3-tap gesture on the About
    // page version footer) takes precedence over the default production URL.
    nonisolated(unsafe) static var baseURL: String {
        if let override = ProcessInfo.processInfo.environment["LEAFLETTER_BASE_URL"] {
            return override
        }
        #if DEBUG
        return "http://10.10.0.200:8000"
        #else
        if let override = UserDefaults.standard.string(forKey: userOverrideKey) {
            return override
        }
        return productionURL
        #endif
    }

    static var isStaging: Bool {
        UserDefaults.standard.string(forKey: userOverrideKey) == stagingURL
    }

    static func toggleStaging() {
        if isStaging {
            UserDefaults.standard.removeObject(forKey: userOverrideKey)
        } else {
            UserDefaults.standard.set(stagingURL, forKey: userOverrideKey)
        }
    }

    private static var isDebuggerAttached: Bool {
        var info = kinfo_proc()
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        var size = MemoryLayout<kinfo_proc>.stride
        sysctl(&mib, UInt32(mib.count), &info, &size, nil, 0)
        return (info.kp_proc.p_flag & P_TRACED) != 0
    }
}
