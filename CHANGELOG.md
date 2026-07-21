# Changelog

All notable changes to SwitchBored are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow `MAJOR.MINOR.PATCH`.

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

[1.4]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.4
[1.3.2]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.3.2
[1.3.1]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.3.1
[1.3]: https://github.com/jtolchard/SwitchBored/releases/tag/v1.3
