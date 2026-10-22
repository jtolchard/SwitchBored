"""Multi-display helpers that keep windows where the user can reach them.

Tk on macOS only reports the primary display's size (winfo_screenwidth /
winfo_screenheight), so a position saved while a window sat on an external
monitor can point at empty space once that monitor is unplugged. When that
happens to a visible window, macOS leaves only a ~40px sliver of it on the
remaining display, and raising or focusing it does not bring it back.

These helpers ask AppKit for every connected display's usable area (minus
the menu bar and Dock) and convert it to Tk's coordinates: origin at the
top-left of the primary display, y increasing downwards.

Rects use Tk geometry semantics: (x, y, width, height), where x/y is the
window frame's top-left corner and width/height is the content size, which
excludes the title bar.
"""

# Height of a standard macOS title bar, in points.
TITLE_BAR_HEIGHT = 28

# How much of a window's title bar must be on a display for the user to be
# able to grab it and drag it.
MIN_GRAB_WIDTH = 120


def visible_screen_rects(widget=None):
    """Return the usable area of each connected display as (x, y, w, h).

    The primary display comes first. If AppKit is unavailable, falls back
    to Tk's idea of the primary display when a widget is given.
    """
    try:
        from AppKit import NSScreen

        screens = NSScreen.screens()
        if screens:
            # Cocoa puts the origin at the primary display's bottom-left
            # with y pointing up; flip it into Tk's top-left, y-down space.
            primary_height = screens[0].frame().size.height
            rects = []
            for screen in screens:
                f = screen.visibleFrame()
                rects.append((
                    int(round(f.origin.x)),
                    int(round(primary_height - (f.origin.y + f.size.height))),
                    int(round(f.size.width)),
                    int(round(f.size.height)),
                ))
            return rects
    except Exception:
        pass

    if widget is not None:
        try:
            return [(0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight())]
        except Exception:
            pass
    return []


def _outer(rect):
    """Return the rect including the title bar."""
    x, y, w, h = rect
    return x, y, w, h + TITLE_BAR_HEIGHT


def _overlap_area(a, b):
    """Return the area shared by two (x, y, w, h) rects."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return w * h if w > 0 and h > 0 else 0


def is_reachable(rect, screens):
    """Return True if enough of the title bar is on a display to drag it."""
    x, y, w, _ = rect
    for sx, sy, sw, sh in screens:
        grab_width = min(x + w, sx + sw) - max(x, sx)
        on_display_vertically = y < sy + sh and y + TITLE_BAR_HEIGHT > sy
        if on_display_vertically and grab_width >= min(MIN_GRAB_WIDTH, w):
            return True
    return False


def best_screen(rect, screens):
    """Return the display showing most of rect, or the primary display."""
    if not screens:
        return None
    outer = _outer(rect)
    best = max(screens, key=lambda s: _overlap_area(outer, s))
    return best if _overlap_area(outer, best) > 0 else screens[0]


def clamp_to_screen(rect, screen):
    """Return rect moved (and shrunk if needed) to fit entirely on screen."""
    x, y, w, h = rect
    sx, sy, sw, sh = screen
    w = min(w, sw)
    h = min(h, sh - TITLE_BAR_HEIGHT)
    x = max(sx, min(x, sx + sw - w))
    y = max(sy, min(y, sy + sh - TITLE_BAR_HEIGHT - h))
    return x, y, w, h


def fit_to_screens(rect, screens):
    """Return rect unchanged if reachable, else pulled onto the best display.

    Reachable windows keep their position, since straddling two displays
    is a legitimate choice.
    """
    if not screens or is_reachable(rect, screens):
        return rect
    return clamp_to_screen(rect, best_screen(rect, screens))


def window_rect(window):
    """Return a window's current (x, y, width, height)."""
    window.update_idletasks()
    return (window.winfo_x(), window.winfo_y(),
            window.winfo_width(), window.winfo_height())


def screen_of(window):
    """Return the usable area of the display showing most of window."""
    screens = visible_screen_rects(window)
    if not screens:
        return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()
    return best_screen(window_rect(window), screens)


def ensure_window_on_screen(window):
    """Move a window back onto a connected display if it is out of reach.

    Returns True if the window was moved.
    """
    rect = window_rect(window)
    fitted = fit_to_screens(rect, visible_screen_rects(window))
    if fitted == rect:
        return False
    x, y, w, h = fitted
    window.geometry(f"{w}x{h}+{x}+{y}")
    return True


def place_window(window, width, height, x, y, anchor=None):
    """Apply a geometry, clamped to fit on the anchor window's display.

    With an anchor (usually the parent), the window opens on the same
    display as that window instead of wherever x/y happens to land.
    Returns the (x, y, width, height) actually applied.
    """
    rect = (int(x), int(y), int(width), int(height))
    screens = visible_screen_rects(window)
    if screens:
        reference = window_rect(anchor) if anchor is not None else rect
        rect = clamp_to_screen(rect, best_screen(reference, screens))
    x, y, w, h = rect
    window.geometry(f"{w}x{h}+{x}+{y}")
    return rect
