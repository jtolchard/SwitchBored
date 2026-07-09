"""Rebuild hero.png from dashboard.png and menubar.png.

Run from this directory after updating either screenshot:
    python3 make_hero.py
"""

from PIL import Image

dash = Image.open("dashboard.png").convert("RGBA")
menu = Image.open("menubar.png").convert("RGBA")

# Dashboard keeps prominence on the left; the menu bar shot fills the right
# column, vertically centered.
H = 550
dash_w = int(dash.width * H / dash.height)
dash_r = dash.resize((dash_w, H), Image.LANCZOS)

menu_w = 600
menu_h = int(menu.height * menu_w / menu.width)
menu_r = menu.resize((menu_w, menu_h), Image.LANCZOS)

gap = 44
canvas = Image.new("RGBA", (dash_w + gap + menu_w, H), (0, 0, 0, 0))
canvas.paste(dash_r, (0, 0), dash_r)
canvas.paste(menu_r, (dash_w + gap, (H - menu_h) // 2), menu_r)
canvas.save("hero.png")
print(f"Wrote hero.png ({canvas.width}x{canvas.height})")
