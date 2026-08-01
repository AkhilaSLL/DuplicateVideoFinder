"""
Detection engine - pure logic, no GUI, independently testable.

There is no whole-file perceptual hash for video the way dHash/pHash work on
a single image, so a video is fingerprinted by sampling a handful of frames
at evenly spaced positions along its timeline (skipping a margin at each end
to dodge black intro/outro/logo frames) and hashing each sampled frame with
the same dHash + pHash pair used for images. Two videos are compared by the
average Hamming distance between corresponding sampled positions.

This was tuned empirically against real re-encodes before being locked in
(see probe/ scripts run during development, not shipped): sampling 7 frames
in the middle 84% of the timeline (8% margin each side), a resized /
re-encoded / re-containerized copy of the same footage stays within ~3 bits
of 64 on both hashes, even across aggressive re-encodes (crf 30, a resize to
480p, mp4 -> mkv/avi/mov/webm). Unrelated footage lands around 29-31 bits.
Just like images, dHash alone is not enough - a trimmed copy (missing its
first few seconds) can still score low on dHash because consecutive video
frames often share similar gradients, but pHash's structural comparison
correctly reports it as different. Requiring both hashes to agree, exactly as
the image engine does, is what makes that rejection reliable. This is also
why trims and crops are not caught: sampling by fraction-of-duration means a
trimmed copy's samples land on different underlying content entirely.

Duration is used as a cheap prefilter before any frame decoding, the same
role file-size collisions play in the image engine: two videos more than a
few seconds apart cannot be the same footage, so there is no reason to open
either of them for a candidate pair like that.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import cv2
import numpy as np
from PIL import Image

with contextlib.suppress(Exception):
    # Best-effort: silences OpenCV's own logger. The "moov atom not found"
    # style warnings on corrupt files come from libavformat writing straight
    # to the process's stderr, underneath OpenCV's logging, so they can still
    # appear - they are harmless and this call only trims what it can.
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)

VIDEO_EXTS = frozenset({
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
    ".mpg", ".mpeg", ".m2ts", ".ts", ".3gp",
})

HASH_SIZE = 8
HASH_BITS = HASH_SIZE * HASH_SIZE          # 64, per sampled frame
PHASH_SIZE = 32

SAMPLE_COUNT = 7            # frames fingerprinted per video
SAMPLE_MARGIN = 0.08        # skip this fraction of the timeline at each end

# Two videos more than this many seconds apart in duration cannot be the same
# footage. The proportional term covers container/frame-rate rounding drift
# on long videos; the floor covers the same drift on short clips where 1% is
# too tight (observed up to ~0.16s drift on a 12s clip from re-containering).
DURATION_TOLERANCE_FLOOR = 1.0
DURATION_TOLERANCE_PCT = 0.01

ProgressFn = Callable[[int, int], None]

AUTOSELECT_OPTIONS = (
    "Smaller file size", "Larger file size",
    "Lower resolution", "Higher resolution",
    "Shorter duration", "Longer duration",
    "Older date", "Newer date",
)


# --------------------------------------------------------------------------- #
#  Data
# --------------------------------------------------------------------------- #

@dataclass(slots=True, frozen=True)
class CachedFingerprint:
    """One row of the on-disk fingerprint cache.  See :mod:`dupvideo.cache`."""

    key: str               # normcase(abspath(path))
    size: int
    mtime_ns: int
    width: int
    height: int
    duration: float
    dhashes: tuple[int, ...]     # one per sampled position
    phashes: tuple[int, ...]
    md5: str = ""


class CacheLike(Protocol):
    """The slice of the fingerprint cache that :func:`scan_videos` needs."""

    def load(self, paths: Iterable[str]) -> dict[str, CachedFingerprint]: ...

    def store(self, entries: Iterable[CachedFingerprint]) -> None: ...


@dataclass(slots=True, eq=False)
class VidInfo:
    """One scanned video.  ``match`` is filled in by :func:`group_duplicates`."""

    path: str
    size: int
    mtime: float
    width: int
    height: int
    duration: float
    dhashes: tuple[int, ...] = field(default_factory=tuple)
    phashes: tuple[int, ...] = field(default_factory=tuple)
    md5: str = ""           # "" when not computed - see scan_videos()
    match: float = 0.0

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def size_str(self) -> str:
        return human_size(self.size)

    @property
    def res_str(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def duration_str(self) -> str:
        return human_duration(self.duration)


def human_size(n: float) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024.0:
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024.0
    return f"{f:.1f} TB"


def human_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# --------------------------------------------------------------------------- #
#  Fingerprinting
# --------------------------------------------------------------------------- #

def dhash(image: Image.Image, hash_size: int = HASH_SIZE) -> int:
    """64-bit horizontal difference hash of ``image``."""
    img = image.convert("L").resize(
        (hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    px = np.asarray(img, dtype=np.int16)
    diff = px[:, 1:] > px[:, :-1]
    return int.from_bytes(np.packbits(diff.ravel()).tobytes(), "big")


def _dct_matrix(n: int) -> np.ndarray:
    """Orthonormal DCT-II basis, so `M @ x @ M.T` is a 2-D DCT."""
    k = np.arange(n)
    basis = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    basis[0] *= 1.0 / np.sqrt(2.0)
    return basis * np.sqrt(2.0 / n)


_DCT = _dct_matrix(PHASH_SIZE)


def phash(image: Image.Image) -> int:
    """64-bit DCT ("perceptual") hash - see dupimage.engine for the rationale."""
    img = image.convert("L").resize((PHASH_SIZE, PHASH_SIZE),
                                    Image.Resampling.LANCZOS)
    coefficients = _DCT @ np.asarray(img, dtype=np.float64) @ _DCT.T
    low = coefficients[:HASH_SIZE, :HASH_SIZE].ravel()
    bits = low > np.median(low)
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def sample_positions(count: int = SAMPLE_COUNT,
                     margin: float = SAMPLE_MARGIN) -> list[float]:
    """``count`` evenly spaced fractional positions in ``(margin, 1-margin)``."""
    lo, hi = margin, 1.0 - margin
    if count == 1:
        return [(lo + hi) / 2]
    return [lo + (hi - lo) * i / (count - 1) for i in range(count)]


def md5_of_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class UnreadableVideoError(RuntimeError):
    """Raised when a file cannot be opened or has no decodable duration."""


def fingerprint(
    path: str, sample_count: int = SAMPLE_COUNT,
    on_frame: Callable[[int, int], None] | None = None,
) -> tuple[int, int, float, tuple[int, ...], tuple[int, ...]]:
    """
    Return ``(width, height, duration, dhashes, phashes)`` for the video at
    ``path``.  Raises :class:`UnreadableVideoError` if it cannot be decoded.

    ``on_frame(done, sample_count)`` fires after each sampled position is
    seeked-to and read, whether or not that read succeeded - seeking a large
    or high-bitrate file can dominate the time a whole video takes to
    fingerprint, so this is the only sub-file granularity available to a
    caller that wants to show the scan isn't actually stuck between one
    file finishing and the next.
    """
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise UnreadableVideoError(f"could not open: {path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps > 0 else 0.0
        if duration <= 0:
            raise UnreadableVideoError(f"no usable duration: {path}")

        dhashes: list[int] = []
        phashes: list[int] = []
        duration_ms = duration * 1000.0
        for index, frac in enumerate(sample_positions(sample_count)):
            cap.set(cv2.CAP_PROP_POS_MSEC, frac * duration_ms)
            ok, frame = cap.read()
            if on_frame is not None:
                on_frame(index + 1, sample_count)
            if not ok:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            dhashes.append(dhash(img))
            phashes.append(phash(img))

        if not dhashes:
            raise UnreadableVideoError(f"no frames could be decoded: {path}")
        return width, height, duration, tuple(dhashes), tuple(phashes)
    finally:
        cap.release()


# --------------------------------------------------------------------------- #
#  Discovery
# --------------------------------------------------------------------------- #

def collect_files(folders: Iterable[str], recursive: bool) -> Iterator[str]:
    """Yield video paths under ``folders``, de-duplicated across folders."""
    seen: set[str] = set()
    for folder in folders:
        if not folder:
            continue
        folder = os.path.abspath(folder)
        if recursive:
            walker = os.walk(folder, onerror=None)
        else:
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            walker = iter([(folder, [], names)])

        for root, _dirs, names in walker:
            for name in names:
                if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                    continue
                full = os.path.join(root, name)
                key = os.path.normcase(os.path.realpath(full))
                if key in seen:
                    continue
                if not recursive and not os.path.isfile(full):
                    continue
                seen.add(key)
                yield full


# --------------------------------------------------------------------------- #
#  Scanning
# --------------------------------------------------------------------------- #

def _default_workers() -> int:
    # Decoding releases the GIL, but each video is much more expensive than an
    # image, so fewer threads are needed to saturate CPU/disk than the image
    # engine uses - a large worker count mostly adds seek contention instead
    # of throughput.
    return max(2, min(8, (os.cpu_count() or 4)))


FrameProgressFn = Callable[[str, int, int], None]


def scan_videos(
    paths: Iterable[str],
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    workers: int | None = None,
    cache: CacheLike | None = None,
    sample_count: int = SAMPLE_COUNT,
    frame_progress: FrameProgressFn | None = None,
) -> tuple[list[VidInfo], list[tuple[str, str]]]:
    """
    Fingerprint every path.  Returns ``(infos, errors)`` where ``errors`` is a
    list of ``(path, message)`` for files that could not be read.

    MD5 is only computed for files whose byte size is shared with at least one
    other file, exactly as in the image engine - a uniquely sized file cannot
    be byte-identical to anything.  Unlike images, only the *decoded
    fingerprint* (a handful of small hashes, not the sampled frames
    themselves) is ever kept in memory or in the cache.

    ``frame_progress(path, done, sample_count)`` is the sub-file counterpart
    to ``progress`` - see :func:`fingerprint`. It does not fire for cache
    hits, since those never call :func:`fingerprint` at all.
    """
    paths = list(paths)
    errors: list[tuple[str, str]] = []

    stats: dict[str, os.stat_result] = {}
    for path in paths:
        try:
            stats[path] = os.stat(path)
        except OSError as exc:
            errors.append((path, str(exc)))

    size_counts = Counter(st.st_size for st in stats.values())
    todo = list(stats)
    total = len(todo)

    cached: dict[str, CachedFingerprint] = {}
    if cache is not None:
        with contextlib.suppress(Exception):     # cache is only an optimisation
            cached = cache.load(todo)

    fresh: dict[str, CachedFingerprint] = {}
    fresh_lock = threading.Lock()

    def work(path: str) -> VidInfo:
        st = stats[path]
        need_md5 = size_counts[st.st_size] > 1
        hit = cached.get(os.path.normcase(os.path.abspath(path)))
        if (hit is not None and hit.size == st.st_size
                and hit.mtime_ns == st.st_mtime_ns
                and len(hit.dhashes) == sample_count
                and (hit.md5 or not need_md5)):
            return VidInfo(path=path, size=st.st_size, mtime=st.st_mtime,
                           width=hit.width, height=hit.height,
                           duration=hit.duration, dhashes=hit.dhashes,
                           phashes=hit.phashes,
                           md5=hit.md5 if need_md5 else "")

        on_frame = ((lambda done, total, p=path: frame_progress(p, done, total))
                    if frame_progress is not None else None)
        width, height, duration, dh, ph = fingerprint(
            path, sample_count, on_frame=on_frame)
        md5 = md5_of_file(path) if need_md5 else ""
        if cache is not None:
            entry = CachedFingerprint(
                key=os.path.normcase(os.path.abspath(path)), size=st.st_size,
                mtime_ns=st.st_mtime_ns, width=width, height=height,
                duration=duration, dhashes=dh, phashes=ph, md5=md5)
            with fresh_lock:
                fresh[entry.key] = entry
        return VidInfo(path=path, size=st.st_size, mtime=st.st_mtime,
                       width=width, height=height, duration=duration,
                       dhashes=dh, phashes=ph, md5=md5)

    infos: list[VidInfo] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers or _default_workers()) as pool:
        futures = {pool.submit(work, p): p for p in todo}
        try:
            for future in futures:
                if cancel is not None and cancel.is_set():
                    break
                path = futures[future]
                try:
                    infos.append(future.result())
                except Exception as exc:      # unreadable / unsupported file
                    errors.append((path, str(exc)))
                done += 1
                if progress is not None:
                    progress(done, total)
        finally:
            if cancel is not None and cancel.is_set():
                for future in futures:
                    future.cancel()

    if cache is not None and fresh:
        with contextlib.suppress(Exception):
            cache.store(fresh.values())

    # Futures complete out of order; sort so results are reproducible.
    infos.sort(key=lambda i: i.path)
    errors.sort()
    return infos, errors


# --------------------------------------------------------------------------- #
#  Grouping
# --------------------------------------------------------------------------- #

class _UnionFind:
    __slots__ = ("parent", "rank")

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        parent = self.parent
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:            # full path compression
            parent[x], x = root, parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def duration_tolerance(a: float, b: float) -> float:
    return DURATION_TOLERANCE_FLOOR + DURATION_TOLERANCE_PCT * min(a, b)


def _duration_candidate_pairs(
    infos: Sequence[VidInfo],
) -> Iterator[tuple[int, int]]:
    """
    Yield index pairs whose durations are close enough to *possibly* be the
    same footage, without ever comparing every pair.

    This plays the same role file-size collisions play for images: sorting by
    duration and sliding a window means only videos within tolerance of each
    other are ever compared, while still being guaranteed to find every pair
    a naive O(n^2) duration check would find (see
    ``test_duration_candidates_match_brute_force``).
    """
    order = sorted(range(len(infos)), key=lambda i: infos[i].duration)
    n = len(order)
    for i in range(n):
        a = infos[order[i]]
        j = i + 1
        while j < n:
            b = infos[order[j]]
            if b.duration - a.duration > duration_tolerance(a.duration,
                                                             b.duration):
                break
            yield order[i], order[j]
            j += 1


def sequence_distance(a: VidInfo, b: VidInfo) -> tuple[float, float]:
    """Average per-position dHash / pHash Hamming distance between two videos."""
    n = min(len(a.dhashes), len(b.dhashes))
    if n == 0:
        return float(HASH_BITS), float(HASH_BITS)
    dd = sum(hamming(a.dhashes[i], b.dhashes[i]) for i in range(n)) / n
    pp = sum(hamming(a.phashes[i], b.phashes[i]) for i in range(n)) / n
    return dd, pp


def combined_similarity(a: VidInfo, b: VidInfo) -> float:
    """
    How alike two videos are, judged by whichever hash sequence is least
    convinced - the same principle as the image engine's dual-hash gate,
    applied to the averaged sequence distance instead of a single frame.
    """
    dd, pp = sequence_distance(a, b)
    return (1.0 - max(dd, pp) / HASH_BITS) * 100.0


def group_duplicates(
    infos: Sequence[VidInfo],
    similarity_pct: float,
    cancel: threading.Event | None = None,
    progress: ProgressFn | None = None,
) -> list[list[VidInfo]]:
    """
    Cluster ``infos`` into duplicate groups.

    Membership is transitive: A~B and B~C puts all three in one group even if
    A and C are not directly similar.  ``match`` on each video is its
    similarity to the group's reference video (longest duration, then highest
    resolution, then largest file), so the reference itself always reads 100%.
    """
    n = len(infos)
    if n < 2:
        return []

    max_dist = HASH_BITS * (1.0 - similarity_pct / 100.0)
    uf = _UnionFind(n)

    # Byte-identical files.
    by_md5: dict[str, list[int]] = {}
    for i, info in enumerate(infos):
        if info.md5:
            by_md5.setdefault(info.md5, []).append(i)
    for idxs in by_md5.values():
        for other in idxs[1:]:
            uf.union(idxs[0], other)

    pairs = list(_duration_candidate_pairs(infos))
    total_pairs = len(pairs)
    for done, (i, j) in enumerate(pairs, 1):
        if cancel is not None and cancel.is_set():
            return []
        dd, pp = sequence_distance(infos[i], infos[j])
        if dd <= max_dist and pp <= max_dist:
            uf.union(i, j)
        if progress is not None and (done % 64 == 0 or done == total_pairs):
            progress(done, total_pairs)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(uf.find(i), []).append(i)

    groups: list[list[VidInfo]] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        group = [infos[m] for m in members]
        ref = max(group, key=lambda x: (x.duration, x.width * x.height,
                                        x.size, x.path))
        for vid in group:
            if vid is ref or (vid.md5 and vid.md5 == ref.md5):
                vid.match = 100.0
            else:
                vid.match = round(combined_similarity(vid, ref), 1)
        group.sort(key=lambda x: (-x.match, -x.duration, -x.size, x.path))
        groups.append(group)

    groups.sort(key=lambda g: (-len(g), -min(i.match for i in g), g[0].path))
    return groups


# --------------------------------------------------------------------------- #
#  Auto-select
# --------------------------------------------------------------------------- #

_KEEPER_KEYS: dict[str, tuple[Callable[[VidInfo], float], bool]] = {
    # criterion -> (attribute to rank on, keep the maximum?)
    # "Delete files with <X>" means keep the opposite extreme.
    "Smaller file size":  (lambda i: i.size, True),
    "Larger file size":   (lambda i: i.size, False),
    "Lower resolution":   (lambda i: i.width * i.height, True),
    "Higher resolution":  (lambda i: i.width * i.height, False),
    "Shorter duration":   (lambda i: i.duration, True),
    "Longer duration":    (lambda i: i.duration, False),
    "Older date":         (lambda i: i.mtime, True),
    "Newer date":         (lambda i: i.mtime, False),
}


def keeper_index(group: Sequence[VidInfo], criterion: str) -> int:
    """
    Index of the file to KEEP in ``group`` for the given delete criterion.

    Ties always resolve to the lowest index, so repeated runs are stable and a
    group of identical files still keeps its first member.
    """
    key = _KEEPER_KEYS.get(criterion)
    if key is None:
        return 0
    value, keep_max = key
    sign = -1 if keep_max else 1
    return min(range(len(group)), key=lambda i: (sign * value(group[i]), i))


def sampled_frame_previews(path: str, sample_count: int = SAMPLE_COUNT,
                           ) -> list[Image.Image]:
    """
    Decode the same positions used for fingerprinting, as full-size RGB
    frames, for a preview grid.  Not used during scanning - the scan itself
    only ever keeps the small hashes; this re-decodes on demand for display.
    """
    cap = cv2.VideoCapture(path)
    frames: list[Image.Image] = []
    try:
        if not cap.isOpened():
            return frames
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        duration = frame_count / fps if fps > 0 else 0.0
        if duration <= 0:
            return frames
        duration_ms = duration * 1000.0
        for frac in sample_positions(sample_count):
            cap.set(cv2.CAP_PROP_POS_MSEC, frac * duration_ms)
            ok, frame = cap.read()
            if not ok:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(rgb))
    finally:
        cap.release()
    return frames
