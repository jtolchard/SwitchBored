import os
import json
import copy
import uuid
import threading
import webbrowser
import customtkinter as ctk
from tkinter import filedialog, messagebox
from ui_components import ToolTip, EmojiPicker, PluginManagerWidget
from .machine_editor import MachineEditorWindow
from .dialogs import ConfirmationDialog
from .ssh_keys import SSHKeyAssistant
from .ui_helpers import center_window_over_parent, find_ui_root, schedule_on_ui_thread
from console.updater import fetch_latest_release, parse_version, is_frozen
from version import VERSION

class SettingsWindow(ctk.CTkToplevel):
    """Top-level settings window for global options, machines, and plugins."""

    def __init__(self, master, core):
        """Build the settings UI and populate it from the current core settings."""
        super().__init__(master)
        
        self.title("General Settings")
        self.geometry("600x790")
        self.minsize(600, 790)

        # Transient keeps this window above the dashboard without the
        # stacking glitches that -topmost toggling causes on macOS.
        self.transient(master)
        self.lift()
        self.focus_force()


        self.master = master
        self.core = core
        self._ui_root = find_ui_root(self)

        # Work on a copy: nothing touches core.settings (or disk) until the
        # user clicks Save & Apply. Closing the window discards edits.
        self.settings = copy.deepcopy(self.core.settings)
        self.original_plugin_settings = copy.deepcopy(self.core.settings.get("plugins", {}))

        self.plugin_widgets = {}
        
        if "machines" not in self.settings:
            self.settings["machines"] = []

        # Main tab layout
        self.tabs = ctk.CTkTabview(self, command=self._on_tab_selected)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=0)

        # Style the tab strip as a single grouped control rather than
        # separate buttons: no per-segment border, a subtly lighter strip
        # so the group reads as one unit, and unselected tabs flat inside
        # it. Only the selected tab is accented, which marks the boundary.
        try:
            self.tabs._segmented_button.configure(
                border_width=0,
                fg_color="#333333",
                unselected_color="#333333",
                unselected_hover_color="#3d3d3d",
                selected_color="#4a4a4a",
                selected_hover_color="#4a4a4a",
                text_color="#dddddd",
            )
        except Exception:
            pass
        self.tab_glob = self.tabs.add("Global Settings")
        self.tab_mach = self.tabs.add("Machines")
        self.plugins_tab = self.tabs.add("Plugins")
        self.tab_updates = self.tabs.add("Updates")

        self._update_check_running = False

        # ------------------------------------------------------------------
        # Machines tab
        # ------------------------------------------------------------------

        self.machine_list = ctk.CTkScrollableFrame(
            self.tab_mach, 
            fg_color="#1e1e1e",
            border_width=1,
            border_color="#333333",
            corner_radius=6
        )

        self.machine_list.pack(fill="both", expand=True, padx=20, pady=(5, 10))

        divider = ctk.CTkFrame(self.tab_mach, height=2, fg_color="#333333")
        divider.pack(fill="x", padx=20, pady=(0, 10))
        
        btn_frame = ctk.CTkFrame(self.tab_mach, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 5))
        
        ctk.CTkButton(
            btn_frame, 
            text="+ Add New Machine", 
            command=self.add_machine
        ).pack(side="left")
        
        ctk.CTkButton(
            btn_frame,
            text="Export Machines",
            fg_color="#d48806",
            hover_color="#b57305",
            command=self.export_config
        ).pack(side="right")

        ctk.CTkButton(
            btn_frame,
            text="Import Machines",
            fg_color="#d48806",
            hover_color="#b57305",
            command=self.import_config
        ).pack(side="right", padx=(0, 10))
        
        self.refresh_machine_list()

        # ------------------------------------------------------------------
        # Global settings tab
        # ------------------------------------------------------------------

        self.glob_scroll = ctk.CTkScrollableFrame(self.tab_glob)
        self.glob_scroll.pack(fill="both", expand=True, padx=5, pady=5)

        self.startup_var = ctk.BooleanVar(value=self.settings.get("open_on_startup", False))
        ctk.CTkSwitch(self.glob_scroll, text="Open Dashboard on Startup", 
                      variable=self.startup_var).pack(anchor="w", padx=10, pady=(0,5))
                
        self.emoji_frame = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        self.emoji_frame.pack(fill="x", padx=10, pady=(0, 20))

        ctk.CTkLabel(self.emoji_frame, text="Menu Bar Icon:").pack(side="left", padx=(0, 15))

        self.current_global_emoji = self.settings.get("global_emoji", "⚙️")

        self.emoji_button = ctk.CTkButton(
            self.emoji_frame,
            text=self.current_global_emoji,
            font=("", 20),
            width=36,  
            height=36,
            corner_radius=6,
            border_width=2,
            border_color=("#aaaaaa", "#555555"),
            fg_color=("#e5e7eb", "#2b2b2b"),
            hover_color=("#d1d5db", "#3f3f3f"),
            command=self._open_global_emoji_picker
        )
        self.emoji_button.pack(side="left")

        raw_val = self.settings.get("use_ref_server", True)
        bool_val = True if raw_val and not isinstance(raw_val, (list, dict)) else False
        
        self.use_ref_var = ctk.BooleanVar(value=bool_val)
        
        ref_top_frame = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        ref_top_frame.pack(anchor="w", padx=10, fill="x")
        
        self.ref_switch = ctk.CTkSwitch(ref_top_frame, text="Regularly test connection to reference server", 
                                        variable=self.use_ref_var, command=self.toggle_ref_server)
        self.ref_switch.pack(side="left")
        
        info_btn = ctk.CTkButton(ref_top_frame, text="?", width=20, height=20, corner_radius=10, fg_color="#555555")
        info_btn.pack(side="left", padx=10)
        ToolTip(info_btn, "If enabled, the app will regularly ping this address\n"
                          "to check if your local network is active.\n"
                          "Status is shown in the menu bar and dashboard.")

        ref_input_frame = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        ref_input_frame.pack(anchor="w", padx=10, pady=(5, 0), fill="x")

        self.ref_server = ctk.CTkEntry(ref_input_frame, width=250)
        self.ref_server.insert(0, self.settings.get("ref_server", "google.com"))
        self.ref_server.pack(side="left")
        
        self.ref_server.bind("<KeyRelease>", self.schedule_ping_test)

        self.ref_status_lbl = ctk.CTkLabel(ref_input_frame, text="")
        self.ref_status_lbl.pack(side="left", padx=10)
        
        self.ping_job = None
        self.toggle_ref_server()

        links_title = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        links_title.pack(fill="x", padx=10, pady=(15, 0))

        ctk.CTkLabel(links_title, text="Custom Menu Bar Shortcuts:", font=("", 12, "bold")).pack(side="left")

        links_info = ctk.CTkButton(links_title, text="?", width=20, height=20,
                                   corner_radius=10, fg_color="#555555")
        links_info.pack(side="left", padx=8)
        ToolTip(links_info,
                "URL — opens in your browser.\n"
                "App — opens a new window of a macOS app; use its exact\n"
                "name, e.g. 'Visual Studio Code' or 'iTerm'.\n"
                "Command — runs a shell command in the background.")

        header_row = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        header_row.pack(fill="x", padx=10, pady=(0, 2))

        ctk.CTkLabel(header_row, text="", width=20).pack(side="left", padx=2)
        ctk.CTkLabel(header_row, text="Name", width=110, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(header_row, text="Type", width=100, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(header_row, text="Target", width=250, anchor="w").pack(side="left", padx=2)

        type_labels = {"url": "URL", "app": "App", "command": "Command"}
        placeholders = {
            "URL": "example.com",
            "App": "Application name",
            "Command": "shell command",
        }

        self.web_link_rows = []
        saved_links = self.settings.get("custom_links", [])

        for i in range(3):
            row = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)

            existing = saved_links[i] if i < len(saved_links) else {}

            ctk.CTkLabel(row, text=f"{i+1}.", width=20, anchor="e").pack(side="left", padx=2)

            name_ent = ctk.CTkEntry(row, placeholder_text="Display Name", width=110)
            name_ent.insert(0, existing.get("name", ""))
            name_ent.pack(side="left", padx=2)

            kind_label = type_labels.get(existing.get("type", "url"), "URL")
            type_var = ctk.StringVar(value=kind_label)

            value_ent = ctk.CTkEntry(row, placeholder_text=placeholders[kind_label], width=250)
            # Older settings stored the target under 'url'.
            value_ent.insert(0, existing.get("value", existing.get("url", "")))

            ctk.CTkOptionMenu(
                row,
                values=["URL", "App", "Command"],
                variable=type_var,
                width=100,
                height=24,
                font=("", 12),
                command=lambda choice, ent=value_ent: ent.configure(placeholder_text=placeholders[choice]),
            ).pack(side="left", padx=2)

            value_ent.pack(side="left", padx=2)

            self.web_link_rows.append((name_ent, type_var, value_ent))

        # --- TERMINAL APP SELECTION ---
        term_frame = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        term_frame.pack(fill="x", padx=10, pady=(15, 5))

        ctk.CTkLabel(term_frame, text="Terminal App:", font=("", 12, "bold"), width=90, anchor="w").pack(side="left", padx=(0, 10))

        self.term_var = ctk.StringVar(value=self.settings.get("terminal_type", "Terminal"))

        ctk.CTkRadioButton(term_frame, text="Terminal", variable=self.term_var, value="Terminal").pack(side="left", padx=(0, 15))

        iterm_installed = self._is_app_installed("iTerm")
        iterm_rb = ctk.CTkRadioButton(term_frame, text="iTerm", variable=self.term_var, value="iTerm")
        iterm_rb.pack(side="left", padx=(0, 5))

        if not iterm_installed:
            iterm_rb.configure(state="disabled", text_color="#d65c5c")
            if self.term_var.get() == "iTerm": self.term_var.set("Terminal")
            
            link = ctk.CTkLabel(term_frame, text="(Download)", text_color="#3498db", cursor="pointinghand", font=("", 11, "underline"))
            link.pack(side="left")
            link.bind("<Button-1>", lambda e: webbrowser.open("https://iterm2.com/"))

        # --- SFTP APP SELECTION ---
        sftp_frame = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        sftp_frame.pack(fill="x", padx=10, pady=(10, 10))

        ctk.CTkLabel(sftp_frame, text="SFTP App:", font=("", 12, "bold"), width=90, anchor="w").pack(side="left", padx=(0, 10))

        self.sftp_var = ctk.StringVar(value=self.settings.get("sftp_tool", "Cyberduck"))

        cd_installed = self._is_app_installed("Cyberduck")
        cd_rb = ctk.CTkRadioButton(sftp_frame, text="Cyberduck", variable=self.sftp_var, value="Cyberduck")
        cd_rb.pack(side="left", padx=(0, 5))
        
        if not cd_installed:
            cd_rb.configure(state="disabled", text_color="#d65c5c")
            link = ctk.CTkLabel(sftp_frame, text="(Download)", text_color="#3498db", cursor="pointinghand", font=("", 11, "underline"))
            link.pack(side="left", padx=(0, 15))
            link.bind("<Button-1>", lambda e: webbrowser.open("https://cyberduck.io/"))
        else:
            # Preserve spacing when no download link is shown.
            ctk.CTkFrame(sftp_frame, width=15, height=0, fg_color="transparent").pack(side="left")

        fz_installed = self._is_app_installed("FileZilla")
        fz_rb = ctk.CTkRadioButton(sftp_frame, text="FileZilla", variable=self.sftp_var, value="FileZilla")
        fz_rb.pack(side="left", padx=(0, 5))
        
        if not fz_installed:
            fz_rb.configure(state="disabled", text_color="#d65c5c")
            link = ctk.CTkLabel(sftp_frame, text="(Download)", text_color="#3498db", cursor="pointinghand", font=("", 11, "underline"))
            link.pack(side="left")
            link.bind("<Button-1>", lambda e: webbrowser.open("https://filezilla-project.org/"))

        if self.sftp_var.get() == "Cyberduck" and not cd_installed:
            self.sftp_var.set("FileZilla" if fz_installed else "")
        elif self.sftp_var.get() == "FileZilla" and not fz_installed:
            self.sftp_var.set("Cyberduck" if cd_installed else "")

        # --- SSH KEY ASSISTANT ---
        ssh_title_frame = ctk.CTkFrame(self.glob_scroll, fg_color="transparent")
        ssh_title_frame.pack(fill="x", padx=10, pady=(15, 5))
        
        ctk.CTkLabel(ssh_title_frame, text="SSH Key Deployment:", font=("", 12, "bold")).pack(side="left")
        
        ssh_info_btn = ctk.CTkButton(
            ssh_title_frame, 
            text="?", 
            width=20, 
            height=20, 
            fg_color="#444444", 
            hover_color="#555555", 
            corner_radius=10
        )
        ssh_info_btn.pack(side="left", padx=5)
        
        ToolTip(ssh_info_btn, "Copies your secure ssh key to saved machines\n" \
        "so you can connect instantly without typing a password every time.")
        
        ssh_frame = ctk.CTkFrame(self.glob_scroll, fg_color="#2b2b2b", corner_radius=10)
        ssh_frame.pack(fill="x", padx=10, pady=(0,5))
        
        self.ssh_status_lbl = ctk.CTkLabel(ssh_frame, text="Checking keys...", text_color="gray")
        self.ssh_status_lbl.pack(pady=(0, 5))
        
        ssh_btn_frame = ctk.CTkFrame(ssh_frame, fg_color="transparent")
        ssh_btn_frame.pack(pady=(0, 10))
        
        self.gen_key_btn = ctk.CTkButton(ssh_btn_frame, text="Generate Key", width=110, command=self.generate_ssh_key)
        self.gen_key_btn.pack(side="left", padx=5)
                
        self.deploy_key_btn = ctk.CTkButton(
            ssh_btn_frame,
            text="Deploy to All",
            width=110,
            fg_color="#3B8ED0",
            hover_color="#36719F",
            command=self.deploy_ssh_keys
        )
        self.deploy_key_btn.pack(side="left", padx=5)

        self.check_ssh_keys()

        self.plugin_manager = PluginManagerWidget(self.plugins_tab, self.core)
        self.plugin_manager.pack(expand=True, fill="both", padx=10, pady=10)

        # ------------------------------------------------------------------
        # Updates tab
        # ------------------------------------------------------------------

        ctk.CTkLabel(
            self.tab_updates,
            text=f"Installed version: v{VERSION}",
            font=("", 12, "bold")
        ).pack(anchor="w", padx=20, pady=(15, 0))

        self.update_status_lbl = ctk.CTkLabel(self.tab_updates, text="", font=("", 12))
        self.update_status_lbl.pack(anchor="w", padx=20, pady=(5, 10))

        ctk.CTkLabel(
            self.tab_updates,
            text="Release notes:",
            font=("", 11, "bold"),
            text_color="gray"
        ).pack(anchor="w", padx=20)

        self.update_notes_box = ctk.CTkTextbox(
            self.tab_updates,
            height=150,
            font=("", 12),
            fg_color="#1e1e1e",
            wrap="word"
        )
        self.update_notes_box.pack(fill="both", expand=True, padx=20, pady=(5, 10))
        self.update_notes_box.configure(state="disabled")

        # Shown only when an installable update is available (bundled app).
        # Default blue styling, matching the "Add New Machine" button.
        self._available_release = None
        self.update_install_btn = ctk.CTkButton(
            self.tab_updates,
            text="Download & Install Update",
            height=36,
            command=self._start_update_install,
        )

        # --- ADVANCED FEATURES SECTION ---
        ctk.CTkLabel(self.glob_scroll, text="Advanced Features:", font=("", 12, "bold")).pack(anchor="w", padx=10, pady=(0, 5))
        
        adv_frame = ctk.CTkFrame(self.glob_scroll, fg_color="#2b2b2b", corner_radius=10)
        adv_frame.pack(fill="x", padx=10, pady=5)

        self.remember_filter_var = ctk.BooleanVar(value=self.settings.get("remember_filter", False))
        ctk.CTkCheckBox(adv_frame, text="Remember Group Filter between sessions", 
                        variable=self.remember_filter_var).pack(anchor="w", padx=15, pady=(0, 5))

        self.debug_var = ctk.BooleanVar(value=self.settings.get("debug_mode", False))
        ctk.CTkCheckBox(adv_frame, text="Enable Debug Mode", variable=self.debug_var).pack(anchor="w", padx=15, pady=(5, 5))

        self.sysadmin_var = ctk.BooleanVar(value=self.settings.get("sysadmin_features", False))
        ctk.CTkCheckBox(adv_frame, text="Enable Sysadmin Features", variable=self.sysadmin_var).pack(anchor="w", padx=15, pady=(5, 10))

        # --- DANGER ZONE ---
        ctk.CTkLabel(self.glob_scroll, text="Danger Zone:", font=("", 12, "bold"), text_color="#e74c3c").pack(anchor="w", padx=10, pady=(15, 0))

        danger_frame = ctk.CTkFrame(self.glob_scroll, fg_color="#2b2b2b", corner_radius=10)
        danger_frame.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(
            danger_frame,
            text="Remove all locally saved settings and restart with defaults. Installed plugins are kept.",
            font=("", 11),
            text_color="gray",
            justify="left"
        ).pack(anchor="w", padx=15, pady=(5, 5))

        ctk.CTkButton(
            danger_frame,
            text="Reset All Settings",
            width=140,
            fg_color="#c0392b",
            hover_color="#e74c3c",
            command=self.confirm_reset_app_data
        ).pack(anchor="w", padx=15, pady=(0, 12))

        ctk.CTkButton(self, text="Save & Apply All Settings", fg_color="#2fa572", height=40, command=self.save_all).pack(pady=(0, 10), fill="x", padx=20)

    def _on_tab_selected(self):
        """React to tab changes; the Updates tab checks on each visit."""
        if self.tabs.get() == "Updates":
            self.run_update_check()

    def _show_update_result(self, status, notes, status_color=None):
        """Fill in the Updates tab from the main thread."""
        if not self.winfo_exists():
            return
        kwargs = {"text": status}
        if status_color:
            kwargs["text_color"] = status_color
        self.update_status_lbl.configure(**kwargs)

        self.update_notes_box.configure(state="normal")
        self.update_notes_box.delete("1.0", "end")
        self.update_notes_box.insert("1.0", notes)
        self.update_notes_box.configure(state="disabled")

    def run_update_check(self):
        """Check GitHub for a newer release and show its notes in the tab."""
        if self._update_check_running:
            return
        self._update_check_running = True
        self.update_status_lbl.configure(text="Checking for updates…", text_color="gray")

        def check():
            try:
                release = fetch_latest_release()
            except Exception:
                release = None

            current = parse_version(VERSION) or (0,)

            def apply():
                self._update_check_running = False
                self._available_release = None
                self.update_install_btn.pack_forget()

                if release is None:
                    self._show_update_result(
                        "Could not reach GitHub to check for updates.",
                        "", status_color="#d48806"
                    )
                elif release["version"] <= current:
                    self._show_update_result(
                        "You are running the latest version.",
                        "", status_color="#2fa572"
                    )
                else:
                    notes = release["notes"] or "No release notes were provided."
                    self._available_release = release

                    if is_frozen() and release.get("asset_url"):
                        self._show_update_result(
                            f"Version {release['tag']} is available.",
                            notes, status_color="#3B8ED0"
                        )
                        self.update_install_btn.configure(
                            state="normal", text="Download & Install Update"
                        )
                        self.update_install_btn.pack(pady=(0, 20))
                    else:
                        self._show_update_result(
                            f"Version {release['tag']} is available — "
                            "download it from the release page.",
                            notes, status_color="#3B8ED0"
                        )

            schedule_on_ui_thread(self._ui_root, apply)

        threading.Thread(target=check, daemon=True).start()

    def _start_update_install(self):
        """Ask the menu-bar app to download and install the available update."""
        try:
            with open(self.core.runtime_path("install_update.flag"), "w") as f:
                f.write("1")
        except Exception as e:
            self.update_status_lbl.configure(
                text=f"Could not start the update: {e}", text_color="#e74c3c"
            )
            return

        self.update_install_btn.configure(state="disabled", text="Downloading…")
        self.update_status_lbl.configure(
            text="Update started — the app will restart when it is ready.",
            text_color="#2fa572",
        )

    def confirm_reset_app_data(self):
        """Ask for typed confirmation before wiping all saved settings."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Reset All Settings")
        dialog.transient(self)
        dialog.update_idletasks()
        center_window_over_parent(dialog, self, 400, 330)

        ctk.CTkLabel(dialog, text="⚠️", font=("", 40)).pack(pady=(15, 5))
        ctk.CTkLabel(
            dialog,
            text=("This permanently deletes every saved setting\n"
                  "(machines, links, window layout, debug logs) from:\n\n"
                  "~/Library/Application Support/SwitchBored\n\n"
                  "Installed plugins are kept.\n"
                  "The app will restart with default settings."),
            font=("", 12),
            justify="center"
        ).pack(pady=5)

        ctk.CTkLabel(dialog, text="Type RESET to confirm:", font=("", 12, "bold")).pack(pady=(10, 2))
        entry = ctk.CTkEntry(dialog, width=200, justify="center")
        entry.pack(pady=(0, 10))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=15)

        def do_reset():
            self.core.log("SETTINGS", "User confirmed a full settings reset")
            self.core.reset_settings()

            # Restart the whole app so every process starts from defaults.
            with open(self.core.runtime_path("restart_app.flag"), "w") as f:
                f.write("restart")

            dialog.destroy()
            self.master.destroy()

        ctk.CTkButton(btn_frame, text="CANCEL", width=100, fg_color="#555555",
                      command=dialog.destroy).pack(side="left", padx=10)

        reset_btn = ctk.CTkButton(btn_frame, text="RESET", width=100, state="disabled",
                                  fg_color="#555555", hover_color="#e74c3c", command=do_reset)
        reset_btn.pack(side="left", padx=10)

        def on_type(event=None):
            if entry.get().strip() == "RESET":
                reset_btn.configure(state="normal", fg_color="#c0392b")
            else:
                reset_btn.configure(state="disabled", fg_color="#555555")

        entry.bind("<KeyRelease>", on_type)
        entry.focus_set()

    def _is_app_installed(self, app_name):
        """Return True if the application exists in a standard macOS app location."""
        paths = [
            f"/Applications/{app_name}.app",
            f"/System/Applications/Utilities/{app_name}.app",
            f"/System/Applications/{app_name}.app",
            f"{os.path.expanduser('~')}/Applications/{app_name}.app"
        ]
        return any(os.path.exists(p) for p in paths)

    def export_config(self):
        """Export the current settings to a user-selected JSON file."""
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile="switchbored_machines.json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            title="Export Machines"
        )
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    json.dump(self.settings, f, indent=4)
                messagebox.showinfo("Export Successful", f"Settings exported to\n{filepath}")
            except Exception as e:
                messagebox.showerror("Export Failed", f"Could not write file:\n{e}")

    def import_config(self):
        """Import settings from a JSON file, sanitize them, and reload the dashboard."""
        filepath = filedialog.askopenfilename(
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            title="Import Machines"
        )
        if filepath:
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)

                self.settings = self.core._sanitize(data)
                self.core.save_settings(self.settings)

                # Tell the console process to rebuild its menu too.
                with open(self.core.runtime_path("reload_menu.flag"), "w") as f:
                    f.write("1")

                messagebox.showinfo("Import Successful", "Settings imported successfully!")
                self.master.refresh_machines()
                self.destroy()
            except Exception as e:
                messagebox.showerror("Import Failed", f"Could not read JSON file:\n{str(e)}")

    def check_ssh_keys(self):
        """Refresh the SSH key status label and enable or disable related actions."""
        keys = SSHKeyAssistant.get_existing_keys()
        if keys:
            self.ssh_status_lbl.configure(text=f"Found {len(keys)} public key(s). Ready to deploy.", text_color="#3B8ED0")
            self.gen_key_btn.configure(state="disabled", fg_color="#555555")
            self.deploy_key_btn.configure(state="normal", fg_color="#3B8ED0")
        else:
            self.ssh_status_lbl.configure(text="No SSH keys found. Generate one first.", text_color="#e74c3c")
            self.deploy_key_btn.configure(state="disabled", fg_color="#555555")

    def generate_ssh_key(self):
        """Generate a default SSH key and refresh the deployment controls."""
        success, msg = SSHKeyAssistant.generate_key()
        if success:
            self.check_ssh_keys()
            messagebox.showinfo("Success", "SSH Key generated successfully!\nYou can now deploy it to your machines.")
        else:
            messagebox.showerror("Error", f"Failed to generate key:\n{msg}")

    def deploy_ssh_keys(self):
        """Route SSH key deployment through direct or interactive key selection."""
        term_type = self.term_var.get()
        machines = self.settings.get("machines", [])
        
        if not machines:
            messagebox.showwarning("No Machines", "Add some machines in the Machines tab before deploying keys.")
            return
            
        keys = SSHKeyAssistant.get_existing_keys()
        if not keys:
            messagebox.showerror("No Keys", "No public SSH keys found in ~/.ssh/")
            return
            
        if len(keys) == 1:
            self.prepare_deployment(keys[0], machines, term_type)
        else:
            self.show_key_selection_dialog(keys, machines, term_type)

    def prepare_deployment(self, key_path, machines, term_type):
        """Filter deployment targets and show a confirmation dialog before starting."""
        reachable_ssh = []
        no_ssh_skipped = 0
        offline_skipped = 0
        
        for m in machines:
            if "SSH" not in m.get("connections", ["SSH", "TMUX", "SFTP", "VNC"]):
                no_ssh_skipped += 1
                continue
                
            ip = m.get('address')
            if not ip:
                offline_skipped += 1
                continue
                
            if self.core.check_status(ip, port=m.get("port") or 22) is not None:
                reachable_ssh.append(m)
            else:
                offline_skipped += 1
                
        hud = ctk.CTkToplevel(self)
        hud.title("Confirm Deployment")
        hud.geometry("450x340")
        hud.transient(self)
        
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 225
        y = self.winfo_y() + (self.winfo_height() // 2) - 170
        hud.geometry(f"+{x}+{y}")
        
        ctk.CTkLabel(hud, text="🔑", font=("", 50)).pack(pady=(20, 10))
        ctk.CTkLabel(hud, text=f"This will open one {term_type} window to copy\nkeys to {len(reachable_ssh)} reachable machines.", font=("", 13, "bold")).pack(pady=10)
        
        skips = []
        if no_ssh_skipped > 0: skips.append(f"{no_ssh_skipped} non-SSH")
        if offline_skipped > 0: skips.append(f"{offline_skipped} offline")
        
        if skips: 
            ctk.CTkLabel(hud, text="(" + " and ".join(skips) + " machines skipped)", font=("", 11), text_color="#e74c3c").pack()
            
        ctk.CTkLabel(hud, text="NOTE: If a machine hangs, press 'Ctrl+C' in terminal.", font=("", 11), text_color="gray").pack(pady=15)
        
        bf = ctk.CTkFrame(hud, fg_color="transparent")
        bf.pack(side="bottom", pady=20)
        
        def start_deploy():
            hud.destroy()
            SSHKeyAssistant.deploy_all_serial(self.core, reachable_ssh, term_type, key_path)
            
        ctk.CTkButton(bf, text="CANCEL", width=100, fg_color="#4A4A4A", command=hud.destroy).pack(side="left", padx=10)
        ctk.CTkButton(bf, text="START", width=100, fg_color="#2fa572", hover_color="#25885c", command=start_deploy).pack(side="left", padx=10)

    def show_key_selection_dialog(self, keys, machines, term_type):
        """Prompt the user to choose which SSH key should be deployed."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Select SSH Key")
        
        w, h = 350, 200
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (w // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (h // 2)
        dialog.geometry(f"{w}x{h}+{x}+{y}")
        dialog.transient(self)

        ctk.CTkLabel(dialog, text="Multiple SSH keys found.", font=("", 14, "bold")).pack(pady=(20, 5))
        ctk.CTkLabel(dialog, text="Which key would you like to deploy?", text_color="gray").pack(pady=(0, 15))

        key_dict = {os.path.basename(k): k for k in keys}
        
        key_var = ctk.StringVar(value=list(key_dict.keys())[0])
        dropdown = ctk.CTkOptionMenu(dialog, values=list(key_dict.keys()), variable=key_var, width=200)
        dropdown.pack(pady=10)

        def on_confirm():
            selected_full_path = key_dict[key_var.get()]
            dialog.destroy()
            self.prepare_deployment(selected_full_path, machines, term_type)

        ctk.CTkButton(dialog, text="Use Selected Key", fg_color="#2fa572", hover_color="#25885c", command=on_confirm).pack(pady=15)


    def _open_global_emoji_picker(self):
        """Open the emoji picker for the menu-bar icon."""
        EmojiPicker(self, self._on_global_emoji_selected)

    def _on_global_emoji_selected(self, selected_emoji):
        """Apply the selected global emoji to the button and pending settings."""
        self.emoji_button.configure(text=selected_emoji)
        self.settings["global_emoji"] = selected_emoji

    def refresh_machine_list(self):
        """Rebuild the machine list shown in the Machines tab."""
        for w in self.machine_list.winfo_children():
            w.destroy()
            
        for idx, m in enumerate(self.settings["machines"]):
            row = ctk.CTkFrame(self.machine_list)
            row.pack(fill="x", pady=3, padx=5)
            
            up_arrow = ctk.CTkLabel(
                row, text="▲", font=("", 16), 
                text_color="#aaaaaa" if idx != 0 else "#444444",
                cursor="hand2" if idx != 0 else "arrow"
            )
            up_arrow.pack(side="left", padx=(5, 2))
            
            if idx != 0: 
                up_arrow.bind("<Button-1>", lambda e, i=idx: self.move_machine(i, -1))
                up_arrow.bind("<Enter>", lambda e, w=up_arrow: w.configure(text_color="#ffffff"))
                up_arrow.bind("<Leave>", lambda e, w=up_arrow: w.configure(text_color="#aaaaaa"))

            down_arrow = ctk.CTkLabel(
                row, text="▼", font=("", 16), 
                text_color="#aaaaaa" if idx != len(self.settings["machines"]) - 1 else "#444444",
                cursor="hand2" if idx != len(self.settings["machines"]) - 1 else "arrow"
            )
            down_arrow.pack(side="left", padx=(0, 0))
            
            if idx != len(self.settings["machines"]) - 1: 
                down_arrow.bind("<Button-1>", lambda e, i=idx: self.move_machine(i, 1))
                down_arrow.bind("<Enter>", lambda e, w=down_arrow: w.configure(text_color="#ffffff"))
                down_arrow.bind("<Leave>", lambda e, w=down_arrow: w.configure(text_color="#aaaaaa"))

            lbl = ctk.CTkLabel(row, text=f"{m.get('icon', '💻')}  {m.get('name', 'Unknown')} ({m.get('address', 'N/A')})", anchor="w")
            lbl.pack(side="left", expand=True, fill="x", padx=(12, 10))
            
            ctk.CTkButton(row, text="Delete", width=60, fg_color="#e74c3c", command=lambda i=idx: self.delete_machine(i)).pack(side="right", expand=False, padx=2)
            ctk.CTkButton(row, text="Edit", width=60, command=lambda i=idx: self.edit_machine(i)).pack(side="right", expand=False, padx=2)

    def move_machine(self, current_index, direction):
        """Move a machine one position up or down in the saved order."""
        new_index = current_index + direction
        
        if 0 <= new_index < len(self.settings["machines"]):
            machines = self.settings["machines"]
            machines[current_index], machines[new_index] = machines[new_index], machines[current_index]
            
            self.refresh_machine_list()

    def add_machine(self):
        """Open the machine editor for a new machine and append it when saved."""
        new_mach = {"id": uuid.uuid4().hex, "name": "New Machine", "address": "192.168.103.1", "user": "user", "port": 22, "icon": "🖥️"}
        
        def on_add_save(saved_machine):
            m_name = saved_machine.get("name", "Unknown")
            m_addr = saved_machine.get("address", "N/A")
            self.core.log("SETTINGS", f"Added new machine: {m_name} ({m_addr})")
            self.settings["machines"].append(saved_machine)
            self.refresh_machine_list()
            
        MachineEditorWindow(self, new_mach, on_add_save)

    def edit_machine(self, idx):
        """Open the machine editor for an existing machine."""
        MachineEditorWindow(self, self.settings["machines"][idx], self.on_machine_saved)

    def on_machine_saved(self, updated_machine):
        """Refresh the machine list after an existing machine is edited."""
        self.refresh_machine_list()

    def delete_machine(self, idx):
        """Confirm and delete a machine from the saved list."""
        m_name = self.settings["machines"][idx].get("name", "Unknown Machine")
        
        def do_delete():
            self.core.log("SETTINGS", f"Deleting machine at index {idx}: {m_name}")
            self.settings["machines"].pop(idx)
            self.refresh_machine_list()
        
        ConfirmationDialog(
            master=self, 
            title="Confirm Deletion", 
            message=f"Permanently remove '{m_name}'?",
            confirm_callback=do_delete
        )

    def toggle_ref_server(self):
        """Enable or disable the reference-server input and status display."""
        if self.use_ref_var.get():
            self.ref_server.configure(state="normal", text_color=["gray10", "gray90"])
            self.schedule_ping_test()
        else:
            self.ref_server.configure(state="disabled", text_color="gray50")
            self.ref_status_lbl.configure(text="Disabled ⏸️", text_color="gray50")

    def schedule_ping_test(self, event=None):
        """Debounce reference-server checks while the user is typing."""
        if not self.use_ref_var.get(): return
        if self.ping_job:
            self.after_cancel(self.ping_job)
        self.ref_status_lbl.configure(text="Testing... ⏳", text_color="gray")
        self.ping_job = self.after(600, self.test_ref_server)

    def test_ref_server(self):
        """Start a background connectivity check for the current reference server."""
        target = self.ref_server.get().strip()
        if not target:
            self.ref_status_lbl.configure(text="No Address ⚠️", text_color="#d48806")
            return

        def do_ping():
            latency = self.core.check_status(target)

            def update_gui():
                if self.winfo_exists() and self.use_ref_var.get():
                    if latency is not None:
                        self.ref_status_lbl.configure(text="Online ✅", text_color="#2fa572")
                    else:
                        self.ref_status_lbl.configure(text="Offline ❌", text_color="#e74c3c")

            schedule_on_ui_thread(self._ui_root, update_gui)

        threading.Thread(target=do_ping, daemon=True).start()

    def _collect_shortcut_links(self):
        """Read the shortcut rows into custom_links entries."""
        label_to_type = {"URL": "url", "App": "app", "Command": "command"}

        links = []
        for name_ent, type_var, value_ent in self.web_link_rows:
            name = name_ent.get().strip()
            value = value_ent.get().strip()
            if not (name and value):
                continue

            kind = label_to_type.get(type_var.get(), "url")
            if kind == "url" and not (value.startswith("http://") or value.startswith("https://")):
                value = "https://" + value

            links.append({"name": name, "type": kind, "value": value})
        return links

    def save_all(self):
        """Validate, collect, and persist all settings, then show the appropriate follow-up dialog."""
        self.core.log("UI", "User clicked Save & Apply. Validating settings...")

        plugin_changed = False
        new_plugin_data = {}

        if hasattr(self, 'plugin_manager'):
            new_plugin_data = self.plugin_manager.get_plugin_data_for_saving()
            
            old_json = json.dumps(self.original_plugin_settings, sort_keys=True)
            new_json = json.dumps(new_plugin_data, sort_keys=True)

            if old_json != new_json:
                plugin_changed = True
                self.core.log("SETTINGS", "Plugin change detected.")

        # The emoji always comes from the picker, so no length validation:
        # multi-codepoint (ZWJ) emojis are legitimate single glyphs.
        g_emoji = self.emoji_button.cget("text").strip()
        self.settings["global_emoji"] = g_emoji or "💻"
        self.settings["open_on_startup"] = self.startup_var.get()
        self.settings["remember_filter"] = self.remember_filter_var.get()
        
        if not self.remember_filter_var.get():
            self.settings["last_filter"] = "All"
            self.settings["last_h"] = None
            
        self.settings["custom_links"] = self._collect_shortcut_links()
        self.settings["terminal_type"] = self.term_var.get()
        self.settings["sftp_tool"] = self.sftp_var.get()
        self.settings["use_ref_server"] = self.use_ref_var.get()
        self.settings["ref_server"] = self.ref_server.get().strip()
        self.settings["debug_mode"] = self.debug_var.get()
        self.settings["sysadmin_features"] = self.sysadmin_var.get()

        if hasattr(self, 'plugin_manager'):
            self.settings["plugins"] = new_plugin_data

        try:
            self.core.save_settings(self.settings)
            self.core.log("SETTINGS", "Settings saved to disk.")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save: {e}")
            return
        
        if plugin_changed:
            self.show_restart_dialog()
            return 

        self.show_success_popup()

    def show_restart_dialog(self):
            """Show a restart-required dialog after plugin settings have changed."""
            popup = ctk.CTkToplevel(self, fg_color="#ffffff") 
            popup.withdraw()
            popup.title("Restart Required")
            popup.geometry("340x180")  
            popup.transient(self)
            
            popup.update_idletasks()
            center_window_over_parent(popup, self, 340, 180)
            popup.deiconify()

            border_frame = ctk.CTkFrame(popup, fg_color="#ffffff", border_width=2, border_color="#e74c3c", corner_radius=10)
            border_frame.pack(fill="both", expand=True, padx=5, pady=5)

            ctk.CTkLabel(border_frame, text="🔄 Restart Required", font=("", 18, "bold"), text_color="#e74c3c").pack(pady=(20, 5))
            ctk.CTkLabel(
                border_frame, 
                text="Plugin changes require a restart.\nWould you like to restart now?", 
                text_color="#555555", 
                font=("", 12)
            ).pack(pady=(0, 15))

            btn_frame = ctk.CTkFrame(border_frame, fg_color="transparent")
            btn_frame.pack(pady=10)

            def do_restart():
                with open(self.core.runtime_path("restart_app.flag"), "w") as f:
                    f.write("restart")
                
                self.master.destroy()

            def do_cancel():
                popup.destroy()
                self.destroy()

            ctk.CTkButton(btn_frame, text="Later", width=90, fg_color="#555555", command=do_cancel).pack(side="left", padx=10)
            ctk.CTkButton(btn_frame, text="Restart", width=90, fg_color="#e74c3c", hover_color="#c0392b", command=do_restart).pack(side="left", padx=10)

    def show_success_popup(self):
        """Show a confirmation dialog after non-restart settings are saved."""
        popup = ctk.CTkToplevel(self, fg_color="#ffffff") 
        popup.withdraw()
        popup.title("Success")
        popup.geometry("300x160")  
        popup.transient(self)
        
        popup.update_idletasks()
        center_window_over_parent(popup, self, 300, 160)
        popup.deiconify()

        border_frame = ctk.CTkFrame(popup, fg_color="#ffffff", border_width=2, border_color="#2fa572", corner_radius=10)
        border_frame.pack(fill="both", expand=True, padx=5, pady=5)

        ctk.CTkLabel(border_frame, text="✅ Settings Saved", font=("", 18, "bold"), text_color="#2fa572").pack(pady=(25, 10))
        ctk.CTkLabel(border_frame, text="Your changes have been applied.", text_color="#555555", font=("", 12)).pack(pady=(0, 15))

        def on_ok():
            # Trigger the lighter 'menu reload' for non-restart changes
            flag_path = self.core.runtime_path("reload_menu.flag")
            with open(flag_path, "w") as f:
                f.write("1")
            popup.destroy()
            self.master.refresh_machines()
            self.destroy()
                    
        ctk.CTkButton(border_frame, text="OK", width=100, text_color="#ffffff", fg_color="#2fa572", hover_color="#25885c", command=on_ok).pack(pady=(0, 20))