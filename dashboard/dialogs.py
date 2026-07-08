import webbrowser
import customtkinter as ctk
from .ui_helpers import center_window_over_parent

class ConfirmationDialog(ctk.CTkToplevel):
    """Simple centered confirmation dialog with cancel and confirm actions."""

    def __init__(self, master, title, message, confirm_callback, confirm_text="DELETE", color="#e74c3c"):
        """Build and display a confirmation dialog centered over the parent window."""
        super().__init__(master)
        self.title(title)
        self.geometry("300x180")
        self.transient(master)
        self.confirm_callback = confirm_callback

        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() // 2) - 150
        y = master.winfo_y() + (master.winfo_height() // 2) - 90
        self.geometry(f"+{x}+{y}")

        ctk.CTkLabel(self, text="⚠️", font=("", 40)).pack(pady=(15, 5))
        ctk.CTkLabel(self, text=message, font=("", 13, "bold"), wraplength=250).pack(pady=5)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=20)

        ctk.CTkButton(btn_frame, text="CANCEL", width=100, fg_color="#555555", 
                      command=self.destroy).pack(side="left", padx=10)
        
        ctk.CTkButton(btn_frame, text=confirm_text, width=100, fg_color=color, 
                      hover_color="#c0392b" if color == "#e74c3c" else "#25885c",
                      command=self.on_confirm).pack(side="left", padx=10)

    def on_confirm(self):
        """Run the confirm callback and close the dialog."""
        self.confirm_callback()
        self.destroy()

def show_error_dialog(parent, title, message, download_url):
    """Show a centered error dialog with an optional download action."""
    popup = ctk.CTkToplevel(parent)
    popup.title(title)
    popup.transient(parent)

    popup.update_idletasks()
    center_window_over_parent(popup, parent, 350, 180)

    ctk.CTkLabel(popup, text=f"❌ {title}", font=("", 18, "bold"), text_color="#e74c3c").pack(pady=(15, 2))
    ctk.CTkLabel(popup, text=message, font=("", 11), text_color="gray", wraplength=300).pack(pady=(0, 15))
    
    btn_frame = ctk.CTkFrame(popup, fg_color="transparent")
    btn_frame.pack(fill="x", padx=20)

    ctk.CTkButton(btn_frame, text="OK", width=100, fg_color="#555555", 
                    command=popup.destroy).pack(side="left", padx=10)
    
    ctk.CTkButton(btn_frame, text="Download", width=140, fg_color="#3498db", 
                    command=lambda: webbrowser.open(download_url)).pack(side="right", padx=10)