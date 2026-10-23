import os
import sys
import time
import queue
import signal
import threading
import tkinter
from concurrent.futures import ThreadPoolExecutor
import customtkinter as ctk
from core import MachineManagerCore, StatusTracker
from actions import ActionManager
from console.bootstrap import spawn_self
from console.dashboard_runtime import SHOW_SIGNAL
from console.process_guard import read_pid_file, is_switchbored_process
from version import APP_NAME
from ui_components import ToolTip, enable_trackpad_scrolling
from .debug_console import DebugConsoleMixin
from .machine_details import MachineDetailsWindow
from .services_window import ServicesWindow
from .settings_window import SettingsWindow
from .dialogs import show_error_dialog
from .screens import (
    visible_screen_rects,
    fit_to_screens,
    ensure_window_on_screen,
    activate_app,
    bring_windows_to_current_desktop,
    hold_on_current_desktop,
)
from .ui_helpers import (
    extract_ordered_tags,
    get_machine_tags,
    calculate_name_max_chars,
    calculate_option_menu_width,
    CONNECTION_COLORS,
    DETAILS_BUTTON_STYLE,
    DISABLED_CONNECTION_BUTTON_STYLE,
    DASHBOARD_LAYOUT,
)

# How often to check whether the display layout changed (monitor plugged or
# unplugged), in ms. Reading NSScreen is cheap.
DISPLAY_POLL_MS = 2000

# Machines are checked in parallel, so one sweep takes about one timeout
# rather than one timeout per unreachable machine.
STATUS_WORKERS = 16

# The menu-bar app publishes its status results every ~5s (longer when
# checks are slow). Older than this, it is assumed not to be running and the
# dashboard checks machines itself, every SWEEP_INTERVAL seconds.
SNAPSHOT_MAX_AGE = 15
SWEEP_INTERVAL = 5


def _visible_toplevels(widget):
    """Yield every visible, decorated toplevel below widget (not tooltips)."""
    for child in widget.winfo_children():
        if isinstance(child, tkinter.Toplevel):
            try:
                if child.winfo_viewable() and not child.wm_overrideredirect():
                    yield child
            except tkinter.TclError:
                continue
        yield from _visible_toplevels(child)


def _load_services_icon(size=20):
    """Return the services toolbar icon as a CTkImage, or None for the fallback glyph.

    In the bundled app the asset is copied into Contents/Resources; from
    source it lives in dashboard/assets/.
    """
    candidates = []
    resources = os.environ.get("RESOURCEPATH")
    if resources:
        candidates.append(os.path.join(resources, "services_icon.png"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "services_icon.png"))

    for path in candidates:
        if os.path.exists(path):
            try:
                from PIL import Image
                img = Image.open(path).convert("RGBA")
                return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
            except Exception:
                return None
    return None


class RemoteManagerDash(DebugConsoleMixin, ctk.CTk):
    """Main dashboard window for machine status, filtering, and quick actions."""

    # --- INITIALIZATION ---
    def __init__(self, core):
        """Initialize the dashboard window, layout, and background monitor."""
        super().__init__()
        
        self.core = core
        self.core.master = self
        self.actions = ActionManager(self.core)
        self.settings = self.core.settings
        self.machines = self.settings.get("machines", [])
        self.stop_event = threading.Event()
        self._ui_queue = queue.Queue()
        self._revealed = False
        self._screen_rects = []
        # Smooths the dashboard's own checks (used when the menu bar's
        # shared results are unavailable), as the menu bar does for its own.
        self._status_tracker = StatusTracker()

        # Plugins that extend dashboard windows (e.g. usb_block) implement
        # on_machine_details / on_machine_editor and are dispatched via
        # plugin_hook. The console loads its own copy for menu-bar plugins.
        self.plugins = self.core.load_plugins()

        self.withdraw()
        self.attributes("-alpha", 0)
        self.title("Dashboard")
        self._setup_app_menu()
        # bind_all covers every toplevel in this process (settings, editor,
        # details, viewers), so one call here is enough.
        enable_trackpad_scrolling(self)
        
        self.geometry(
            f"{DASHBOARD_LAYOUT['default_width']}x{DASHBOARD_LAYOUT['initial_height']}+"
            f"{DASHBOARD_LAYOUT['initial_x']}+{DASHBOARD_LAYOUT['initial_y']}"
        )
        self.minsize(
            DASHBOARD_LAYOUT["default_width"],
            DASHBOARD_LAYOUT["min_height"],
        )
        ctk.set_appearance_mode("dark")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1) 

        self.setup_header()
        self.setup_status_bar()
        self.setup_machine_list()
        
        self.setup_debug_console()
        self.toggle_status_bar()
        self.toggle_debug_console()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Must happen before the PID file is written: the console only
        # signals a dashboard whose PID file exists.
        self._install_lifecycle_handlers()

        try:
            pid_path = self.core.runtime_path("dashboard.pid")
            with open(pid_path, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        self.bind("<Configure>", self.on_resize)
        threading.Thread(target=self.monitor, daemon=True).start()

        self.after(150, self._drain_ui_queue)
        self.after(100, self.launch_snap)

    def _setup_app_menu(self):
        """Replace Tk's default macOS application menu.

        Without this, the top-left app menu's About item shows the standard
        Cocoa panel; this points it at the same About window the menu-bar
        icon uses.
        """
        if sys.platform != "darwin":
            return
        try:
            menubar = tkinter.Menu(self)
            app_menu = tkinter.Menu(menubar, name="apple", tearoff=0)
            app_menu.add_command(
                label=f"About {APP_NAME}",
                command=lambda: spawn_self("--about"),
            )
            menubar.add_cascade(menu=app_menu)
            self.config(menu=menubar)
        except Exception:
            pass

    def _install_lifecycle_handlers(self):
        """Route quit requests and the console's 'show' signal through Tk.

        Python runs signal handlers between bytecodes on the main thread;
        the queue-drain 'after' loop guarantees that happens promptly.
        """
        # Cmd+Q, the app menu's Quit and logout send a quit Apple Event.
        # Tk's default reaction is to exit at once, skipping on_closing, so
        # the window position was never saved and the PID file went stale.
        if sys.platform == "darwin":
            try:
                self.createcommand("::tk::mac::Quit", self.on_closing)
            except Exception:
                pass

        # The console asks us to quit with SIGTERM; close cleanly so window
        # geometry is saved and the PID file is removed.
        signal.signal(signal.SIGTERM, lambda *_: self.after(0, self.on_closing))
        # "Open Dashboard" while we're already running.
        signal.signal(SHOW_SIGNAL, lambda *_: self.after(0, self.bring_to_front))

    def plugin_hook(self, hook_name, *args, **kwargs):
        """Call a named hook on each loaded plugin that implements it.

        Child windows reach this via find_ui_root(widget).plugin_hook(...).
        """
        for plugin in getattr(self, "plugins", []):
            fn = getattr(plugin, hook_name, None)
            if callable(fn):
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    name = plugin.__class__.__module__.split(".")[-1]
                    self.core.log("PLUGINS", f"{name}.{hook_name} error: {type(e).__name__}: {e}")

    # --- THREAD-SAFE UI SCHEDULING ---
    def ui_call(self, fn):
        """Queue a callable to run on the Tk main thread. Safe from any thread."""
        self._ui_queue.put(fn)

    def _drain_ui_queue(self):
        """Run queued UI updates on the main thread, then reschedule."""
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                pass

        if not self.stop_event.is_set():
            try:
                self.after(150, self._drain_ui_queue)
            except Exception:
                pass

    def launch_snap(self):
        """Apply final startup geometry, filtering, and reveal the window."""
        self.update_idletasks()

        saved_w = self.settings.get("last_w", DASHBOARD_LAYOUT["default_width"])
        final_h = self._get_initial_window_height()
        pos_x, pos_y = self.get_last_position()

        initial_choice = (
            self.filter_var.get()
            if self.settings.get("remember_filter", False)
            else "All"
        )
        self.filter_machines(initial_choice)

        # The saved position may be on a monitor that is no longer attached.
        self._screen_rects = visible_screen_rects(self)
        x, y, w, h = fit_to_screens((pos_x, pos_y, saved_w, final_h), self._screen_rects)

        self.geometry(f"{w}x{h}+{x}+{y}")
        self.deiconify()
        self.attributes("-alpha", 1)
        self._revealed = True
        self.truncate_names()
        self._raise_and_activate()

        self.after(DISPLAY_POLL_MS, self._watch_displays)

    def _raise_and_activate(self):
        """Raise the window above other apps' windows and give it focus.

        The window comes to the user's current desktop rather than taking
        the user to the desktop it was on.
        """
        bring_windows_to_current_desktop()
        self.lift()
        try:
            self.focus_force()
        except tkinter.TclError:
            pass
        activate_app()
        hold_on_current_desktop(self)

    def bring_to_front(self):
        """Show the window where the user can see it ('Open Dashboard')."""
        if getattr(self, "_closing", False) or not self._revealed:
            return  # still starting: launch_snap reveals and activates it
        try:
            if self.state() == "iconic":
                self.deiconify()
            if ensure_window_on_screen(self):
                self.core.log("UI", "Dashboard was out of reach; moved it back on screen")
            self._raise_and_activate()
        except tkinter.TclError:
            pass

    def _watch_displays(self):
        """Watch for display changes, such as a monitor being unplugged."""
        if self.stop_event.is_set():
            return
        try:
            rects = visible_screen_rects(self)
            if rects and rects != self._screen_rects:
                self._screen_rects = rects
                self.core.log("UI", f"Display layout changed: {rects}")
                # Give macOS a moment to finish rearranging windows first.
                self.after(500, self._recover_windows)
        except Exception:
            pass
        self.after(DISPLAY_POLL_MS, self._watch_displays)

    def _recover_windows(self):
        """Move this window and its open child windows back within reach."""
        if self.stop_event.is_set():
            return
        windows = [self] if self.state() != "iconic" else []
        windows.extend(_visible_toplevels(self))
        for win in windows:
            try:
                if ensure_window_on_screen(win):
                    self.core.log("UI", f"Moved '{win.title()}' back on screen after a display change")
            except tkinter.TclError:
                continue

    def _get_display_count_for_current_filter(self):
        """Return the number of machines visible under the current startup filter."""
        current_filter = (
            self.filter_var.get()
            if self.settings.get("remember_filter", False)
            else "All"
        )

        if current_filter == "All":
            return len(self.machines)

        return sum(
            1
            for machine in self.machines
            if current_filter in get_machine_tags(machine)
        )

    def _get_initial_window_height(self):
        """Calculate the initial dashboard height based on saved or filtered content."""
        saved_h = self.settings.get("last_h", None)

        if self.settings.get("remember_filter", False) and saved_h is not None:
            return saved_h

        display_count = self._get_display_count_for_current_filter()
        calc_h = (
            DASHBOARD_LAYOUT["base_chrome_height"]
            + display_count * DASHBOARD_LAYOUT["row_height"]
        )
        max_h = int(self.winfo_screenheight() * DASHBOARD_LAYOUT["max_screen_fraction"])

        return min(calc_h, max_h)

    # --- UI SETUP ---
    def setup_header(self):
        """Create the header row with the group filter, title, and settings button."""
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=20)
        self.header_frame.grid_columnconfigure(1, weight=1)

        ordered_tags = extract_ordered_tags(self.machines)
        filter_values = ["All"] + ordered_tags
        filter_width = calculate_option_menu_width(filter_values)

        starting_filter = "All"
        if self.settings.get("remember_filter", False):
            saved_val = self.settings.get("last_filter", "All")
            if saved_val in filter_values:
                starting_filter = saved_val

        self.filter_var = ctk.StringVar(value=starting_filter)

        self.filter_menu = ctk.CTkOptionMenu(
            self.header_frame,
            values=filter_values,
            variable=self.filter_var,
            command=self.filter_machines,
            width=filter_width,
            height=20,
            font=("", 12, "italic"),
            dynamic_resizing=False,
        )
        self.filter_menu.grid(row=0, column=0, pady=(10, 0), sticky="w")

        ctk.CTkLabel(
            self.header_frame,
            text="SwitchBored",
            font=("", 18, "bold")
        ).place(relx=0.5, rely=0.5, anchor="center")

        self.header_btns = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.header_btns.grid(row=0, column=2, pady=(12, 5), sticky="e")

        self.settings_btn = ctk.CTkLabel(
            self.header_btns,
            text="⚙",
            font=("", 28),
            cursor="pointinghand"
        )
        # The glyph's font metrics make it sit visually low; a little bottom
        # padding lifts it so it centres with the services image icon.
        self.settings_btn.pack(side="right", pady=(0, 5))
        self.settings_btn.bind("<Button-1>", lambda e: self.open_settings())
        ToolTip(self.settings_btn, "Settings")

        # Services grid button; shown only with Sysadmin Features enabled.
        services_icon = _load_services_icon()
        if services_icon is not None:
            self.services_btn = ctk.CTkLabel(
                self.header_btns,
                image=services_icon,
                text="",
                cursor="pointinghand"
            )
            self.services_btn._services_icon = services_icon  # keep a reference
        else:
            self.services_btn = ctk.CTkLabel(
                self.header_btns,
                text="▦",
                font=("", 26),
                cursor="pointinghand"
            )
        self.services_btn.bind("<Button-1>", lambda e: self.open_services())
        ToolTip(self.services_btn, "Services")
        self._update_services_button()

    def setup_status_bar(self):
        """Create the bottom status bar used for reference-server connectivity updates."""
        self.status_frame = ctk.CTkFrame(self, height=30, fg_color="#353535", corner_radius=8)
        self.status_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 5))
        self.heartbeat_lbl = ctk.CTkLabel(self.status_frame, text="● Connectivity Heartbeat: ...", font=("", 12))
        self.heartbeat_lbl.pack(side="left", padx=20)

    def setup_machine_list(self):
        """Create the scrollable machine list and populate its initial rows."""
        self.list_container = ctk.CTkScrollableFrame(self, corner_radius=10, fg_color="#1a1a1a", border_width=1, border_color="#2b2b2b")
        self.list_container.grid(row=1, column=0, padx=20, pady=(0, 4), sticky="nsew")
        
        self.machine_rows = []
        for m in self.machines:
            row_data = self.create_machine_row(self.list_container, m)
            self.machine_rows.append(row_data)

    # --- MACHINE ROWS / DISPLAY ---
    def create_machine_row(self, parent, machine):
        """Create one dashboard row for a machine and return its tracked widgets."""
        row_frame = ctk.CTkFrame(parent)
        row_frame.pack(fill="x", padx=10, pady=5)
        row_frame.grid_columnconfigure(0, weight=1)

        name_text = f"{machine['icon']} {machine['name']}"
        name_lbl = ctk.CTkLabel(row_frame, text=name_text, anchor="w")
        name_lbl.grid(row=0, column=0, padx=10, sticky="ew")
        name_lbl.full_text = name_text
        
        name_tooltip = ToolTip(name_lbl, "")
        
        status_dot = ctk.CTkLabel(row_frame, text="●", text_color="gray", font=("", 14))
        status_dot.grid(row=0, column=1, padx=5)
        
        dot_tip = ToolTip(status_dot, "Checking status...")

        actions_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        actions_frame.grid(row=0, column=2, padx=10)

        det_btn = ctk.CTkButton(
            actions_frame,
            text="Details",
            width=60,
            height=24,
            fg_color=DETAILS_BUTTON_STYLE["fg"],
            hover_color=DETAILS_BUTTON_STYLE["hover"],
            command=lambda m=machine: self.show_det(m)
        )
        det_btn.pack(side="left", padx=(2, 15))

        enabled_conns = machine.get("connections", [])

        for conn in ["SSH", "TMUX", "SFTP", "VNC"]:
            if conn in enabled_conns:

                btn = ctk.CTkButton(
                    actions_frame,
                    text=conn,
                    width=60,
                    height=24,
                    fg_color=CONNECTION_COLORS[conn]["fg"],
                    hover_color=CONNECTION_COLORS[conn]["hover"],
                    command=lambda c=conn, m=machine: self.handle_action(c, m)
                )
            else:
                btn = ctk.CTkButton(
                    actions_frame,
                    text=conn,
                    width=60,
                    height=24,
                    state="disabled",
                    fg_color=DISABLED_CONNECTION_BUTTON_STYLE["fg"],
                    text_color=DISABLED_CONNECTION_BUTTON_STYLE["text"],
                )

            btn.pack(side="left", padx=2)
 
        return {
            "frame": row_frame,
            "dot": status_dot, 
            "dot_tip": dot_tip,
            "name_lbl": name_lbl, 
            "name_tooltip": name_tooltip,
            "data": machine
        }
    
    def truncate_names(self, event=None):
        """Truncate machine names to fit the current list width and update tooltips."""
        if not hasattr(self, 'machine_rows') or not self.machine_rows: 
            return
        
        container_width = self.list_container.winfo_width()
        max_chars = calculate_name_max_chars(container_width)
        if max_chars is None:
            return

        for row in self.machine_rows:
            lbl = row['name_lbl']
            tip = row['name_tooltip']
            full = lbl.full_text
            
            if len(full) > max_chars:
                truncated = full[:max_chars-3] + "..."
                lbl.configure(text=truncated)
                tip.text = full 
            else:
                lbl.configure(text=full)
                tip.text = ""

    def refresh_machines(self):
        """Reload machine settings, rebuild the list, and refresh filter controls."""
        for widget in self.list_container.winfo_children():
            widget.destroy()
            
        self.settings = self.core.load_settings()
        self.core.settings = self.settings
        self.machines = self.settings.get("machines", [])
                
        self.machine_rows = []
        for m in self.machines:
            row_data = self.create_machine_row(self.list_container, m)
            self.machine_rows.append(row_data)

        ordered_tags = extract_ordered_tags(self.machines)
        
        filter_values = ["All"] + ordered_tags
        filter_width = calculate_option_menu_width(filter_values)
        self.filter_menu.configure(values=filter_values, width=filter_width)
                
        current_filter = self.filter_var.get()
        if current_filter not in filter_values:
            self.filter_var.set("All")

        self.filter_machines(self.filter_var.get())

        self.toggle_status_bar()
        self.toggle_debug_console()
        self._update_services_button()

        self.update_idletasks()
        self.after(10, self.truncate_names)

    def filter_machines(self, choice):
        """Show only rows matching the selected filter and persist the choice if enabled."""
        self.core.log("UI", f"Filtering machine list by: '{choice}'")
        if self.settings.get("remember_filter", False):
            self.settings["last_filter"] = choice
            self.core.save_settings(self.settings)

        for row in self.machine_rows:
            row['frame'].pack_forget()

        for row in self.machine_rows:
            machine_tags = get_machine_tags(row["data"])
            
            if choice == "All" or choice in machine_tags:
                row['frame'].pack(fill="x", padx=10, pady=5)

        self.update_idletasks()               
        self.after_idle(self.truncate_names)

    def update_row_status(self, row, status):
        """Update a machine row's status indicator and tooltip safely.

        status is a StatusTracker state dict (online, latency, last_seen,
        missed); a bare latency or None is accepted too.
        """
        dot = row.get('dot')
        tooltip = row.get('dot_tip')

        if not dot or not dot.winfo_exists():
            return

        if not isinstance(status, dict):
            status = {"online": status is not None, "latency": status, "last_seen": None, "missed": 0}

        if status.get("online"):
            dot.configure(text="●", text_color="#2fa572") # Green
            if tooltip:
                latency = status.get("latency")
                tooltip.text = f"{latency * 1000:.1f} ms" if latency is not None else "Online"
                if status.get("missed") and status.get("last_seen"):
                    # Still shown online: a busy machine missed a check or two.
                    ago = max(0, int(time.time() - status["last_seen"]))
                    tooltip.text = f"No reply for {ago}s (last reply {tooltip.text})"
        else:
            dot.configure(text="●", text_color="#e74c3c") # Red
            if tooltip:
                tooltip.text = "Offline"

    # --- WINDOW ACTIONS ---
    def show_error_dialog(self, title, message, download_url):
        """Show the shared 'helper app missing' dialog over this window."""
        show_error_dialog(self, title, message, download_url)

    def show_det(self, m):
        """Open the details window for a machine, replacing any existing details window."""
        if hasattr(self, "det_win") and self.det_win.winfo_exists():
            self.det_win.destroy()
        self.det_win = MachineDetailsWindow(self, m, self.core)

    def open_settings(self):
        """Open the settings window, or focus it if it is already open."""
        self.core.log("UI", "Opening Settings Window")
        if hasattr(self, "set_win") and self.set_win.winfo_exists():
            self.set_win.focus()
        else:
            self.set_win = SettingsWindow(self, self.core)

    def open_services(self):
        """Open the services grid, replacing any existing services window."""
        self.core.log("UI", "Opening Services Window")
        if hasattr(self, "svc_win") and self.svc_win.winfo_exists():
            self.svc_win.destroy()
        self.svc_win = ServicesWindow(self, self.core)

    def _update_services_button(self):
        """Show or hide the services button based on the sysadmin setting."""
        enabled = self.settings.get("sysadmin_features", False)
        if enabled and not self.services_btn.winfo_ismapped():
            # side="right" packs later widgets further left, so packing after
            # the cog places the services button to its left.
            self.services_btn.pack(side="right", padx=(0, 14), after=self.settings_btn)
        elif not enabled and self.services_btn.winfo_ismapped():
            self.services_btn.pack_forget()

    def handle_action(self, action_type, machine):
        """Dispatch a row action to the appropriate ActionManager method."""
        if action_type == "SSH": self.actions.open_terminal(machine)
        elif action_type == "TMUX": self.actions.open_tmux(machine)
        elif action_type == "SFTP": self.actions.open_sftp(machine)
        elif action_type == "VNC": self.actions.open_vnc(machine)

    # --- WINDOW GEOMETRY / STATE ---
    def toggle_status_bar(self):
        """Show or hide the status bar based on the reference-server setting."""
        if self.settings.get("use_ref_server", True):
            self.status_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 5))
        else:
            self.status_frame.grid_remove()

    def on_resize(self, event):
        """Handle resize events, update truncation, and persist window state with debounce."""
        if event.widget == self:
            if event.width > 100 and event.height > 100:
                self.truncate_names()

                # The event carries the new position; winfo_x()/winfo_y()
                # still report the previous one while the handler runs.
                self.settings["last_x"] = event.x
                self.settings["last_y"] = event.y
                
                if self.settings.get("remember_filter", False):
                    self.settings["last_w"] = event.width
                    self.settings["last_h"] = event.height
                    
                if hasattr(self, '_save_timer') and self._save_timer is not None:
                    self.after_cancel(self._save_timer)
                
                self._save_timer = self.after(500, self._commit_window_size)

    def _commit_window_size(self):
        """Persist the current window geometry after resize activity has settled."""
        self.core.save_settings(self.settings, silent=True)

        w = self.settings.get("last_w", self.winfo_width())
        h = self.settings.get("last_h", self.winfo_height())
        self.core.log("UI", f"Window repositioned/resized. Layout saved ({w}x{h}).")

    def get_last_position(self):
        """Return a validated window position, falling back to defaults if it is implausible."""
        default_x, default_y = (
            DASHBOARD_LAYOUT["default_x"],
            DASHBOARD_LAYOUT["default_y"],
        )
                
        last_x = self.settings.get("last_x", default_x)
        last_y = self.settings.get("last_y", default_y)

        if abs(last_x) > 10000 or abs(last_y) > 10000:
            return default_x, default_y
            
        return last_x, last_y

    # --- BACKGROUND MONITORING ---
    def monitor(self):
        """Keep the status dots and connectivity heartbeat current.

        The menu-bar app already checks every machine every few seconds, and
        while the dashboard is open it publishes those results; showing them
        avoids pinging every machine twice. Only when they are missing or
        stale (e.g. the dashboard was started on its own) does this check
        machines itself.

        This runs on a worker thread and must never touch Tk directly; all UI
        updates are posted through ui_call() and applied on the main thread.
        """
        last_snapshot = None
        last_sweep = 0.0

        while not self.stop_event.is_set():
            self.settings = self.core.settings

            snapshot = self.core.load_status_snapshot(max_age=SNAPSHOT_MAX_AGE)
            if snapshot is not None:
                if snapshot["updated"] != last_snapshot:
                    last_snapshot = snapshot["updated"]
                    self._apply_status_snapshot(snapshot)
            elif time.monotonic() - last_sweep >= SWEEP_INTERVAL:
                self._update_reference_status()
                self._ping_rows(self._checkable_rows())
                last_sweep = time.monotonic()

            # Look for a fresh snapshot about once a second.
            for _ in range(10):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)

    def _apply_status_snapshot(self, snapshot):
        """Show the menu-bar app's results; check anything they don't cover."""
        if not self.settings.get("use_ref_server", True):
            self._set_reference_status_label("")
        else:
            reference = snapshot.get("reference") or {}
            if reference.get("target") == self.settings.get("ref_server", "1.1.1.1"):
                online = reference.get("online", reference.get("latency") is not None)
                self._show_reference_online(online)
            else:
                self._update_reference_status()

        known = snapshot["machines"]
        missing = []
        for row in self._checkable_rows():
            data = row["data"]
            key = self.core.status_key(data["address"], data.get("port") or 22)
            if key in known:
                self.ui_call(lambda r=row, l=known[key]: self.update_row_status(r, l))
            else:
                # e.g. a machine added since the menu bar last rebuilt.
                missing.append(row)

        if missing:
            self._ping_rows(missing)

    def _update_reference_status(self):
        """Check the reference server and update the status bar."""
        if not self.settings.get("use_ref_server", True):
            self._set_reference_status_label("")
            return

        target = self.settings.get("ref_server", "1.1.1.1")
        state = self._status_tracker.update(("reference", target), self.core.check_reference(target))
        self._show_reference_online(state["online"])

    def _show_reference_online(self, online):
        """Show the reference server's (smoothed) state in the status bar."""
        if online:
            self._set_reference_status_label("Network Connection: Online ✅", "#2fa572")
        else:
            self._set_reference_status_label("Network Connection: Offline ❌", "#e74c3c")

    def _set_reference_status_label(self, text, color=None):
        """Safely update the reference status label from the monitor thread."""
        kwargs = {"text": text}
        if color is not None:
            kwargs["text_color"] = color

        def apply():
            if self.winfo_exists() and self.heartbeat_lbl.winfo_exists():
                self.heartbeat_lbl.configure(**kwargs)

        self.ui_call(apply)

    def _checkable_rows(self):
        """Return the rows that have an address to check."""
        # Copy: refresh_machines() may replace the list on the main thread.
        return [r for r in list(getattr(self, "machine_rows", [])) if r["data"].get("address")]

    def _ping_rows(self, rows):
        """Check machines in parallel and schedule safe row-status updates.

        Checked one at a time, each unreachable machine cost up to two
        seconds (ping timeout plus TCP fallback), so with a few offline
        machines the dots further down the list took tens of seconds to
        fill in after the dashboard opened.
        """
        if not rows:
            return

        def check(row):
            if self.stop_event.is_set():
                return
            data = row["data"]
            key = self.core.status_key(data["address"], data.get("port") or 22)
            state = self._status_tracker.update(key, self.core.check_machine(data))
            self.ui_call(lambda r=row, s=state: self.update_row_status(r, s))

        with ThreadPoolExecutor(max_workers=min(STATUS_WORKERS, len(rows))) as pool:
            list(pool.map(check, rows))

    # --- SHUTDOWN / LIFECYCLE ---

    def on_closing(self):
        """Persist window state, stop background tasks, and close the dashboard cleanly."""
        if getattr(self, "_closing", False):
            return
        self._closing = True

        if hasattr(self, "_debug_server_stop"):
            self._debug_server_stop.set()
        if hasattr(self, "_debug_server"):
            try:
                self._debug_server.close()
            except Exception:
                pass

        if self.winfo_exists():
            self.settings["last_x"] = self.winfo_x()
            self.settings["last_y"] = self.winfo_y()
            self.settings["last_w"] = self.winfo_width()
            self.settings["last_h"] = self.winfo_height()
            self.core.save_settings(self.settings)

        self.stop_event.set()

        pid_path = self.core.runtime_path("dashboard.pid")
        try:
            if os.path.exists(pid_path):
                os.remove(pid_path)
        except Exception:
            pass

        self.destroy()

    def destroy(self):
        """Ensure the monitor loop is stopped before destroying the window."""
        self.stop_event.set()
        super().destroy()

def main():
    """Launch the dashboard unless an existing instance is already running."""
    core = MachineManagerCore(test_mode="--test" in sys.argv)
    pid_file = core.runtime_path("dashboard.pid")

    old_pid = read_pid_file(pid_file)
    if old_pid and old_pid != os.getpid():
        # A stale file can name a PID that macOS has since reused, so check
        # it really is a dashboard; if so, bring that one forward instead.
        if is_switchbored_process(old_pid, "--dashboard"):
            try:
                os.kill(old_pid, SHOW_SIGNAL)
            except OSError:
                pass
            sys.exit(0)
        try:
            os.remove(pid_file)
        except OSError:
            pass

    app = RemoteManagerDash(core)
    app.mainloop()


if __name__ == "__main__":
    main()