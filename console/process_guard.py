import os
import sys
import subprocess

def app_support_path(filename: str) -> str:
    """Return the path to a file in the SwitchBored Application Support directory."""
    base = os.path.expanduser("~/Library/Application Support/SwitchBored")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, filename)

def console_pid_path() -> str:
    """Return the path of the console's single-instance lock file."""
    return app_support_path("console.pid")

def read_pid_file(path):
    """Return the PID recorded in a PID file, or None if missing or invalid."""
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None

def is_switchbored_process(pid, flag=None):
    """Return True if pid is a live SwitchBored process.

    PID files outlive their process whenever it exits without cleaning up
    (macOS terminates the menu-bar app with exit(), and Cmd+Q or logout can
    end the dashboard the same way), and macOS reuses PIDs, especially after
    a reboot. Checking the command line keeps a stale file from blocking
    startup, or from making us signal an unrelated process.

    flag selects a child window process (e.g. "--dashboard"); without it,
    only the menu-bar console matches.
    """
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return False  # alive, but owned by another user: not ours
    except OSError:
        return False

    try:
        res = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return False
    command = res.stdout.strip()
    if res.returncode != 0 or not command:
        return False

    # Match on the executable or script name, not any mention of the app:
    # an editor with .../SwitchBored/core.py open must not count.
    #   Bundle: .../SwitchBored.app/Contents/MacOS/SwitchBored [--flag]
    #   Source: .../Python .../console.py [--flag]  (or dashboard.py)
    args = command.split()
    names = {os.path.basename(a) for a in args}

    if flag == "--dashboard" and "dashboard.py" in names:
        return True
    if "SwitchBored" not in names and "console.py" not in names:
        return False
    if flag:
        return flag in args
    return "--dashboard" not in args and "--about" not in args

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
    pid_file = console_pid_path()

    old_pid = read_pid_file(pid_file)
    if old_pid and old_pid != os.getpid() and is_switchbored_process(old_pid):
        print("SwitchBored is already running.")
        _notify_already_running()
        sys.exit(0)

    # Missing, stale, or pointing at a reused PID: take over the lock.
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    return pid_file

def release_console_lock(pid_file):
    """Remove the console PID lock file if it still belongs to this process."""
    if read_pid_file(pid_file) != os.getpid():
        return
    try:
        os.remove(pid_file)
    except OSError:
        pass
