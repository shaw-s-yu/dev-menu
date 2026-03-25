#!/usr/bin/env python3
"""macOS menu bar app for managing OD connections via `dev` CLI.
Uses raw AppKit (like KeepAwake) for py2app compatibility."""

import glob
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSFont,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSRunLoop, NSTimer, NSDefaultRunLoopMode, NSAppleScript

logging.basicConfig(
    filename="/tmp/devmenu_debug.log",
    level=logging.DEBUG,
    format="%(asctime)s %(message)s",
)
log = logging.getLogger("devmenu")

CONNECT_CMD = "export PATH=/opt/facebook/bin:$PATH; dev connect -t www_fbsource_configerator"
VSCODE_APP = "/Applications/VS Code @ FB.app"
GHOSTTY_APP = "/Applications/Ghostty.app"

# SSH tunnel config: keep port-forward alive while the app is running
SSH_TUNNEL_HOST = "devvm28908.rva0.facebook.com"
SSH_TUNNEL_PORT = 8085

_CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
_CLEAN_ENV["PATH"] = os.path.expanduser("~/bin") + ":/opt/facebook/bin:/usr/local/bin:/usr/bin:/bin:" + _CLEAN_ENV.get("PATH", "")
# Ensure SSH_AUTH_SOCK is set — prefer Meta agent socket, fallback to launchd
_meta_sock = glob.glob(os.path.expanduser("~/.ssh/agent/s.*"))
if _meta_sock:
    _CLEAN_ENV["SSH_AUTH_SOCK"] = _meta_sock[0]
elif "SSH_AUTH_SOCK" not in _CLEAN_ENV:
    try:
        _sock = subprocess.run(
            ["launchctl", "getenv", "SSH_AUTH_SOCK"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if _sock:
            _CLEAN_ENV["SSH_AUTH_SOCK"] = _sock
    except Exception:
        pass


def _ghostty_run_command(cmd=""):
    if cmd:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.sh', delete=False, prefix='devmenu_ghostty_',
        ) as f:
            f.write("#!/bin/bash\n" + cmd + "\n")
            f.flush()
            os.chmod(f.name, 0o755)
            subprocess.Popen(
                ["open", "-na", GHOSTTY_APP, "--args", "-e", f.name],
                env=_CLEAN_ENV, start_new_session=True,
            )
    else:
        subprocess.Popen(
            ["open", "-a", GHOSTTY_APP],
            env=_CLEAN_ENV, start_new_session=True,
        )


def _ghostty_menu_click(menu_name, item_name, submenu_item=None):
    """Click a menu item in Ghostty via System Events.
    Much more reliable than keystroke simulation."""
    if submenu_item:
        script = (
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events"\n'
            f'  tell process "ghostty"\n'
            f'    click menu item "{submenu_item}" of menu 1 of menu item "{item_name}" of menu 1 of menu bar item "{menu_name}" of menu bar 1\n'
            f'  end tell\n'
            'end tell'
        )
    else:
        script = (
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events"\n'
            f'  tell process "ghostty"\n'
            f'    click menu item "{item_name}" of menu 1 of menu bar item "{menu_name}" of menu bar 1\n'
            f'  end tell\n'
            'end tell'
        )
    subprocess.Popen(
        ["/usr/bin/osascript", "-e", script],
        env=_CLEAN_ENV, start_new_session=True,
    )


class DevMenuController(NSObject):
    def init(self):
        self = objc.super(DevMenuController, self).init()
        if self is None:
            return None
        self.status_item = None
        self._last_names = []
        self._pending_names = None
        self._tunnel_proc = None
        self._tunnel_stop = threading.Event()
        return self

    def setupMenuBar(self):
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.status_item.button().setTitle_("\u2699 Dev")
        self.status_item.button().setFont_(NSFont.systemFontOfSize_(13))
        self._rebuild_menu([])
        self._start_fetch_thread()
        self._start_ssh_tunnel()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self, "applyUpdate:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(self._timer, NSDefaultRunLoopMode)

    def _rebuild_menu(self, names):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        # Connect New OD
        connect_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Connect New OD", None, "")
        connect_sub = NSMenu.alloc().init()
        for title, sel in [("ssh", "connectNewOdSsh:"), ("vscode", "connectNewOdVscode:")]:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            mi.setTarget_(self)
            connect_sub.addItem_(mi)
        connect_item.setSubmenu_(connect_sub)
        menu.addItem_(connect_item)

        # Local
        local_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Local", None, "")
        local_sub = NSMenu.alloc().init()
        for title, sel in [("terminal", "openTerminal:"), ("claude", "openClaude:")]:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            mi.setTarget_(self)
            local_sub.addItem_(mi)
        local_item.setSubmenu_(local_sub)
        menu.addItem_(local_item)

        # Add New Split
        add_pane = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Add New Split", "addNewPane:", "")
        add_pane.setTarget_(self)
        menu.addItem_(add_pane)

        menu.addItem_(NSMenuItem.separatorItem())

        # Dev Servers
        if names:
            header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("\u2500\u2500 Dev Servers \u2500\u2500", None, "")
            header.setEnabled_(False)
            menu.addItem_(header)
            for name in names:
                server_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(name, None, "")
                server_sub = NSMenu.alloc().init()
                vsc = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("vscode", "vscodeServer:", "")
                vsc.setTarget_(self)
                vsc.setRepresentedObject_(name)
                server_sub.addItem_(vsc)
                ssh = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("ssh", "sshServer:", "")
                ssh.setTarget_(self)
                ssh.setRepresentedObject_(name)
                server_sub.addItem_(ssh)
                if SSH_TUNNEL_HOST.startswith(name.split(".")[0]):
                    tun = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"tunnel {SSH_TUNNEL_PORT}", "tunnelServer:", "")
                    tun.setTarget_(self)
                    tun.setRepresentedObject_(name)
                    server_sub.addItem_(tun)
                server_item.setSubmenu_(server_sub)
                menu.addItem_(server_item)
        else:
            no_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("No servers", None, "")
            no_item.setEnabled_(False)
            menu.addItem_(no_item)

        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", "quitApp:", "")
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)

        self.status_item.setMenu_(menu)

    # -- Split pane actions --

    @objc.IBAction
    def addNewPane_(self, sender):
        log.info("Add New Pane clicked")
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'tell application "System Events"\n'
            '    tell process "ghostty"\n'
            '        if (count of windows) = 0 then\n'
            '            return "No Ghostty windows"\n'
            '        end if\n'
            '        tell window 1\n'
            '            set allElems to entire contents\n'
            '            set paneCount to 0\n'
            '            repeat with elem in allElems\n'
            '                try\n'
            '                    if role of elem is "AXTextArea" then\n'
            '                        set paneCount to paneCount + 1\n'
            '                    end if\n'
            '                end try\n'
            '            end repeat\n'
            '            if paneCount < 1 then\n'
            '                return "No panes found"\n'
            '            end if\n'
            '            -- Move focus to the rightmost pane\n'
            '            repeat paneCount times\n'
            '                key code 124 using {command down, option down}\n'
            '            end repeat\n'
            '            -- Vertical split\n'
            '            keystroke "d" using command down\n'
            '            -- Equalize split sizes\n'
            '            click menu item "Equalize Splits" of menu 1 of menu bar item "Window" of menu bar 1\n'
            '        end tell\n'
            '    end tell\n'
            'end tell'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    @objc.IBAction
    def splitRight_(self, sender):
        log.info("Split Right clicked")
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events" to keystroke "d" using command down'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    @objc.IBAction
    def splitDown_(self, sender):
        log.info("Split Down clicked")
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events" to keystroke "d" using {command down, shift down}'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    @objc.IBAction
    def gotoSplitPrev_(self, sender):
        log.info("Goto Split Previous clicked")
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events" to keystroke "[" using command down'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    @objc.IBAction
    def gotoSplitNext_(self, sender):
        log.info("Goto Split Next clicked")
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events" to keystroke "]" using command down'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    @objc.IBAction
    def toggleSplitZoom_(self, sender):
        log.info("Toggle Split Zoom clicked")
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events" to keystroke return using {command down, shift down}'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    @objc.IBAction
    def equalizeSplits_(self, sender):
        log.info("Equalize Splits clicked")
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'delay 0.3\n'
            'tell application "System Events"\n'
            '    tell process "ghostty"\n'
            '        click menu item "Equalize Splits" of menu 1 of menu bar item "Window" of menu bar 1\n'
            '    end tell\n'
            'end tell'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    # -- Connect / Local actions --

    def _add_pane_and_run(self, cmd=""):
        """Add a new Ghostty pane (split) and optionally run a command in it."""
        escaped_cmd = cmd.replace('\\', '\\\\').replace('"', '\\"') if cmd else ""
        cmd_lines = (
            f'            keystroke "{escaped_cmd}"\n'
            '            keystroke return\n'
        ) if cmd else ""
        script = NSAppleScript.alloc().initWithSource_(
            'tell application "Ghostty" to activate\n'
            'tell application "System Events"\n'
            '    tell process "ghostty"\n'
            '        if (count of windows) = 0 then\n'
            '            keystroke "n" using command down\n'
            + cmd_lines +
            '            return "opened new window"\n'
            '        end if\n'
            '        tell window 1\n'
            '            set allElems to entire contents\n'
            '            set paneCount to 0\n'
            '            repeat with elem in allElems\n'
            '                try\n'
            '                    if role of elem is "AXTextArea" then\n'
            '                        set paneCount to paneCount + 1\n'
            '                    end if\n'
            '                end try\n'
            '            end repeat\n'
            '            if paneCount < 1 then\n'
            '                keystroke "d" using command down\n'
            + cmd_lines +
            '                return "split right"\n'
            '            end if\n'
            '            -- Move focus to the rightmost pane\n'
            '            repeat paneCount times\n'
            '                key code 124 using {command down, option down}\n'
            '            end repeat\n'
            '            -- Vertical split\n'
            '            keystroke "d" using command down\n'
            '            -- Equalize split sizes\n'
            '            click menu item "Equalize Splits" of menu 1 of menu bar item "Window" of menu bar 1\n'
            + cmd_lines +
            '        end tell\n'
            '    end tell\n'
            'end tell'
        )
        threading.Thread(target=lambda: script.executeAndReturnError_(None), daemon=True).start()

    @objc.IBAction
    def connectNewOdSsh_(self, sender):
        log.info("Connect New OD (ssh) clicked")
        self._add_pane_and_run(CONNECT_CMD)

    @objc.IBAction
    def connectNewOdVscode_(self, sender):
        log.info("Connect New OD (vscode) clicked")
        def run():
            if not self._ensure_vscode_running():
                log.error("Could not launch VS Code")
                return
            subprocess.Popen([
                "/usr/bin/osascript", "-e",
                'tell application "VS Code @ FB" to activate',
            ], env=_CLEAN_ENV)
            result = subprocess.run([
                "vscode-cmd", "fb.ms-remote-connections.connectToOnDemand",
            ], env=_CLEAN_ENV, capture_output=True, text=True)
            log.info("vscode-cmd rc=%s", result.returncode)
        threading.Thread(target=run, daemon=True).start()

    @objc.IBAction
    def openTerminal_(self, sender):
        log.info("Open terminal (Ghostty) clicked")
        self._add_pane_and_run()

    @objc.IBAction
    def openClaude_(self, sender):
        log.info("Open claude clicked")
        self._add_pane_and_run("claude")

    @objc.IBAction
    def tunnelServer_(self, sender):
        host = sender.representedObject()
        log.info("tunnel called for host=%s", host)
        self._add_pane_and_run(f"ssh -N -L {SSH_TUNNEL_PORT}:localhost:{SSH_TUNNEL_PORT} {host}")

    @objc.IBAction
    def vscodeServer_(self, sender):
        host = sender.representedObject()
        log.info("vscode_connect called for host=%s", host)
        def run():
            if not self._ensure_vscode_running():
                log.error("Could not launch VS Code")
                return
            try:
                front_app = subprocess.run(
                    ["/usr/bin/osascript", "-e",
                     'tell application "System Events" to get bundle identifier of first process whose frontmost is true'],
                    capture_output=True, text=True, timeout=3, env=_CLEAN_ENV,
                ).stdout.strip()
            except Exception:
                front_app = None
            result = subprocess.run([
                "vscode-cmd", "fb.ms-remote-connections.connectToHost", host,
            ], env=_CLEAN_ENV, capture_output=True, text=True)
            log.info("vscode-cmd rc=%s", result.returncode)
            if front_app:
                try:
                    subprocess.Popen(["/usr/bin/osascript", "-e",
                        f'tell application id "{front_app}" to activate'], env=_CLEAN_ENV)
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True).start()

    @objc.IBAction
    def sshServer_(self, sender):
        host = sender.representedObject()
        log.info("ssh_connect called for host=%s", host)
        self._clean_stale_ssh_sockets(host)
        self._add_pane_and_run(f"x2ssh {host} -t 'cd ~/www && exec $SHELL -l'")

    @objc.IBAction
    def quitApp_(self, sender):
        self._stop_ssh_tunnel()
        NSApplication.sharedApplication().terminate_(self)

    # -- SSH Tunnel --

    def _start_ssh_tunnel(self):
        """Start a background thread that keeps the SSH tunnel alive."""
        # Kill any stale SSH tunnel from a previous DevMenu instance
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"ssh.*-L.*{SSH_TUNNEL_PORT}.*{SSH_TUNNEL_HOST}"],
                capture_output=True, text=True,
            )
            for pid in result.stdout.strip().split():
                if pid:
                    log.info("Killing stale SSH tunnel pid %s", pid)
                    os.kill(int(pid), signal.SIGTERM)
        except Exception as e:
            log.warning("Failed to clean stale tunnels: %s", e)
        threading.Thread(target=self._tunnel_loop, daemon=True).start()
        log.info("SSH tunnel manager started for %s:%s", SSH_TUNNEL_HOST, SSH_TUNNEL_PORT)

    def _stop_ssh_tunnel(self):
        """Stop the SSH tunnel on quit."""
        self._tunnel_stop.set()
        if self._tunnel_proc and self._tunnel_proc.poll() is None:
            log.info("Killing SSH tunnel (pid %s)", self._tunnel_proc.pid)
            self._tunnel_proc.terminate()
            try:
                self._tunnel_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._tunnel_proc.kill()

    def _tunnel_loop(self):
        """Keep the SSH tunnel alive. Reconnects every 30s if it drops."""
        while not self._tunnel_stop.is_set():
            # Check if tunnel is already running
            if self._tunnel_proc and self._tunnel_proc.poll() is None:
                self._tunnel_stop.wait(10)
                continue

            if self._tunnel_proc:
                log.info("SSH tunnel died (rc=%s), reconnecting...", self._tunnel_proc.returncode)

            try:
                self._tunnel_proc = subprocess.Popen(
                    [
                        "ssh",
                        "-N",                    # no remote command
                        "-o", "ServerAliveInterval=30",
                        "-o", "ServerAliveCountMax=3",
                        "-o", "ExitOnForwardFailure=yes",
                        "-o", "StrictHostKeyChecking=no",
                        "-L", f"{SSH_TUNNEL_PORT}:localhost:{SSH_TUNNEL_PORT}",
                        SSH_TUNNEL_HOST,
                    ],
                    env=_CLEAN_ENV,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                log.info("SSH tunnel started (pid %s) -> %s:%s",
                         self._tunnel_proc.pid, SSH_TUNNEL_HOST, SSH_TUNNEL_PORT)
            except Exception as e:
                log.error("Failed to start SSH tunnel: %s", e)

            # Wait before checking again
            self._tunnel_stop.wait(30)

    # -- Helpers --

    def _ensure_vscode_running(self):
        pid = subprocess.run(["pgrep", "-f", "VS Code.*MacOS/Electron"],
                             capture_output=True, text=True).stdout.strip()
        if pid:
            return True
        for cmd in [["open", VSCODE_APP], ["open", "-a", "VS Code @ FB"]]:
            if subprocess.run(cmd, capture_output=True, text=True, env=_CLEAN_ENV).returncode == 0:
                break
        else:
            return False
        for _ in range(40):
            threading.Event().wait(0.5)
            if subprocess.run(["pgrep", "-f", "VS Code.*MacOS/Electron"],
                              capture_output=True, text=True).stdout.strip():
                threading.Event().wait(3)
                return True
        return False

    def _clean_stale_ssh_sockets(self, host):
        socket_dir = os.path.expanduser("~/.ssh/sockets")
        if not os.path.isdir(socket_dir):
            return
        for sock in glob.glob(os.path.join(socket_dir, "*")):
            sock_name = os.path.basename(sock)
            if host not in sock_name and not sock_name.startswith("shawyu@"):
                continue
            try:
                result = subprocess.run(["ssh", "-O", "check", "-S", sock, "dummy"],
                                        capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    log.info("Socket %s is alive, keeping it", sock_name)
                    continue
            except Exception:
                pass
            log.info("Removing stale SSH socket: %s", sock_name)
            try:
                os.remove(sock)
            except OSError:
                pass

    def _parse_host_names(self, lines):
        names = []
        for line in lines:
            if line.startswith("NAME"):
                continue
            m = re.match(r'^(\S+)', line)
            if not m:
                continue
            name = m.group(1)
            sb_match = re.search(r'(\w+\.sb)\b', line)
            if sb_match and 'devvm' in name:
                names.append(sb_match.group(1))
            elif '.od' in name or 'devvm' in name:
                names.append(name)
        return names

    def _fetch_loop(self):
        while True:
            try:
                result = subprocess.run(["dev", "list"], capture_output=True,
                                        text=True, timeout=15, env=_CLEAN_ENV)
                lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
                self._pending_names = self._parse_host_names(lines)
            except Exception:
                self._pending_names = []
            threading.Event().wait(10)

    def _start_fetch_thread(self):
        threading.Thread(target=self._fetch_loop, daemon=True).start()

    @objc.IBAction
    def applyUpdate_(self, sender):
        names = self._pending_names
        if names is None or names == self._last_names:
            return
        self._last_names = list(names)
        self._rebuild_menu(names)


def main():
    global _controller
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    _controller = DevMenuController.alloc().init()
    _controller.setupMenuBar()
    app.run()


if __name__ == "__main__":
    main()
