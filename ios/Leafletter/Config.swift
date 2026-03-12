import Foundation
import Darwin

enum Config {
    // When a debugger is attached (i.e. running from Xcode), uses the local
    // dev server. Otherwise uses the production server.
    // Adjust the dev URL to your machine's LAN IP if running on a real device.
    static var baseURL: String {
        isDebuggerAttached ? "http://10.10.0.200:8000" : "https://leafletter.app"
    }

    private static var isDebuggerAttached: Bool {
        var info = kinfo_proc()
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        var size = MemoryLayout<kinfo_proc>.stride
        sysctl(&mib, UInt32(mib.count), &info, &size, nil, 0)
        return (info.kp_proc.p_flag & P_TRACED) != 0
    }
}
