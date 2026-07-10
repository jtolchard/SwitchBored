import os
import sys
import time
import queue
import signal
import threading
import tkinter
import customtkinter as ctk
from core import MachineManagerCore
from actions import ActionManager
from console.bootstrap import spawn_self
from version import APP_NAME
from ui_components import ToolTip, enable_trackpad_scrolling
from .debug_console import DebugConsoleMixin
from .machine_details import MachineDetailsWindow
from .settings_window import SettingsWindow
from .dialogs import show_error_dialog
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

        self.geometry(f"{saved_w}x{final_h}+{pos_x}+{pos_y}")
        self.deiconify()
        self.attributes("-alpha", 1)
        self.truncate_names()

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

        self.settings_btn = ctk.CTkLabel(
            self.header_frame,
            text="⚙",
            font=("", 28),
            cursor="pointinghand"
        )
        self.settings_btn.grid(row=0, column=2, pady=(12, 5), sticky="e")
        self.settings_btn.bind("<Button-1>", lambda e: self.open_settings())
        ToolTip(self.settings_btn, "Settings")

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

    def update_row_status(self, row, latency):
        """Update a machine row's status indicator and tooltip safely."""
        dot = row.get('dot')
        tooltip = row.get('dot_tip')
        
        if not dot or not dot.winfo_exists():
            return
            
        if latency is not None:
            ms_val = latency * 1000
            dot.configure(text="●", text_color="#2fa572") # Green
            if tooltip:
                tooltip.text = f"{ms_val:.1f} ms"
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
                
                self.settings["last_x"] = self.winfo_x()
                self.settings["last_y"] = self.winfo_y()
                
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
        """Run the background monitoring loop for reference and machine status checks.

        This runs on a worker thread and must never touch Tk directly; all UI
        updates are posted through ui_call() and applied on the main thread.
        """
        while not self.stop_event.is_set():
            self.settings = self.core.settings

            self._update_reference_status()
            self._ping_all_machine_rows()

            for _ in range(50):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)

    def _update_reference_status(self):
        """Check the reference server and update the status bar."""
        if not self.settings.get("use_ref_server", True):
            self._set_reference_status_label("")
            return

        target = self.settings.get("ref_server", "1.1.1.1")
        latency = self.core.check_status(target)

        if latency is not None:
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

    def _ping_all_machine_rows(self):
        """Ping each machine and schedule a safe row-status update."""
        if not hasattr(self, "machine_rows"):
            return

        for row in self.machine_rows:
            if self.stop_event.is_set():
                break

            ip = row["data"].get("address")
            if not ip:
                continue

            mach_latency = self.core.check_status(ip, port=row["data"].get("port") or 22)

            self.ui_call(
                lambda r=row, l=mach_latency: self.update_row_status(r, l)
            )

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

    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            sys.exit(0)
        except (ProcessLookupError, ValueError, OSError):
            try:
                os.remove(pid_file)
            except OSError:
                pass

    app = RemoteManagerDash(core)

    # The console asks us to quit with SIGTERM; close cleanly so window
    # geometry is saved and the PID file is removed. The queue-drain 'after'
    # loop guarantees the Python-level handler runs promptly.
    signal.signal(signal.SIGTERM, lambda *_: app.after(0, app.on_closing))

    app.mainloop()


if __name__ == "__main__":
    main()