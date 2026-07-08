import customtkinter as ctk

class AdminCommandEditor(ctk.CTkToplevel):
    """Dialog for defining custom remote administrative commands."""

    def __init__(self, parent, current_cmds, callback):
        """Build the editor window and populate any existing commands."""
        super().__init__(parent)
        self.title("Remote Admin Commands")
        self.geometry("520x280")
        self.transient(parent)
        self.callback = callback
        
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - 260
        y = parent.winfo_y() + (parent.winfo_height() // 2) - 150
        self.geometry(f"+{x}+{y}")

        self.bind("<Escape>", lambda e: self.destroy())

        ctk.CTkLabel(self, text="Define Custom Remote Actions", font=("", 14, "bold")).pack(pady=(15, 10))
        
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.pack(fill="x", expand=False, pady=0)
        
        self.main_view = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.main_view.pack(anchor="n", pady=0)

        h_row = ctk.CTkFrame(self.main_view, fg_color="transparent")
        h_row.pack(fill="x", pady=(0, 2))
        
        ctk.CTkLabel(h_row, text="Display Name", width=150, font=("", 12), anchor="w").pack(side="left", padx=5)
        ctk.CTkLabel(h_row, text="Bash/Shell Command", width=310, font=("", 12), anchor="w").pack(side="left", padx=5)

        self.rows = []
        for i in range(4):
            r = ctk.CTkFrame(self.main_view, fg_color="transparent")
            r.pack(fill="x", pady=2)
            existing = current_cmds[i] if i < len(current_cmds) else {"name": "", "cmd": ""}
            
            n_e = ctk.CTkEntry(r, width=150, placeholder_text="e.g. Reboot")
            n_e.insert(0, existing.get("name", ""))
            n_e.pack(side="left", padx=5)
            
            c_e = ctk.CTkEntry(r, width=310, placeholder_text="e.g. sudo systemctl restart nginx")
            c_e.insert(0, existing.get("cmd", ""))
            c_e.pack(side="left", padx=5)
            self.rows.append((n_e, c_e))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="top", pady=(20, 25))
        
        ctk.CTkButton(footer, text="Cancel", fg_color="#555555", width=110, 
                      command=self.destroy).pack(side="left", padx=10)
        ctk.CTkButton(footer, text="Apply Changes", fg_color="#2fa572", width=110, 
                      command=self.apply).pack(side="left", padx=10)

    def apply(self):
        """Collect the completed rows and hand them back to the caller."""
        new_list = []
        for n_e, c_e in self.rows:
            name, cmd = n_e.get().strip(), c_e.get().strip()
            if name and cmd:
                new_list.append({"name": name, "cmd": cmd})
        self.callback(new_list)
        self.destroy()