"""py2app build configuration for the SwitchBored menu-bar app.

Build from a clean virtual environment so py2app's dependency scan only
sees the app's own requirements:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt py2app
    .venv/bin/python setup.py py2app

The bundle lands in dist/SwitchBored.app.
"""

import glob
import os

from setuptools import setup

from version import APP_NAME, VERSION


def mypyc_helper_modules():
    """Find charset_normalizer's mypyc helper extension.

    The wheel ships a compiled module with a hashed name (something like
    'ada9...__mypyc') that py2app's dependency scan cannot discover; without
    it, charset_normalizer fails to import inside the bundle.
    """
    try:
        import charset_normalizer
    except ImportError:
        return []

    site_dir = os.path.dirname(os.path.dirname(os.path.abspath(charset_normalizer.__file__)))
    return [
        os.path.basename(path).split(".")[0]
        for path in glob.glob(os.path.join(site_dir, "*__mypyc.cpython-*.so"))
    ]

setup(
    app=["console.py"],
    name=APP_NAME,
    options={
        "py2app": {
            "iconfile": "icon.icns",
            # "plugins" must stay a real directory (not zipped) so the
            # plugin manager can list the bundled examples.
            # charset_normalizer is imported conditionally by requests, so
            # the dependency scan misses it.
            "packages": [
                "rumps", "customtkinter", "PIL", "ping3", "requests",
                "charset_normalizer", "plugins",
            ],
            "includes": mypyc_helper_modules(),
            "plist": {
                "CFBundleName": APP_NAME,
                "CFBundleIdentifier": "com.jtolchard.switchbored",
                "CFBundleShortVersionString": VERSION,
                # Menu-bar app: no Dock icon
                "LSUIElement": True,
            },
        }
    },
)
