import subprocess
import os

from proc_env import clean_child_env

class ActionManager:
    """Launch helper for terminal, SSH, SFTP, and VNC actions."""

    def __init__(self, core_instance):
        """Store a reference to the shared core service object."""
        self.core = core_instance

    def _notify_app_missing(self, title, message, download_url):
        """Tell the user a helper application is missing, via whichever UI hosts us."""
        self.core.log("ACTIONS", f"{title}: {message}")
        master = getattr(self.core, "master", None)
        notify = getattr(master, "show_error_dialog", None)
        if callable(notify):
            notify(title, message, download_url)

    def _launch_in_terminal(self, command_string):
        """Run a shell command in the user's preferred terminal application."""
        settings = self.core.settings
        term_app = settings.get('terminal_type', 'Terminal')
        self.core.log("TERMINAL", f"Launching in {term_app}: {command_string}")

        # Escape for embedding inside an AppleScript double-quoted string.
        escaped = command_string.replace("\\", "\\\\").replace('"', '\\"')

        if term_app == "iTerm":
            script = f'tell application "iTerm" to create window with profile "Default" command "{escaped}"'
        else:
            script = f'tell application "Terminal" to do script "{escaped}"'
        subprocess.Popen(["osascript", "-e", script], env=clean_child_env())

    def open_terminal(self, machine):
        """Open an interactive SSH session in the preferred terminal."""
        ssh_str = self.core.get_ssh_string(machine)
        self._launch_in_terminal(f"ssh {ssh_str}")

    def open_tmux(self, machine):
        """Open or attach to the machine's main tmux session."""
        tmux_cmd = self.core.get_tmux_string(machine)
        self._launch_in_terminal(tmux_cmd)

    def open_sftp(self, machine):
        """Open the configured SFTP client (Cyberduck or FileZilla) for the machine."""
        self.core.log("SFTP", f"Opening SFTP for {machine.get('name')} at {machine.get('address')}")
        tool = self.core.settings.get("sftp_tool") or "Cyberduck"

        user = machine.get("user", "")
        addr = machine.get("address", "")
        port = machine.get("port") or 22
        url = f'sftp://{user}@{addr}:{port}'

        if tool == "FileZilla":
            fz_path = "/Applications/FileZilla.app/Contents/MacOS/filezilla"
            if os.path.exists(fz_path):
                subprocess.Popen([fz_path, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=clean_child_env())
            else:
                self._notify_app_missing(
                    "FileZilla Not Found",
                    "FileZilla was not found in your Applications folder.",
                    "https://filezilla-project.org/download.php?platform=osx"
                )
        else:
            # Default to Cyberduck via 'open' command
            duck_path = "/Applications/Cyberduck.app"
            if os.path.exists(duck_path):
                subprocess.Popen(["open", "-a", "Cyberduck", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=clean_child_env())
            else:
                self._notify_app_missing(
                    "Cyberduck Not Found",
                    "Cyberduck was not found in your Applications folder.",
                    "https://cyberduck.io/"
                )

    def open_vnc(self, machine):
        """Open a VNC session for the selected machine."""
        self.core.log("VNC", f"Opening VNC for {machine.get('name')} at {machine.get('address')}")
        address = machine.get('address')
        exec_path = "/Applications/VNC Viewer.app/Contents/MacOS/vncviewer"

        if os.path.exists(exec_path):
            cmd = [exec_path, f"{address}:5900", "-ProtocolVersion=3.3"]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=clean_child_env())
        else:
            self._notify_app_missing(
                "VNC Viewer Not Found",
                "RealVNC Viewer was not found in your Applications folder.",
                "https://www.realvnc.com/en/connect/download/viewer/macos/"
            )
