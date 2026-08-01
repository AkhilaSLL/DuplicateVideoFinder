# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands assume the virtual environment is active (`.venv\Scripts\activate` on Windows) or are prefixed accordingly.

```powershell
# Setup
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pytest ruff pyright        # dev tools, not in requirements.txt

# Run from source
python run.py

# Test
pytest                                 # 33 headless tests, no GUI required, self-contained
pytest tests/test_engine.py::test_group_duplicates_needs_both_hashes_to_agree   # single test
pytest -k duration                     # by keyword

# Lint / type-check
ruff check src tests scripts run.py --select E,F,W,B,UP,SIM,I
pyright                                # config lives in [tool.pyright] in pyproject.toml

# Build the Windows .exe
python scripts\make_icon.py            # regenerate assets/app.ico + app.png (only if assets/ missing)
pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\DuplicateVideoFinder.spec
# or just double-click build.bat, which does all of the above from a clean venv
```

There is no `pip install -e .` workflow in active use — `run.py` inserts `src/` onto `sys.path` manually, and PyInstaller's `pathex` does the same for the build. If you add a new module, it only needs to live under `src/dupvideo/`.

## Sibling project

This is a from-scratch build with the same standards as `../DuplicateImageFinder` (same author, same dark UI, same Recycle-Bin-only deletion policy). Its `CLAUDE.md`/`README.md` are worth reading for patterns that transferred directly — `shellops.py`, `resources.py` and `theme.py` here are near-verbatim copies. Video introduced problems that have no image-world answer; see "Detection pipeline" below for how each was resolved, and do not re-derive them from scratch — the reasoning was empirical (see the probing method below) and is easy to get subtly wrong by intuition alone.

**Probing method**: before writing engine.py, real re-encoded/resized/re-containerized copies of a synthetic test clip were generated with `ffmpeg` (mp4/mkv/avi/mov/webm, several codecs, one trimmed copy, one unrelated clip) and run through candidate hashing strategies with small throwaway scripts. The numbers quoted below (e.g. "~3 bits of drift on same footage vs ~30 bits on different footage") came from that, not from assumption. If you change the sampling strategy, margin, or sample count, re-verify against real re-encodes rather than only synthetic hash arithmetic — synthetic tests can only check the *math* (grouping, prefiltering), not whether the fingerprint itself still tracks real footage.

## Architecture

**The engine (`engine.py`) has zero GUI dependencies** and is the only module covered by the test suite. Everything GUI-related (`app.py`, `gallery.py`, `theme.py`) is exercised only by manual/smoke testing, never by `pytest`. When changing detection logic, prefer adding a test in `tests/test_engine.py` over manual verification.

### Why video needed a different design than images

- **There is no whole-file perceptual hash for video.** dHash/pHash work on a single image; a video is fingerprinted by sampling `SAMPLE_COUNT` (7) frames at evenly spaced *fractional* positions along the timeline, skipping `SAMPLE_MARGIN` (8%) at each end to dodge black intro/outro/logo frames, and hashing each sampled frame with the same dHash+pHash pair the image project uses. Comparing two videos means comparing these two *sequences* position-by-position, not a single 64-bit value.
- **Decoding backend: `opencv-python-headless`, not a bundled `ffmpeg.exe`.** Both were probed against real mp4/mkv/avi/mov/webm files. OpenCV's own bundled ffmpeg decoded every one of them correctly (frame count, fps, seeking via `CAP_PROP_POS_MSEC`) with no extra binary to ship, keeping the exe roughly the size of the image finder's instead of +70-100MB. There is no ffmpeg subprocess anywhere in this codebase.
- **Duration is the prefilter, playing the role file-size collisions play for images** (`_duration_candidate_pairs`, `duration_tolerance`). Two videos can only be the same footage if their durations are close, so no pair outside `duration_tolerance()` (a 1.0s floor plus 1% of the shorter duration — sized from *observed* container/frame-rate rounding drift of up to ~0.16s on a 12s re-containered clip) is ever opened for frame comparison. This is a sliding window over durations sorted ascending, not a hash bucket, because duration is continuous rather than discrete like file size; `test_duration_candidates_match_brute_force_random` and `..._with_clusters` are the safety net that this window finds exactly the same candidate pairs an O(n²) duration check would, so don't touch `_duration_candidate_pairs` without keeping those green.
- **The dual-hash gate still applies, just aggregated across the sequence.** `sequence_distance()` averages per-position dHash distance and per-position pHash distance separately across all sampled positions; `combined_similarity()` takes whichever average is *worse*, exactly mirroring the image engine's "both hashes must agree" rule. This is not cosmetic: probing showed a trimmed copy (missing its first few seconds) can still score a near-zero *average dHash* distance against the original, because consecutive video frames often share similar gradients — dHash alone would wrongly call it a match. pHash's structural comparison correctly reports the trim as different. `test_group_duplicates_needs_both_hashes_to_agree` encodes this directly.
- **Trims and crops are not caught, by design** — this is the direct consequence of fraction-of-duration sampling: a trimmed copy's sample positions land on genuinely different underlying content. Don't scope-creep into shot alignment/detection to "fix" this for v1; it's documented as a limitation in the README, the same way the image project documents not handling cropped photos.
- **No frame data is ever cached, only hashes.** `sampled_frame_previews()` (used only by the GUI's preview grid) re-decodes on demand; the scan path (`fingerprint()`, `scan_videos()`) discards every decoded frame the instant its two hashes are computed. `gallery.py`'s `_FrameSetCache` is a separate, GUI-only LRU cache with its own byte budget (160MB vs the image project's 192MB — sized down because a video's preview is `SAMPLE_COUNT` frames, not one).

### Detection pipeline (`engine.py`)

1. `collect_files()` walks the configured folders and de-duplicates paths across overlapping selections, using `normcase(realpath(...))` as the key — identical to the image engine.
2. `scan_videos()` fingerprints every file on a `ThreadPoolExecutor` (2-8 workers — fewer than images' 2-16, since each video is far more expensive to decode and extra threads mostly add seek contention rather than throughput). For each file:
   - `fingerprint()` opens the file once with `cv2.VideoCapture`, reads `fps`/`frame_count` to get `duration`, then seeks to each sampled fractional position with `CAP_PROP_POS_MSEC` and decodes just that one frame — never the whole file.
   - **MD5** is computed only for files sharing a byte size with another file (`Counter` over `st_size`), exactly as in the image engine — a uniquely sized file cannot be byte-identical to anything.
   - A corrupt/unreadable/zero-duration file raises `UnreadableVideoError`, which `scan_videos()` catches and reports in `errors` rather than aborting the whole scan.
3. `group_duplicates()` clusters videos with the same `_UnionFind` as the image engine. Three things feed it, cheapest first:
   - Files sharing an MD5 union immediately (byte-identical), **ignoring the duration prefilter** — `test_group_duplicates_md5_short_circuit_ignores_duration` guards this, since an exact-copy file is trivially a match regardless of what its metadata claims.
   - `_duration_candidate_pairs()` yields every pair within `duration_tolerance()` of each other.
   - Each candidate pair is checked with `sequence_distance()`; both the aggregate dHash and aggregate pHash distance must be within `max_dist` (see "dual-hash gate" above) before `uf.union()` is called.
   - Reported `match` on each `VidInfo` is `combined_similarity()` against the group's reference video (longest duration, then highest resolution, then largest file, then path — for determinism).
4. `keeper_index()` is the same lookup-table pattern as images, extended with `Shorter duration` / `Longer duration` criteria alongside the file-size/resolution/date ones.

Video libraries are expected to be orders of magnitude smaller in file count than photo libraries (hundreds, not hundreds of thousands), and duration is a far more selective prefilter than file size ever was for images, so there was no need to port the image engine's multi-index-hashing (`_segments`/`_near_pairs`) machinery — a duration-bucketed near-linear scan is enough here, and simpler. If a future scan target is large enough that this stops being true, that's the first place to look.

### Fingerprint cache (`cache.py`)

Same SQLite/`CacheLike` protocol design as the image engine, keyed on `normcase(abspath(path))` with `size` + `mtime_ns` as staleness check, degrading to a silent no-op on any failure. The schema additionally stores `duration` and packs the *tuple* of per-position dHash/pHash values as a comma-separated hex string (`_pack`/`_unpack`) rather than a single hash column, since a cache hit needs to restore the whole sampled sequence, not one value. `MAX_ROWS` is 100k rather than the image project's 400k, since a video fingerprint cache is expected to track a much smaller library.

### Settings (`settings.py`)

Unchanged in structure from the image project: hand-parsed field-by-field so a corrupted file can't inject a wrong type, written atomically via `tmp` + `os.replace`.

### GUI (`app.py`, `gallery.py`, `theme.py`, `resources.py`, `shellops.py`)

- `app.py` mirrors the image project's `App` almost exactly (same `POLL_MS` queue-polling architecture, same incremental `_sync_row`/`_bulk_update` selection pattern — don't reintroduce the O(n²) per-checkbox-write bug that pattern exists to avoid). The differences are all schema: an extra Duration tree column, `AUTOSELECT_OPTIONS` includes shorter/longer duration, and the scan-phase label reads "Fingerprinting videos…" / "Comparing videos…" instead of "Hashing images…" / "Grouping duplicates…".
- `gallery.py`'s `Gallery` shows a **grid of sampled frames** per video instead of one thumbnail — full playback would need `python-vlc` (which needs VLC installed), out of scope for a single-file exe. `_FrameSetCache` decodes via `sampled_frame_previews()` (same sample positions used for fingerprinting) and caches the *frame list*, not a single image. Double-click on any tile still opens the real file in the user's default player via `shellops.open_file`, unchanged from the image project.
- `resources.py`/`shellops.py`/`theme.py` are copied over near-verbatim from the image project (only `APP_ID` and the "PREVIEW" default message text changed) — they were never image-specific to begin with.

### Packaging

`packaging/DuplicateVideoFinder.spec` adds `cv2` to `hiddenimports` and otherwise matches the image project's spec (same exclude list to keep the onefile exe from pulling in matplotlib/scipy/pandas transitively). `opencv-python-headless` has no Qt/GTK GUI backend to strip since the headless wheel never bundled one.
