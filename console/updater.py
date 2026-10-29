import os
import re
import sys
import json
import time
import tempfile
import threading
import subprocess
import webbrowser

import requests

GITHUB_REPO = "jtolchard/SwitchBored"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases"

# Releases are tagged like v1.5.1. Development builds from the dev branch
# add -dev.N (v1.5.2-dev.1) and are published as GitHub pre-releases; only
# installs with "Include development builds in updates" ticked get them.
# Versions before 1.5.1 can't read -dev tags, so they never see them.
VERSION_PATTERN = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:-dev\.(\d+))?$")

# Sorts below every real version: used when the running version is unreadable.
OLDEST_VERSION = ((0,), 0, 0)

# Releases scanned per check, newest first. The newest release is always
# among them.
RELEASES_PER_CHECK = 30

# Automatic checks run at most once per day.
AUTO_CHECK_INTERVAL = 24 * 3600

# Progress of an install requested from the dashboard's Updates tab, which
# follows this file. States: checking, downloading, installing (the app is
# about to restart), current, manual (download from the release page), and
# failed. Without it the tab sat on "Downloading…" whenever an install
# failed or was skipped.
INSTALL_STATUS_FILE = "install_update_status.json"

# How long an explicit install waits for an automatic check that is
# already running, rather than dropping the request.
INSTALL_LOCK_WAIT = 30

# Runs detached after the app exits: swaps the bundle, relaunches, cleans up.
INSTALL_SCRIPT = """#!/bin/bash
PID="$1"; APP="$2"; NEW="$3"; STAGING="$4"

# Wait (up to a minute) for the running app to exit.
for _ in $(seq 1 120); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
done

# Keep the old bundle until the new one is fully in place, so a failed copy
# (full disk, permissions) leaves the working app rather than no app.
BACKUP="$APP.previous"
rm -rf "$BACKUP"
if mv "$APP" "$BACKUP"; then
    if ditto "$NEW" "$APP"; then
        rm -rf "$BACKUP"
    else
        rm -rf "$APP"
        mv "$BACKUP" "$APP"
    fi
fi
open "$APP"
rm -rf "$STAGING"
"""


def parse_version(text):
    """Turn a tag like 'v1.5.1' or 'v1.5.2-dev.3' into a comparable key.

    A development build sorts just below the release it leads up to:
    1.5.1 < 1.5.2-dev.1 < 1.5.2-dev.2 < 1.5.2. Trailing zeros don't count,
    so 'v1.3' and '1.3.0' are equal. Returns None for any other format.
    """
    match = VERSION_PATTERN.match((text or "").strip())
    if not match:
        return None
    numbers = [int(n) for n in match.group(1).split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    dev = match.group(2)
    if dev is None:
        return (tuple(numbers), 1, 0)
    return (tuple(numbers), 0, int(dev))


def is_dev_version(text):
    """Return True for a development build version such as '1.5.2-dev.1'."""
    version = parse_version(text)
    return bool(version) and version[1] == 0


def is_frozen():
    """Return True when running inside a packaged .app bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_path():
    """Return the path of the running .app bundle, or None when run from source."""
    if not is_frozen():
        return None
    # sys.executable is <bundle>.app/Contents/MacOS/<name>
    path = os.path.abspath(sys.executable)
    for _ in range(3):
        path = os.path.dirname(path)
    return path if path.endswith(".app") else None


def fetch_latest_release(include_dev=False):
    """Return details of the newest published release, or None.

    include_dev also considers development builds and other pre-releases.
    Without it, only full releases count; a -dev tag is skipped even if its
    pre-release box was left unticked on GitHub. Shared by the console's
    automatic check and the dashboard's Updates tab.
    """
    resp = requests.get(
        API_RELEASES,
        params={"per_page": RELEASES_PER_CHECK},
        timeout=10,
        headers={"Accept": "application/vnd.github+json"},
    )
    if resp.status_code != 200:
        return None

    data = resp.json()
    if not isinstance(data, list):
        return None

    releases = []
    for item in data:
        if not isinstance(item, dict):
            continue
        release = _release_details(item)
        if release is None:
            continue
        if not include_dev and (release["is_dev"] or item.get("prerelease")):
            continue
        releases.append(release)

    return max(releases, key=lambda r: r["version"], default=None)


def _release_details(data):
    """Return what the updater needs from one GitHub release, or None."""
    if data.get("draft"):
        return None

    tag = data.get("tag_name", "")
    version = parse_version(tag)
    if not version:
        return None

    asset_url = None
    asset_name = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.lower().endswith(".zip") and "switchbored" in name.lower():
            asset_url = asset.get("browser_download_url")
            asset_name = name
            break

    return {
        "tag": tag,
        "version": version,
        "page_url": data.get("html_url", RELEASES_PAGE),
        "asset_url": asset_url,
        "asset_name": asset_name,
        "notes": (data.get("body") or "").strip(),
        "is_dev": version[1] == 0,
    }


class UpdateManager:
    """Checks GitHub Releases for new versions and installs update bundles.

    Settings and user plugins live in Application Support, outside the app
    bundle, so replacing the bundle preserves them. Updater bookkeeping is
    kept in its own state file rather than the settings file, which the
    dashboard process owns.
    """

    def __init__(self, console, current_version):
        self.console = console
        self.core = console.core
        self.current_label = current_version
        self.current_version = parse_version(current_version) or OLDEST_VERSION
        self.state_path = self.core.runtime_path("update_state.json")
        self._check_lock = threading.Lock()

    def include_dev_builds(self):
        """Return True when Settings asks for development builds too."""
        return bool(self.core.settings.get("update_dev_builds", False))

    # ------------------------------------------------------------------
    # State file
    # ------------------------------------------------------------------

    def _load_state(self):
        """Return the persisted updater state, or an empty dict."""
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self, **updates):
        """Merge the given keys into the persisted updater state."""
        state = self._load_state()
        state.update(updates)
        try:
            with open(self.state_path, "w") as f:
                json.dump(state, f)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------

    def maybe_auto_check(self):
        """Run a silent check if the last one was more than a day ago."""
        if time.time() - self._load_state().get("last_check", 0) < AUTO_CHECK_INTERVAL:
            return
        self.check_now()

    def check_now(self):
        """Check for a newer release and offer it once per version.

        Call from a background thread. Failures and up-to-date results stay
        silent; this is the automatic daily check.
        """
        if not self._check_lock.acquire(blocking=False):
            return

        try:
            try:
                release = fetch_latest_release(include_dev=self.include_dev_builds())
            except Exception as e:
                release = None
                self.core.log("UPDATER", f"Update check failed: {type(e).__name__}: {e}")

            self._save_state(last_check=time.time())

            if release is None or release["version"] <= self.current_version:
                return

            if self._load_state().get("prompted_tag") == release["tag"]:
                return
            self._save_state(prompted_tag=release["tag"])

            self._offer_update(release)
        finally:
            self._check_lock.release()

    def install_latest(self, include_dev=None):
        """Download and install the latest release without prompting.

        Triggered by the dashboard's Updates tab, where the user has already
        chosen to install; call from a background thread. Every outcome is
        reported back to the tab through INSTALL_STATUS_FILE.

        include_dev: whether development builds count, as the Updates tab
        checked; None uses the saved setting.
        """
        if include_dev is None:
            include_dev = self.include_dev_builds()
        self._report_install("checking", "Checking the latest release…")

        # The user asked for this, so wait out an automatic check that is
        # already running instead of silently dropping the request.
        if not self._check_lock.acquire(timeout=INSTALL_LOCK_WAIT):
            self._report_install("failed", "Another update check is still running. Try again in a moment.")
            return

        try:
            try:
                release = fetch_latest_release(include_dev=include_dev)
            except Exception as e:
                release = None
                self.core.log("UPDATER", f"Install check failed: {type(e).__name__}: {e}")

            if release is None:
                self._report_install("failed", "Could not reach GitHub to download the update.")
                return

            if release["version"] <= self.current_version:
                self._report_install("current", "You are already running the latest version.")
                return

            if not (is_frozen() and release["asset_url"]):
                # Nothing to self-install (running from source, or no bundle
                # attached); fall back to the release page.
                self._report_install(
                    "manual", f"Version {release['tag']} must be downloaded from the release page."
                )
                self._offer_update(release)
                return

            self._download_and_install(release, from_dashboard=True)
        finally:
            self._check_lock.release()

    def _report_install(self, state, message):
        """Record install progress for the dashboard's Updates tab."""
        path = self.core.runtime_path(INSTALL_STATUS_FILE)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"state": state, "message": message, "time": time.time()}, f)
            os.replace(tmp, path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Prompting and installing
    # ------------------------------------------------------------------

    def _alert(self, title, message):
        """Show a simple alert on the main thread."""
        import rumps
        self.console.ui_call(lambda: rumps.alert(title=title, message=message, ok="OK"))

    def _offer_update(self, release):
        """Prompt for the found update; install in place or open the release page."""
        import rumps

        current = self.current_label
        kind = "Development Build" if release.get("is_dev") else "Update"

        def prompt():
            if is_frozen() and release["asset_url"]:
                clicked = rumps.alert(
                    title=f"SwitchBored {release['tag']} Available",
                    message=(
                        f"You are running v{current}.\n\n"
                        f"Download and install this {kind.lower()} now?\n"
                        "Your settings and plugins are kept, and the app "
                        "restarts when the update is applied."
                    ),
                    ok="Install",
                    cancel="Later",
                )
                if clicked == 1:
                    threading.Thread(
                        target=self._download_and_install,
                        args=(release,),
                        daemon=True,
                    ).start()
            else:
                extra = (
                    "\nYou are running from source; update with git pull, "
                    "or download the app bundle from the release page."
                    if not is_frozen()
                    else "\nNo installable bundle was attached to this "
                         "release, so it must be downloaded manually."
                )
                clicked = rumps.alert(
                    title=f"SwitchBored {release['tag']} Available",
                    message=f"You are running v{current}.{extra}",
                    ok="View Release",
                    cancel="Later",
                )
                if clicked == 1:
                    webbrowser.open(release["page_url"])

        self.console.ui_call(prompt)

    def _download_and_install(self, release, from_dashboard=False):
        """Download the release bundle, stage the swap, and restart the app.

        from_dashboard: the Updates tab shows failures itself, so no alert.
        """
        app_path = bundle_path()
        if not app_path:
            self._report_install("failed", "Could not locate the installed app bundle.")
            return

        try:
            self._report_install("downloading", f"Downloading {release['tag']}…")
            staging = tempfile.mkdtemp(prefix="switchbored_update_")
            zip_path = os.path.join(staging, release["asset_name"])

            self.core.log("UPDATER", f"Downloading {release['asset_name']}")
            with requests.get(release["asset_url"], stream=True, timeout=30) as resp:
                resp.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)

            # ditto preserves permissions, symlinks, and signatures, which
            # zipfile does not.
            extract_dir = os.path.join(staging, "extracted")
            subprocess.run(["ditto", "-x", "-k", zip_path, extract_dir], check=True)

            new_app = self._find_app_bundle(extract_dir)
            if not new_app:
                raise RuntimeError("no .app bundle found in the downloaded archive")

            helper = os.path.join(staging, "install_update.sh")
            with open(helper, "w") as f:
                f.write(INSTALL_SCRIPT)
            os.chmod(helper, 0o755)

            subprocess.Popen(
                ["/bin/bash", helper, str(os.getpid()), app_path, new_app, staging],
                start_new_session=True,
            )

            self.core.log("UPDATER", f"Update staged; restarting into {release['tag']}")
            self._report_install("installing", "Installing — SwitchBored will restart in a moment.")
            self.console.ui_call(lambda: self.console.custom_quit(None))

        except Exception as e:
            self.core.log("UPDATER", f"Update failed: {type(e).__name__}: {e}")
            self._report_install("failed", f"The update could not be installed: {e}")
            if not from_dashboard:
                self._alert("Update Failed", f"The update could not be installed:\n{e}")

    @staticmethod
    def _find_app_bundle(root):
        """Find a valid .app bundle at the top of the extracted archive."""
        candidates = []
        for entry in sorted(os.listdir(root)):
            path = os.path.join(root, entry)
            if entry.endswith(".app"):
                candidates.append(path)
            elif os.path.isdir(path):
                # Some archives nest the bundle one folder deep.
                candidates.extend(
                    os.path.join(path, inner)
                    for inner in sorted(os.listdir(path))
                    if inner.endswith(".app")
                )

        for candidate in candidates:
            if os.path.isdir(os.path.join(candidate, "Contents", "MacOS")):
                return candidate
        return None
