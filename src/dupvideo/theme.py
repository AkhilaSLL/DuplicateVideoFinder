"""
Dark theme for the ttk widgets used by the app.

The palette is measured rather than eyeballed: every foreground/background pair
the app actually ships was checked against WCAG, and two of the results decided
the design.

* **Saturated fills carry dark ink, not white.**  White on the accent blue is
  3.02:1, on the green 2.66:1 - both well under the 4.5:1 a 9pt semibold label
  needs.  Getting white over the line means darkening the fill until it is
  nearly black, which throws away the colour.  Dark ink on the *bright* fill
  passes everywhere (6.45:1 on the accent, 7.30:1 on the green) and keeps the
  hues.  One rule, applied to all three semantic fills.
* **Pressed states brighten, they do not darken.**  ``DANGER_ACTIVE`` was the
  one value that failed the ink check when it followed the usual
  darken-on-press habit, at 3.93:1; it is now only slightly deeper than the
  base red.

Spacing is a 4-unit scale exposed under semantic names.  Everything used to be
padded 6 or 8 pixels regardless of what it was separating, which left every
element on screen weighing the same; the names below exist so that "inside a
control cluster" and "between two sections" cannot accidentally become the
same number again.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

# --- surfaces, darkest to lightest --------------------------------------- #
WINDOW = "#0b0d11"          # app background + gallery backdrop
SURFACE = "#1b1f28"         # cards
SURFACE_HI = "#222732"      # hovered rows
ELEV = "#262c37"            # inputs, list backgrounds, secondary buttons
ELEV_HI = "#2f3642"         # their hover
BORDER = "#313846"          # quiet hairlines between sections
BORDER_HI = "#414a5a"       # emphasised edges, drop zones
# The boundary of a control the user is meant to find and operate has
# to clear 3:1 against what surrounds it, and BORDER does not: it is
# 1.40 against SURFACE, so an unticked checkbox all but disappeared.
# This clears 3.63 on the card and 3.09 on an input.  Decorative rules
# keep BORDER, which carries no meaning and should stay quiet.
CONTROL = "#6b7789"         # checkbox, entry and combobox boundaries

# --- text ----------------------------------------------------------------- #
FG = "#e9ecf1"
FG_DIM = "#aab2bf"          # secondary text that still has to be read
FG_MUTED = "#8b94a3"        # labels, captions, disabled

# --- semantic fills.  All three carry ON_FILL as their ink. --------------- #
ON_FILL = "#0b0d11"
ACCENT = "#5b93f7"
ACCENT_HOVER = "#7dabff"
ACCENT_ACTIVE = "#4a7fe0"
ACCENT_TEXT = "#93b8ff"     # the accent as *text*, which needs to be lighter
DANGER = "#e5484d"
DANGER_HOVER = "#f26a6e"
DANGER_ACTIVE = "#dd4247"
SELECT_BG = "#28406b"
CHECKED_FG = "#f0a35e"      # rows queued for deletion

FONT = "Segoe UI"
FONT_BOLD = "Segoe UI Semibold"

# --- spacing scale, 4-unit, named for what it separates ------------------ #
GAP_TIGHT = 4               # inside one control cluster
GAP = 8                     # between related controls
GAP_WIDE = 16               # between groups of controls on a row
PAD_CARD = 14               # a card's inner padding
GAP_SECTION = 10            # between stacked cards
EDGE = 20                   # window edge


SUPERSAMPLE = 4             # drawn this much larger, then resampled down


def _indicator(size: int, gap: int, fill: str, outline: str | None,
               tick: str | None) -> ImageTk.PhotoImage:
    """
    One checkbox indicator, drawn oversized and resampled for clean edges.

    ``gap`` is transparent padding carried on the right of the box.  The drawn
    element replaces clam's, and ``indicatormargin`` only applies to clam's
    own, so the space between box and label has to live inside the image.
    """
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big + gap * SUPERSAMPLE, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = SUPERSAMPLE // 2
    draw.rounded_rectangle((inset, inset, big - inset - 1, big - inset - 1),
                           radius=big * 0.28, fill=fill, outline=outline,
                           width=SUPERSAMPLE if outline else 0)
    if tick:
        draw.line([(big * 0.27, big * 0.53), (big * 0.43, big * 0.69),
                   (big * 0.75, big * 0.32)],
                  fill=tick, width=max(2, round(big * 0.11)), joint="curve")
    return ImageTk.PhotoImage(image.resize(
        (size + gap, size), Image.Resampling.LANCZOS))


def _install_checkbox_element(root: tk.Tk, style: ttk.Style,
                              size: int, gap: int) -> bool:
    """
    Replace clam's checkbox indicator with a drawn one.

    clam does not draw a tick.  Whatever ``indicatorcolor`` is set to, the
    selected state is a *cross* in a box, which reads as "no" at the exact
    moment it means "yes" - measured across every combination of
    ``indicatorcolor``, ``indicatorbackground``, ``indicatorrelief`` and
    ``indicatorsize``, none of which touch the glyph.  A ttk image element is
    the only way to change the mark itself, so the indicator is drawn here
    with the Pillow that is already a dependency.
    """
    images = {
        "off": _indicator(size, gap, ELEV, CONTROL, None),
        "off_hover": _indicator(size, gap, ELEV_HI, FG_MUTED, None),
        "on": _indicator(size, gap, ACCENT, None, ON_FILL),
        "on_hover": _indicator(size, gap, ACCENT_HOVER, None, ON_FILL),
        "off_disabled": _indicator(size, gap, SURFACE, BORDER_HI, None),
        "on_disabled": _indicator(size, gap, BORDER, None, FG_MUTED),
    }
    # ttk only borrows the images; nothing else holds a reference once this
    # function returns, and a collected PhotoImage draws as an empty box.
    root._checkbox_images = images            # type: ignore[attr-defined]

    try:
        style.element_create(
            "Drawn.Checkbutton.indicator", "image", images["off"],
            ("disabled", "selected", images["on_disabled"]),
            ("disabled", images["off_disabled"]),
            ("pressed", "selected", images["on_hover"]),
            ("active", "selected", images["on_hover"]),
            ("selected", images["on"]),
            ("active", images["off_hover"]),
            border=0, sticky="")
    except tk.TclError:
        return False                          # already installed on this root
    style.layout("TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Drawn.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w",
                                   "children": [
                                       ("Checkbutton.label",
                                        {"sticky": "nswe"})]}),
        ]}),
    ])
    return True


def apply_theme(root: tk.Tk, scale: float = 1.0) -> None:
    """
    Install the dark ttk theme.

    ``scale`` multiplies every pixel-valued measurement so the layout keeps its
    proportions on high-DPI displays.  Font sizes are in points and are scaled
    by Tk itself.
    """
    def px(value: float) -> int:
        return max(1, round(value * scale))

    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=WINDOW)

    base_font = (FONT, 10)
    small_font = (FONT, 9)
    micro_font = (FONT_BOLD, 8)

    style.configure(".", background=SURFACE, foreground=FG,
                    fieldbackground=ELEV, bordercolor=BORDER,
                    lightcolor=SURFACE, darkcolor=SURFACE,
                    focuscolor=ACCENT, font=base_font)

    # --- frames ----------------------------------------------------------- #
    style.configure("TFrame", background=SURFACE)
    style.configure("Window.TFrame", background=WINDOW)
    style.configure("Card.TFrame", background=SURFACE, borderwidth=1,
                    relief="solid", bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER)
    # A hairline: a frame one pixel tall is more predictable across themes
    # than TSeparator, which clam draws with its own 3-D edge colours.
    style.configure("Rule.TFrame", background=BORDER)
    style.configure("Elev.TFrame", background=ELEV)

    # --- text ------------------------------------------------------------- #
    style.configure("TLabel", background=SURFACE, foreground=FG)
    style.configure("Window.TLabel", background=WINDOW, foreground=FG)
    style.configure("Muted.TLabel", background=SURFACE, foreground=FG_MUTED)
    style.configure("Dim.TLabel", background=SURFACE, foreground=FG_DIM)
    style.configure("WindowMuted.TLabel", background=WINDOW,
                    foreground=FG_MUTED, font=small_font)
    style.configure("Title.TLabel", background=WINDOW, foreground=FG,
                    font=(FONT_BOLD, 16))
    style.configure("Tagline.TLabel", background=WINDOW, foreground=FG_MUTED,
                    font=small_font)
    # Section headings sit inside cards.  They used to be accent blue, which
    # spent the one colour that means "primary action" on three static labels.
    style.configure("Legend.TLabel", background=SURFACE, foreground=FG_MUTED,
                    font=micro_font)
    style.configure("Value.TLabel", background=SURFACE, foreground=FG,
                    font=(FONT_BOLD, 10))
    style.configure("Accent.TLabel", background=SURFACE,
                    foreground=ACCENT_TEXT, font=small_font)

    # --- buttons ---------------------------------------------------------- #
    style.configure("TButton", background=ACCENT, foreground=ON_FILL,
                    borderwidth=0, focusthickness=0,
                    padding=(px(16), px(8)), font=(FONT_BOLD, 9),
                    anchor="center")
    style.map("TButton",
              background=[("disabled", ELEV), ("pressed", ACCENT_ACTIVE),
                          ("active", ACCENT_HOVER)],
              foreground=[("disabled", FG_MUTED)])

    style.configure("Secondary.TButton", background=ELEV, foreground=FG)
    style.map("Secondary.TButton",
              background=[("disabled", SURFACE), ("pressed", BORDER),
                          ("active", ELEV_HI)],
              foreground=[("disabled", FG_MUTED)])

    # Ghost: low-stakes repeated actions (select all, clear, remove) that must
    # not compete with the primary button.  Outlined rather than filled - an
    # unfilled ghost still has to read as a control once it is disabled, and a
    # borderless one just reads as a stray caption.
    style.configure("Ghost.TButton", background=SURFACE, foreground=FG_DIM,
                    borderwidth=1, relief="solid", bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER,
                    padding=(px(13), px(7)))
    style.map("Ghost.TButton",
              background=[("disabled", SURFACE), ("pressed", ELEV),
                          ("active", ELEV)],
              foreground=[("disabled", FG_MUTED), ("active", FG)],
              bordercolor=[("disabled", BORDER), ("active", BORDER_HI)],
              lightcolor=[("active", BORDER_HI)],
              darkcolor=[("active", BORDER_HI)])

    style.configure("Danger.TButton", background=DANGER, foreground=ON_FILL)
    style.map("Danger.TButton",
              background=[("disabled", ELEV), ("pressed", DANGER_ACTIVE),
                          ("active", DANGER_HOVER)],
              foreground=[("disabled", FG_MUTED)])

    # --- entry / combobox -------------------------------------------------- #
    style.configure("TEntry", fieldbackground=ELEV, foreground=FG,
                    bordercolor=CONTROL, insertcolor=FG,
                    lightcolor=CONTROL, darkcolor=CONTROL,
                    padding=px(8))
    style.map("TEntry",
              bordercolor=[("focus", ACCENT)],
              lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)])

    style.configure("TCombobox", fieldbackground=ELEV, background=ELEV,
                    foreground=FG, arrowcolor=FG_DIM,
                    bordercolor=CONTROL,
                    lightcolor=CONTROL, darkcolor=CONTROL,
                    selectbackground=ELEV, selectforeground=FG,
                    padding=px(6))
    style.map("TCombobox",
              fieldbackground=[("readonly", ELEV)],
              foreground=[("readonly", FG)],
              arrowcolor=[("active", FG)],
              bordercolor=[("focus", ACCENT), ("active", FG_MUTED)],
              lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", ELEV)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ON_FILL)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # --- checkbutton ------------------------------------------------------- #
    _install_checkbox_element(root, style, px(15), px(GAP))
    style.configure("TCheckbutton", background=SURFACE, foreground=FG_DIM,
                    focuscolor=SURFACE, padding=(0, px(3)))
    style.map("TCheckbutton",
              background=[("active", SURFACE)],
              foreground=[("disabled", FG_MUTED), ("active", FG),
                          ("selected", FG)])

    style.configure("Window.TCheckbutton", background=WINDOW, foreground=FG_DIM,
                    focuscolor=WINDOW, padding=(0, px(3)))
    style.map("Window.TCheckbutton",
              background=[("active", WINDOW)],
              foreground=[("disabled", FG_MUTED), ("active", FG),
                          ("selected", FG)])

    # --- treeview ---------------------------------------------------------- #
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                    foreground=FG, rowheight=px(30), borderwidth=0,
                    font=base_font)
    style.map("Treeview",
              background=[("selected", SELECT_BG)],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=SURFACE_HI,
                    foreground=FG_MUTED, relief="flat", borderwidth=0,
                    font=micro_font, padding=(px(8), px(9)))
    style.map("Treeview.Heading",
              background=[("active", ELEV)],
              foreground=[("active", FG_DIM)])
    style.layout("Treeview.Heading", [
        ("Treeheading.cell", {"sticky": "nswe"}),
        ("Treeheading.padding", {"sticky": "nswe", "children": [
            ("Treeheading.text", {"sticky": "we"})]}),
    ])                       # drops clam's raised border under every heading

    # --- scrollbars / scale / progress ------------------------------------- #
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=BORDER,
                        troughcolor=SURFACE, bordercolor=SURFACE,
                        lightcolor=SURFACE, darkcolor=SURFACE,
                        arrowcolor=FG_MUTED, borderwidth=0, width=px(11))
        style.map(f"{orient}.TScrollbar",
                  background=[("pressed", ACCENT), ("active", BORDER_HI)],
                  arrowcolor=[("active", FG_DIM)])

    style.configure("Horizontal.TScale", background=SURFACE, troughcolor=ELEV,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT,
                    borderwidth=0, sliderthickness=px(16))
    style.map("Horizontal.TScale",
              background=[("active", ACCENT_HOVER)],
              lightcolor=[("active", ACCENT_HOVER)],
              darkcolor=[("active", ACCENT_HOVER)])

    style.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=ELEV, bordercolor=ELEV,
                    lightcolor=ACCENT, darkcolor=ACCENT,
                    borderwidth=0, thickness=px(5))

    style.configure("TPanedwindow", background=WINDOW)
    style.configure("Sash", sashthickness=px(8), gripcount=0)
