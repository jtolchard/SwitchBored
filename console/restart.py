import os
import sys

from console.bootstrap import spawn_self


def handle_restart(console):
    """Perform a full application restart if the restart flag file is present."""
    flag_file = console.core.runtime_path("restart_app.flag")

    if not os.path.exists(flag_file):
        return

    console.core.log("SYSTEM", "Restart flag detected; relaunching the application")

    try:
        os.remove(flag_file)
    except Exception:
        pass

    # Remove PID files so the replacement process does not treat itself as a duplicate.
    for pid_file in ["console.pid", "dashboard.pid"]:
        p = console.core.runtime_path(pid_file)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # Give plugins a chance to stop background work cleanly.
    for p in console.plugins:
        if hasattr(p, "stop"):
            try:
                p.stop()
            except Exception:
                pass

    # Relaunch with the same flags (e.g. --test); spawn_self picks the right
    # entry point for source and bundled runs.
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    spawn_self(*flags)

    os._exit(0)