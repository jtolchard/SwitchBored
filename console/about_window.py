import sys
import webbrowser

import customtkinter as ctk

from console.bootstrap import find_app_icon

GITHUB_URL = "https://github.com/jtolchard/SwitchBored"
LINKEDIN_URL = "https://www.linkedin.com/in/jamestolchard/"

def _load_icon_image(size=96):
    """Return the app icon as a CTkImage, or None if it cannot be loaded."""
    path = find_app_icon()
    if not path:
        return None

    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return None

def _bring_to_current_desktop(title):
    """Move the window to the desktop (Space) the user is on, and activate it.

    This window belongs to a short-lived background process that never
    activated itself, so macOS could place it on the desktop where
    SwitchBored was last active and leave the user where they were: the
    window opened, just out of sight.

    Call after the window is shown: Tk resets the window's collection
    behaviour when it maps it. With MoveToActiveSpace set, activating the
    window pulls it onto the current desktop instead of switching desktops.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSWindowCollectionBehaviorMoveToActiveSpace
        app = NSApplication.sharedApplication()
        for window in app.windows():
            if window.title() == title:
                window.setCollectionBehavior_(
                    window.collectionBehavior() | NSWindowCollectionBehaviorMoveToActiveSpace
                )
                window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)
    except Exception:
        pass

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

    # Show it invisibly first, so it is never seen on the wrong desktop,
    # then move it to the user's desktop, in front, before revealing it.
    app.attributes("-alpha", 0)
    app.deiconify()
    app.update_idletasks()
    _bring_to_current_desktop(f"About {app_name}")
    app.lift()
    app.focus_force()
    app.attributes("-alpha", 1)
    app.mainloop()
