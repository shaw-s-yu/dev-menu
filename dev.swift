import Cocoa
import IOKit.pwr_mgt

// ============================================================
// MARK: - Process Protocol
// ============================================================
// To add a new process:
// 1. Create a class conforming to DevProcess
// 2. Add it to the `processes` array in AppDelegate.applicationDidFinishLaunching

protocol DevProcess: AnyObject {
    var name: String { get }
    var state: String { get }  // "on", "off", "error"
    var onStateChange: (() -> Void)? { get set }
    func start()
    func stop()
    func menuAction()  // called when the menu row is clicked
}

extension DevProcess {
    func menuAction() {
        if state == "on" { stop() } else { start() }
    }
}

// ============================================================
// MARK: - Menu Row Formatting (pure, testable)
// ============================================================

/// Status dot for a process state. "on" -> green, anything else -> red.
func devStateDot(_ state: String) -> String {
    state == "on" ? "🟢" : "🔴"
}

/// Human label for a process state. Unknown states render as "off".
func devStateLabel(_ state: String) -> String {
    switch state {
    case "on":    return "on"
    case "error": return "error"
    default:      return "off"
    }
}

/// Right-pad a process name to `width` for monospace column alignment.
/// Never truncates: a name longer than `width` is returned unchanged.
func devPaddedName(_ name: String, width: Int) -> String {
    name.padding(toLength: max(width, name.count), withPad: " ", startingAt: 0)
}

// ============================================================
// MARK: - Wake Process
// ============================================================

class WakeProcess: DevProcess {
    let name = "wake"
    var state = "off"
    var onStateChange: (() -> Void)?
    var systemAssertionID: IOPMAssertionID = 0
    var displayAssertionID: IOPMAssertionID = 0

    func start() {
        let reason = "Dev - Keep Mac Awake" as CFString
        IOPMAssertionCreateWithName(
            kIOPMAssertionTypePreventUserIdleSystemSleep as CFString,
            IOPMAssertionLevel(kIOPMAssertionLevelOn), reason, &systemAssertionID)
        IOPMAssertionCreateWithName(
            kIOPMAssertionTypePreventUserIdleDisplaySleep as CFString,
            IOPMAssertionLevel(kIOPMAssertionLevelOn), reason, &displayAssertionID)
        state = "on"
        onStateChange?()
    }

    func stop() {
        if systemAssertionID != 0 { IOPMAssertionRelease(systemAssertionID); systemAssertionID = 0 }
        if displayAssertionID != 0 { IOPMAssertionRelease(displayAssertionID); displayAssertionID = 0 }
        state = "off"
        onStateChange?()
    }
}

// ============================================================
// MARK: - Equalizer Process
// ============================================================

class EqualizerProcess: DevProcess {
    let name = "equalizer"
    var state = "off"
    var onStateChange: (() -> Void)?
    var monitor: Any?
    var lastTrigger: TimeInterval = 0
    let debounce: TimeInterval = 2.0
    let scriptPath = NSHomeDirectory() + "/bin/cmux-pane-resize.py"
    let socketPath = NSHomeDirectory() + "/Library/Application Support/cmux/cmux.sock"

    func start() {
        guard state != "on" else { return }
        monitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self = self else { return }
            let flags = event.modifierFlags
            if event.keyCode == 14 && flags.contains(.command)
                && !flags.contains(.shift) && !flags.contains(.control) && !flags.contains(.option) {
                self.onHotkey()
            }
        }
        state = monitor != nil ? "on" : "error"
        onStateChange?()
    }

    func stop() {
        if let m = monitor { NSEvent.removeMonitor(m); monitor = nil }
        state = "off"
        onStateChange?()
    }

    func onHotkey() {
        let now = Date().timeIntervalSince1970
        guard now - lastTrigger >= debounce else { return }
        lastTrigger = now
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            proc.arguments = [self.scriptPath, "-e"]
            var env = ProcessInfo.processInfo.environment
            env["CMUX_SOCKET_PATH"] = self.socketPath
            proc.environment = env
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = pipe
            do {
                try proc.run()
                proc.waitUntilExit()
                if proc.terminationStatus != 0 {
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    let msg = String(data: data, encoding: .utf8) ?? "unknown"
                    DispatchQueue.main.async { self.state = "error"; self.onStateChange?() }
                } else if self.state == "error" {
                    DispatchQueue.main.async { self.state = "on"; self.onStateChange?() }
                }
            } catch {
                DispatchQueue.main.async { self.state = "error"; self.onStateChange?() }
            }
        }
    }
}

// ============================================================
// MARK: - Diffs Site Process
// ============================================================

class DiffsSiteProcess: DevProcess {
    let name = "diffs"
    var state = "off"
    var onStateChange: (() -> Void)?
    var serverProcess: Process?
    let scriptPath = NSHomeDirectory() + "/my-diffs-site/generate.py"

    func log(_ msg: String) {
        let entry = "\(Date()): \(msg)\n"
        if let fh = FileHandle(forWritingAtPath: "/tmp/diffs-server.log") {
            fh.seekToEndOfFile()
            fh.write(entry.data(using: .utf8)!)
            fh.closeFile()
        } else {
            FileManager.default.createFile(atPath: "/tmp/diffs-server.log", contents: entry.data(using: .utf8))
        }
    }

    func start() {
        guard state != "on" else { return }
        log("start() called")
        state = "on"
        onStateChange?()

        DispatchQueue.global().async { [weak self] in
            guard let self = self else { self?.log("self is nil in kill block"); return }
            self.log("kill block entered")

            // Kill any existing server on 8787 using fuser (non-blocking)
            let kill = Process()
            kill.executableURL = URL(fileURLWithPath: "/bin/bash")
            kill.arguments = ["-c", "lsof -ti :8787 | xargs kill 2>/dev/null; exit 0"]
            kill.standardOutput = FileHandle.nullDevice
            kill.standardError = FileHandle.nullDevice
            try? kill.run()
            // Give it 3 seconds max
            let deadline = Date().addingTimeInterval(3)
            while kill.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.1)
            }
            if kill.isRunning { kill.terminate() }
            self.log("kill block done")
            Thread.sleep(forTimeInterval: 0.5)

            // Start server
            self.log("server block entered, script exists: \(FileManager.default.fileExists(atPath: self.scriptPath))")

            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/Library/Developer/CommandLineTools/usr/bin/python3")
            proc.arguments = [self.scriptPath, "serve"]
            proc.currentDirectoryURL = URL(fileURLWithPath: NSHomeDirectory() + "/my-diffs-site")
            var env = ProcessInfo.processInfo.environment
            env["BROWSER"] = ""
            // Ensure PATH includes locations for jf, sl, etc.
            let extraPaths = "/opt/facebook/bin:/usr/local/bin:/opt/homebrew/bin:" + NSHomeDirectory() + "/bin"
            env["PATH"] = extraPaths + ":" + (env["PATH"] ?? "/usr/bin:/bin")
            proc.environment = env
            let logFile = FileHandle(forWritingAtPath: "/tmp/diffs-server.log") ?? {
                FileManager.default.createFile(atPath: "/tmp/diffs-server.log", contents: nil)
                return FileHandle(forWritingAtPath: "/tmp/diffs-server.log")!
            }()
            logFile.seekToEndOfFile()
            proc.standardOutput = logFile
            proc.standardError = logFile
            do {
                try proc.run()
                self.log("proc.run() succeeded, pid=\(proc.processIdentifier)")
                self.serverProcess = proc
                proc.waitUntilExit()
                self.log("proc exited with status \(proc.terminationStatus)")
                DispatchQueue.main.async {
                    if self.state == "on" { self.state = "error"; self.onStateChange?() }
                }
            } catch {
                self.log("proc.run() FAILED: \(error)")
                DispatchQueue.main.async { self.state = "error"; self.onStateChange?() }
            }
        }
    }

    func stop() {
        if let proc = serverProcess, proc.isRunning {
            proc.terminate()
            serverProcess = nil
        }
        state = "off"
        onStateChange?()
    }

    func menuAction() {
        NSWorkspace.shared.open(URL(string: "http://localhost:8787")!)
    }
}

// ============================================================
// MARK: - App Delegate
// ============================================================

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var processes: [DevProcess] = []
    var menuItems: [NSMenuItem] = []

    // Longest process name, for alignment
    var maxNameLen = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        // ── Register processes here ──
        processes = [
            WakeProcess(),
            EqualizerProcess(),
            DiffsSiteProcess(),
        ]
        // ── Auto-start list ──
        let autoStart: Set<String> = ["equalizer", "diffs"]

        maxNameLen = processes.map { $0.name.count }.max() ?? 0

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.title = "DEV"
            button.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        }

        let menu = NSMenu()
        for (i, proc) in processes.enumerated() {
            let item = NSMenuItem(title: "", action: #selector(menuAction(_:)), keyEquivalent: "")
            item.target = self
            item.tag = i
            proc.onStateChange = { [weak self] in self?.updateMenuItem(index: i) }
            menuItems.append(item)
            menu.addItem(item)
            updateMenuItem(index: i)
        }

        menu.addItem(NSMenuItem.separator())
        let quitItem = NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu

        for proc in processes where autoStart.contains(proc.name) {
            proc.start()
        }
    }

    func updateMenuItem(index: Int) {
        let proc = processes[index]
        let dot = devStateDot(proc.state)
        let label = devStateLabel(proc.state)
        let mono = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        let paddedName = devPaddedName(proc.name, width: maxNameLen)
        let result = NSMutableAttributedString()
        result.append(NSAttributedString(string: paddedName, attributes: [.font: mono]))
        result.append(NSAttributedString(string: " \(dot) ", attributes: [.font: NSFont.systemFont(ofSize: 13)]))
        result.append(NSAttributedString(string: label, attributes: [.font: mono]))
        menuItems[index].attributedTitle = result
    }

    @objc func menuAction(_ sender: NSMenuItem) {
        processes[sender.tag].menuAction()
    }

    @objc func quitApp() {
        processes.forEach { $0.stop() }
        NSApp.terminate(nil)
    }
}

// The GUI bootstrap is an @main declaration (not top-level code) so it can be
// excluded when compiling the test runner (-DTESTING); the process/formatting
// logic above is then unit-testable without launching the menu bar app.
// Built normally with `swiftc -parse-as-library` (see build.sh).
#if !TESTING
@main
struct DevToolApp {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
#endif
