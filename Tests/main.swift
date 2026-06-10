// DevTool test runner.
//
// Compiled together with dev.swift under -D TESTING (which strips the GUI
// bootstrap), this is a self-contained test binary that runs without XCTest /
// full Xcode. Run via ./run-tests.sh. Exits non-zero if any assertion fails.

import Foundation

// ── Tiny assertion harness ──────────────────────────────────────────────────

var passed = 0
var failed = 0

func check(_ condition: Bool, _ message: String) {
    if condition {
        passed += 1
    } else {
        failed += 1
        print("  ✗ FAIL: \(message)")
    }
}

func equal<T: Equatable>(_ actual: T, _ expected: T, _ message: String) {
    check(actual == expected, "\(message) — expected \(expected), got \(actual)")
}

func test(_ name: String, _ body: () -> Void) {
    print("• \(name)")
    body()
}

// ── Test double ─────────────────────────────────────────────────────────────

final class FakeProcess: DevProcess {
    let name: String
    var state = "off"
    var onStateChange: (() -> Void)?
    private(set) var startCount = 0
    private(set) var stopCount = 0

    init(name: String = "fake") { self.name = name }

    func start() { startCount += 1; state = "on"; onStateChange?() }
    func stop()  { stopCount += 1; state = "off"; onStateChange?() }
}

// ── Tests ─────────────────────────────────────────────────────────────────

test("devStateDot maps states to dots") {
    equal(devStateDot("on"), "🟢", "on is green")
    equal(devStateDot("off"), "🔴", "off is red")
    equal(devStateDot("error"), "🔴", "error is red")
    equal(devStateDot("anything"), "🔴", "unknown is red")
}

test("devStateLabel maps states to labels") {
    equal(devStateLabel("on"), "on", "on label")
    equal(devStateLabel("off"), "off", "off label")
    equal(devStateLabel("error"), "error", "error label")
    equal(devStateLabel("bogus"), "off", "unknown falls back to off")
}

test("devPaddedName aligns to width without truncating") {
    equal(devPaddedName("wake", width: 9), "wake     ", "pads short name to width")
    equal(devPaddedName("wake", width: 9).count, 9, "padded length equals width")
    equal(devPaddedName("equalizer", width: 9), "equalizer", "exact-width name unchanged")
    equal(devPaddedName("equalizer", width: 4), "equalizer", "longer name is not truncated")
}

test("real processes expose the expected menu names") {
    equal(WakeProcess().name, "wake", "wake name")
    equal(EqualizerProcess().name, "equalizer", "equalizer name")
    equal(DiffsSiteProcess().name, "diffs", "diffs name")
}

test("processes start in the off state") {
    equal(WakeProcess().state, "off", "wake starts off")
    equal(EqualizerProcess().state, "off", "equalizer starts off")
    equal(DiffsSiteProcess().state, "off", "diffs starts off")
}

test("DevProcess.menuAction toggles based on state") {
    let p = FakeProcess()
    equal(p.state, "off", "initial state")

    p.menuAction()                       // off -> start
    equal(p.state, "on", "menuAction turns on when off")
    equal(p.startCount, 1, "start called once")
    equal(p.stopCount, 0, "stop not called yet")

    p.menuAction()                       // on -> stop
    equal(p.state, "off", "menuAction turns off when on")
    equal(p.startCount, 1, "start still called once")
    equal(p.stopCount, 1, "stop called once")
}

test("onStateChange fires on every transition") {
    let p = FakeProcess()
    var notifications = 0
    p.onStateChange = { notifications += 1 }
    p.start()
    p.stop()
    equal(notifications, 2, "callback fired for start and stop")
}

test("WakeProcess toggles state and releases assertions on stop") {
    let w = WakeProcess()
    w.start()
    equal(w.state, "on", "start sets state on")

    w.stop()
    equal(w.state, "off", "stop sets state off")
    equal(w.systemAssertionID, 0, "system assertion id reset on stop")
    equal(w.displayAssertionID, 0, "display assertion id reset on stop")
}

// ── Summary ─────────────────────────────────────────────────────────────────

print("")
print("\(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
