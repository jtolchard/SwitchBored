import os
import sys
import subprocess

def app_support_path(filename: str) -> str:
    """Return the path to a file in the SwitchBored Application Support directory."""
    base = os.path.expanduser("~/Library/Application Support/SwitchBored")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, filename)

def _notify_already_running():
    """Show a visible notice that another instance owns the lock.

    When launched from Finder there is no terminal to print to, and a
    menu-bar app has no window — without this, declining to start looks
    exactly like a crash.
    """
    script = (
        'display alert "SwitchBored is already running" '
        'message "Look for its icon in the menu bar." '
        'as informational giving up after 30'
    )
    try:
        subprocess.Popen(["osascript", "-e", script])
    except Exception:
        pass

def acquire_console_lock():
    """Ensure only one SwitchBored console instance is running."""
    pid_file = app_support_path("console.pid")

    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())

            # Check whether the recorded process is still alive.
            os.kill(old_pid, 0)

            # Bring the existing console instance to the foreground.
            script = (
                'tell application "System Events" '
                'to tell (first process whose unix id is {}) to set frontmost to true'
            ).format(old_pid)

            os.system(f"osascript -e '{script}' > /dev/null 2>&1")

            print("SwitchBored is already running.")
            _notify_already_running()
            sys.exit(0)

        except (ProcessLookupError, ValueError, OSError):
            # Stale or invalid PID file; remove it so a new instance can start.
            try:
                os.remove(pid_file)
            except OSError:
                pass

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    return pid_file

def release_console_lock(pid_file):
    """Remove the console PID lock file."""
    if os.path.exists(pid_file):
        os.remove(pid_file)