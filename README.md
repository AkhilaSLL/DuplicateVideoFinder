# Duplicate Video Finder

Find duplicate and near-duplicate **videos** — resized, re-encoded,
re-compressed or re-containerized copies of the same footage — **across any
number of folders**, compare them side by side, auto-select which copies to
remove, and send them to the **Recycle Bin** — never a permanent delete, and
undoable in one click.

Windows desktop app, dark UI (title bar included), single-file `.exe`, no
Python needed to run it.

- Each video is fingerprinted by sampling a handful of frames along its
  timeline and hashing them the same way the sibling
  [Duplicate Image Finder](https://github.com/AkhilaSLL/DuplicateImageFinder)
  hashes a whole photo — two independent perceptual hashes must agree, so
  resized and re-encoded copies are caught without false positives.
- Duration is checked first, so wildly different-length videos are never even
  opened for comparison.
- Fingerprints are cached, so rescanning a large library is near-instant.
- Nothing is ever permanently deleted, and **Undo delete** restores the last
  batch from the Recycle Bin.

```
┌──────────────────────────────┬─────────────────────────────────┐
│  Group 1 · 3 files      94%  │            PREVIEW               │
│    clip_1080p.mkv  100.0%    │  [▦▦▦▦]      [▦▦▦▦]      [▦▦▦▦]  │
│  ☑ clip_720p.mp4    96.4%    │  [▦▦▦▦]      [▦▦▦▦]      [▦▦▦▦]  │
│  ☑ clip_old.avi     93.8%    │  clip_1080p   clip_720p  clip_old │
└──────────────────────────────┴─────────────────────────────────┘
```

---

## Install

### Option A — download the `.exe` (easiest)

Grab `DuplicateVideoFinder.exe` from the
[latest release](https://github.com/AkhilaSLL/DuplicateVideoFinder/releases/latest).
It is a single self-contained file: put it anywhere and double-click. No
installer, no Python, no admin rights.

> Windows SmartScreen may warn about an unrecognised publisher because the
> `.exe` is unsigned. Choose **More info → Run anyway**.

### Option B — run from source

```powershell
git clone https://github.com/AkhilaSLL/DuplicateVideoFinder.git
cd DuplicateVideoFinder
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Requires **Python 3.10+**. `python -m dupvideo` also works once `src/` is on
`PYTHONPATH`, or after `pip install -e .`.

---

## Build the `.exe` yourself

A Windows `.exe` must be built on Windows — PyInstaller cannot cross-compile
from Linux or macOS.

**One click:** double-click `build.bat`. It creates `.venv`, installs
dependencies, generates the icon, and builds the app.

**Manually:**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts\make_icon.py                       # only if assets/ is missing
pyinstaller --noconfirm --clean packaging\DuplicateVideoFinder.spec
```

The result is a single standalone file:

```
dist\DuplicateVideoFinder.exe
```

---

## How to use it

### 1. Choose folders

**Drag folders straight in from Explorer**, or use **Add folder…**. Add as
many as you like; duplicates are found inside each folder *and* across all of
them. Dropping a *file* adds the folder containing it, so a handful of selected
clips works as well as the folder itself. Select entries and press
**Remove** (or <kbd>Delete</kbd>) to drop them. The folder list is *not* kept
between launches — every session starts empty, so a folder can never be swept
into a scan you didn't mean to run. Everything else (window size, match
threshold, options) is remembered.

- **Include subfolders** toggles recursive scanning.
- **Reuse cached hashes** skips re-fingerprinting files that haven't changed
  since the last scan — see [Scan cache](#scan-cache).
- **Min match** is how similar two videos must be to count as duplicates. 90%
  is a good default; 100% means byte-identical copies only.

Click **Scan**. The button becomes **Cancel** while a scan is running, and the
status bar reports live throughput and an ETA.

### 2. Review results

The left panel lists each duplicate **group** with size, modified date,
resolution, duration and match %. Selecting any row shows that whole group
**side by side** in the preview.

Each preview tile shows a small grid of frames sampled from that video — the
same frames the detector actually compared, not a live player. **Double-click**
any frame — or a row in the list — to open the real file in your default
video player. **Right-click** a row for **Open**, **Show in Explorer** and
**Copy full path**.

Each tile is captioned with its file name **and the folder it came from**, which
is what tells two copies apart when you are scanning more than one folder and
both copies share a name. Very long paths are shortened from the middle, keeping
the drive and the deepest folders — the parts that actually differ.

Match % is the *lower* of the two hash sequences' scores, so it reflects
whichever one was least convinced. Byte-identical files always read **100%**.

### 3. Select what to delete

**Auto-select for deletion → "Delete files with":**

| Option            | Keeps in each group        |
|-------------------|-----------------------------|
| Smaller file size | the **largest** file       |
| Larger file size  | the **smallest** file      |
| Lower resolution  | the **highest** resolution |
| Higher resolution | the **lowest** resolution  |
| Shorter duration  | the **longest** video      |
| Longer duration   | the **shortest** video     |
| Older date        | the **newest** file        |
| Newer date        | the **oldest** file        |

Auto-select always **keeps one file per group**. You can also tick files
individually — click the ☐ next to a file name in the results list, or use the
preview checkboxes — or **select several rows** (click,
<kbd>Ctrl</kbd>-click, <kbd>Shift</kbd>-click — selecting a group header covers
its whole group) and press <kbd>Space</kbd> to toggle them together. **Select
all / Clear** cover everything.

### 4. Delete — and undo

**Delete selected → Recycle Bin** sends every ticked file to the Windows
Recycle Bin, so anything can be restored. If you ticked *every* file in a
group, it warns you first.

**Undo delete** puts the last batch straight back where it came from and
restores the result list, without needing to rescan. It stays available until
the next scan. If a file can no longer be found in the Recycle Bin — for
instance because it was emptied — the app says which ones it could not bring
back rather than silently dropping them.

---

## How detection works

There is no single whole-file hash for video the way dHash/pHash work on a
single image, so each video is fingerprinted by sampling **7 frames** at
evenly spaced points across the middle 84% of its timeline (the first and
last 8% are skipped, to dodge black intro/outro/logo frames), and hashing
each sampled frame with the same two hashes the image project uses:

- **dHash** (difference hash) — fast, but flat or low-contrast frames give it
  little to work with.
- **pHash** (DCT hash) — slower, describes overall structure, much harder to
  fool.

Two videos are compared by the *average* Hamming distance between their
sampled frames at corresponding positions. Both the dHash and pHash averages
must independently fall within the match threshold — requiring both is what
lets the app correctly refuse to match a trimmed copy (a video missing its
first few seconds can still look close on dHash alone, since neighbouring
video frames often have similar gradients, but pHash's structural comparison
catches the difference).

Before any frame is even decoded, **duration** is used as a prefilter: two
videos more than about a second apart (plus a small percentage for
frame-rate/container rounding) cannot be the same footage, so the pair is
never opened. **MD5** is computed only for files that share a byte size with
another file, and a byte-identical match always scores 100% regardless of
duration. Matches are merged transitively: if A≈B and B≈C, all three land in
one group.

### Scan cache

Decoding sample frames is the expensive part of a scan, and a video library
barely changes between runs. Fingerprints (the small per-frame hashes, never
the frames themselves) are stored in a small SQLite database keyed on *path +
size + modification time*, so a rescan only re-decodes files that actually
changed.

Untick **Reuse cached hashes** to force a full re-fingerprint, or press
**Clear cache** to discard it. The cache is strictly an optimisation: a stale
row is detected by the size/mtime check rather than trusted, and if the
database cannot be opened the app simply scans without it.

Both the cache and your saved settings live in:

```
%LOCALAPPDATA%\DuplicateVideoFinder\
```

---

## Project layout

```
├── build.bat                       one-click Windows build
├── run.py                          launch from a source checkout
├── requirements.txt
├── pyproject.toml
├── assets/                         app.ico, app.png
├── packaging/
│   └── DuplicateVideoFinder.spec   PyInstaller build definition
├── scripts/
│   └── make_icon.py                regenerates the icon artwork
├── src/dupvideo/
│   ├── engine.py                   fingerprinting, scanning, grouping (no GUI)
│   ├── cache.py                    SQLite fingerprint cache
│   ├── settings.py                 settings persisted between launches
│   ├── shellops.py                 open / reveal / restore from Recycle Bin
│   ├── dnd.py                      folders dragged in from Explorer
│   ├── folderpanel.py              folder list, drop zone, empty state
│   ├── app.py                      main window and controller
│   ├── gallery.py                  side-by-side sampled-frame preview panel
│   ├── theme.py                    dark ttk theme
│   └── resources.py                assets, DPI, dark title bar, taskbar id
└── tests/
    └── test_engine.py              headless engine, cache and settings tests
```

## Development

```powershell
pip install -r requirements.txt
pip install pytest ruff
pytest            # 33 headless tests, no GUI or external ffmpeg required
ruff check src tests scripts run.py
```

The engine has no GUI dependencies, so `dupvideo.engine` can be imported and
tested — or scripted — on its own. Test videos are generated on the fly with
OpenCV's own bundled encoder (`cv2.VideoWriter`), so the suite needs no
external ffmpeg binary or fixture files.

---

## Notes

- Supported containers: MP4, MKV, AVI, MOV, WEBM, WMV, FLV, MPG/MPEG, M2TS/TS,
  3GP — decoded via OpenCV's bundled ffmpeg (no separate ffmpeg install
  needed).
- **Trimmed or cropped videos are not detected as duplicates.** Sampling by
  fraction of the timeline means a video missing its first 10 seconds, or
  cropped to a different aspect ratio, lands its samples on genuinely
  different content and correctly won't match. This is a known limitation,
  not a bug — the same way the sibling image finder doesn't handle cropped
  photos.
- Unreadable or corrupt files are skipped and counted in the status bar.
- Scanning runs on a thread pool off the UI thread, with a progress bar, live
  throughput and ETA, and a working Cancel button.
- Deletion always goes to the Recycle Bin via `Send2Trash`. There is no
  permanent-delete path in the app.
- Window size, split position, folder list, threshold and options are saved
  on exit and restored next launch.
- The title bar follows the app's dark theme on Windows 10/11; older builds
  that don't support the DWM attributes keep the default frame.
- Grouping is **transitive**: A≈B and B≈C puts all three in one group even if
  A and C are not directly similar. At 90% that is almost always what you
  want, but loosening the threshold a long way can chain unrelated clips
  together.

## License

[MIT](LICENSE)
