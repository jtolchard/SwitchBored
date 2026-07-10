import rumps
import os
import sys
import time
import queue
import signal
import socket
import tempfile
import threading
import subprocess
import webbrowser
import atexit
from collections import deque
from PIL import Image, ImageDraw
from core import MachineManagerCore
from actions import ActionManager
from console.dashboard_runtime import DashboardRuntime
from console.restart import handle_restart
from console.bootstrap import apply_macos_bundle_name, spawn_self
from console.process_guard import acquire_console_lock, release_console_lock
from console.updater import UpdateManager
from version import APP_NAME, VERSION

# How long a machine must be continuously unreachable (while the reference
# server is still reachable) before an offline notification fires.
OFFLINE_NOTIFY_SECONDS = 30

def _install_notification_presenter():
    """Make notifications show as banners even while the app is active.

    rumps' notification delegate only handles clicks; it does not implement
    userNotificationCenter:shouldPresentNotification:, so macOS may deliver
    notifications silently to Notification Center without showing a banner.
    Adding the method and returning True forces presentation.
    """
    try:
        import objc
        from rumps.rumps import NSApp as _RumpsNSApp

        def userNotificationCenter_shouldPresentNotification_(self, center, notification):
            return True

        objc.classAddMethods(
            _RumpsNSApp,
            [
                objc.selector(
                    userNotificationCenter_shouldPresentNotification_,
                    selector=b"userNotificationCenter:shouldPresentNotification:",
                    signature=b"Z@:@@",
                )
            ],
        )
        return True
    except Exception:
        return False

class SysAdminConsole(rumps.App):
    """Menu-bar application for launching, monitoring, and controlling SwitchBored."""
    def __init__(self, test_mode):
        """Initialize the console app, menus, timers, plugins, and runtime helpers."""
        self.core = MachineManagerCore(test_mode)
        self.core.master = self
        self.plugins = []

        self._debug_socket_path = self.core.runtime_path("debug_console.sock")
        self._pending_debug_logs = deque(maxlen=1000)

        def _console_log(msg: str):
            """Buffer debug messages until the dashboard console is ready to receive them."""
            self._pending_debug_logs.append(msg)
            self._flush_debug_log_backlog()

        self.core.log_callback = _console_log
        self.dashboard_runtime = DashboardRuntime(self.core)
        self.reload_flag_file = self.core.runtime_path("reload_menu.flag")
        self.actions = ActionManager(self.core)
        
        self._last_links = self.core.settings.get("custom_links", [])
        self._last_emoji = self.core.settings.get("global_emoji", "💻")
        self._last_ref_toggle = self.core.settings.get("use_ref_server", True)
        self._current_title = self._last_emoji
        
        super().__init__(APP_NAME, title=self._current_title, quit_button=None)

        self.plugins = self.core.load_plugins(self)

        self.worker_lock = threading.Lock()
        # Status results computed by worker threads, applied to the menu on
        # the main thread by process_ui_updates (AppKit is not thread-safe).
        self._status_lock = threading.Lock()
        self._pending_status = {}
        # General-purpose queue of callables to run on the main thread,
        # drained by the same UI timer.
        self._main_thread_calls = queue.Queue()
        self.reference_status = None

        # Outage tracking for per-machine offline notifications, keyed by
        # machine address: when the outage started, and which addresses have
        # already been notified for the current outage.
        self._offline_since = {}
        self._offline_notified = set()

        if not _install_notification_presenter():
            self.core.log("NOTIFY", "Could not enable banner presentation; notifications may arrive silently")

        self.updater = UpdateManager(self, VERSION)
        self.green_dot = self._generate_dot("green", "#2fa572")
        self.red_dot = self._generate_dot("red", "#e74c3c")

        self.menu_green_dot = self._get_status_icon("#2fa572")
        self.menu_red_dot = self._get_status_icon("#e74c3c")
        
        self.rebuild_menu()
        self.plugin_hook("on_console_init", self)

        # The app has no Dock icon, so on the very first launch the dashboard
        # opens automatically — otherwise a new user sees nothing but a small
        # menu-bar icon. After that, the open_on_startup setting decides.
        if self.core.first_run or self.core.settings.get("open_on_startup", False):
            threading.Timer(0.5, lambda: self.open_dash(None)).start()

        atexit.register(self.dashboard_runtime.cleanup_dashboard)

        rumps.Timer(self.process_ui_updates, 1).start()
        rumps.Timer(self.background_worker, 5).start()

        self._restart_timer = rumps.Timer(lambda _: handle_restart(self), 1)
        self._restart_timer.start()

        # Daily update check: once shortly after launch, then re-tested hourly.
        startup_check = threading.Timer(15.0, self.updater.maybe_auto_check)
        startup_check.daemon = True
        startup_check.start()
        rumps.Timer(self._auto_update_tick, 3600).start()

    def ui_call(self, fn):
        """Queue a callable to run on the main thread. Safe from any thread."""
        self._main_thread_calls.put(fn)

    def _auto_update_tick(self, _):
        """Kick the daily update check off the main thread."""
        threading.Thread(target=self.updater.maybe_auto_check, daemon=True).start()

    def _check_install_request(self):
        """Install an update when the dashboard's Updates tab requests one."""
        flag = self.core.runtime_path("install_update.flag")
        if not os.path.exists(flag):
            return
        try:
            os.remove(flag)
        except OSError:
            pass
        self.core.log("UPDATER", "Install requested from dashboard")
        threading.Thread(target=self.updater.install_latest, daemon=True).start()

    def build_machine_list(self):
        """Rebuild the Quick Connect submenu from the current machine settings."""
        self.menu_lookup = {}
        
        if hasattr(self.machine_menu, '_menu') and self.machine_menu._menu is not None:
            self.machine_menu.clear()
            
        machines = self.core.settings.get("machines", [])
        
        red_dot_path = self._get_status_icon("#e74c3c")
        
        for index, m in enumerate(machines):
            m_name = m.get('name', f'Machine {index}').strip()
            m_addr = m.get('address', '').strip()
            icon = m.get('icon', '💻')

            if not m_addr: continue

            m_sub = rumps.MenuItem(f"{icon} {m_name}")
            m_sub.icon = red_dot_path
            m_sub.template = False
            
            enabled_conns = m.get("connections", [])
            
            if "SSH" in enabled_conns:
                m_sub.add(rumps.MenuItem("SSH", callback=lambda _, mach=m: self.actions.open_terminal(mach)))
            if "TMUX" in enabled_conns:
                m_sub.add(rumps.MenuItem("TMUX", callback=lambda _, mach=m: self.actions.open_tmux(mach)))
            if "SFTP" in enabled_conns:
                m_sub.add(rumps.MenuItem("SFTP", callback=lambda _, mach=m: self.actions.open_sftp(mach)))
            if "VNC" in enabled_conns:
                m_sub.add(rumps.MenuItem("VNC", callback=lambda _, mach=m: self.actions.open_vnc(mach)))
            
            self.machine_menu.add(m_sub)

            self.menu_lookup[index] = {
                "item": m_sub,
                "name": m_name,
                "address": m_addr,
                "port": m.get("port") or 22,
            }

    def rebuild_menu(self):
        """Clear and rebuild the entire menu bar structure."""
        self.menu.clear()

        self.menu.add(rumps.MenuItem("About SwitchBored", callback=self.show_about))
        self.menu.add(rumps.separator)
        self.machine_menu = rumps.MenuItem("Quick Connect")
        
        self.build_machine_list()
        
        self.menu.add(self.machine_menu)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Open Dashboard", callback=self.open_dash))
        self.menu.add(rumps.separator)
        self.plugin_hook("on_menu_build", self)
        self.core.attach_plugin_menus(self)

        links = self.core.settings.get("custom_links", [])
        if links:
            for link in links:
                self.add_shortcut_item(link)
            self.menu.add(rumps.separator)

        self.menu.add(rumps.MenuItem("Quit", callback=self.custom_quit))

    def add_shortcut_item(self, link):
        """Add a menu item for a custom shortcut (URL, app, or shell command)."""
        name = link.get("name", "")
        # Older settings stored the target under 'url' with no type.
        value = link.get("value") or link.get("url", "")
        kind = link.get("type", "url")

        if not name or not value:
            return

        self.menu.add(rumps.MenuItem(
            name,
            callback=lambda _, k=kind, v=value: self._launch_shortcut(k, v),
        ))

    def _launch_shortcut(self, kind, value):
        """Run a custom shortcut: open a URL, an app instance, or a command."""
        self.core.log("SHORTCUT", f"Launching {kind}: {value}")
        try:
            if kind == "app":
                # -n opens a new instance, so e.g. iTerm or VS Code get a
                # fresh window rather than just focusing an existing one.
                res = subprocess.run(["open", "-na", value], capture_output=True, text=True)
                if res.returncode != 0:
                    rumps.alert(
                        title="App Not Found",
                        message=(
                            f'macOS could not open an application called "{value}".\n'
                            'Use the app\'s exact name, e.g. "Visual Studio Code".'
                        ),
                        ok="OK",
                    )
            elif kind == "command":
                subprocess.Popen(value, shell=True, start_new_session=True)
            else:
                webbrowser.open(value)
        except Exception as e:
            self.core.log("SHORTCUT", f"Launch failed: {type(e).__name__}: {e}")


    def _flush_debug_log_backlog(self):
        """Send any queued debug messages to the dashboard debug socket, if available."""
        if not self._pending_debug_logs:
            return

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.2)
            sock.connect(self._debug_socket_path)

            while self._pending_debug_logs:
                msg = self._pending_debug_logs.popleft()
                sock.sendall(msg.encode("utf-8", errors="replace"))

            sock.close()

        except Exception:
            pass

    def _apply_pending_status(self):
        """Apply machine status results computed by worker threads (main thread only)."""
        with self._status_lock:
            pending, self._pending_status = self._pending_status, {}

        if not pending or not hasattr(self, "menu_lookup"):
            return

        machines = self.core.settings.get("machines", [])
        now = time.time()

        for idx, is_online in pending.items():
            entry = self.menu_lookup.get(idx)
            if not entry:
                continue

            entry["item"].icon = self.menu_green_dot if is_online else self.menu_red_dot

            if 0 <= idx < len(machines):
                machine = machines[idx]
                icon = machine.get("icon", "💻")
                entry["item"].title = f"{icon} {entry['name']}"

                if self._should_notify_offline(machine, entry["address"], is_online, now):
                    self._notify_machine_offline(machine)

    def _should_notify_offline(self, machine, addr, is_online, now):
        """Track outage state; return True exactly when a notification is due.

        Fires once per outage, only for machines with notify_offline enabled,
        only after OFFLINE_NOTIFY_SECONDS of continuous unreachability, and
        only while the reference server is reachable (so a dead machine is
        distinguishable from a dead network). Coming back online re-arms it.
        """
        if is_online:
            self._offline_since.pop(addr, None)
            self._offline_notified.discard(addr)
            return False

        if not machine.get("notify_offline") or not self.reference_status:
            return False

        first_seen = self._offline_since.setdefault(addr, now)
        if now - first_seen >= OFFLINE_NOTIFY_SECONDS and addr not in self._offline_notified:
            self._offline_notified.add(addr)
            return True

        return False

    def _notify_machine_offline(self, machine):
        """Post a macOS notification for an unreachable machine.

        rumps notifications need an app bundle (Info.plist); when running
        from source, fall back to an osascript notification instead.
        """
        name = machine.get("name") or machine.get("address", "Machine")
        addr = machine.get("address", "")
        subtitle = f"{name} is unreachable"
        message = f"{addr} has not responded for {OFFLINE_NOTIFY_SECONDS} seconds."

        try:
            rumps.notification(title=APP_NAME, subtitle=subtitle, message=message)
            self.core.log("NOTIFY", f"Offline notification sent for {name} ({addr})")
            return
        except Exception as e:
            self.core.log("NOTIFY", f"rumps notification failed ({type(e).__name__}: {e}); trying osascript")

        try:
            import subprocess

            def esc(s):
                return s.replace("\\", "\\\\").replace('"', '\\"')

            script = (
                f'display notification "{esc(message)}" '
                f'with title "{esc(APP_NAME)}" subtitle "{esc(subtitle)}"'
            )
            subprocess.Popen(["osascript", "-e", script])
            self.core.log("NOTIFY", f"Offline notification sent via osascript for {name} ({addr})")
        except Exception as e:
            self.core.log("NOTIFY", f"Notification unavailable ({type(e).__name__}: {e})")

    def process_ui_updates(self, timer):
        """Refresh settings-driven UI state and rebuild menu elements when needed."""
        self._flush_debug_log_backlog()

        # Reap the dashboard child promptly if it has exited, so no zombie
        # entry lingers in Activity Monitor until the next interaction.
        proc = self.dashboard_runtime.dashboard_proc
        if proc is not None:
            proc.poll()

        self.core.settings = self.core.load_settings(silent=True)

        self._apply_pending_status()
        self._check_install_request()

        while True:
            try:
                fn = self._main_thread_calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                pass

        flag_path = self.reload_flag_file
        if os.path.exists(flag_path):
            try:
                os.remove(flag_path)
            except OSError:
                pass

            self.rebuild_menu()

            self._last_links = self.core.settings.get("custom_links", [])
            self._last_emoji = self.core.settings.get("global_emoji", "💻")
            self._last_ref_toggle = self.core.settings.get("use_ref_server", True)

            new_title = self._last_emoji
            if self._current_title != new_title:
                self.title = new_title
                self._current_title = new_title

            return

        curr_links = self.core.settings.get("custom_links", [])
        curr_emoji = self.core.settings.get("global_emoji", "💻")
        curr_ref_toggle = self.core.settings.get("use_ref_server", True)

        if curr_links != self._last_links:
            self._last_links = curr_links
            self.rebuild_menu()

        if curr_ref_toggle != self._last_ref_toggle:
            self._last_ref_toggle = curr_ref_toggle
            if not curr_ref_toggle:
                self.reference_status = None

        if curr_emoji != self._last_emoji:
            self._last_emoji = curr_emoji

        new_title = curr_emoji
        new_icon = None

        if curr_ref_toggle and self.reference_status is not None:
            new_icon = self.green_dot if self.reference_status else self.red_dot

        if self._current_title != new_title:
            self.title = new_title
            self._current_title = new_title

        if getattr(self, "icon", None) != new_icon:
            self.icon = new_icon

    def background_worker(self, _):
        """Run periodic reference-server and machine-status checks without overlapping runs."""
        if not self.worker_lock.acquire(blocking=False):
            return
    
        def perform_checks():
            """Perform one full batch of background status checks."""
            try:
                threads = []

                if self.core.settings.get("use_ref_server", True):
                    target = self.core.settings.get("ref_server", "1.1.1.1")
                    def check_ref():
                        lat = self.core.check_status(target)
                        self.reference_status = (lat is not None)
                    
                    t_ref = threading.Thread(target=check_ref, daemon=True)
                    t_ref.start()
                    threads.append(t_ref)
                    
                if hasattr(self, 'menu_lookup'):
                    for index, data in list(self.menu_lookup.items()):
                        def check_machine(idx=index, addr=data["address"], port=data.get("port")):
                            is_online = self.core.check_status(addr, port=port) is not None
                            # Never touch the menu from a worker thread; queue
                            # the result for the main-thread UI timer.
                            with self._status_lock:
                                self._pending_status[idx] = is_online

                        t_mac = threading.Thread(target=check_machine, daemon=True)
                        t_mac.start()
                        threads.append(t_mac)

                # Wait briefly for all checks in this batch to complete.
                for t in threads:
                    t.join(timeout=4.0)

            finally:
                self.worker_lock.release()

        # Run the entire batch in a manager thread so the rumps timer itself stays responsive.
        threading.Thread(target=perform_checks, daemon=True).start()

    def _generate_dot(self, color_name, hex_color):
        """Create a retina-quality dot icon for the menu bar title area."""
        img = Image.new('RGBA', (36, 36), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        draw.ellipse((18, 10, 34, 26), fill=hex_color)
        
        path = os.path.join(tempfile.gettempdir(), f"dot_{color_name}.png")
        img.save(path)
        return path

    def _get_status_icon(self, color_hex):
        """Create a retina-quality status dot icon for machine submenu items."""
        img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((8, 8, 24, 24), fill=color_hex)
        
        path = os.path.join(tempfile.gettempdir(), f"menu_dot_{color_hex.replace('#','')}.png")
        img.save(path)
        return path

    def custom_quit(self, _):
        """Stop plugins, shut down the dashboard gracefully, then quit the menu-bar app."""
        for p in self.plugins:
            if hasattr(p, "stop"):
                try:
                    p.stop()
                except Exception:
                    pass

        pid_file = self.dashboard_runtime.dashboard_pid_file
        proc = self.dashboard_runtime.dashboard_proc
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

        # Also clean up any surviving dashboard process recorded by PID file.
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())

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
            finally:
                try:
                    os.remove(pid_file)
                except Exception:
                    pass

        rumps.quit_application()

    def show_about(self, _):
        """Open the About window in a separate process."""
        spawn_self("--about")

    def plugin_hook(self, hook_name: str, *args, **kwargs):
        """Call a named hook on each loaded plugin if that hook is implemented."""
        for plugin in getattr(self, "plugins", []):
            fn = getattr(plugin, hook_name, None)
            if callable(fn):
                plugin_name = plugin.__class__.__module__.split(".")[-1]
                try:
                    self.core.log("PLUGINS", f"{plugin_name}.{hook_name} running")
                    fn(*args, **kwargs)
                    self.core.log("PLUGINS", f"{plugin_name}.{hook_name} completed")
                except Exception as e:
                    self.core.log(
                        "PLUGINS",
                        f"{plugin_name}.{hook_name} error: {type(e).__name__}: {e}"
                    )

    def show_error_dialog(self, title, message, download_url):
        """Show a 'helper app missing' alert with an optional download action."""
        clicked = rumps.alert(
            title=title,
            message=f"{message}\n\nOpen the download page?",
            ok="Download",
            cancel="Close",
        )
        if clicked == 1:
            webbrowser.open(download_url)

    def open_dash(self, _):
        """Open the dashboard if needed, or focus the existing dashboard window."""
        self.dashboard_runtime.open_or_focus()

def main():
    """Route to the requested window, defaulting to the menu-bar console.

    A py2app bundle has a single entry point, so the dashboard and About
    windows are launched by re-invoking the app with these flags.
    """
    if "--dashboard" in sys.argv:
        from dashboard.app import main as dashboard_main
        dashboard_main()
        return

    if "--about" in sys.argv:
        from console.about_window import show_about_window
        show_about_window(APP_NAME, VERSION)
        return

    apply_macos_bundle_name(APP_NAME)

    pid_file = acquire_console_lock()

    try:
        is_test_mode = "--test" in sys.argv
        app = SysAdminConsole(test_mode=is_test_mode)
        app.run()
    finally:
        release_console_lock(pid_file)