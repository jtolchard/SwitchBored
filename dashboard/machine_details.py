import os
import time
import shlex
import threading
import customtkinter as ctk
from ui_components import ToolTip
from .service_status import parse_service_state
from .ui_helpers import find_ui_root, schedule_on_ui_thread

class MachineDetailsWindow(ctk.CTkToplevel):
    """Details window for a single machine, including status, services, logs, and admin actions."""

    def __init__(self, master, machine, core):
        """Build and display the machine details window."""
        super().__init__(master)
        self.machine = machine
        self.core = core
        self._ui_root = find_ui_root(self)
        self.sysadmin_enabled = master.settings.get("sysadmin_features", False)
        self.admin_user = machine.get("admin_user", "").strip()
        
        self.title(f"Details: {machine.get('name', 'Unknown')}")
        self.geometry(f"450x200+{(master.winfo_x()+50)}+{(master.winfo_y()+50)}")
        self.transient(master)

        self.ref_btn = ctk.CTkButton(self, text="↻", width=30, height=30, fg_color="transparent", 
                                     font=("", 20), command=self.refresh_all_data)
        self.ref_btn.place(relx=0.97, rely=0.02, anchor="ne")
        
        ctk.CTkLabel(self, text=machine.get("icon", "🖥"), font=("", 45)).pack(pady=(25, 5))
        ctk.CTkLabel(self, text=machine.get("name", "Unknown"), font=("", 22, "bold")).pack(pady=(0, 15))
        
        self.inf = ctk.CTkFrame(self, fg_color="transparent")
        self.inf.pack(fill="x", padx=20)
        self.add_row(self.inf, "Address:", machine.get('address', 'Unknown'), 0)
        self.add_row(self.inf, "Nickname:", machine.get('nickname', 'N/A'), 1)
        self.p_lbl = ctk.CTkLabel(self.inf, text="Checking...", font=("", 12, "bold"))
        self.add_row(self.inf, "Live Ping:", self.p_lbl, 2)
        filter_group = machine.get("tag", "").strip()
        display_tag = filter_group if filter_group else "-"
        self.add_row(self.inf, "Group:", display_tag, 3)

        if self.sysadmin_enabled:
            # --- SERVICES ---
            services = self.machine.get("services", [])
            if services:
                ctk.CTkLabel(self, text="SYSTEM SERVICES", font=("", 10, "bold"), text_color="gray").pack(pady=(20, 5))
                svc_f = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=10)
                svc_f.pack(fill="x", padx=20)
                
                self.svcs = {}
                for s in services:
                    s = s.strip()
                    if not s: continue
                    
                    row = ctk.CTkFrame(svc_f, fg_color="transparent")
                    row.pack(fill="x", padx=10, pady=5)
                    
                    ctk.CTkLabel(row, text=s, font=("", 11, "bold"), width=100, anchor="w").pack(side="left")
                    
                    p = ctk.CTkLabel(row, text="● ...", font=("", 11))
                    p.pack(side="left", padx=5)
                    self.svcs[s] = p
                    
                    bf = ctk.CTkFrame(row, fg_color="transparent")
                    bf.pack(side="right")
                    
                    actions = [("start", "#2fa572"), ("stop", "#e74c3c"), ("restart", "#d48806")]
                    for action_name, color in actions:
                        ctk.CTkButton(bf, text=action_name.upper(), width=65, height=24, 
                                    font=("", 9, "bold"), fg_color=color, 
                                    command=lambda svc=s, act=action_name: self.s_run(svc, act)).pack(side="left", padx=1)

            # --- LOG FILES ---
            logs = self.machine.get("logs", [])
            if logs:
                ctk.CTkLabel(self, text="LOG FILES", font=("", 10, "bold"), text_color="gray").pack(pady=(20, 5))
                log_outer = ctk.CTkFrame(self, fg_color="transparent")
                log_outer.pack(fill="x")
                
                log_inner = ctk.CTkFrame(log_outer, fg_color="transparent")
                log_inner.pack(expand=True)

                self.log_dropdown = ctk.CTkOptionMenu(log_inner, values=logs, width=220)
                self.log_dropdown.pack(side="left", padx=5)

                self.view_btn = ctk.CTkButton(log_inner, text="VIEW", width=60, height=30, command=self.view_log)
                self.view_btn.pack(side="left", padx=5)

                if not self.admin_user:
                    self.view_btn.configure(state="disabled", fg_color="#555555")
                    ToolTip(self.view_btn, "Define an admin user to view logs.")

            # --- ADMIN COMMANDS ---
            admin_cmds = self.machine.get("admin_cmds", [])
            if admin_cmds:
                ctk.CTkLabel(self, text="ADMIN COMMANDS", font=("", 10, "bold"), text_color="gray").pack(pady=(20, 5))
                cmd_f = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=10)
                cmd_f.pack(fill="x", padx=20, pady=(0, 20))
                for admin_cmd in admin_cmds:
                    row = ctk.CTkFrame(cmd_f, fg_color="transparent")
                    row.pack(fill="x", padx=10, pady=5)
                    ctk.CTkLabel(row, text=admin_cmd["name"], font=("", 11, "bold")).pack(side="left")
                    btn = ctk.CTkButton(row, text="RUN", width=50, height=22, font=("", 9, "bold"),
                                       command=lambda c=admin_cmd: self.run_admin_cmd(c))
                    btn.pack(side="right")
                    if not self.admin_user:
                        btn.configure(state="disabled", fg_color="#555555")

        # Let plugins add their own sections (e.g. usb_block) before the
        # window measures its natural height below.
        find_ui_root(self).plugin_hook("on_machine_details", self, self.machine, self)

        self.schedule_refresh()
        self.withdraw()
        self.update_idletasks()
        
        natural_width = 550 
        natural_height = self.winfo_reqheight() 
        
        x = master.winfo_x() + 50
        y = master.winfo_y() + 50
        
        self.minsize(natural_width, natural_height)
        self.geometry(f"{natural_width}x{natural_height}+{x}+{y}")
        
        self.deiconify()

    def schedule_refresh(self):
        """Schedule the recurring details refresh loop."""
        if not self.winfo_exists():
            return
            
        self.refresh_all_data()
        self.refresh_job = self.after(5000, self.schedule_refresh)

    def add_row(self, f, l, v, r):
        """Add a two-column label/value row to the info grid."""
        ctk.CTkLabel(f, text=l, font=("", 12, "bold")).grid(row=r, column=0, sticky="w", pady=2)
        if isinstance(v, str):
            ctk.CTkLabel(f, text=v, font=("", 12)).grid(row=r, column=1, sticky="w", padx=10)
        else:
            v.grid(row=r, column=1, sticky="w", padx=10)

    def refresh_all_data(self):
        """Refresh the live ping and, if enabled, service states."""
        threading.Thread(target=self._fetch_status, daemon=True).start()
        if self.master.settings.get("sysadmin_features"):
            threading.Thread(target=self.up_s, daemon=True).start()

    def _fetch_status(self):
        """Check machine reachability and update the live ping label."""
        latency = self.core.check_status(
            self.machine.get('address'),
            port=self.machine.get('port') or 22,
        )

        def apply():
            if not self.winfo_exists() or not self.p_lbl.winfo_exists():
                return
            if latency is not None:
                ms_val = latency * 1000
                self.p_lbl.configure(text=f"{ms_val:.1f} ms", text_color="#2fa572")
            else:
                self.p_lbl.configure(text="Offline", text_color="#e74c3c")

        schedule_on_ui_thread(self._ui_root, apply)

    def destroy(self):
        """Cancel the refresh loop before destroying the window."""
        if hasattr(self, 'refresh_job'):
            try:
                self.after_cancel(self.refresh_job)
            except Exception:
                pass
        super().destroy()

    def s_run(self, service, action):
        """Run a systemctl action for a service and refresh its displayed state."""
        target_user = self.admin_user if self.admin_user else self.machine.get("user")
        m_ref = self.machine.copy()
        m_ref["user"] = target_user
        
        if service in self.svcs:
            self.svcs[service].configure(text="● PENDING...", text_color="gray")
        
        def run_it():
            cmd = f"systemctl {action} {shlex.quote(service)}"
            self.core.run_ssh_command(m_ref, cmd)
            time.sleep(1.2)
            self.up_s()

        threading.Thread(target=run_it, daemon=True).start()

    def up_s(self):
        """Refresh the displayed status of all configured services."""
        if not hasattr(self, 'svcs') or not self.svcs: return
            
        target_user = self.admin_user if self.admin_user else self.machine.get("user")
        m_ref = self.machine.copy()
        m_ref["user"] = target_user

        for s, pill in self.svcs.items():
            # `systemctl show` reports machine-readable state (LoadState=...,
            # ActiveState=...) in English keywords regardless of the remote
            # host's locale, unlike `systemctl status`, whose text is
            # translated and version-dependent. cmd is a default arg so the
            # loop variable isn't resolved late onto the wrong service.
            def check(p=pill, cmd=f"systemctl show {shlex.quote(s)} --property=LoadState,ActiveState", m_ref=m_ref):
                ok, raw = self.core.run_ssh_command(m_ref, cmd)
                label, color = parse_service_state(ok, raw)

                def apply():
                    if self.winfo_exists() and p.winfo_exists():
                        p.configure(text=f"● {label}", text_color=color)

                schedule_on_ui_thread(self._ui_root, apply)

            threading.Thread(target=check, daemon=True).start()

    def run_admin_cmd(self, cmd_data):
        """Run a configured admin command and display the output in a viewer."""
        target_user = self.admin_user if self.admin_user else self.machine.get("user")
        m_ref = self.machine.copy()
        m_ref["user"] = target_user
        
        self.open_viewer(f"Admin: {cmd_data['name']}", cmd_data["cmd"], m_ref)

    def view_log(self):
        """Open the selected log file in the output viewer."""
        selected_path = self.log_dropdown.get()
        log_name = os.path.basename(selected_path)
        cmd = f"cat {shlex.quote(selected_path)}"

        m_ref = self.machine.copy()
        m_ref["user"] = self.admin_user
        self.open_viewer(f"Log: {log_name}", cmd, m_ref)

    def open_viewer(self, title, command, custom_machine=None):
        """Open a searchable text viewer for remote command output or log content."""
        target_machine = custom_machine if custom_machine else self.machine
        
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("900x700")
        win.transient(self)

        # Search state is local to this viewer so multiple open viewers
        # don't interfere with each other.
        search_state = {"indices": [], "current": -1}

        search_frame = ctk.CTkFrame(win, fg_color="transparent")
        search_frame.pack(fill="x", padx=10, pady=5)
        
        search_ent = ctk.CTkEntry(search_frame, placeholder_text="Search (Enter/Space/Down for Next, Up for Prev)", width=400)
        search_ent.pack(side="left", padx=5)
        
        match_count_lbl = ctk.CTkLabel(search_frame, text="0 matches", font=("", 11), text_color="gray")
        match_count_lbl.pack(side="left", padx=10)

        def jump_to_match(direction=1):
            if not search_state["indices"]: return
            search_state["current"] = (search_state["current"] + direction) % len(search_state["indices"])
            pos = search_state["indices"][search_state["current"]]

            txt.tag_remove("current", "1.0", "end")
            end_pos = f"{pos}+{len(search_ent.get())}c"
            txt.tag_add("current", pos, end_pos)
            txt.tag_config("current", foreground="white", background="#3498db")
            txt.see(pos)
            match_count_lbl.configure(text=f"{search_state['current'] + 1} of {len(search_state['indices'])} matches")

        def run_search(event=None):
            """Search the viewer contents and highlight matching text."""
            if event and event.keysym in ["Return", "space", "Down", "Up"]:
                if event.keysym == "Up": jump_to_match(-1)
                else: jump_to_match(1)
                return "break"

            txt.tag_remove("search", "1.0", "end")
            txt.tag_remove("current", "1.0", "end")
            search_state["indices"] = []

            query = search_ent.get().strip()
            if not query:
                match_count_lbl.configure(text="0 matches", text_color="gray")
                return

            start_pos = "1.0"
            while True:
                start_pos = txt.search(query, start_pos, stopindex="end", nocase=True)
                if not start_pos: break
                end_pos = f"{start_pos}+{len(query)}c"
                txt.tag_add("search", start_pos, end_pos)
                search_state["indices"].append(start_pos)
                start_pos = end_pos

            txt.tag_config("search", foreground="black", background="#f1c40f")

            if search_state["indices"]:
                search_state["current"] = 0
                match_count_lbl.configure(text=f"1 of {len(search_state['indices'])} matches", text_color="white")
                jump_to_match(0)
            else:
                match_count_lbl.configure(text="0 matches", text_color="gray")

        search_ent.bind("<KeyRelease>", run_search)
        search_ent.bind("<Return>", lambda e: jump_to_match(1))
        search_ent.bind("<space>", lambda e: jump_to_match(1))
        search_ent.bind("<Down>", lambda e: jump_to_match(1))
        search_ent.bind("<Up>", lambda e: jump_to_match(-1))

        txt = ctk.CTkTextbox(win, font=("Courier New", 12), fg_color="#111111", text_color="white")
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        def fetch():
            ok, res = self.core.run_ssh_command(target_machine, command, timeout=15)
            if not ok:
                res = f"[connection failed]\n{res}"

            def apply():
                if win.winfo_exists():
                    txt.insert("end", res)
                    txt.see("end")

            schedule_on_ui_thread(self._ui_root, apply)

        threading.Thread(target=fetch, daemon=True).start()