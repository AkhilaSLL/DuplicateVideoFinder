"""Dark theme for the ttk widgets used by the app."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

WINDOW = "#15171c"          # root + preview gallery backdrop
SURFACE = "#1d2026"         # cards / frames / tree
ELEV = "#262a32"            # inputs, scrollbar trough
BORDER = "#333843"
FG = "#e6e8eb"
FG_MUTED = "#969ba6"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6ea0ff"
ACCENT_ACTIVE = "#3d78e8"
DANGER = "#e5484d"
DANGER_HOVER = "#f0595e"
SELECT_BG = "#2d4a7a"
CHECKED_FG = "#f0a35e"      # rows queued for deletion

FONT = "Segoe UI"
FONT_BOLD = "Segoe UI Semibold"


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

    style.configure(".", background=SURFACE, foreground=FG,
                    fieldbackground=ELEV, bordercolor=BORDER,
                    lightcolor=SURFACE, darkcolor=SURFACE,
                    focuscolor=ACCENT, font=base_font)

    style.configure("TFrame", background=SURFACE)
    style.configure("Window.TFrame", background=WINDOW)
    style.configure("TLabel", background=SURFACE, foreground=FG)
    style.configure("Window.TLabel", background=WINDOW, foreground=FG)
    style.configure("Muted.TLabel", background=SURFACE, foreground=FG_MUTED)
    style.configure("WindowMuted.TLabel", background=WINDOW,
                    foreground=FG_MUTED, font=small_font)
    style.configure("Title.TLabel", background=WINDOW, foreground=FG,
                    font=(FONT_BOLD, 15))

    style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=SURFACE,
                    foreground=ACCENT, font=(FONT_BOLD, 9))

    # Buttons -------------------------------------------------------------- #
    style.configure("TButton", background=ACCENT, foreground="#ffffff",
                    borderwidth=0, focusthickness=0,
                    padding=(px(14), px(7)), font=(FONT_BOLD, 9),
                    anchor="center")
    style.map("TButton",
              background=[("disabled", ELEV), ("pressed", ACCENT_ACTIVE),
                          ("active", ACCENT_HOVER)],
              foreground=[("disabled", FG_MUTED)])

    style.configure("Secondary.TButton", background=ELEV, foreground=FG)
    style.map("Secondary.TButton",
              background=[("disabled", SURFACE), ("pressed", BORDER),
                          ("active", "#30353f")],
              foreground=[("disabled", FG_MUTED)])

    style.configure("Danger.TButton", background=DANGER, foreground="#ffffff")
    style.map("Danger.TButton",
              background=[("disabled", ELEV), ("pressed", "#c93b40"),
                          ("active", DANGER_HOVER)],
              foreground=[("disabled", FG_MUTED)])

    # Entry / Combobox ----------------------------------------------------- #
    style.configure("TEntry", fieldbackground=ELEV, foreground=FG,
                    bordercolor=BORDER, insertcolor=FG, padding=px(6))
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

    style.configure("TCombobox", fieldbackground=ELEV, background=ELEV,
                    foreground=FG, arrowcolor=FG_MUTED, bordercolor=BORDER,
                    selectbackground=ELEV, selectforeground=FG,
                    padding=px(5))
    style.map("TCombobox",
              fieldbackground=[("readonly", ELEV)],
              foreground=[("readonly", FG)],
              bordercolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", ELEV)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # Checkbutton ---------------------------------------------------------- #
    style.configure("TCheckbutton", background=SURFACE, foreground=FG,
                    focuscolor=SURFACE)
    style.map("TCheckbutton",
              background=[("active", SURFACE)],
              indicatorcolor=[("selected", ACCENT), ("!selected", ELEV)],
              foreground=[("disabled", FG_MUTED)])

    # Treeview ------------------------------------------------------------- #
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                    foreground=FG, rowheight=px(28), borderwidth=0,
                    font=base_font)
    style.map("Treeview",
              background=[("selected", SELECT_BG)],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=ELEV, foreground=FG_MUTED,
                    relief="flat", borderwidth=0,
                    font=(FONT_BOLD, 9), padding=px(6))
    style.map("Treeview.Heading", background=[("active", BORDER)])

    # Scrollbars / scale / progress ---------------------------------------- #
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=ELEV,
                        troughcolor=WINDOW, bordercolor=WINDOW,
                        arrowcolor=FG_MUTED, borderwidth=0, width=px(12))
        style.map(f"{orient}.TScrollbar", background=[("active", BORDER)])

    style.configure("Horizontal.TScale", background=SURFACE, troughcolor=ELEV)
    style.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=ELEV, bordercolor=ELEV, borderwidth=0,
                    thickness=px(6))
    style.configure("TPanedwindow", background=WINDOW)
    style.configure("Sash", sashthickness=px(6), gripcount=0)
