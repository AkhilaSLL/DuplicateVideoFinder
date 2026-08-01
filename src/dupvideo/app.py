"""Tkinter front-end for the duplicate video finder."""

from __future__ import annotations

import contextlib
import os
import queue
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from .cache import FingerprintCache
from .engine import (
    AUTOSELECT_OPTIONS,
    VidInfo,
    collect_files,
    group_duplicates,
    human_size,
    keeper_index,
    scan_videos,
)
from .gallery import Gallery
from .resources import (
    apply_dark_titlebar,
    asset_path,
    enable_dpi_awareness,
    set_windows_app_id,
)
from .settings import Settings
from .shellops import open_file, restore_from_recycle_bin, reveal_in_file_manager
from .theme import ACCENT, BORDER, CHECKED_FG, ELEV, FG, FG_MUTED, WINDOW, apply_theme

try:
    from send2trash import send2trash
except ImportError:                                   # pragma: no cover
    send2trash = None

POLL_MS = 60
CHECKED_MARK = "   ☑  "
UNCHECKED_MARK = "   ☐  "


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return "<1s"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class App(tk.Tk):
    def __init__(self, scale: float = 1.0) -> None:
        super().__init__()
        self.scale_factor = scale
        self.settings = Settings.load()
        self.cache = FingerprintCache()
        self.cache.prune()

        self.title("Duplicate Video Finder")
        self.geometry(f"{round(1240 * scale)}x{round(880 * scale)}")
        self.minsize(round(1040 * scale), round(720 * scale))

        # Tk sizes point-based fonts from this; the DPI opt-in means the OS no
        # longer stretches the window for us.
        self.tk.call("tk", "scaling", scale * 96.0 / 72.0)
        apply_theme(self, scale)
        self._set_icon()

        self.groups: list[list[VidInfo]] = []
        self.checkbox_vars: dict[str, tk.BooleanVar] = {}
        self._info_for_path: dict[str, VidInfo] = {}
        self._item_for_path: dict[str, str] = {}
        self._path_for_item: dict[str, str] = {}
        self._group_of_path: dict[str, int] = {}
        self._checked: set[str] = set()
        self._checked_bytes = 0
        self._bulk = False
        self._shown_group: int | None = None
        self._summary = "Ready."
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cancel: threading.Event | None = None
        self._poll_job: str | None = None
        self._undo: tuple[list[str], list[list[VidInfo]]] | None = None

        # Scan progress bookkeeping, for throughput and ETA.
        self._scan_started = 0.0
        self._phase_started = 0.0
        self._phase_total = 0
        self._phase_rate = False
        self._phase_unit = ""
        self._phase_done = 0
        self._current_file = ""
        self._current_frame_done = 0
        self._current_frame_total = 0

        self._build_ui()
        self._apply_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._style_titlebar)

        if send2trash is None:
            self.delete_btn.config(state="disabled")
            self.after(200, lambda: messagebox.showwarning(
                "Missing dependency",
                "The 'Send2Trash' package is not installed, so deletion is "
                "disabled.\n\nInstall it with:  pip install Send2Trash"))

    # ---- construction ----------------------------------------------------- #
    def _px(self, value: float) -> int:
        return max(1, round(value * self.scale_factor))

    def _style_titlebar(self) -> None:
        apply_dark_titlebar(self, caption=WINDOW, text=FG, border=BORDER)

    def _set_icon(self) -> None:
        """Window / taskbar icon.  Missing assets must never be fatal."""
        try:
            ico = asset_path("app.ico")
            if os.path.exists(ico):
                with contextlib.suppress(tk.TclError):
                    self.iconbitmap(default=ico)      # best on Windows
            png = asset_path("app.png")
            if os.path.exists(png):
                # Tk 8.6 reads PNG natively, which keeps iconphoto happy - it
                # only accepts a tkinter PhotoImage, not a Pillow one.
                self._icon_photo = tk.PhotoImage(file=png)
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _build_ui(self) -> None:
        edge = self._px(18)

        header = ttk.Frame(self, style="Window.TFrame")
        header.pack(fill="x", padx=edge, pady=(self._px(14), self._px(6)))
        try:
            with Image.open(asset_path("app.png")) as raw:
                logo = raw.convert("RGBA").resize(
                    (self._px(34), self._px(34)), Image.Resampling.LANCZOS)
            self._logo_photo = ImageTk.PhotoImage(logo)
            tk.Label(header, image=self._logo_photo, bg=WINDOW,
                     bd=0).pack(side="left")
        except Exception:
            pass
        ttk.Label(header, text="  Duplicate Video Finder",
                  style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="finds re-encoded & near-duplicate videos  ·  "
                              "deletes only to the Recycle Bin",
                  style="WindowMuted.TLabel").pack(side="left",
                                                   padx=self._px(14))

        self._build_folders(edge)
        self._build_results(edge)
        self._build_actions(edge)
        self._build_status(edge)
        self._build_context_menu()

    def _build_folders(self, edge: int) -> None:
        top = ttk.LabelFrame(self, text="  FOLDERS  ")
        top.pack(fill="x", padx=edge, pady=self._px(6))

        self.recursive = tk.BooleanVar(value=True)
        self.use_cache = tk.BooleanVar(value=True)
        self.threshold = tk.IntVar(value=90)

        listing = ttk.Frame(top)
        listing.grid(row=0, column=0, sticky="nsew",
                     padx=(self._px(8), self._px(4)), pady=self._px(8))
        self.folder_list = tk.Listbox(
            listing, height=3, selectmode="extended", activestyle="none",
            bg=ELEV, fg=FG, bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            selectbackground=ACCENT, selectforeground="#ffffff",
            exportselection=False, font=("Segoe UI", 10))
        scroll = ttk.Scrollbar(listing, orient="vertical",
                               command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=scroll.set)
        self.folder_list.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.folder_list.bind("<Double-1>", self._on_folder_double)
        self.folder_list.bind("<Delete>", lambda _e: self._remove_folders())

        buttons = ttk.Frame(top)
        buttons.grid(row=0, column=1, sticky="n",
                     padx=(0, self._px(8)), pady=self._px(8))
        ttk.Button(buttons, text="Add folder…", width=13,
                   command=self._add_folder).pack(fill="x")
        ttk.Button(buttons, text="Remove", width=13, style="Secondary.TButton",
                   command=self._remove_folders).pack(fill="x",
                                                      pady=(self._px(4), 0))
        top.columnconfigure(0, weight=1)

        options = ttk.Frame(top)
        options.grid(row=1, column=0, columnspan=2, sticky="we",
                     padx=self._px(8), pady=(0, self._px(8)))
        ttk.Checkbutton(options, text="Include subfolders",
                        variable=self.recursive).pack(
            side="left", padx=(0, self._px(16)))
        ttk.Checkbutton(options, text="Reuse cached hashes",
                        variable=self.use_cache).pack(side="left")
        self.cache_btn = ttk.Button(options, text="Clear cache", width=12,
                                    style="Secondary.TButton",
                                    command=self._clear_cache)
        self.cache_btn.pack(side="left", padx=self._px(8))

        self.scan_btn = ttk.Button(options, text="Scan", width=10,
                                   command=self._on_scan_clicked)
        self.scan_btn.pack(side="right")
        self.thr_label = ttk.Label(options, text="90%", width=5,
                                   style="Muted.TLabel")
        self.thr_scale = ttk.Scale(options, from_=70, to=100,
                                   command=self._on_threshold,
                                   length=self._px(200))
        ttk.Label(options, text="Min match").pack(side="left",
                                                  padx=(self._px(16), 0))
        self.thr_scale.pack(side="left", padx=self._px(8))
        self.thr_label.pack(side="left", padx=(0, self._px(10)))
        # Only now - Scale.set() fires the command, which needs thr_label.
        self.thr_scale.set(self.threshold.get())

    def _build_results(self, edge: int) -> None:
        self.paned = ttk.Panedwindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=edge, pady=self._px(6))

        left = ttk.Frame(self.paned)
        columns = ("size", "date", "res", "duration", "match")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text="File")
        self.tree.heading("size", text="Size")
        self.tree.heading("date", text="Modified")
        self.tree.heading("res", text="Resolution")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("match", text="Match")
        self.tree.column("#0", width=self._px(260), minwidth=self._px(140),
                         stretch=True)
        self.tree.column("size", width=self._px(76), stretch=False, anchor="e")
        self.tree.column("date", width=self._px(136), stretch=False,
                         anchor="center")
        self.tree.column("res", width=self._px(96), stretch=False,
                         anchor="center")
        self.tree.column("duration", width=self._px(76), stretch=False,
                         anchor="center")
        self.tree.column("match", width=self._px(70), stretch=False,
                         anchor="center")
        self.tree.tag_configure("group", foreground=ACCENT)
        self.tree.tag_configure("checked", foreground=CHECKED_FG)

        ysb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<space>", self._on_toggle_selected)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.paned.add(left, weight=3)

        self.gallery = Gallery(self.paned, self.scale_factor,
                               on_open=self._open_path)
        self.paned.add(self.gallery, weight=5)
        self.after(120, self._init_sash)

    def _build_actions(self, edge: int) -> None:
        bottom = ttk.Frame(self, style="Window.TFrame")
        bottom.pack(fill="x", padx=edge, pady=(self._px(6), 0))

        auto = ttk.LabelFrame(bottom, text="  AUTO-SELECT FOR DELETION  ")
        auto.pack(side="left", fill="x", expand=True)
        ttk.Label(auto, text="Delete files with").pack(
            side="left", padx=(self._px(10), self._px(6)), pady=self._px(8))
        self.auto_choice = tk.StringVar(value=AUTOSELECT_OPTIONS[0])
        ttk.Combobox(auto, textvariable=self.auto_choice,
                     values=list(AUTOSELECT_OPTIONS), state="readonly",
                     width=18).pack(side="left", padx=self._px(4))
        ttk.Button(auto, text="Apply", command=self._apply_autoselect).pack(
            side="left", padx=self._px(6))
        ttk.Label(auto, text="(always keeps one per group)",
                  style="Muted.TLabel").pack(side="left", padx=self._px(6))
        ttk.Button(auto, text="Clear", style="Secondary.TButton",
                   command=lambda: self._set_all(False)).pack(
            side="left", padx=(self._px(10), self._px(2)))
        ttk.Button(auto, text="Select all", style="Secondary.TButton",
                   command=lambda: self._set_all(True)).pack(
            side="left", padx=self._px(2))

        self.delete_btn = ttk.Button(
            bottom, text="Delete selected  →  Recycle Bin",
            style="Danger.TButton", command=self._delete_selected)
        self.delete_btn.pack(side="right", padx=(self._px(6), 0))
        self.undo_btn = ttk.Button(bottom, text="Undo delete", width=13,
                                   style="Secondary.TButton", state="disabled",
                                   command=self._undo_delete)
        self.undo_btn.pack(side="right", padx=(self._px(14), 0))

    def _build_status(self, edge: int) -> None:
        bar = ttk.Frame(self, style="Window.TFrame")
        bar.pack(fill="x", padx=edge, pady=(self._px(8), self._px(12)))
        self.status = tk.StringVar(value=self._summary)
        ttk.Label(bar, textvariable=self.status, anchor="w",
                  style="WindowMuted.TLabel").pack(side="left", fill="x",
                                                   expand=True)
        self.progress = ttk.Progressbar(bar, mode="determinate",
                                        length=self._px(260))
        self.progress.pack(side="right", padx=(self._px(14), 0))

    def _build_context_menu(self) -> None:
        self.menu = tk.Menu(self, tearoff=0, bg=ELEV, fg=FG,
                            activebackground=ACCENT, activeforeground="#ffffff",
                            activeborderwidth=0, bd=0,
                            disabledforeground=FG_MUTED)
        self.menu.add_command(label="Open", command=self._open_selected)
        self.menu.add_command(label="Show in Explorer",
                              command=self._reveal_selected)
        self.menu.add_command(label="Copy full path",
                              command=self._copy_selected_path)
        self.menu.add_separator()
        self.menu.add_command(label="Toggle for deletion",
                              command=self._on_toggle_selected)

    def _init_sash(self) -> None:
        total = self.paned.winfo_width()
        if total <= 200:
            return
        saved = self.settings.sash
        if 120 < saved < total - 200:
            self.paned.sashpos(0, saved)
            return
        columns = self._px(260 + 76 + 136 + 96 + 76 + 70 + 22)
        self.paned.sashpos(0, int(min(columns, total * 0.55)))

    # ---- settings --------------------------------------------------------- #
    def _apply_settings(self) -> None:
        saved = self.settings
        for folder in saved.folders:
            if os.path.isdir(folder):
                self.folder_list.insert("end", folder)
        self.recursive.set(saved.recursive)
        self.use_cache.set(saved.use_cache)
        self.thr_scale.set(saved.threshold)
        if saved.auto_choice in AUTOSELECT_OPTIONS:
            self.auto_choice.set(saved.auto_choice)
        if saved.geometry:
            with contextlib.suppress(tk.TclError):
                self.geometry(saved.geometry)
        self._refresh_cache_label()

    def _capture_settings(self) -> Settings:
        sash = 0
        with contextlib.suppress(tk.TclError):
            sash = self.paned.sashpos(0)
        return Settings(
            folders=self._folders(),
            recursive=bool(self.recursive.get()),
            threshold=int(self.threshold.get()),
            auto_choice=self.auto_choice.get(),
            use_cache=bool(self.use_cache.get()),
            geometry=self.winfo_geometry(),
            sash=sash,
        )

    def _refresh_cache_label(self) -> None:
        count = self.cache.count()
        self.cache_btn.config(
            text="Clear cache" if not count else f"Clear cache ({count:,})",
            state="normal" if count else "disabled")

    def _clear_cache(self) -> None:
        if messagebox.askyesno(
                "Clear cache",
                "Discard all stored video fingerprints?\n\nThe next scan will "
                "have to re-decode every file again."):
            self.cache.clear()
            self._refresh_cache_label()

    # ---- folders ---------------------------------------------------------- #
    def _folders(self) -> list[str]:
        return list(self.folder_list.get(0, "end"))

    def _add_folder(self) -> None:
        existing = self._folders()
        chosen = filedialog.askdirectory(
            title="Add a folder to scan",
            initialdir=existing[-1] if existing else None)
        if not chosen:
            return
        folder = os.path.normpath(chosen)
        if any(os.path.normcase(f) == os.path.normcase(folder)
               for f in existing):
            return
        self.folder_list.insert("end", folder)

    def _remove_folders(self) -> None:
        for index in reversed(self.folder_list.curselection()):
            self.folder_list.delete(index)

    def _on_folder_double(self, _event: tk.Event | None = None) -> None:
        selection = self.folder_list.curselection()
        if selection:
            self._open_path(self.folder_list.get(selection[0]))

    # ---- small helpers ---------------------------------------------------- #
    def _on_threshold(self, raw: str) -> None:
        value = int(round(float(raw)))
        if value != self.threshold.get():
            self.threshold.set(value)
        self.thr_label.config(text=f"{value}%")

    def _refresh_status(self) -> None:
        if self._checked:
            self.status.set(
                f"{len(self._checked)} file(s) selected  ·  "
                f"{human_size(self._checked_bytes)} to free      "
                f"{self._summary}")
        else:
            self.status.set(self._summary)

    # ---- scanning --------------------------------------------------------- #
    def _on_scan_clicked(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.scan_btn.config(state="disabled", text="Stopping…")
            return

        folders = self._folders()
        if not folders:
            messagebox.showerror(
                "No folders", "Add at least one folder to scan.")
            return
        missing = [f for f in folders if not os.path.isdir(f)]
        if missing:
            messagebox.showerror(
                "Invalid folder",
                "These folders no longer exist:\n\n" + "\n".join(missing[:8]))
            return

        self._clear_results()
        self._scan_started = time.monotonic()
        self._set_phase("Collecting files…", rate=False)
        self.scan_btn.config(text="Cancel", style="Danger.TButton")

        self._cancel = threading.Event()
        threading.Thread(
            target=self._scan_worker,
            args=(folders, self.recursive.get(), float(self.threshold.get()),
                  self.cache if self.use_cache.get() else None, self._cancel),
            daemon=True).start()
        self._poll_job = self.after(POLL_MS, self._poll_queue)

    def _set_phase(self, text: str, rate: bool, unit: str = "") -> None:
        self._summary = text
        self._phase_started = time.monotonic()
        self._phase_total = 0
        self._phase_rate = rate
        self._phase_unit = unit
        self._phase_done = 0
        self._current_file = ""
        self._current_frame_done = 0
        self._current_frame_total = 0
        self.progress.config(maximum=100, value=0)
        self._refresh_status()

    def _scan_worker(self, folders: list[str], recursive: bool,
                     threshold: float, cache, cancel: threading.Event) -> None:
        put = self._queue.put
        try:
            paths = list(collect_files(folders, recursive))
            if cancel.is_set():
                put(("cancelled", None))
                return
            put(("phase", ("Fingerprinting videos…", True, "video")))
            infos, errors = scan_videos(
                paths,
                progress=lambda done, total: put(("progress", (done, total))),
                frame_progress=lambda path, done, total: put(
                    ("frame", (path, done, total))),
                cancel=cancel, cache=cache)
            if cancel.is_set():
                put(("cancelled", None))
                return

            put(("phase", ("Comparing videos…", True, "pair")))
            groups = group_duplicates(
                infos, threshold, cancel=cancel,
                progress=lambda done, total: put(("progress", (done, total))))
            if cancel.is_set():
                put(("cancelled", None))
                return
            put(("done", (groups, len(infos), errors)))
        except Exception:
            put(("error", traceback.format_exc()))

    def _poll_queue(self) -> None:
        self._poll_job = None
        latest_progress: tuple[int, int] | None = None
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "phase":
                    text, rate, unit = payload
                    latest_progress = None
                    self._set_phase(text, rate, unit)
                elif kind == "progress":
                    # Coalesce: one repaint per poll, not one per file/pair.
                    latest_progress = payload
                elif kind == "frame":
                    # Sub-file granularity: which video is in flight right
                    # now, and how far into its sampled frames. Seeking a
                    # single large/high-bitrate file can take far longer than
                    # the average, so without this the status line looks
                    # frozen between file completions even though a worker
                    # is actively decoding.
                    path, fdone, ftotal = payload
                    self._current_file = os.path.basename(path)
                    self._current_frame_done = fdone
                    self._current_frame_total = ftotal
                elif kind == "error":
                    self._finish_scan()
                    self._summary = "Scan failed."
                    self._refresh_status()
                    messagebox.showerror("Scan failed", str(payload))
                    return
                elif kind == "cancelled":
                    self._finish_scan()
                    self.progress.config(value=0)
                    self._summary = "Scan cancelled."
                    self._refresh_status()
                    return
                elif kind == "done":
                    groups, scanned, errors = payload
                    self._finish_scan()
                    self._populate(groups, scanned, errors)
                    return
                elif kind == "undone":
                    self._finish_undo(*payload)
                    return
        except queue.Empty:
            pass
        if latest_progress is not None:
            self._show_progress(*latest_progress)
        elif self._phase_rate:
            # No new file/pair finished this tick, but repaint anyway so
            # elapsed time (and the recalculated ETA) visibly keep moving
            # instead of looking stuck on a slow file.
            self._show_progress(self._phase_done, self._phase_total)
        self._poll_job = self.after(POLL_MS, self._poll_queue)

    def _show_progress(self, done: int, total: int) -> None:
        self._phase_done = done
        self._phase_total = total
        self.progress.config(maximum=max(1, total), value=done)
        if not self._phase_rate or not total:
            return
        elapsed = time.monotonic() - self._phase_started
        if elapsed < 0.4 or done <= 0:
            return
        rate = done / elapsed
        remaining = (total - done) / rate if rate > 0 else 0
        current = ""
        if self._current_file and self._current_frame_total:
            current = (f"  ·  {self._current_file}: frame "
                      f"{self._current_frame_done}/{self._current_frame_total}")
        scan_elapsed = format_duration(time.monotonic() - self._scan_started)
        self.status.set(
            f"{self._summary}  {done:,} / {total:,}{current}  ·  "
            f"{rate:,.1f} {self._phase_unit}/s  ·  {format_duration(remaining)} left"
            f"  ·  {scan_elapsed} elapsed")

    def _finish_scan(self) -> None:
        self._cancel = None
        self.scan_btn.config(state="normal", text="Scan", style="TButton")
        self._refresh_cache_label()

    def _populate(self, groups: list[list[VidInfo]], scanned: int,
                  errors: list[tuple[str, str]]) -> None:
        self.groups = groups
        self._build_tree()
        duplicates = sum(len(g) for g in groups)
        reclaimable = sum(i.size for g in groups for i in g[1:])
        self._summary = (
            f"Scanned {scanned:,} video(s) in "
            f"{format_duration(time.monotonic() - self._scan_started)}  ·  "
            f"{len(groups)} group(s)  ·  {duplicates} file(s)  ·  up to "
            f"{human_size(reclaimable)} reclaimable")
        if errors:
            self._summary += f"  ·  {len(errors)} skipped"
        self._refresh_status()
        self.progress.config(value=self.progress["maximum"])
        if groups:
            first = self.tree.get_children()
            if first:
                self.tree.selection_set(first[0])
                self.tree.focus(first[0])
        else:
            messagebox.showinfo("Done", "No duplicates found.")

    # ---- results ---------------------------------------------------------- #
    def _clear_results(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.groups = []
        self.checkbox_vars.clear()
        self._info_for_path.clear()
        self._item_for_path.clear()
        self._path_for_item.clear()
        self._group_of_path.clear()
        self._checked.clear()
        self._checked_bytes = 0
        self._shown_group = None
        self._set_undo(None)
        self.gallery.clear_cache()
        self.gallery.show_message("Scan, then select a duplicate group to\n"
                                  "compare its videos side by side.")

    def _build_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.checkbox_vars.clear()
        self._info_for_path.clear()
        self._item_for_path.clear()
        self._path_for_item.clear()
        self._group_of_path.clear()
        self._checked.clear()
        self._checked_bytes = 0
        self._shown_group = None

        for index, group in enumerate(self.groups):
            worst = min(i.match for i in group)
            total = human_size(sum(i.size for i in group))
            parent = self.tree.insert(
                "", "end",
                text=f"  Group {index + 1}   ·   {len(group)} files",
                values=(total, "", "", "", f"{worst:.0f}%"), open=True,
                tags=("group",))
            for info in group:
                variable = tk.BooleanVar(value=False)
                variable.trace_add(
                    "write", lambda *_a, p=info.path: self._on_check(p))
                self.checkbox_vars[info.path] = variable
                self._info_for_path[info.path] = info
                self._group_of_path[info.path] = index
                item = self.tree.insert(
                    parent, "end",
                    text=UNCHECKED_MARK + os.path.basename(info.path),
                    values=(info.size_str, info.date_str, info.res_str,
                            info.duration_str, f"{info.match:.1f}%"))
                self._item_for_path[info.path] = item
                self._path_for_item[item] = info.path

    # ---- check state ------------------------------------------------------ #
    def _on_check(self, path: str) -> None:
        if self._bulk:
            return
        if self._sync_row(path):
            self._refresh_status()

    def _sync_row(self, path: str) -> bool:
        """Reconcile one row with its variable.  Returns True if it changed."""
        variable = self.checkbox_vars.get(path)
        info = self._info_for_path.get(path)
        if variable is None or info is None:
            return False
        checked = bool(variable.get())
        if checked == (path in self._checked):
            return False
        if checked:
            self._checked.add(path)
            self._checked_bytes += info.size
        else:
            self._checked.discard(path)
            self._checked_bytes -= info.size
        item = self._item_for_path.get(path)
        if item:
            mark = CHECKED_MARK if checked else UNCHECKED_MARK
            self.tree.item(item, text=mark + os.path.basename(path),
                           tags=("checked",) if checked else ())
        return True

    def _bulk_update(self, apply_changes) -> None:
        """
        Run a mass selection change without doing O(n) work per variable.

        Every write to a BooleanVar fires its trace, so setting n variables one
        at a time would cost O(n^2) tree updates and freeze the UI on large
        result sets.
        """
        self._bulk = True
        try:
            apply_changes()
        finally:
            self._bulk = False
        for path in self.checkbox_vars:
            self._sync_row(path)
        self._refresh_status()

    def _set_all(self, value: bool) -> None:
        def apply_changes() -> None:
            for variable in self.checkbox_vars.values():
                variable.set(value)

        self._bulk_update(apply_changes)

    def _apply_autoselect(self) -> None:
        if not self.groups:
            return
        criterion = self.auto_choice.get()

        def apply_changes() -> None:
            for group in self.groups:
                keep = keeper_index(group, criterion)
                for position, info in enumerate(group):
                    variable = self.checkbox_vars.get(info.path)
                    if variable is not None:
                        variable.set(position != keep)

        self._bulk_update(apply_changes)

    def _on_toggle_selected(self, _event: tk.Event | None = None) -> str | None:
        paths = self._selected_paths()
        if not paths:
            return None
        # Mixed selections all become checked; a uniform one flips.
        target = not all(p in self._checked for p in paths)

        def apply_changes() -> None:
            for path in paths:
                variable = self.checkbox_vars.get(path)
                if variable is not None:
                    variable.set(target)

        self._bulk_update(apply_changes)
        return "break"

    # ---- preview & selection ---------------------------------------------- #
    def _selected_paths(self) -> list[str]:
        """Every file path implied by the tree selection, headers expanded."""
        paths: list[str] = []
        for item in self.tree.selection():
            path = self._path_for_item.get(item)
            if path is not None:
                paths.append(path)
                continue
            for child in self.tree.get_children(item):
                child_path = self._path_for_item.get(child)
                if child_path is not None:
                    paths.append(child_path)
        return paths

    def _focused_path(self) -> str | None:
        """The single file a context action should act on."""
        item = self.tree.focus() or (self.tree.selection() or (None,))[0]
        if item is None:
            return None
        path = self._path_for_item.get(item)
        if path is not None:
            return path
        children = self.tree.get_children(item)
        return self._path_for_item.get(children[0]) if children else None

    def _on_tree_select(self, _event: tk.Event | None = None) -> None:
        path = self._focused_path()
        if path is None:
            return
        index = self._group_of_path.get(path)
        if index is None or index == self._shown_group:
            return
        self._shown_group = index
        self.gallery.show_group(self.groups[index], self.checkbox_vars)

    def _on_tree_double(self, event: tk.Event) -> str | None:
        item = self.tree.identify_row(event.y)
        path = self._path_for_item.get(item) if item else None
        if path is None:
            return None                       # group headers keep expanding
        self._open_path(path)
        return "break"

    def _on_tree_right_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        if item not in self.tree.selection():
            self.tree.selection_set(item)
        self.tree.focus(item)
        state = "normal" if self._focused_path() else "disabled"
        for index in (0, 1, 2):
            self.menu.entryconfig(index, state=state)
        self.menu.tk_popup(event.x_root, event.y_root)
        self.menu.grab_release()

    # ---- shell actions ---------------------------------------------------- #
    def _open_path(self, path: str) -> None:
        try:
            open_file(path)
        except Exception as exc:
            messagebox.showwarning("Cannot open", f"{path}\n\n{exc}")

    def _open_selected(self) -> None:
        path = self._focused_path()
        if path:
            self._open_path(path)

    def _reveal_selected(self) -> None:
        path = self._focused_path()
        if not path:
            return
        try:
            reveal_in_file_manager(path)
        except Exception as exc:
            messagebox.showwarning("Cannot show file", f"{path}\n\n{exc}")

    def _copy_selected_path(self) -> None:
        path = self._focused_path()
        if not path:
            return
        self.clipboard_clear()
        self.clipboard_append(path)
        self._summary = f"Copied path: {os.path.basename(path)}"
        self._refresh_status()

    # ---- deletion --------------------------------------------------------- #
    def _set_undo(self, state: tuple[list[str], list[list[VidInfo]]] | None
                  ) -> None:
        self._undo = state
        self.undo_btn.config(state="normal" if state else "disabled")

    def _delete_selected(self) -> None:
        if send2trash is None:
            return
        targets = sorted(self._checked)
        if not targets:
            messagebox.showinfo("Nothing selected",
                                "No files are selected for deletion.")
            return

        selected = set(targets)
        wiped = sum(1 for g in self.groups
                    if all(i.path in selected for i in g))
        if wiped and not messagebox.askyesno(
                "Warning: full groups selected",
                f"Every file in {wiped} group(s) is selected, so no copy "
                f"would be kept for those groups.\n\nContinue anyway?"):
            return
        if not messagebox.askyesno(
                "Confirm",
                f"Send {len(targets)} file(s) to the Recycle Bin?\n\n"
                f"This frees {human_size(self._checked_bytes)}."):
            return

        snapshot = [list(group) for group in self.groups]
        failed: list[tuple[str, str]] = []
        for path in targets:
            try:
                send2trash(os.path.abspath(path))
            except Exception as exc:
                failed.append((path, str(exc)))

        deleted = selected - {p for p, _ in failed}
        self.gallery.forget_paths(deleted)
        self.groups = [kept for kept in
                       ([i for i in g if i.path not in deleted]
                        for g in self.groups)
                       if len(kept) >= 2]
        self._build_tree()
        self.gallery.show_message(
            "Select a duplicate group to\ncompare its videos side by side.")
        self._set_undo((sorted(deleted), snapshot) if deleted else None)

        if failed:
            messagebox.showwarning(
                "Some deletions failed",
                f"{len(deleted)} deleted, {len(failed)} failed.\n\n"
                + "\n".join(f"{os.path.basename(p)}: {e}"
                            for p, e in failed[:8]))
        self._summary = (f"Deleted {len(deleted)} file(s) to the Recycle Bin"
                         f"  ·  {len(self.groups)} group(s) remaining")
        self._refresh_status()

    def _undo_delete(self) -> None:
        if self._undo is None or self._cancel is not None:
            return
        paths, snapshot = self._undo
        self.undo_btn.config(state="disabled", text="Restoring…")
        self.scan_btn.config(state="disabled")
        self._summary = f"Restoring {len(paths)} file(s) from the Recycle Bin…"
        self._refresh_status()

        def worker() -> None:
            try:
                restored, failed = restore_from_recycle_bin(paths)
            except Exception as exc:
                restored, failed = [], list(paths)
                del exc
            self._queue.put(("undone", (restored, failed, snapshot)))

        threading.Thread(target=worker, daemon=True).start()
        if self._poll_job is None:
            self._poll_job = self.after(POLL_MS, self._poll_queue)

    def _finish_undo(self, restored: list[str], failed: list[str],
                     snapshot: list[list[VidInfo]]) -> None:
        self.undo_btn.config(text="Undo delete")
        self.scan_btn.config(state="normal")
        self._set_undo(None)

        back = {os.path.normcase(p) for p in restored}
        self.groups = [kept for kept in
                       ([i for i in g
                         if os.path.normcase(i.path) in back
                         or os.path.exists(i.path)]
                        for g in snapshot)
                       if len(kept) >= 2]
        self._build_tree()
        self.gallery.show_message(
            "Select a duplicate group to\ncompare its videos side by side.")

        self._summary = (f"Restored {len(restored)} file(s)  ·  "
                         f"{len(self.groups)} group(s)")
        self._refresh_status()
        if failed:
            messagebox.showwarning(
                "Some files could not be restored",
                f"{len(restored)} restored, {len(failed)} failed.\n\n"
                "They may have been removed from the Recycle Bin, or it may "
                "have been emptied.\n\n"
                + "\n".join(os.path.basename(p) for p in failed[:8]))

    # ---- lifecycle -------------------------------------------------------- #
    def _on_close(self) -> None:
        with contextlib.suppress(Exception):
            self._capture_settings().save()
        if self._cancel is not None:
            self._cancel.set()
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self.destroy()


def main() -> None:
    scale = enable_dpi_awareness()
    set_windows_app_id()
    App(scale).mainloop()
