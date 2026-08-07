"""
Duplicate Video Finder
======================

Finds duplicate / near-duplicate videos (resized, re-encoded, re-compressed
or re-containerized copies of the same footage) within and across any number
of folders.

There is no whole-file perceptual hash for video, so each file is
fingerprinted by sampling a handful of frames at evenly spaced points along
its timeline and hashing each one with the same dHash + pHash pair the
sibling image project uses - both must agree before two videos count as
duplicates. Duration is used as a cheap prefilter before any frame is
decoded, the same role file-size collisions play for images.

Deletion is ALWAYS to the Recycle Bin (via send2trash) - never permanent -
and can be undone.
"""

__version__ = "1.3.0"

__all__ = ["__version__", "main"]


def main() -> None:
    """Launch the GUI.  Imported lazily so ``import dupvideo`` stays cheap."""
    from .app import main as _main

    _main()
