import os
import sys
import json
import time
import socket
import getpass
import platform
import subprocess
from ping3 import ping

class MachineManagerCore:
    """Core service layer for settings, connectivity checks, SSH helpers, and plugins."""

    def __init__(self, test_mode=False):
        """Initialize the core state and load persisted settings."""
        self.test_mode = test_mode
        self.settings_path = self._get_path()

        self._settings_mtime = None
        self.log_callback = None
        self.settings = {}

        # True when no settings file existed and defaults were just created,
        # i.e. the very first launch on this machine.
        self.first_run = False

        self.settings = self.load_settings()

    def runtime_path(self, filename):
        """Return a path inside the app's Application Support directory."""
        base = os.path.expanduser("~/Library/Application Support/SwitchBored")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, filename)

    def plugins_dir(self):
        """Return the user plugins directory, creating it if needed.

        User-installed plugins live under Application Support rather than
        inside the app itself, so they survive app updates.
        """
        path = self.runtime_path("plugins")
        os.makedirs(path, exist_ok=True)
        return path

    def reset_settings(self):
        """Delete persisted settings, logs, and updater state, then recreate defaults.

        Installed plugins are deliberately left in place; this only removes
        configuration and bookkeeping files.
        """
        for filename in ("switchbored_debug.log", "switchbored_debug.log.old",
                         "update_state.json"):
            try:
                os.remove(self.runtime_path(filename))
            except OSError:
                pass

        try:
            os.remove(self.settings_path)
        except OSError:
            pass

        self._settings_mtime = None
        return self._create_default_settings()

    def _get_path(self):
        """Return the settings file path for normal or test mode."""
        filename = "sysadmin_settings_test.json" if self.test_mode else "sysadmin_settings.json"
        if platform.system() == "Darwin":
            path = os.path.expanduser("~/Library/Application Support/SwitchBored")
            os.makedirs(path, exist_ok=True)
            return os.path.join(path, filename)
        return filename

    def load_settings(self, silent=False):
        """Load settings from disk, retrying briefly if the file is busy or incomplete."""
        if os.path.exists(self.settings_path):
            if self._settings_mtime == os.path.getmtime(self.settings_path):
                return self.settings

        for _ in range(5):
            try:
                if not os.path.exists(self.settings_path):
                    if not silent:
                        self.log("SETTINGS", f"Settings file not found. Creating defaults at {self.settings_path}")
                    return self._create_default_settings()

                mtime = os.path.getmtime(self.settings_path)

                with open(self.settings_path, 'r') as f:
                    loaded_data = json.load(f)

                self.settings = self._sanitize(loaded_data)

                # Only remember the mtime once the file has parsed successfully,
                # so a half-written file is retried on the next call instead of
                # being cached as the current state.
                self._settings_mtime = mtime

                if not silent:
                    self.log("SETTINGS", f"Successfully loaded settings from {self.settings_path}")

                return self.settings

            except (json.JSONDecodeError, IOError):
                time.sleep(0.05)

        return self.settings

    def _create_default_settings(self):
        """Create, persist, and return a default settings dictionary."""
        self.first_run = True
        defaults = self.get_defaults()

        # Ensure the parent folder exists (Application Support path)
        settings_dir = os.path.dirname(self.settings_path)
        if settings_dir:
            os.makedirs(settings_dir, exist_ok=True)

        try:
            self._write_settings_file(defaults)
            self.log("SETTINGS", f"Created default settings at {self.settings_path}")
        except Exception as e:
            # If writing fails, still return defaults so the app can run
            self.log("SETTINGS", f"Failed to write default settings: {type(e).__name__}: {e}")

        self.settings = defaults
        return defaults

    def _write_settings_file(self, data):
        """Atomically write the settings file so readers never see a partial file."""
        tmp_path = self.settings_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_path, self.settings_path)
        self._settings_mtime = os.path.getmtime(self.settings_path)

    def _sanitize(self, data):
        """Fill missing or invalid settings values with safe defaults."""
        defaults = self.get_defaults()
        # Clean top-level keys
        for key in defaults:
            if key not in data or not isinstance(data[key], type(defaults[key])):
                data[key] = defaults[key]

        # Clean individual machine objects
        valid_machines = []
        for m in data.get("machines", []):
            if isinstance(m, dict) and "address" in m:
                # Fill in missing machine keys with safe defaults
                m.setdefault("name", "New Machine")
                m.setdefault("user", getpass.getuser())
                m.setdefault("port", "22")
                m.setdefault("icon", "💻")
                m.setdefault("connections", ["SSH", "SFTP"])
                valid_machines.append(m)
        data["machines"] = valid_machines
        return data

    def save_settings(self, settings_dict=None, **kwargs):
        """Persist settings to disk, optionally replacing the in-memory settings first.

        The dashboard process is the sole writer of the settings file; the
        console only reads it. Keep it that way to avoid two-writer races.
        """
        if settings_dict is not None:
            self.settings = settings_dict

        try:
            self._write_settings_file(self.settings)

            if not kwargs.get('silent'):
                self.log("SETTINGS", f"Saved successfully to {self.settings_path}")
            return True
        except Exception as e:
            # Fallback print if logging fails
            print(f"ERROR: Failed to save settings: {e}")
            return False

    def get_defaults(self):
        """Return the default application settings."""
        return {
            "global_emoji": "🖥️",
            "use_ref_server": True,
            "ref_server": "1.1.1.1",
            "terminal_type": "Terminal",
            "sftp_tool": "Cyberduck",
            "open_on_startup": False,
            "machines": [],
            "plugins": {
                "enabled": [],
                "config": {}
            }
        }

    def get_ssh_string(self, machine):
        """Return a standard SSH target string in the form user@host -p port."""
        user = machine.get("user") or getpass.getuser()
        addr = machine.get("address", "")
        port = machine.get("port") or "22"
        return f"{user}@{addr} -p {port}"

    def _ssh_argv(self, machine, command):
        """Build an argv list for running a remote command over SSH."""
        user = machine.get("user") or getpass.getuser()
        addr = machine.get("address", "")
        port = str(machine.get("port") or "22")
        return [
            "ssh",
            "-o", "ConnectTimeout=3",
            "-o", "BatchMode=yes",
            "-p", port,
            f"{user}@{addr}",
            command,
        ]

    def run_ssh_command(self, machine, command, timeout=5):
        """Run a remote command over SSH and return (ok, output).

        ok is False when the connection itself failed (local error, timeout,
        or ssh exiting 255); output is the combined stdout/stderr either way.
        The command is passed as a single argv element, so no local shell is
        involved and no local quoting is needed.
        """
        try:
            p = subprocess.run(
                self._ssh_argv(machine, command),
                capture_output=True,
                text=True,
                timeout=timeout
            )
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            # ssh itself exits 255 on connection failure; remote commands
            # report their own status through other exit codes.
            if p.returncode == 255:
                return False, out
            return True, out
        except subprocess.TimeoutExpired:
            return False, "Error: timeout"
        except Exception as e:
            return False, f"Error: {e}"

    def get_tmux_string(self, machine):
        """Return an SSH command that attaches to or creates the remote tmux session."""
        ssh_str = self.get_ssh_string(machine)
        # This command checks if 'main' exists, attaches if it does, creates if not
        tmux_cmd = "tmux attach-session -t main || tmux new-session -s main"
        return f"ssh -t {ssh_str} '{tmux_cmd}'"

    def check_status(self, address, timeout=1.0, port=None):
        """Ping a host and return latency in seconds, or None if it is unreachable.

        When ICMP fails (blocked by a firewall, or raw sockets unavailable)
        and a TCP port is provided, fall back to timing a TCP connection to
        that port so reachable-but-unpingable machines still show online.
        """
        if not address:
            return None

        try:
            res = ping(address, timeout=timeout)
            if res is not None and res is not False:
                return res
        except Exception as e:
            self.log("NETWORK", f"Ping error on {address}: {type(e).__name__}: {e}")

        if port:
            try:
                start = time.monotonic()
                with socket.create_connection((address, int(port)), timeout=timeout):
                    return time.monotonic() - start
            except Exception:
                pass

        self.log("NETWORK", f"Ping failed for {address}")
        return None

    def log(self, category, message):
        """Write a formatted debug log entry to the callback and shared log file."""
        if not self.settings.get("debug_mode", False):
            return

        try:
            import inspect
            from datetime import datetime

            caller = inspect.stack()[1]
            filename = os.path.basename(caller.filename)
            func = caller.function
            line = caller.lineno

            log_str = f"[{category.center(8)}] {message} ({filename}:{func}:{line})\n"

            if self.log_callback:
                self.log_callback(log_str)

            log_path = self.runtime_path("switchbored_debug.log")

            # Rotate the log once it grows past ~1 MB so it never balloons.
            try:
                if os.path.exists(log_path) and os.path.getsize(log_path) > 1_000_000:
                    os.replace(log_path, log_path + ".old")
            except OSError:
                pass

            with open(log_path, "a") as f:
                ts = datetime.now().strftime("%H:%M:%S")
                f.write(f"{ts} {log_str}")

        except Exception:
            pass

    def _import_plugin_module(self, name):
        """Import a plugin module, preferring the user plugins directory.

        Falls back to the plugins package bundled with the app, so shipped
        examples like menu_template remain importable.
        """
        import importlib
        import importlib.util

        user_path = os.path.join(self.plugins_dir(), f"{name}.py")
        if os.path.exists(user_path):
            spec = importlib.util.spec_from_file_location(
                f"switchbored_plugins.{name}", user_path
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module

        module_name = name if name.startswith("plugins.") else f"plugins.{name}"
        importlib.invalidate_caches()
        return importlib.import_module(module_name)

    def load_plugins(self, app_instance=None):
        """Instantiate and return the enabled plugins.

        Menu attachment for start-style plugins happens separately in
        attach_plugin_menus(), which the console calls on every menu rebuild
        (rebuild_menu() clears the whole menu, so anything attached here
        would be wiped by the first rebuild).
        """
        plugins_cfg = self.settings.get("plugins", {})
        enabled = plugins_cfg.get("enabled", []) or []

        self.log("PLUGINS", f"Enabled plugins: {', '.join(enabled) if enabled else 'none'}")

        loaded = []

        for name in enabled:
            self.log("PLUGINS", f"Loading plugin: {name}")

            try:
                module = self._import_plugin_module(name)

                plugin_cls = getattr(module, "Plugin", None)
                if plugin_cls is None:
                    self.log("PLUGINS", f"{name}: no Plugin class found")
                    continue

                instance = plugin_cls(self)
                loaded.append(instance)

                self.log("PLUGINS", f"{name}: initialized")

            except Exception as e:
                self.log("PLUGINS", f"{name}: failed to load ({type(e).__name__}: {e})")

        self.log("PLUGINS", f"{len(loaded)} plugin(s) successfully loaded")

        return loaded

    def attach_plugin_menus(self, app_instance):
        """Attach start-style plugin submenus under a 'Plugins' root menu item.

        Called from rebuild_menu() on every rebuild; plugins implementing
        start() are invoked each time with a fresh submenu, mirroring the
        on_menu_build hook semantics.
        """
        import rumps

        starters = [p for p in getattr(app_instance, "plugins", []) if hasattr(p, "start")]
        if not starters:
            return

        app_instance.menu.add(rumps.separator)
        plugins_root = rumps.MenuItem("Plugins 🧩")
        app_instance.menu.add(plugins_root)

        for instance in starters:
            name = instance.__class__.__module__.split(".")[-1]
            plugin_sub_header = rumps.MenuItem(name.replace("_", " ").title())
            plugins_root.add(plugin_sub_header)

            try:
                instance.start(plugin_sub_header)
                self.log("PLUGINS", f"{name}: menu attached")
            except Exception as e:
                self.log("PLUGINS", f"{name}: start failed ({type(e).__name__}: {e})")
