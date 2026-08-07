"""
The folder list, its empty state, and the buttons that fill it.

The empty state and the drop target are deliberately the same object.  Windows
gives a ``WM_DROPFILES`` window no drag-over notification at all - there is no
"enter" or "leave", only the drop itself - so a zone that lights up under the
cursor is not available without reimplementing ``IDropTarget`` in COM.  The
answer is to make the affordance permanent instead of reactive: when the list
is empty it *is* a drop zone, drawn and labelled as one, and it is also the
biggest click target for the Add folder dialog.  A user who never drags
anything still gets a first-run panel that says what to do, which the bare
three-row listbox it replaces did not.
"""

from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, ttk

from .dnd import register_drop_target
from .theme import (
    ACCENT,
    BORDER_HI,
    CONTROL,
    ELEV,
    FG,
    FG_DIM,
    FG_MUTED,
    FONT,
    FONT_BOLD,
    GAP,
    GAP_TIGHT,
    SURFACE,
)


class FolderPanel(ttk.Frame):
    """A list of folders to work on, with drag-and-drop and an empty state."""

    def __init__(self, master: tk.Misc, scale: float = 1.0, *,
                 on_open: Callable[[str], None] | None = None,
                 on_change: Callable[[], None] | None = None,
                 add_text: str = "Add folder…",
                 dialog_title: str = "Add a folder to scan",
                 empty_hint: str = "Drop folders of images here") -> None:
        super().__init__(master, style="TFrame")
        self._scale = scale
        self._on_open = on_open
        self._on_change = on_change
        self._dialog_title = dialog_title
        self._empty_hint = empty_hint

        self.columnconfigure(0, weight=1)
        # Pin the row height so the card does not jump when the empty state
        # gives way to the list.  Without it the bare Canvas asks for its Tk
        # default of 265px and the drop zone swallows the window.
        self.rowconfigure(0, weight=1, minsize=self._px(104))

        # --- the list, and the empty state that stands in for it ---------- #
        self._listing = ttk.Frame(self, style="TFrame")
        self._listing.grid(row=0, column=0, sticky="nsew")
        self._listing.rowconfigure(0, weight=1)
        self._listing.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(
            self._listing, height=4, selectmode="extended", activestyle="none",
            bg=ELEV, fg=FG, bd=0, highlightthickness=1,
            highlightbackground=CONTROL, highlightcolor=ACCENT,
            selectbackground=ACCENT, selectforeground=SURFACE,
            exportselection=False, font=(FONT, 10))
        scroll = ttk.Scrollbar(self._listing, orient="vertical",
                               command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.bind("<Double-1>", self._on_double)
        self.listbox.bind("<Delete>", lambda _e: self.remove_selected())
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._sync_buttons())

        self._empty = tk.Canvas(self, bg=SURFACE, bd=0, highlightthickness=0,
                                width=1, height=self._px(104), cursor="hand2")
        self._empty.grid(row=0, column=0, sticky="nsew")
        self._empty.bind("<Configure>", lambda _e: self._draw_empty())
        self._empty.bind("<Button-1>", lambda _e: self.add_folder())

        # --- buttons ------------------------------------------------------ #
        buttons = ttk.Frame(self, style="TFrame")
        buttons.grid(row=0, column=1, sticky="n", padx=(self._px(GAP), 0))
        self.add_btn = ttk.Button(buttons, text=add_text, width=14,
                                  style="Secondary.TButton",
                                  command=self.add_folder)
        self.add_btn.pack(fill="x")
        self.remove_btn = ttk.Button(buttons, text="Remove", width=14,
                                     style="Ghost.TButton", state="disabled",
                                     command=self.remove_selected)
        self.remove_btn.pack(fill="x", pady=(self._px(GAP_TIGHT), 0))

        # Arming has to come last: register_drop_target only covers the
        # widgets that exist when it runs.
        self.drop_enabled = register_drop_target(self, self._on_dropped)
        self._sync_empty()

    # ---- geometry ---------------------------------------------------- #
    def _px(self, value: float) -> int:
        return max(1, round(value * self._scale))

    # ---- contents ----------------------------------------------------- #
    def folders(self) -> list[str]:
        return list(self.listbox.get(0, "end"))

    def add(self, folders: list[str]) -> int:
        """Append folders that are not already listed.  Returns how many."""
        existing = {os.path.normcase(f) for f in self.folders()}
        added = 0
        for folder in folders:
            key = os.path.normcase(folder)
            if key in existing:
                continue
            existing.add(key)
            self.listbox.insert("end", folder)
            added += 1
        if added:
            self.listbox.see("end")
            self._changed()
        return added

    def add_folder(self) -> None:
        existing = self.folders()
        chosen = filedialog.askdirectory(
            title=self._dialog_title,
            initialdir=existing[-1] if existing else None)
        if chosen:
            self.add([os.path.normpath(chosen)])

    def remove_selected(self) -> None:
        if not self.listbox.curselection():
            return
        for index in reversed(self.listbox.curselection()):
            self.listbox.delete(index)
        self._changed()

    def _on_dropped(self, folders: list[str]) -> None:
        self.add(folders)

    def _on_double(self, _event: tk.Event) -> None:
        selection = self.listbox.curselection()
        if selection and self._on_open is not None:
            self._on_open(self.listbox.get(selection[0]))

    def _changed(self) -> None:
        self._sync_empty()
        self._sync_buttons()
        if self._on_change is not None:
            self._on_change()

    def _sync_buttons(self) -> None:
        self.remove_btn.config(
            state="normal" if self.listbox.curselection() else "disabled")

    # ---- empty state --------------------------------------------------- #
    def _sync_empty(self) -> None:
        if self.listbox.size():
            self._empty.grid_remove()
            self._listing.grid()
        else:
            self._listing.grid_remove()
            self._empty.grid()
            self._draw_empty()

    def _draw_empty(self) -> None:
        canvas = self._empty
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 2 or height < 2:
            return

        inset = self._px(1)
        canvas.create_rectangle(
            inset, inset, width - inset - 1, height - inset - 1,
            outline=BORDER_HI, dash=(self._px(4), self._px(4)),
            width=self._px(1))

        # A drawn mark rather than a glyph: a tray with an arrow falling into
        # it, at one stroke weight, so it belongs to the same drawing as the
        # dashed edge around it.
        stroke = max(1, self._px(1.6))
        cx = width // 2
        top = height // 2 - self._px(20)
        if height >= self._px(74):
            arrow = self._px(13)
            canvas.create_line(cx, top, cx, top + arrow,
                               fill=FG_MUTED, width=stroke, capstyle="round")
            # Coordinates go in as one flat sequence: the multi-point form
            # taking loose arguments has no typed overload.
            canvas.create_line(
                [cx - self._px(5), top + arrow - self._px(5),
                 cx, top + arrow,
                 cx + self._px(5), top + arrow - self._px(5)],
                fill=FG_MUTED, width=stroke, capstyle="round",
                joinstyle="round")
            tray = top + arrow + self._px(6)
            canvas.create_line(
                [cx - self._px(11), tray,
                 cx - self._px(11), tray + self._px(7),
                 cx + self._px(11), tray + self._px(7),
                 cx + self._px(11), tray],
                fill=FG_MUTED, width=stroke, capstyle="round",
                joinstyle="round")
            text_y = tray + self._px(24)
        else:
            text_y = height // 2 - self._px(8)

        headline = (self._empty_hint if self.drop_enabled
                    else "No folders added yet")
        canvas.create_text(cx, text_y, text=headline, fill=FG_DIM,
                           font=(FONT_BOLD, 10))
        canvas.create_text(cx, text_y + self._px(19),
                           text=("or click to browse" if self.drop_enabled
                                 else "Click to browse"),
                           fill=FG_MUTED, font=(FONT, 9))
