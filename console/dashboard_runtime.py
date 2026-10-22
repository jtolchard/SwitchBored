import os
import time
import signal

from console.bootstrap import spawn_self
from console.process_guard import read_pid_file, is_switchbored_process

# Signal asking a running dashboard to come to the front. The dashboard
# handles it itself (un-minimise, pull back on screen, raise, activate), so
# no osascript round trip or System Events permission is needed.
SHOW_SIGNAL = signal.SIGUSR1


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

    def _remove_pid_file(self):
        try:
            os.remove(self.dashboard_pid_file)
        except OSError:
            pass

    def _running_dashboard(self):
        """Return (pid, ready) for a running dashboard, or (None, False).

        ready means the dashboard has installed its signal handlers, which
        it announces by writing its PID file. Before that, SIGUSR1 would
        still have its default action and kill the starting process.
        """
        recorded = read_pid_file(self.dashboard_pid_file)

        proc = self.dashboard_proc
        if proc is not None and proc.poll() is None:
            return proc.pid, recorded == proc.pid

        # A dashboard this console didn't spawn, e.g. one that outlived a
        # console restart. Verify it before trusting a possibly stale file.
        if recorded and is_switchbored_process(recorded, "--dashboard"):
            return recorded, True

        if recorded is not None:
            self._remove_pid_file()
        return None, False

    def open_or_focus(self):
        """Focus the running dashboard if there is one, otherwise launch it."""
        pid, ready = self._running_dashboard()

        if pid is not None:
            if not ready:
                # Still starting up; it reveals and activates itself.
                self.core.log("UI", "Dashboard is still starting; not signalling it")
                return
            try:
                os.kill(pid, SHOW_SIGNAL)
                return
            except OSError as e:
                self.core.log("UI", f"Could not signal dashboard {pid}: {e}; relaunching")
                self._remove_pid_file()

        # Otherwise launch a new dashboard process. The app re-invokes itself
        # with --dashboard, which works both from source and inside a bundle.
        args = ["--dashboard"]
        if getattr(self.core, "test_mode", False):
            args.append("--test")

        self.dashboard_proc = spawn_self(*args)

    def shutdown(self):
        """Close any running dashboard, letting it save its window state first."""
        proc = self.dashboard_proc
        if proc is not None and proc.poll() is None:
            try:
                # SIGTERM lets the dashboard save its window state and clean up.
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Also stop a dashboard this console didn't spawn — but only after
        # confirming the PID really is one, so a stale file can never make
        # us kill an unrelated process that inherited the PID.
        pid = read_pid_file(self.dashboard_pid_file)
        if pid and is_switchbored_process(pid, "--dashboard"):
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    time.sleep(0.1)
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

        self._remove_pid_file()
