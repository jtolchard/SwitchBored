import os
import sys
import subprocess
from pathlib import Path

def _frozen_launcher():
    """Return the app bundle's launcher binary.

    Inside a py2app bundle, sys.executable is the embedded plain Python
    interpreter, which does not understand our flags. The launcher named by
    CFBundleExecutable is what runs the main script and forwards arguments.
    """
    resources = os.environ.get("RESOURCEPATH")
    if not resources:
        return sys.executable

    contents = os.path.dirname(resources)
    try:
        import plistlib
        with open(os.path.join(contents, "Info.plist"), "rb") as f:
            name = plistlib.load(f)["CFBundleExecutable"]
    except Exception:
        name = "SwitchBored"
    return os.path.join(contents, "MacOS", name)

def spawn_self(*extra_args):
    """Launch another instance of this application with the given arguments.

    Running from source this is `python console.py <args>`; inside a py2app
    bundle it invokes the bundle's launcher binary instead.
    """
    if getattr(sys, "frozen", False):
        cmd = [_frozen_launcher(), *extra_args]
    else:
        entry = Path(__file__).resolve().parent.parent / "console.py"
        cmd = [sys.executable, str(entry), *extra_args]
    return subprocess.Popen(cmd, start_new_session=True)

def apply_macos_bundle_name(app_name: str) -> None:
    """
    Set the macOS bundle display name at runtime.

    This ensures the application appears with the correct name in macOS UI
    elements such as the menu bar, Dock, and application switcher when the
    app is launched from Python rather than from a packaged .app bundle.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle()
        if bundle:
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
            if info:
                info["CFBundleName"] = app_name
    except Exception:
        pass