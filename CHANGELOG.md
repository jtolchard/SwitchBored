# Changelog

All notable changes to SwitchBored are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow `MAJOR.MINOR.PATCH`.

## [1.5.0] - 2026-10-23

### Changed
- Open Dashboard now brings the dashboard, with any open Settings or
  details windows, to the desktop you're using instead of switching you
  to the desktop it was left on. An open dashboard comes to the front
  instantly, without needing automation permission for System Events.
- While the dashboard is open it shows the menu bar's status checks
  instead of pinging every machine a second time.
- A machine is now shown offline only after three missed checks in a row
  (about 15 seconds), so slow or busy devices no longer flicker between
  online and offline; the reference server works the same way. Hovering a
  dot shows how long a machine hasn't replied during that grace period.
- When a machine doesn't answer ping, the fallback check now tries the
  services it is set up for (its SSH port, and port 5900 if VNC is
  enabled), and a refused connection counts as reachable.
- The "SwitchBored is already running" notice now shows the app's icon
  instead of a generic folder.
- Debug Mode logging is much lighter and no longer slows the app down.

### Fixed
- The dashboard no longer gets lost after disconnecting an external
  monitor: it moves itself back onto a connected display when the display
  layout changes, when it opens, and when you choose Open Dashboard.
- A newly opened dashboard no longer appears behind other windows.
- Machine status dots fill in within a couple of seconds of opening the
  dashboard; machines are now checked in parallel.
- SwitchBored could refuse to start ("already running"), or close an
  unrelated process on quit, when a leftover lock file named a process ID
  macOS had since reused.
- Quitting the dashboard with Cmd+Q, or logging out, now saves its window
  position.
- Settings, the machine editor, details and services windows open on the
  same display as the dashboard, and Settings fits on shorter screens with
  its Save button visible.
- The About window now opens on the desktop you're using, in front, even
  if SwitchBored has been assigned to a specific desktop.
- The System Services window no longer flashes up at a tiny size before
  it opens.
- Closing the Settings Saved popup with its close button now applies the
  changes, like clicking OK.
- The Updates tab follows an install through to the end and reports
  failures, instead of staying on "Downloading…".
- Deploying SSH keys no longer freezes the Settings window while it checks
  which machines are reachable.
- A failed update install now keeps the existing app instead of removing it.

### Security
- SSH and TMUX connections now quote the machine's user, address and port,
  so a crafted value (e.g. in an imported machine list) can no longer run
  a local command or inject ssh options.

## [1.4] - 2026-07-21

### Added
- System Services Overview: a machines × services grid with live status
  and start/stop/restart per service, opened from a new dashboard button
  (visible with Sysadmin Features enabled). Status is fetched with one
  batched query per machine; unreachable machines show muted grey.

### Changed
- The Updates tab now shows the current version's release notes (muted)
  when you are already up to date, instead of an empty pane.

### Fixed
- Menu bar Quick Connect status dots no longer vanish after macOS's
  periodic temp-file cleanup (they are now stored in Application Support
  and regenerate themselves if missing).

## [1.3.2] - 2026-07-10

### Added
- Custom Menu Bar Shortcuts: menu entries can now be a URL, an app launcher
  (opens a new window of e.g. iTerm or VS Code), or a shell command.
- A Download & Install button in the Updates tab, so updates can be applied
  without waiting for the daily check.
- Dashboard plugin hooks (`on_machine_editor`, `on_machine_editor_save`,
  `on_machine_details`) so plugins can extend the machine editor and
  details windows.

### Changed
- "Import JSON" / "Export JSON" renamed to "Import Machines" /
  "Export Machines".
- The machine editor window now sizes itself to its content.
- The Settings tab strip is styled as one grouped control with the active
  tab highlighted.

### Fixed
- The app no longer leaks its internal Python environment (PYTHONHOME /
  PYTHONPATH) into Terminal windows, shortcuts, or other processes it
  launches, which could break the system `python`.
- Service status now reads correctly on non-English (localized) hosts.
- Launching the app while it is already running now shows a notice instead
  of silently exiting.
- Save/Cancel buttons in the machine editor could be squeezed out of view.

## [1.3.1] - 2026-07-09

### Fixed
- The bundled app now runs on macOS 11 (Big Sur) and later; the previous
  build only launched on the machine it was built on.
- Two-finger trackpad scrolling works in all windows (Tk 9 compatibility).
- Notifications now appear as banners instead of arriving silently in
  Notification Center.
- Window stacking: Settings and its child windows stay above the dashboard,
  and tooltips are no longer hidden behind their windows.

### Added
- Per-machine "Notify when unreachable" option: a macOS notification fires
  after 30 seconds of unreachability while the reference server is still up.
- The About window is now the same everywhere (menu bar and app menu) and
  shows the app icon.

## [1.3] - 2026-07-08

First public release.

### Added
- Menu bar Quick Connect with live online/offline status per machine.
- Dashboard with latency, group filtering, and one-click SSH / TMUX /
  SFTP / VNC.
- Sysadmin features: systemd service control, remote log viewing with
  search, and custom admin commands over SSH.
- SSH key generation and guided deployment to all reachable machines.
- Plugin system with user plugins stored in Application Support.
- In-app updates from GitHub Releases, with a daily check and an Updates
  tab showing release notes.
- Per-machine offline detection with ICMP ping and TCP fallback.

[1.5.0]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.5.0
[1.4]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.4.0
[1.3.2]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.3.2
[1.3.1]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.3.1
[1.3]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.3
