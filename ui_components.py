import os
import shutil
import importlib
import copy
import json
import customtkinter as ctk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox
import tkinter as tk

class ToolTip:
    """Simple hover tooltip for Tkinter or CustomTkinter widgets."""

    def __init__(self, widget, text):
        """Bind a tooltip to a widget."""
        self.widget = widget
        self.text = text
        self.tw = None
        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        """Display the tooltip near the mouse pointer."""
        if not self.text:
            return

        # A second <Enter> without a <Leave> would otherwise leak a toplevel.
        self.hide()

        try:
            x = event.x_root + 15
            y = event.y_root + 10
            self.tw = tk.Toplevel(self.widget)
            self.tw.wm_overrideredirect(True)
            # Give the window macOS's native tooltip level so it floats
            # above every app window; plain -topmost is unreliable for
            # override-redirect windows on aqua.
            try:
                self.tw.tk.call(
                    "::tk::unsupported::MacWindowStyle",
                    "style", self.tw._w, "help", "noActivates"
                )
            except tk.TclError:
                self.tw.attributes("-topmost", True)
            self.tw.geometry(f"+{x}+{y}")
            label = tk.Label(self.tw, text=self.text, background="#333333", foreground="white",
                             relief="solid", borderwidth=1, padx=5, pady=3)
            label.pack()
        except tk.TclError:
            # Parent widget destroyed while hovering; nothing to show.
            self.tw = None

    def hide(self, event=None):
        """Hide the tooltip if it is currently visible."""
        if self.tw:
            try:
                self.tw.destroy()
            except tk.TclError:
                pass
            self.tw = None

class EmojiPicker(ctk.CTkToplevel):
    """Popup window that lets the user choose an emoji icon."""

    def __init__(self, parent, on_emoji_selected_callback):
        """Create the emoji picker and bind the selection callback."""
        super().__init__(parent)
        self.on_emoji_selected = on_emoji_selected_callback
        
        self.title("Select Icon")
        self.geometry("340x360")
        self.transient(parent)
        self.resizable(False, False)

        # Extended, categorized emoji list for sysadmin/dashboard usage
        emojis = [
            # Tech & Devices
            "💻", "🖥️", "🛡️", "⚙️", "📡", "🚀", "💾", "🔌", "🔒", "🔑", "📊", "☁️", 
            "📱", "⌚", "🖨️", "🖱️", "🔋", "💽", "🌐", "🔍", "🕸️", "🤖","🧲",
            # OS & Brands
            "🐧", "🍎", "🪟", 
            # Status & Indicators
            "🟢", "🟡", "🔴", "✅", "❌", "⚠️", "🔥", "❄️", "⚡", "💤", "⏱️","🔁","☠️",
            # Tools & Misc
            "🛠️", "💡", "📦", "📚", "📋", "📁", "📂", "🗑️", "🔨","⛽️", "🔧", "🪛", "🎈","🧠"
        ]

        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_frame.pack(expand=True, fill="both", padx=10, pady=10)

        columns = 6
        for i, emoji in enumerate(emojis):
            btn = ctk.CTkButton(
                self.scroll_frame, 
                text=emoji, 
                width=42, 
                height=42, 
                font=("", 24),
                fg_color="transparent", 
                hover_color=("#e0e0e0", "#333333"),
                text_color=("#000000", "#FFFFFF"),
                command=lambda e=emoji: self._select(e)
            )
            btn.grid(row=i // columns, column=i % columns, padx=2, pady=4)

    def _select(self, emoji):
        """Return the selected emoji to the caller and close the picker."""
        self.on_emoji_selected(emoji)
        self.destroy()

class PluginManagerWidget(ctk.CTkFrame):
    """Widget for installing, enabling, inspecting, and removing plugins."""

    def __init__(self, parent, core, *args, **kwargs):
        """Initialize the plugin manager with a temporary editable settings copy."""
        super().__init__(parent, *args, **kwargs)
        self.core = core

        try:
            self._plugin_font = tkfont.nametofont("TkDefaultFont")
        except Exception:
            self._plugin_font = tkfont.Font(family="Arial", size=13)

        self._inspector_title_font = tkfont.Font(size=16, weight="bold")

        self.temp_settings = copy.deepcopy(
            self.core.settings.get("plugins", {"enabled": [], "config": {}})
        )
        self.temp_settings.setdefault("enabled", [])
        self.temp_settings.setdefault("config", {})

        self.selected_plugin = None

        self._build_ui()
        self.refresh_plugin_list()

    def _build_ui(self):
        """Build the two-pane plugin manager interface."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # ------------------------------------------------------------------
        # LEFT PANE
        # ------------------------------------------------------------------
        self.left_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self.plugin_list_frame = ctk.CTkScrollableFrame(
            self.left_frame,
            label_text="Installed Plugins",
            label_fg_color="#34495e",
            label_text_color="white"
        )
        self.plugin_list_frame.pack(expand=True, fill="both", pady=(0, 10))

        self.install_btn = ctk.CTkButton(
            self.left_frame,
            text="+ Install Plugin (.py)",
            fg_color="#d48806",
            hover_color="#b57305",
            command=self.install_new_plugin
        )
        self.install_btn.pack(fill="x")

        # ------------------------------------------------------------------
        # RIGHT PANE
        # ------------------------------------------------------------------
        self.right_frame = ctk.CTkFrame(self)
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.right_frame.grid_rowconfigure(2, weight=1)
        self.right_frame.grid_columnconfigure(0, weight=1)

        self.inspector_header = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.inspector_header.pack(fill="x", pady=(15, 5), padx=10)

        self.inspector_header.grid_columnconfigure(0, weight=0)
        self.inspector_header.grid_columnconfigure(1, weight=1)

        self.inspector_prefix = ctk.CTkLabel(
            self.inspector_header,
            text="Plugin Settings",
            font=("", 16, "bold"),
            anchor="w"
        )
        self.inspector_prefix.grid(row=0, column=0, sticky="w")

        self.inspector_name = ctk.CTkLabel(
            self.inspector_header,
            text="",
            font=("", 16, "bold"),
            anchor="w"
        )
        self.inspector_name.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self.inspector_name.bind(
            "<Configure>",
            lambda event: self._update_inspector_name(self.selected_plugin or "")
        )

        self.inspector_desc = ctk.CTkLabel(
            self.right_frame,
            text="Click the ⚙️ button next to a plugin on the left to view or edit its configuration.",
            text_color="gray",
            wraplength=300,
            justify="left",
            anchor="w"
        )
        self.inspector_desc.pack(fill="x", pady=(0, 10), padx=10)

        self.config_textbox = ctk.CTkTextbox(self.right_frame, height=150)
        self.config_textbox.pack(expand=True, fill="both", padx=10, pady=(0, 10))
        self.config_textbox.bind("<KeyRelease>", self._on_config_edited)

        self.remove_btn = ctk.CTkButton(
            self.right_frame,
            text="Remove Plugin",
            fg_color="#c0392b",
            hover_color="#e74c3c",
            command=self.remove_selected_plugin
        )

    def _bundled_plugins_dir(self):
        """Return the plugins directory shipped with the app, if it exists on disk."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
        return path if os.path.isdir(path) else None

    def _discover_plugins(self):
        """Return {name: is_user_installed} for every available plugin.

        Bundled plugins ship inside the app; user-installed ones live in
        Application Support and take precedence on a name clash.
        """
        found = {}

        bundled = self._bundled_plugins_dir()
        if bundled:
            for f in os.listdir(bundled):
                if f.endswith(".py") and f != "__init__.py":
                    found[f[:-3]] = False

        for f in os.listdir(self.core.plugins_dir()):
            if f.endswith(".py"):
                found[f[:-3]] = True

        return found

    def refresh_plugin_list(self):
        """Rebuild the plugin list from the bundled and user plugin directories."""
        for widget in self.plugin_list_frame.winfo_children():
            widget.destroy()

        available_plugins = sorted(self._discover_plugins())

        self.core.log("PLUGINS", f"Discovered plugins: {', '.join(available_plugins) or 'none'}")

        for p_name in available_plugins:
            row = ctk.CTkFrame(self.plugin_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2, padx=2)

            row.grid_columnconfigure(0, weight=0)
            row.grid_columnconfigure(1, weight=1)
            row.grid_columnconfigure(2, weight=0)

            switch = ctk.CTkSwitch(
                row,
                text="",
                width=40,
                command=lambda n=p_name: self._toggle_plugin(n)
            )
            if p_name in self.temp_settings["enabled"]:
                switch.select()
            switch.grid(row=0, column=0, padx=(5, 4), pady=5, sticky="w")

            name_label = ctk.CTkLabel(row, text=p_name, anchor="w")
            name_label.grid(row=0, column=1, padx=(0, 4), pady=5, sticky="ew")

            cog_btn = ctk.CTkButton(
                row,
                text="⚙️",
                width=30,
                fg_color="transparent",
                hover_color=("#e8e8e8", "#2f2f2f"),
                command=lambda n=p_name: self.inspect_plugin(n)
            )
            cog_btn.grid(row=0, column=2, padx=(0, 5), pady=5, sticky="e")

            ToolTip(cog_btn, f"Configure {p_name}")

            row.bind(
                "<Configure>",
                lambda event, label=name_label, full_text=p_name:
                    self._update_plugin_label(event, label, full_text)
            )

            row.after(
                10,
                lambda label=name_label, full_text=p_name:
                    self._update_plugin_label(None, label, full_text)
            )

    def _truncate_text_to_pixel_width(self, text, max_width_px, font=None):
        """Truncate text with an ellipsis so it fits within the given pixel width."""
        if max_width_px <= 1:
            return text

        font = font or self._plugin_font

        if font.measure(text) <= max_width_px:
            return text

        ellipsis = "..."
        if font.measure(ellipsis) > max_width_px:
            return ellipsis

        truncated = text
        while truncated:
            candidate = truncated + ellipsis
            if font.measure(candidate) <= max_width_px:
                return candidate
            truncated = truncated[:-1]

        return ellipsis

    def _truncate_right_title(self, text, max_width_px):
        """Truncate the inspector title text to fit the available pixel width."""
        return self._truncate_text_to_pixel_width(
            text, max_width_px, font=self._inspector_title_font
        )

    def _update_plugin_label(self, event, label, full_text):
        """Resize a plugin label so it fits the current row width."""
        width_px = label.winfo_width()
        if width_px <= 1:
            return
        label.configure(text=self._truncate_text_to_pixel_width(full_text, width_px))

    def _update_inspector_name(self, full_name):
        """Update the inspector title, truncating it to the available width."""
        if not full_name:
            self.inspector_name.configure(text="")
            return

        try:
            available = self.inspector_name.winfo_width() - 8
            if available <= 1:
                self.inspector_name.configure(text=full_name)
                return

            truncated = self._truncate_right_title(full_name, available)
            self.inspector_name.configure(text=truncated)
        except Exception:
            self.inspector_name.configure(text=full_name)

    def inspect_plugin(self, name):
        """Load a plugin's configuration into the inspector panel."""
        self.selected_plugin = name
        self.core.log("PLUGINS", f"Inspecting plugin configuration: {name}")

        self.inspector_prefix.configure(text="Configuring:")
        self._update_inspector_name(name)
        self.inspector_desc.configure(
            text="Edit the JSON configuration below. Changes are stored in the temporary settings until you save."
        )

        self.remove_btn.pack_forget()
        self.remove_btn.pack(pady=10, padx=10, side="bottom", anchor="e")

        config_data = self._ensure_plugin_config(name)

        self.config_textbox.delete("1.0", "end")
        self.config_textbox.insert("1.0", json.dumps(config_data, indent=4))

        self.inspector_name.after(10, lambda: self._update_inspector_name(name))

    def remove_selected_plugin(self):
        """Delete the currently selected plugin and clear it from temporary settings."""

        if not self.selected_plugin:
            return

        file_path = os.path.join(self.core.plugins_dir(), f"{self.selected_plugin}.py")
        if not os.path.exists(file_path):
            messagebox.showinfo(
                "Bundled Plugin",
                f"'{self.selected_plugin}' ships with the app and cannot be deleted.\n"
                "Disable it with its toggle instead."
            )
            return

        if messagebox.askyesno("Confirm Deletion", f"Permanently delete '{self.selected_plugin}'?"):
            try:
                os.remove(file_path)

                if self.selected_plugin in self.temp_settings["enabled"]:
                    self.temp_settings["enabled"].remove(self.selected_plugin)

                self.temp_settings["config"].pop(self.selected_plugin, None)

                self.selected_plugin = None
                self.inspector_prefix.configure(text="Plugin Settings")
                self.inspector_name.configure(text="")
                self.inspector_desc.configure(
                    text="Click the ⚙️ button next to a plugin on the left to view or edit its configuration."
                )
                self.config_textbox.delete("1.0", "end")
                self.remove_btn.pack_forget()
                self.refresh_plugin_list()

            except Exception as e:
                messagebox.showerror("Error", f"Delete failed: {e}")

    def _toggle_plugin(self, name):
        """Enable or disable a plugin in the temporary settings."""
        if name in self.temp_settings["enabled"]:
            self.temp_settings["enabled"].remove(name)
        else:
            self.temp_settings["enabled"].append(name)

    def _on_config_edited(self, event):
        """Validate edited JSON and update temporary config when valid."""
        if not self.selected_plugin:
            return

        try:
            parsed = json.loads(self.config_textbox.get("1.0", "end").strip())
            self.temp_settings["config"][self.selected_plugin] = parsed
            self.config_textbox.configure(text_color=["black", "white"])
        except Exception:
            self.config_textbox.configure(text_color="#e74c3c")

    def install_new_plugin(self):
        """Install a plugin file into the plugins directory and open it in the inspector."""
        file_path = filedialog.askopenfilename(filetypes=[("Python Files", "*.py")])
        if not file_path:
            return

        try:
            dest = os.path.join(self.core.plugins_dir(), os.path.basename(file_path))
            shutil.copy(file_path, dest)

            importlib.invalidate_caches()

            p_name = os.path.basename(file_path).replace(".py", "")
            if p_name not in self.temp_settings["enabled"]:
                self.temp_settings["enabled"].append(p_name)

            self._ensure_plugin_config(p_name)

            self.refresh_plugin_list()
            self.inspect_plugin(p_name)

        except Exception as e:
            messagebox.showerror("Error", f"Install failed: {e}")

    def get_plugin_data_for_saving(self):
        """Return the temporary plugin settings for persistence."""
        return self.temp_settings
    
    def _get_plugin_module(self, plugin_name):
        """Import and return a plugin module, or None if it cannot be loaded."""
        try:
            return self.core._import_plugin_module(plugin_name)
        except Exception:
            return None
        
    def _get_default_config_for_plugin(self, plugin_name):
        """Extract default config values from a plugin's settings schema."""
        module = self._get_plugin_module(plugin_name)
        if not module:
            return {}

        schema_fn = getattr(module, "get_settings_schema", None)
        if not callable(schema_fn):
            return {}

        try:
            schema = schema_fn()
        except Exception:
            return {}

        defaults = {}
        for key, spec in schema.items():
            if isinstance(spec, dict) and "default" in spec:
                defaults[key] = copy.deepcopy(spec["default"])
        return defaults
    
    def _ensure_plugin_config(self, plugin_name):
        """Ensure a plugin has a config dict, seeded with schema defaults."""
        defaults = self._get_default_config_for_plugin(plugin_name)
        existing = self.temp_settings["config"].get(plugin_name, {})

        if not isinstance(existing, dict):
            existing = {}

        merged = copy.deepcopy(defaults)
        merged.update(existing)

        self.temp_settings["config"][plugin_name] = merged
        return merged