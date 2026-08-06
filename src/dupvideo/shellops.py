"""
Operating-system integration: opening files, revealing them in the file
manager, and restoring items from the Recycle Bin.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def open_file(path: str) -> None:
    """Open ``path`` in the user's default application for that file type."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if sys.platform == "win32":
        os.startfile(path)                                # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal_command(full: str) -> str:
    """
    Build the exact command line used to reveal ``full`` in Explorer.

    Explorer does **not** parse its command line the way the C runtime does,
    and this is the whole reason this function exists rather than an argument
    list.  Handing ``["explorer.exe", "/select,C:\\a b\\c.mp4"]`` to
    :mod:`subprocess` makes ``list2cmdline`` wrap the argument *as a whole*
    because it contains a space::

        explorer.exe "/select,C:\\a b\\c.mp4"      <- silently wrong

    Explorer cannot read that form: it fails to find a path it recognises and
    falls back to opening the user's default folder (Documents), which looks
    exactly like "Show in Explorer is broken" for every path with a space in
    it - i.e. most of them.  The quotes have to go around the *path* only::

        explorer.exe /select,"C:\\a b\\c.mp4"      <- correct

    Both forms were checked against real Explorer windows before this was
    written; the first opened Documents, the second selected the file.

    Quoting is safe to do by hand here because Windows forbids ``"`` in a path,
    so there is nothing in ``full`` that could terminate the quoted string.
    """
    return f'explorer.exe /select,"{full}"'


def reveal_in_file_manager(path: str) -> None:
    """Show ``path`` selected in Explorer / Finder / the desktop file manager."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    full = os.path.normpath(os.path.abspath(path))
    if sys.platform == "win32":
        # Passed as a string, not a list, so subprocess hands it to
        # CreateProcess verbatim instead of re-quoting it - see
        # reveal_command.  explorer.exe returns 1 even when it succeeds, so
        # the exit code is ignored.
        subprocess.run(reveal_command(full), check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", full], check=False)
    else:
        subprocess.run(["xdg-open", os.path.dirname(full)], check=False)


# --------------------------------------------------------------------------- #
#  Recycle Bin restore
# --------------------------------------------------------------------------- #

# Windows exposes no plain "undelete" API - the Recycle Bin is a shell
# namespace, so restoring means finding the item and invoking its verb through
# Shell.Application.  Driving that from PowerShell avoids a COM dependency.
#
# Two details make this fiddly, and both are handled below:
#   * a recycled item's .Path is its $R... name inside $Recycle.Bin, not where
#     it came from; the original folder is column 1 of GetDetailsOf.
#   * .Name honours Explorer's "hide extensions for known file types", so the
#     extension is taken from the $R... path instead.
_RESTORE_PS1 = r"""
param([string]$ListFile)
$utf8 = [System.Text.Encoding]::UTF8
$want = @{}
foreach ($line in [System.IO.File]::ReadAllLines($ListFile, $utf8)) {
    if ($line) { $want[$line.ToLowerInvariant()] = $true }
}
$shell = New-Object -ComObject Shell.Application
$bin = $shell.NameSpace(0xA)
if ($null -eq $bin) { exit 0 }
foreach ($item in @($bin.Items())) {
    $folder = $bin.GetDetailsOf($item, 1)
    if (-not $folder) { continue }
    $name = $item.Name
    $ext = [System.IO.Path]::GetExtension($item.Path)
    if ($ext -and -not $name.EndsWith($ext, 'OrdinalIgnoreCase')) {
        $name = $name + $ext
    }
    $orig = Join-Path $folder $name
    if (-not $want.ContainsKey($orig.ToLowerInvariant())) { continue }

    $done = $false
    try {
        $item.InvokeVerb('undelete')
        $done = Test-Path -LiteralPath $orig
    } catch { }
    if (-not $done) {
        # 'undelete' is the canonical verb but is not always accepted; the
        # localised Restore verb is the first one offered on recycled items.
        foreach ($verb in @($item.Verbs())) {
            try { $verb.DoIt() } catch { }
            if (Test-Path -LiteralPath $orig) { $done = $true; break }
        }
    }
    if ($done) { Write-Output ("OK`t" + $orig) }
    else { Write-Output ("FAIL`t" + $orig) }
}
"""


def restore_from_recycle_bin(
        paths: Iterable[str], timeout: float = 120.0
) -> tuple[list[str], list[str]]:
    """
    Put previously trashed files back where they came from.

    Returns ``(restored, failed)`` as absolute paths.  Anything the shell
    declines to restore is reported rather than silently dropped.
    """
    wanted = [os.path.normpath(os.path.abspath(p)) for p in paths]
    if not wanted:
        return [], []
    if sys.platform != "win32":
        return [], wanted

    work = tempfile.mkdtemp(prefix="dupvideo-undo-")
    listing = os.path.join(work, "paths.txt")
    script = os.path.join(work, "restore.ps1")
    try:
        with open(listing, "w", encoding="utf-8") as fh:
            fh.write("\n".join(wanted))
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_RESTORE_PS1)

        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", script, listing],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW, check=False)

        restored = []
        for line in result.stdout.splitlines():
            marker, _, path = line.partition("\t")
            if marker == "OK" and path:
                restored.append(path)
    except (OSError, subprocess.SubprocessError):
        return [], wanted
    finally:
        for leftover in (listing, script):
            with contextlib.suppress(OSError):
                os.remove(leftover)
        with contextlib.suppress(OSError):
            os.rmdir(work)

    # Trust the filesystem over the script's own reporting.
    done = {p for p in restored if os.path.exists(p)}
    return sorted(done), sorted(p for p in wanted if p not in done)
