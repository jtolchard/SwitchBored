def extract_ordered_tags(machines):
    """Return unique machine tags in first-seen order."""
    ordered_tags = []
    for machine in machines:
        raw_tag = machine.get("tag", "")
        if raw_tag:
            individual_tags = [t.strip() for t in raw_tag.split(",") if t.strip()]
            for tag in individual_tags:
                if tag not in ordered_tags:
                    ordered_tags.append(tag)
    return ordered_tags

def get_machine_tags(machine):
    """Return a cleaned list of tags for a single machine."""
    raw_tags = machine.get("tag", "")
    return [t.strip() for t in raw_tags.split(",") if t.strip()]

def calculate_name_max_chars(container_width, reserved_width=380, min_chars=12, px_per_char=8.5):
    """Estimate label capacity in characters, or return None if the container is too narrow."""
    if container_width < 300:
        return None
    available_width = container_width - reserved_width
    return max(min_chars, int(available_width / px_per_char))

def calculate_option_menu_width(values, min_width=120, px_per_char=8, padding=40):
    """Return a width large enough to display the longest option."""
    if not values:
        return min_width
    longest = max(len(str(v)) for v in values)
    return max(min_width, longest * px_per_char + padding)

def find_ui_root(widget):
    """Walk up the master chain to the window that owns the UI update queue."""
    w = widget
    while w is not None:
        if hasattr(w, "ui_call"):
            return w
        w = getattr(w, "master", None)
    return widget

def schedule_on_ui_thread(ui_root, fn):
    """Run fn on the Tk main thread. Safe to call from background threads."""
    ui_call = getattr(ui_root, "ui_call", None)
    if callable(ui_call):
        ui_call(fn)
    else:
        try:
            ui_root.after(0, fn)
        except Exception:
            pass

def center_window_over_parent(window, parent, width, height):
    """Center a toplevel window over its parent and apply the target size."""
    parent.update_idletasks()
    x = parent.winfo_x() + (parent.winfo_width() // 2) - (width // 2)
    y = parent.winfo_y() + (parent.winfo_height() // 2) - (height // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")

MAX_DEBUG_LINES = 2000

# Dashboard button and connection color styles.
CONNECTION_COLORS = {
    "SSH":  {"fg": "#2980b9", "hover": "#1f618d"},
    "TMUX": {"fg": "#16a085", "hover": "#117a65"},
    "SFTP": {"fg": "#cd9555", "hover": "#7b7034"},
    "VNC":  {"fg": "#5d6d7e", "hover": "#34495e"},
}

DETAILS_BUTTON_STYLE = {
    "fg": "#34495e",
    "hover": "#2c3e50",
}

DISABLED_CONNECTION_BUTTON_STYLE = {
    "fg": "#2b2b2b",
    "text": "#555555",
}

# Main dashboard window layout defaults.
DASHBOARD_LAYOUT = {
    "default_width": 650,
    "default_x": 20,
    "default_y": 50,
    "initial_x": 200,
    "initial_y": 50,
    "initial_height": 450,
    "min_height": 200,
    "base_chrome_height": 100,
    "row_height": 42,
    "max_screen_fraction": 0.9,
}