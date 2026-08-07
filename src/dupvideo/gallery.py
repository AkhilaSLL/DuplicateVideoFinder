"""
Side-by-side preview gallery for one duplicate group.

Full video playback inside tkinter needs something heavy (python-vlc, which
in turn needs VLC installed) - out of scope for a single-file exe with no
extra runtime dependency.  Instead each video shows a small grid of the same
frames sampled for fingerprinting, which is both cheap (already-decoded
positions, no extra seeking logic to write) and honest about what the
detector actually looked at.  Double-click still opens the real file in the
user's default player via :func:`dupvideo.shellops.open_file`.
"""

from __future__ import annotations

import math
import os
import sys
import tkinter as tk
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from tkinter import ttk

from PIL import Image, ImageTk

from .engine import VidInfo, sampled_frame_previews
from .theme import (
    ACCENT_TEXT,
    BORDER,
    FG,
    FG_MUTED,
    FONT,
    FONT_BOLD,
    GAP,
    PAD_CARD,
    WINDOW,
)

THUMB_MIN = 160             # never shrink a video's whole preview block below this
THUMB_MAX = 620             # width cap so a lone video isn't huge
CAPTION_SPACE = 182         # vertical room reserved for caption + checkbox
FRAME_GAP = 2               # gap between mini-frames inside one video's grid
CACHE_BUDGET = 160 << 20    # ~160 MB of decoded preview frames
RESIZE_DEBOUNCE_MS = 130
FOLDER_CHARS_MAX = 120      # never show more of a folder path than this
FOLDER_CHARS_MIN = 44       # ...nor less, however narrow the cell gets


def elide_middle(text: str, limit: int) -> str:
    """
    Shorten ``text`` to ``limit`` characters by dropping from the middle.

    The middle is the right thing to drop for a path: the head carries the
    drive and top-level folder, the tail carries the deepest folders, and
    between them those are what tell two source folders apart.  Trimming from
    either end alone throws away one half of that.
    """
    if len(text) <= limit:
        return text
    keep = limit - 1                        # one character goes to the ellipsis
    head = keep // 2
    return f"{text[:head]}…{text[len(text) - (keep - head):]}"


def folder_caption(path: str, box_w: int) -> str:
    """
    The folder ``path`` sits in, trimmed to roughly two lines at ``box_w``.

    Ordinary paths fit whole; the budget only exists so one deeply nested path
    can't wrap into five lines and stretch its cell taller than its neighbours.
    At the 9pt caption font a character averages a little under 5px, so
    ``box_w // 3`` is a conservative two lines' worth.
    """
    budget = min(FOLDER_CHARS_MAX, max(FOLDER_CHARS_MIN, box_w // 3))
    return elide_middle(os.path.dirname(path), budget)


class _FrameSetCache:
    """
    LRU cache of decoded sample-frame lists, keyed by video path.

    Re-decoding 7 frames from a video on every window resize is far too slow,
    but an unbounded cache would happily eat all available memory while
    browsing a large result set - each video costs several times what a
    single image thumbnail did in the sibling project, so the budget is
    tracked the same way but sized down accordingly.
    """

    def __init__(self, budget: int = CACHE_BUDGET) -> None:
        self._budget = budget
        self._items: OrderedDict[str, list[Image.Image]] = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _cost(frames: list[Image.Image]) -> int:
        return sum(f.width * f.height * 3 for f in frames)

    def get(self, path: str) -> list[Image.Image]:
        cached = self._items.get(path)
        if cached is not None:
            self._items.move_to_end(path)
            return cached

        frames = sampled_frame_previews(path)
        for frame in frames:
            frame.thumbnail((480, 480), Image.Resampling.LANCZOS)

        self._items[path] = frames
        self._bytes += self._cost(frames)
        while self._bytes > self._budget and len(self._items) > 1:
            _, evicted = self._items.popitem(last=False)
            self._bytes -= self._cost(evicted)
        return frames

    def discard(self, path: str) -> None:
        frames = self._items.pop(path, None)
        if frames is not None:
            self._bytes -= self._cost(frames)

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0


def _grid_shape(n: int) -> tuple[int, int]:
    """(rows, cols) for arranging ``n`` mini-frames into a compact block."""
    if n <= 0:
        return (1, 1)
    cols = min(n, 4)
    rows = math.ceil(n / cols)
    return rows, cols


class Gallery(ttk.Frame):
    """Scrollable, resizable strip of per-video frame grids with checkboxes."""

    def __init__(self, master: tk.Misc, scale: float = 1.0,
                 on_open: Callable[[str], None] | None = None) -> None:
        super().__init__(master, style="Card.TFrame")
        self._scale = scale
        self._on_open = on_open
        self._cache = _FrameSetCache()
        self._group: Sequence[VidInfo] | None = None
        self._vars: Mapping[str, tk.BooleanVar] = {}
        self._photos: list[ImageTk.PhotoImage] = []
        self._resize_job: str | None = None
        self._last_size = (0, 0)
        self._weighted: tuple[tuple[int, ...], tuple[int, ...]] = ((), ())

        self.canvas = tk.Canvas(self, background=WINDOW, highlightthickness=0,
                                bd=0, takefocus=True)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal",
                            command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        header = ttk.Frame(self, style="TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew",
                    padx=self._px(PAD_CARD),
                    pady=(self._px(PAD_CARD), self._px(GAP)))
        ttk.Label(header, text="PREVIEW", style="Legend.TLabel").pack(
            side="left")
        self.caption = ttk.Label(header, text="", style="Muted.TLabel")
        self.caption.pack(side="right")

        self.canvas.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.inner = tk.Frame(self.canvas, bg=WINDOW)
        self._window = self.canvas.create_window((0, 0), window=self.inner,
                                                 anchor="nw")

        self.canvas.bind("<Configure>", self._on_configure)
        self.inner.bind("<Configure>", lambda _e: self._fit_window())
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

        self.show_message("Scan, then select a duplicate group to\n"
                          "compare its videos side by side.")

    def _px(self, value: float) -> int:
        return max(1, round(value * self._scale))

    # ---- scrolling -------------------------------------------------------- #
    def _bind_wheel(self, _event: tk.Event | None = None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Shift-MouseWheel>", self._on_shift_wheel)
        if sys.platform.startswith("linux"):    # X11 sends buttons 4/5
            self.canvas.bind_all("<Button-4>", self._on_wheel)
            self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _pointer_inside(self) -> bool:
        """Is the mouse still somewhere within this gallery?"""
        try:
            widget = self.winfo_containing(self.winfo_pointerx(),
                                           self.winfo_pointery())
        except tk.TclError:
            return False
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _unbind_wheel(self, _event: tk.Event | None = None) -> None:
        """
        Let the wheel go, but only once the pointer has really left.

        Every thumbnail is a real child widget inside the canvas, and Tk sends
        a widget <Leave> when the pointer crosses into one of its children.
        Unbinding on any <Leave> therefore disarmed the wheel the moment the
        cursor touched a picture - which is nearly the whole panel - leaving it
        working only over the few pixels of backdrop between cells.  Vertical
        scrolling hid the symptom because the strip usually fits vertically;
        sideways it just looked broken.
        """
        if self._pointer_inside():
            return
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>",
                         "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(sequence)

    @staticmethod
    def _wheel_steps(event: tk.Event) -> int:
        if getattr(event, "num", 0) == 4:
            return -1
        if getattr(event, "num", 0) == 5:
            return 1
        return -1 if event.delta > 0 else 1

    def _on_wheel(self, event: tk.Event) -> None:
        if self._overflows_vertically():
            self.canvas.yview_scroll(self._wheel_steps(event), "units")
        else:
            self.canvas.xview_scroll(self._wheel_steps(event) * 3, "units")

    def _on_shift_wheel(self, event: tk.Event) -> None:
        self.canvas.xview_scroll(self._wheel_steps(event) * 3, "units")

    def _overflows_vertically(self) -> bool:
        first, last = self.canvas.yview()
        return not (first <= 0.0 and last >= 1.0)

    # ---- layout ----------------------------------------------------------- #
    def _on_configure(self, event: tk.Event) -> None:
        width, height = event.width, event.height
        last_w, last_h = self._last_size
        if abs(width - last_w) < 8 and abs(height - last_h) < 8:
            return
        self._last_size = (width, height)
        self._fit_window()
        if self._group is None:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(RESIZE_DEBOUNCE_MS, self._render)

    def _fit_window(self) -> None:
        width = max(self.canvas.winfo_width(), self.inner.winfo_reqwidth())
        height = max(self.canvas.winfo_height(), self.inner.winfo_reqheight())
        self.canvas.itemconfigure(self._window, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _video_box(self, count: int) -> tuple[int, int]:
        """Total block size (grid of mini-frames) reserved for one video."""
        scale = self._scale
        canvas_w = self.canvas.winfo_width() or round(900 * scale)
        canvas_h = self.canvas.winfo_height() or round(540 * scale)
        reserved = round(CAPTION_SPACE * scale)
        box_h = int(max(THUMB_MIN * scale,
                        min(THUMB_MAX, canvas_h - reserved)))
        share = (canvas_w - 28 * scale) / max(1, count) - 18 * scale
        box_w = int(min(THUMB_MAX * scale, max(THUMB_MIN * scale, share)))
        return box_w, box_h

    # ---- content ---------------------------------------------------------- #
    def _reset(self) -> None:
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
            self._resize_job = None
        for child in self.inner.winfo_children():
            child.destroy()
        self._photos.clear()
        for index in self._weighted[0]:
            self.inner.grid_rowconfigure(index, weight=0)
        for index in self._weighted[1]:
            self.inner.grid_columnconfigure(index, weight=0)
        self._weighted = ((), ())

    def _set_spacers(self, rows: tuple[int, ...],
                     columns: tuple[int, ...]) -> None:
        for index in rows:
            self.inner.grid_rowconfigure(index, weight=1)
        for index in columns:
            self.inner.grid_columnconfigure(index, weight=1)
        self._weighted = (rows, columns)

    def show_message(self, text: str) -> None:
        self._group = None
        self.caption.config(text="")
        self._vars = {}
        self._reset()
        self._set_spacers((0, 2), (0, 2))
        tk.Label(self.inner, text=text, bg=WINDOW, fg=FG_MUTED,
                 font=(FONT, 11), justify="center").grid(row=1, column=1)
        self._fit_window()

    def show_group(self, group: Sequence[VidInfo],
                   variables: Mapping[str, tk.BooleanVar]) -> None:
        self._group = group
        self._vars = variables
        self.caption.config(
            text=f"{len(group)} videos"
            if len(group) != 1 else "1 video")
        self._render()

    def forget_paths(self, paths: set[str]) -> None:
        for path in paths:
            self._cache.discard(path)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _render(self) -> None:
        self._resize_job = None
        group = self._group
        if not group:
            return
        self._reset()

        box_w, box_h = self._video_box(len(group))
        scale = self._scale
        pad = round(8 * scale)

        self._set_spacers((0, 2), (0, len(group) + 1))

        for column, info in enumerate(group, start=1):
            cell = tk.Frame(self.inner, bg=WINDOW)
            cell.grid(row=1, column=column, sticky="n",
                      padx=pad, pady=round(10 * scale))
            self._build_cell(cell, info, box_w, box_h)

        self._fit_window()
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)

    def _frame_grid(self, parent: tk.Widget, info: VidInfo, box_w: int,
                    box_h: int) -> None:
        try:
            frames = self._cache.get(info.path)
        except Exception:
            frames = []
        if not frames:
            tk.Label(parent, text="[cannot preview]", fg=FG_MUTED, bg=WINDOW,
                     width=20, height=6, wraplength=box_w,
                     justify="center").pack()
            return

        rows, cols = _grid_shape(len(frames))
        gap = FRAME_GAP
        cell_w = max(1, (box_w - gap * (cols - 1)) // cols)
        cell_h = max(1, (box_h - gap * (rows - 1)) // rows)

        grid = tk.Frame(parent, bg=WINDOW)
        grid.pack()
        opener = self._on_open
        for index, frame in enumerate(frames):
            thumb = frame.copy()
            thumb.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            self._photos.append(photo)
            holder = tk.Frame(grid, bg=BORDER, padx=1, pady=1,
                              width=cell_w, height=cell_h)
            holder.grid(row=index // cols, column=index % cols,
                       padx=gap // 2, pady=gap // 2)
            holder.grid_propagate(False)
            label = tk.Label(holder, image=photo, bg=WINDOW, bd=0,
                             cursor="hand2")
            label.place(relx=0.5, rely=0.5, anchor="center")
            if opener is not None:
                label.bind("<Double-1>", lambda _e, p=info.path: opener(p))

    def _build_cell(self, cell: tk.Frame, info: VidInfo, box_w: int,
                    box_h: int) -> None:
        scale = self._scale
        self._frame_grid(cell, info, box_w, box_h)

        tk.Label(cell, text=os.path.basename(info.path), bg=WINDOW, fg=FG,
                 font=(FONT_BOLD, 10), wraplength=max(box_w, 160),
                 justify="center").pack(pady=(round(8 * scale), 1))
        # Which folder the file came from.  A group can span several scanned
        # folders and two copies of the same video usually share a name, so the
        # folder is the only thing in the caption that tells them apart.
        tk.Label(cell, text=folder_caption(info.path, box_w),
                 bg=WINDOW, fg=FG_MUTED, font=(FONT, 8),
                 wraplength=max(box_w, 160), justify="center").pack(pady=(0, 2))
        tk.Label(cell, text=f"Match {info.match:.1f}%", bg=WINDOW, fg=ACCENT_TEXT,
                 font=(FONT_BOLD, 9)).pack()
        tk.Label(cell, text=f"{info.res_str}   ·   {info.duration_str}   ·   "
                            f"{info.size_str}\n{info.date_str}",
                 bg=WINDOW, fg=FG_MUTED, font=(FONT, 9),
                 justify="center").pack(pady=(1, round(6 * scale)))

        variable = self._vars.get(info.path)
        if variable is not None:
            ttk.Checkbutton(
                cell, text="Delete this file", variable=variable,
                style="Window.TCheckbutton",
                cursor="hand2").pack()
