#!/bin/bash
# Build and run the DevTool test suite.
#
# Compiles dev.swift (with the GUI bootstrap stripped via -D TESTING) together
# with the test runner into a single binary and executes it. Works with the
# Command Line Tools toolchain — no Xcode / XCTest required.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$(mktemp -t devtool-tests)"

swiftc \
  "$SCRIPT_DIR/dev.swift" \
  "$SCRIPT_DIR/Tests/main.swift" \
  -DTESTING \
  -framework Cocoa -framework IOKit \
  -o "$BIN"

"$BIN"
