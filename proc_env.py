"""Environment helper for launching external subprocesses.

When SwitchBored runs as a py2app bundle, the embedded interpreter has
PYTHONHOME/PYTHONPATH pointed at the app's own Contents/Resources. Any
subprocess inherits these, so a `python` run inside a Terminal window we
open, or a custom shortcut command, breaks with:

    Fatal Python error: Failed to import encodings module

Pass clean_child_env() as `env=` to every external process we launch so the
bundle's interpreter settings never leak into the user's shell or tools.
"""

import os

# py2app sets these for its embedded interpreter; external processes must
# not inherit them. (Left intact for our own relaunches via spawn_self,
# which re-launch the same bundle.)
_LEAK_VARS = ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE")


def clean_child_env():
    """Return a copy of the environment safe to pass to external subprocesses."""
    env = os.environ.copy()
    for var in _LEAK_VARS:
        env.pop(var, None)
    return env
