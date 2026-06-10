# DevTool.app

A lightweight macOS **menu bar app** for developer utilities. It lives in the
menu bar (no Dock icon) and shows one toggleable row per background "process":

```
wake      🟢 on
equalizer 🟢 on
diffs     🟢 on
─────────────
Quit      ⌘Q
```

Each row shows `name 🟢/🔴 on/off/error`. Clicking a row toggles that process
(or, for `diffs`, opens the site).

![macOS](https://img.shields.io/badge/macOS-11+-blue)
![Swift](https://img.shields.io/badge/Swift-native-orange)

## Built-in processes

| Process | What it does |
|---------|--------------|
| **wake** | Prevents system & display sleep via IOKit power assertions (`IOPMAssertionCreateWithName` for `PreventUserIdleSystemSleep` / `PreventUserIdleDisplaySleep`). |
| **equalizer** | Installs a global **Cmd+E** hotkey that runs `~/bin/cmux-pane-resize.py -e` to equalize [cmux](https://github.com) terminal panes. Auto-starts. |
| **diffs** | Runs a local diffs site server (`~/my-diffs-site/generate.py serve`) on port `8787`, killing any stale instance first. Clicking the row opens `http://localhost:8787`. Auto-starts. |

## Architecture

The app uses a small protocol-based plugin system. Every process is a Swift
class conforming to `DevProcess`:

```swift
protocol DevProcess: AnyObject {
    var name: String { get }        // shown in the menu, used for alignment
    var state: String { get }       // "on", "off", or "error"
    var onStateChange: (() -> Void)? { get set }
    func start()
    func stop()
    func menuAction()               // called when the row is clicked
}
```

`AppDelegate` holds a `processes` array, auto-generates a menu row per process,
and calls `start()` for any process listed in the `autoStart` set. State changes
call back into `updateMenuItem(...)` to re-render the row (dot + label, monospace
aligned).

## Build & install

```bash
./build.sh
```

`build.sh` kills any running instance, compiles `dev.swift` with
`swiftc -framework Cocoa -framework IOKit` directly into
`~/Applications/DevTool.app/Contents/MacOS/DevTool`, writes the `Info.plist`
(with `LSUIElement = true` so there's no Dock icon), and clears quarantine attrs.

Then launch via **Cmd+Space → "DevTool"**.

## Tests

```bash
./run-tests.sh
```

`run-tests.sh` compiles `dev.swift` (with the GUI bootstrap stripped via
`-DTESTING`) together with `Tests/main.swift` into a self-contained test binary
and runs it. It uses only the Command Line Tools toolchain — **no Xcode or
XCTest required** — and exits non-zero if any assertion fails.

The suite covers the headlessly-testable logic: menu-row formatting
(`devStateDot` / `devStateLabel` / `devPaddedName`), the `DevProcess.menuAction`
toggle behavior, `onStateChange` callbacks, process name constants, and
`WakeProcess` state/assertion lifecycle. GUI-only paths (global hotkey monitor,
subprocess servers) are intentionally not exercised.

## Adding a new process

1. Add a class conforming to `DevProcess` in `dev.swift`.
2. Register it in the `processes` array in `AppDelegate.applicationDidFinishLaunching`.
3. Optionally add its `name` to the `autoStart` set.
4. Run `./build.sh` and relaunch.
5. Add assertions to `Tests/main.swift` and run `./run-tests.sh`.

## Notes / gotchas

- The app **must** be named `DevTool.app` — naming it `Dev.app` collides with
  `/usr/local/bin/dev` in Spotlight and launches the wrong binary.
- The app is intentionally **unsigned** — re-signing on every rebuild makes
  macOS distrust it. Unsigned works fine for a local menu bar app.
- When running external scripts, `PATH` is extended to include
  `/opt/facebook/bin:/usr/local/bin:/opt/homebrew/bin:~/bin` so tools like
  `jf` / `sl` resolve.
