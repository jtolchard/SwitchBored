import threading
import customtkinter as ctk
from tkinter import messagebox
from ui_components import EmojiPicker, ToolTip
from .admin_commands import AdminCommandEditor
from .ui_helpers import find_ui_root, schedule_on_ui_thread

class MachineEditorWindow(ctk.CTkToplevel):
    """Editor window for creating or modifying a single machine entry."""

    def __init__(self, parent, machine_data, on_save_callback):
        """Build the editor form, sized to match the enabled feature set."""
        super().__init__(parent)
        self.core = parent.core
        self._ui_root = find_ui_root(self)
        self.title("Edit Machine")

        # --- WINDOW GEOMETRY ---
        sysadmin_enabled = parent.settings.get("sysadmin_features", False)

        # The sysadmin section adds extra rows, so the window grows with it.
        window_width = 650
        window_height = 680 if sysadmin_enabled else 420
        
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        center_x = int((screen_width / 2) - (window_width / 2))
        center_y = int((screen_height / 2) - (window_height / 2))
        
        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        self.resizable(False, False)
        self.transient(parent)
        self.bind("<Escape>", lambda e: self.destroy())

        self.machine_data = machine_data
        self.temp_admin_cmds = machine_data.get("admin_cmds", [])
        self.on_save = on_save_callback

        # Main Container
        self.main_view = ctk.CTkFrame(self, fg_color="transparent")
        self.main_view.pack(fill="both", expand=True, padx=30, pady=(10, 0))

        # --- SECTION: GENERAL (Integrated Header) ---
        self._styled_header(self.main_view, "General Settings")
        
        r1 = self._create_row(self.main_view)
        self.name_ent = self._create_field(r1, "Machine Name", self.machine_data.get("name", ""), width=280)
        self.nick_ent = self._create_field(r1, "Nickname", self.machine_data.get("nickname", ""), width=280, padx=(20, 0))

        r2 = self._create_row(self.main_view)
        self.ip_ent = self._create_field(r2, "IP Address", self.machine_data.get("address", ""), width=280)
        self.tag_ent = self._create_field(r2, "Group Labels (comma separated)", self.machine_data.get("tag", ""), width=280, padx=(20, 0))
        
        # Floating status label for IP check
        self.status_lbl = ctk.CTkLabel(r2, text="", font=("", 10))
        self.status_lbl.place(x=220, y=5) 
        self.ip_ent.bind("<KeyRelease>", self.check_ip)
        
        r3 = self._create_row(self.main_view)
        self.user_ent = self._create_field(r3, "Standard Username", self.machine_data.get("user", ""), width=280)
        self.port_ent = self._create_field(r3, "Port (ssh/sftp)", str(self.machine_data.get("port", "22")), width=280, padx=(20, 0))

        r4 = self._create_row(self.main_view)
        self._add_centered_icon_selector(r4)
        self._add_centered_connection_toggles(r4)

        r5 = self._create_row(self.main_view)
        r5.pack_configure(pady=(16, 4))
        self.notify_var = ctk.BooleanVar(value=bool(self.machine_data.get("notify_offline", False)))
        notify_group = ctk.CTkFrame(r5, fg_color="transparent")
        notify_group.pack()
        ctk.CTkSwitch(
            notify_group,
            text="Notify when unreachable",
            variable=self.notify_var,
            font=("", 12)
        ).pack(side="left")

        notify_info = ctk.CTkButton(notify_group, text="?", width=20, height=20,
                                    corner_radius=10, fg_color="#555555")
        notify_info.pack(side="left", padx=8)
        ToolTip(notify_info,
                "Sends a notification after this machine has been\n"
                "unreachable for 30 seconds. Requires the reference server\n"
                "check, so a network outage doesn't trigger false alarms.")

        # --- SECTION: ADVANCED ---
        if sysadmin_enabled:
            self._styled_header(self.main_view, "Advanced / Sysadmin")
            
            r5 = self._create_row(self.main_view)
            self.admin_user_ent = self._create_field(r5, "Admin Username", self.machine_data.get("admin_user", ""), width=280)
            
            # Setup Commands Trigger
            cmd_btn_group = ctk.CTkFrame(r5, fg_color="transparent")
            cmd_btn_group.pack(side="left", padx=(20, 0))
            ctk.CTkLabel(cmd_btn_group, text="Custom Commands", font=("", 12)).pack(anchor="w")
            ctk.CTkButton(cmd_btn_group, text="⚙️ Configure Admin Commands", width=280, height=32,
                           fg_color=("#3b82f6", "#1e40af"), hover_color="#1d4ed8",
                           command=self._open_admin_cmd_editor).pack(pady=(2, 0))

            r6 = self._create_row(self.main_view)
            self.services_entry = self._create_field(r6, "Tracked System Services (comma separated)", 
                                                   ", ".join(self.machine_data.get("services", [])), width=590)
            r7 = self._create_row(self.main_view)
            self.logs_entry = self._create_field(r7, "Log File Paths (comma separated)", 
                                               ", ".join(self.machine_data.get("logs", [])), width=590)

        # --- FOOTER ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=20)
        btn_container = ctk.CTkFrame(footer, fg_color="transparent")
        btn_container.pack(expand=True)
        
        ctk.CTkButton(btn_container, text="Cancel", fg_color="#555555", width=120, command=self.destroy).pack(side="left", padx=10)
        ctk.CTkButton(btn_container, text="Save Machine", fg_color="#2fa572", width=120, command=self.save).pack(side="left", padx=10)

    # --- UI HELPERS ---
    def _styled_header(self, parent, text):
        """Add a section header label with a horizontal rule beside it."""
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="x", pady=(5, 2))
        lbl = ctk.CTkLabel(container, text=text, font=("", 11, "italic", "bold"), text_color="white")
        lbl.pack(side="left")
        line = ctk.CTkFrame(container, height=1, fg_color=("#dbdbdb", "#444444"))
        line.pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _add_centered_icon_selector(self, parent):
        """Add the machine icon button with its label, centered in its column."""
        outer = ctk.CTkFrame(parent, fg_color="transparent", width=280, height=60)
        outer.pack(side="left", fill="both", expand=True)
        outer.pack_propagate(False)
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(expand=True)
        ctk.CTkLabel(inner, text="Custom Icon", font=("", 12)).pack()
        self.icon_button = ctk.CTkButton(inner, text=self.machine_data.get("icon", "💻"), 
                                         width=55, height=32, font=("", 20),
                                         command=self._open_machine_emoji_picker)
        self.icon_button.pack(pady=(2, 0))

    def _add_centered_connection_toggles(self, parent):
        """Add the SSH/TMUX/SFTP/VNC checkboxes, centered in their column."""
        outer = ctk.CTkFrame(parent, fg_color="transparent", width=280, height=60)
        outer.pack(side="left", padx=(20, 0), fill="both", expand=True)
        outer.pack_propagate(False)
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(expand=True)
        ctk.CTkLabel(inner, text="Enabled Connections", font=("", 12)).pack()
        self.conn_vars = {}
        cb_frame = ctk.CTkFrame(inner, fg_color="transparent")
        cb_frame.pack()
        for conn in ["SSH", "TMUX", "SFTP", "VNC"]:
            var = ctk.BooleanVar(value=(conn in self.machine_data.get("connections", ["SSH"])))
            ctk.CTkCheckBox(cb_frame, text=conn, variable=var, width=60, font=("", 11)).pack(side="left", padx=2)
            self.conn_vars[conn] = var

    def _create_row(self, parent):
        """Add a horizontal form row and return its container frame."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", pady=4)
        return frame

    def _create_field(self, parent, label, value, width, padx=(0,0)):
        """Add a labelled entry field pre-filled with the given value."""
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.pack(side="left", padx=padx)
        ctk.CTkLabel(group, text=label, font=("", 12)).pack(anchor="w")
        ent = ctk.CTkEntry(group, width=width)
        ent.insert(0, value)
        ent.pack(pady=(2, 0))
        return ent

    # --- ACTION HANDLERS ---
    def _open_admin_cmd_editor(self):
        """Open the admin command editor for this machine's custom commands."""
        AdminCommandEditor(self, self.temp_admin_cmds, self._update_admin_cmds)

    def _update_admin_cmds(self, new_cmds):
        """Hold edited admin commands until the machine itself is saved."""
        self.temp_admin_cmds = new_cmds

    def _open_machine_emoji_picker(self):
        """Open the emoji picker for the machine icon."""
        picker = EmojiPicker(self, self._on_machine_emoji_selected)
        picker.update_idletasks()
        # Center the picker over this window
        p_x = self.winfo_x() + (self.winfo_width() // 2) - (picker.winfo_width() // 2)
        p_y = self.winfo_y() + (self.winfo_height() // 2) - (picker.winfo_height() // 2)
        picker.geometry(f"+{p_x}+{p_y}")

    def _on_machine_emoji_selected(self, emoji):
        """Apply the chosen emoji to the icon button."""
        self.icon_button.configure(text=emoji)

    def save(self):
        """Validate the form, write it back to the machine dict, and close."""
        # Validate the port before committing anything, so a typo doesn't
        # silently abort the save inside the Tk callback.
        port_raw = self.port_ent.get().strip() or "22"
        if not port_raw.isdigit() or not (0 < int(port_raw) < 65536):
            messagebox.showerror(
                "Invalid Port",
                f"'{port_raw}' is not a valid port number (1-65535).",
                parent=self
            )
            return

        # Update machine data dictionary
        self.machine_data["name"] = self.name_ent.get()
        self.machine_data["nickname"] = self.nick_ent.get()
        self.machine_data["address"] = self.ip_ent.get().strip()
        self.machine_data["user"] = self.user_ent.get()
        self.machine_data["port"] = int(port_raw)
        self.machine_data["icon"] = self.icon_button.cget("text")
        self.machine_data["tag"] = self.tag_ent.get()
        self.machine_data["connections"] = [c for c, v in self.conn_vars.items() if v.get()]
        self.machine_data["notify_offline"] = self.notify_var.get()

        if hasattr(self, 'admin_user_ent'):
            self.machine_data["admin_user"] = self.admin_user_ent.get()
            self.machine_data["admin_cmds"] = self.temp_admin_cmds
            self.machine_data["services"] = [s.strip() for s in self.services_entry.get().split(",") if s.strip()]
            self.machine_data["logs"] = [l.strip() for l in self.logs_entry.get().split(",") if l.strip()]

        self.on_save(self.machine_data)
        self.destroy()

    def check_ip(self, event=None):
        """Check reachability of the typed address and show the result inline."""
        ip = self.ip_ent.get().strip()
        if not ip:
            self.status_lbl.configure(text="")
            return
        self.status_lbl.configure(text="Checking...", text_color="gray")

        port = self.port_ent.get().strip() or "22"

        def ping_check():
            latency = self.core.check_status(ip, port=port if port.isdigit() else None)

            def apply():
                if not self.winfo_exists() or not self.status_lbl.winfo_exists():
                    return
                if latency is not None:
                    self.status_lbl.configure(text="Online ✅", text_color="#2fa572")
                else:
                    self.status_lbl.configure(text="Offline ❌", text_color="#e74c3c")

            schedule_on_ui_thread(self._ui_root, apply)

        threading.Thread(target=ping_check, daemon=True).start()