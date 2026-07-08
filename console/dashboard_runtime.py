import os
import subprocess

from console.bootstrap import spawn_self


class DashboardRuntime:
    """Helper for launching, focusing, and cleaning up the dashboard process."""

    def __init__(self, core):
        """Store core references and initialize dashboard process tracking."""
        self.core = core
        self.dashboard_proc = None
        self.dashboard_pid_file = core.runtime_path("dashboard.pid")

    def cleanup_dashboard(self):
        """Terminate the dashboard process if it was spawned by this console instance."""
        if self.dashboard_proc and self.dashboard_proc.poll() is None:
            try:
                self.dashboard_proc.terminate()
            except Exception:
                pass

    def open_or_focus(self):
        """Focus the running dashboard if there is one, otherwise launch it."""
        pid_file = self.dashboard_pid_file

        def focus_pid(pid: int) -> bool:
            """Bring the process with the given PID to the foreground."""
            try:
                script = f'''
                tell application "System Events"
                    set frontmost of (first process whose unix id is {pid}) to true
                end tell
                '''
                res = subprocess.run(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return res.returncode == 0
            except Exception:
                return False

        # If this console instance already launched the dashboard, focus that process.
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)
                focus_pid(pid)
                return
            except Exception:
                try:
                    os.remove(pid_file)
                except Exception:
                    pass

        # If this console instance already launched the dashboard, focus that process.
        if self.dashboard_proc and self.dashboard_proc.poll() is None:
            focus_pid(self.dashboard_proc.pid)
            return

        # Otherwise launch a new dashboard process. The app re-invokes itself
        # with --dashboard, which works both from source and inside a bundle.
        args = ["--dashboard"]
        if getattr(self.core, "test_mode", False):
            args.append("--test")

        self.dashboard_proc = spawn_self(*args)