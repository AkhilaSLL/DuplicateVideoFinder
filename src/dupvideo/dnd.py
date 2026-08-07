"""
Accepting folders dragged out of Explorer, using only ctypes and the stdlib.

Tk has no drag-and-drop of its own.  The usual answer is the ``tkinterdnd2``
wheel, which carries native tkdnd binaries that then have to be collected into
the one-file exe; this does the same job through the Win32 call every simple
Windows app has used since Win95 - set ``WS_EX_ACCEPTFILES`` on a window and
handle ``WM_DROPFILES`` - and so adds no dependency and nothing to package.
It sits alongside ``resources.py`` as the other place ctypes talks to Windows.

Two things here were settled by probing rather than by reading, because both
are the kind of thing that looks fine in review and misbehaves against a real
Explorer window:

* **Every drop target registers its whole widget subtree, not just its outer
  frame.**  OLE is documented to walk up from the window under the cursor
  looking for an accepting ancestor, which would make registering the card
  enough.  But ``DragAcceptFiles`` only sets the extended style - it does not
  leave the ``OleDropTargetInterface`` property that a real ``RegisterDragDrop``
  leaves - so there is nothing to observe the walk-up with short of performing
  a live drag.  Registering every descendant makes the question moot at a cost
  of a dozen extra HWNDs.
* **The window procedure must not call into Tk at all - not even
  ``after_idle``.**  Windows dispatches the message from inside Tcl's own event
  loop, and tkinter drops the GIL around that loop; re-entering Tcl from the
  ctypes callback it dispatched kills the interpreter outright with
  ``PyEval_RestoreThread: the current Python thread state is NULL``.  It is a
  hard crash, not an exception, and it takes the app with it.  So the
  procedure only appends to a plain list, and a small ``after`` loop running
  under the normal event loop drains that list and calls the application back.
  ``probe_gil2.py`` narrows it to exactly this statement: reading the paths is
  fine, ``DragFinish`` is fine, the ``after_idle`` is fatal.

Everything degrades to a silent no-op off Windows or on any failure:
drag-and-drop is a convenience over the Add folder button, so losing it must
never cost more than the convenience itself.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import tkinter as tk
from collections.abc import Callable, Iterable

_WINDOWS = sys.platform == "win32"

GWLP_WNDPROC = -4
WM_DROPFILES = 0x0233
DRAIN_MS = 80               # how often Tk looks for a completed drop


def dropped_folders(paths: Iterable[str]) -> list[str]:
    """
    Reduce a raw drop payload to the folders it implies.

    A dropped *file* stands for the folder holding it.  Dragging a handful of
    pictures across is a perfectly clear way of saying "this folder", and
    rejecting it would leave the user with nothing to do but go back and drag
    the parent instead.

    Order is preserved and case-insensitive duplicates collapse onto the first
    spelling seen, matching what the folder list already does for Add folder -
    dropping a folder and three of its files must add one entry, not four.
    """
    folders: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        path = os.path.normpath(raw)
        if not os.path.isdir(path):
            parent = os.path.dirname(path)
            # A bare drive root is its own parent; anything else that is not a
            # directory has nothing usable behind it.
            if not parent or parent == path or not os.path.isdir(parent):
                continue
            path = parent
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        folders.append(path)
    return folders


if _WINDOWS:                                          # pragma: no cover - GUI
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p,
                                  ctypes.c_uint, ctypes.c_size_t,
                                  ctypes.c_ssize_t)

    # SetWindowLongPtrW only exists in the 64-bit user32; the 32-bit build
    # spells the same call SetWindowLongW.
    _set_long = getattr(_user32, "SetWindowLongPtrW", None) or \
        _user32.SetWindowLongW
    _get_long = getattr(_user32, "GetWindowLongPtrW", None) or \
        _user32.GetWindowLongW
    _set_long.restype = ctypes.c_ssize_t
    _set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    _get_long.restype = ctypes.c_ssize_t
    _get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]

    _user32.CallWindowProcW.restype = ctypes.c_ssize_t
    _user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, ctypes.c_void_p,
                                        ctypes.c_uint, ctypes.c_size_t,
                                        ctypes.c_ssize_t]
    _shell32.DragQueryFileW.restype = ctypes.c_uint
    _shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_wchar_p, ctypes.c_uint]
    _shell32.DragFinish.argtypes = [ctypes.c_void_p]
    _shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, ctypes.c_bool]

    # hwnd -> (previous window procedure, the WNDPROC object keeping the
    # trampoline alive).  Losing the second element to the garbage collector
    # would leave Windows calling into freed memory, so this dict is the only
    # thing standing between a drop and a crash.
    #
    # Entries are never removed, including when the window is destroyed: more
    # messages reach a window after WM_DESTROY, and dropping the trampoline
    # then is a use-after-free for the sake of reclaiming one small object.
    # These apps build their UI once, so the dict is bounded by widget count.
    _installed: dict[int, tuple[int, object]] = {}

    def _read_paths(hdrop: int) -> list[str]:
        count = _shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        out: list[str] = []
        for index in range(count):
            length = _shell32.DragQueryFileW(hdrop, index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            _shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
            if buffer.value:
                out.append(buffer.value)
        return out

    def _subclass(hwnd: int, deliver: Callable[[list[str]], None]) -> None:
        if hwnd in _installed:
            return                    # chaining onto ourselves would not return
        previous = _get_long(hwnd, GWLP_WNDPROC)

        def procedure(this_hwnd, message, wparam, lparam):
            if message == WM_DROPFILES:
                try:
                    paths = _read_paths(wparam)
                    _shell32.DragFinish(wparam)
                except Exception:
                    paths = []
                if paths:
                    deliver(paths)    # must not touch Tk - see module docstring
                return 0
            return _user32.CallWindowProcW(previous, this_hwnd, message,
                                           wparam, lparam)

        trampoline = _WNDPROC(procedure)
        _set_long(hwnd, GWLP_WNDPROC,
                  ctypes.cast(trampoline, ctypes.c_void_p).value)
        _installed[hwnd] = (previous, trampoline)
        _shell32.DragAcceptFiles(hwnd, True)


def register_drop_target(widget: tk.Misc,
                         on_drop: Callable[[list[str]], None]) -> bool:
    """
    Let folders be dragged onto ``widget`` and everything inside it.

    ``on_drop`` is called from Tk's event loop with the folders the drop
    implies, already reduced by :func:`dropped_folders`; it never sees an empty
    list.  Returns whether the target was actually armed, so a caller can keep
    its "drag folders here" wording honest.

    Call this once the subtree is fully built - widgets created afterwards are
    not covered.
    """
    if not _WINDOWS:
        return False

    # The hand-off out of the window procedure.  A plain list is enough: both
    # ends run on the main thread, and appending is what the procedure is
    # allowed to do where calling Tk is not.
    pending: list[list[str]] = []

    def deliver(paths: list[str]) -> None:
        folders = dropped_folders(paths)
        if folders:
            pending.append(folders)

    def drain() -> None:
        while pending:
            # One bad drop must not stop the ones behind it, nor the loop.
            with contextlib.suppress(Exception):
                on_drop(pending.pop(0))
        with contextlib.suppress(tk.TclError):
            widget.after(DRAIN_MS, drain)     # gone once the widget is gone

    try:
        widget.update_idletasks()             # force the HWNDs into existence
        for target in (widget, *_descendants(widget)):
            _subclass(target.winfo_id(), deliver)
        widget.after(DRAIN_MS, drain)
    except Exception:
        return False
    return True


def _descendants(widget: tk.Misc) -> list[tk.Misc]:
    found: list[tk.Misc] = []
    stack = list(widget.winfo_children())
    while stack:
        child = stack.pop()
        found.append(child)
        stack.extend(child.winfo_children())
    return found
