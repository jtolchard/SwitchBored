import os
import webbrowser
from pathlib import Path

import customtkinter as ctk

GITHUB_URL = "https://github.com/jtolchard/SwitchBored"
LINKEDIN_URL = "https://www.linkedin.com/in/jamestolchard/"

def _find_icon_file():
    """Return the app icon path, preferring the bundle's Resources copy."""
    candidates = []

    resources = os.environ.get("RESOURCEPATH")
    if resources:
        candidates.append(os.path.join(resources, "icon.icns"))

    project_root = Path(__file__).resolve().parent.parent
    candidates.append(str(project_root / "icon.png"))
    candidates.append(str(project_root / "icon.icns"))

    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def _load_icon_image(size=96):
    """Return the app icon as a CTkImage, or None if it cannot be loaded."""
    path = _find_icon_file()
    if not path:
        return None

    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return None

def show_about_window(app_name, version):
    """Display the About window and block until it is closed.

    Runs in its own process (launched via spawn_self with --about) so the
    Tk main loop never competes with the rumps menu-bar run loop. Both the
    menu-bar About item and the dashboard's application menu open this.
    """
    app = ctk.CTk()
    # Stay hidden until sized and positioned, so the window never flashes
    # at the default top-left location.
    app.withdraw()

    app.title(f"About {app_name}")
    app.resizable(False, False)
    app.attributes("-topmost", True)

    icon_image = _load_icon_image()

    width = 320

    # This window gets its own macOS application menu; point its About item
    # back at itself rather than the standard Cocoa panel.
    try:
        import tkinter as tk
        menubar = tk.Menu(app)
        app_menu = tk.Menu(menubar, name="apple", tearoff=0)
        app_menu.add_command(label=f"About {app_name}", command=app.lift)
        menubar.add_cascade(menu=app_menu)
        app.config(menu=menubar)
    except Exception:
        pass

    if icon_image is not None:
        ctk.CTkLabel(app, image=icon_image, text="").pack(pady=(24, 0))

    # App name, linked to the project page
    title_link = ctk.CTkLabel(app, text=app_name, font=("", 22, "bold"),
                              text_color="#3498db", cursor="pointinghand")
    title_link.pack(pady=(8, 0))
    title_link.bind("<Button-1>", lambda e: webbrowser.open(GITHUB_URL))

    ctk.CTkLabel(app, text=f"v{version}", font=("", 14), text_color="gray").pack(pady=(0, 12))

    # Author, linked to LinkedIn
    link = ctk.CTkLabel(app, text="James Tolchard", text_color="#3498db",
                        cursor="pointinghand", font=("", 14, "underline"))
    link.pack(pady=(0, 4))
    link.bind("<Button-1>", lambda e: webbrowser.open(LINKEDIN_URL))

    # Bottom padding mirrors the 24px above the icon.
    ctk.CTkLabel(app, text="© 2026 | MIT License", font=("", 10), text_color="gray").pack(pady=(6, 24))

    # Size the window to its content so the layout stays symmetric, then
    # center it on screen.
    app.update_idletasks()
    height = app.winfo_reqheight()
    x = (app.winfo_screenwidth() // 2) - (width // 2)
    y = (app.winfo_screenheight() // 2) - (height // 2)
    app.geometry(f"{width}x{height}+{x}+{y}")

    app.deiconify()
    app.mainloop()
