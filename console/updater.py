import os
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
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Automatic checks run at most once per day.
AUTO_CHECK_INTERVAL = 24 * 3600

# Runs detached after the app exits: swaps the bundle, relaunches, cleans up.
INSTALL_SCRIPT = """#!/bin/bash
PID="$1"; APP="$2"; NEW="$3"; STAGING="$4"

# Wait (up to a minute) for the running app to exit.
for _ in $(seq 1 120); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
done

rm -rf "$APP"
ditto "$NEW" "$APP"
open "$APP"
rm -rf "$STAGING"
"""


def parse_version(text):
    """Turn a tag like 'v1.2.1' or '1.3' into a comparable tuple of ints."""
    if not text:
        return None
    parts = []
    for chunk in text.strip().lstrip("vV").split("."):
        if not chunk.isdigit():
            return None
        parts.append(int(chunk))
    return tuple(parts) or None


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


def fetch_latest_release():
    """Return details of the latest published release, or None.

    Shared by the console's automatic check and the dashboard's Updates tab.
    """
    resp = requests.get(
        API_LATEST,
        timeout=10,
        headers={"Accept": "application/vnd.github+json"},
    )
    if resp.status_code != 200:
        return None

    data = resp.json()
    if data.get("draft") or data.get("prerelease"):
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
        self.current_version = parse_version(current_version) or (0,)
        self.state_path = self.core.runtime_path("update_state.json")
        self._check_lock = threading.Lock()

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
                release = fetch_latest_release()
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

    def install_latest(self):
        """Download and install the latest release without prompting.

        Triggered by the dashboard's Updates tab, where the user has already
        chosen to install; call from a background thread.
        """
        if not self._check_lock.acquire(blocking=False):
            return

        try:
            try:
                release = fetch_latest_release()
            except Exception as e:
                release = None
                self.core.log("UPDATER", f"Install check failed: {type(e).__name__}: {e}")

            if release is None:
                self._alert("Update Failed", "Could not reach GitHub to download the update.")
                return

            if release["version"] <= self.current_version:
                return

            if not (is_frozen() and release["asset_url"]):
                # Nothing to self-install (running from source, or no bundle
                # attached); fall back to the release page.
                self._offer_update(release)
                return

            self._download_and_install(release)
        finally:
            self._check_lock.release()

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

        current = ".".join(map(str, self.current_version))

        def prompt():
            if is_frozen() and release["asset_url"]:
                clicked = rumps.alert(
                    title=f"SwitchBored {release['tag']} Available",
                    message=(
                        f"You are running v{current}.\n\n"
                        "Download and install the update now?\n"
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

    def _download_and_install(self, release):
        """Download the release bundle, stage the swap, and restart the app."""
        app_path = bundle_path()
        if not app_path:
            return

        try:
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
            self.console.ui_call(lambda: self.console.custom_quit(None))

        except Exception as e:
            self.core.log("UPDATER", f"Update failed: {type(e).__name__}: {e}")
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
