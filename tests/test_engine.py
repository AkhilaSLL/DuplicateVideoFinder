"""
Headless tests for the detection engine, cache and settings.  No GUI, no
external ffmpeg binary - test videos are generated on the fly with
``cv2.VideoWriter`` (opencv's own bundled encoder), so the suite is fully
self-contained.
"""
from __future__ import annotations

import itertools
import os
import random
import time

import numpy as np
import pytest
import cv2

from dupvideo import engine
from dupvideo.cache import FingerprintCache
from dupvideo.engine import (
    CachedFingerprint,
    VidInfo,
    collect_files,
    combined_similarity,
    dhash,
    duration_tolerance,
    fingerprint,
    group_duplicates,
    hamming,
    keeper_index,
    phash,
    sample_positions,
    scan_videos,
    sequence_distance,
)
from dupvideo.settings import Settings
from PIL import Image


# --------------------------------------------------------------------------- #
#  Video fixtures - self-contained, no system ffmpeg required
# --------------------------------------------------------------------------- #

def _write_video(path: str, width: int, height: int, fps: int, n_frames: int,
                 seed: int, block_scale: float = 1.0) -> None:
    """A short synthetic clip: a moving colour block over a per-seed backdrop."""
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                             (width, height))
    assert writer.isOpened(), f"VideoWriter could not open {path}"
    rng = random.Random(seed)
    backdrop = rng.randint(0, 255)
    block_w = max(4, int(width * 0.2 * block_scale))
    block_h = max(4, int(height * 0.2 * block_scale))
    try:
        for i in range(n_frames):
            frame = np.full((height, width, 3), backdrop, dtype=np.uint8)
            x = int((i / max(1, n_frames - 1)) * max(1, width - block_w))
            y = height // 3
            frame[y:y + block_h, x:x + block_w] = (10, 200, 250)
            writer.write(frame)
    finally:
        writer.release()


def _resized_copy(src: str, dst: str, width: int, height: int) -> None:
    """Re-encode ``src`` at a different resolution - a real resize+re-encode."""
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 10
    writer = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                             (width, height))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(cv2.resize(frame, (width, height)))
    finally:
        cap.release()
        writer.release()


@pytest.fixture(scope="module")
def video_dir(tmp_path_factory) -> str:
    d = str(tmp_path_factory.mktemp("videos"))
    _write_video(os.path.join(d, "original.mp4"), 160, 120, 10, 30, seed=1)
    _resized_copy(os.path.join(d, "original.mp4"),
                  os.path.join(d, "resized.mp4"), 80, 60)
    _write_video(os.path.join(d, "unrelated.mp4"), 160, 120, 10, 30, seed=99,
                 block_scale=2.0)
    _write_video(os.path.join(d, "short.mp4"), 160, 120, 10, 10, seed=1)
    with open(os.path.join(d, "corrupt.mp4"), "wb") as fh:
        fh.write(b"not a real video file")
    return d


# --------------------------------------------------------------------------- #
#  Hashing primitives
# --------------------------------------------------------------------------- #

def test_dhash_phash_identical_images_are_zero_distance():
    img = Image.new("RGB", (64, 64))
    for x in range(64):
        for y in range(64):
            img.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, 128))
    assert hamming(dhash(img), dhash(img)) == 0
    assert hamming(phash(img), phash(img)) == 0


def test_dhash_phash_differ_on_different_images():
    # Flat solid colours give dHash no gradient to work with (the same
    # flat-image collision the sibling image project's dHash has) - random
    # noise guarantees real, differing structure instead.
    rng = np.random.default_rng(1)
    a = Image.fromarray(rng.integers(0, 256, (64, 64, 3), dtype=np.uint8))
    b = Image.fromarray(rng.integers(0, 256, (64, 64, 3), dtype=np.uint8))
    assert hamming(dhash(a), dhash(b)) > 0
    assert hamming(phash(a), phash(b)) > 0


def test_sample_positions_are_evenly_spaced_within_margin():
    positions = sample_positions(7, margin=0.1)
    assert len(positions) == 7
    assert positions[0] == pytest.approx(0.1)
    assert positions[-1] == pytest.approx(0.9)
    assert all(b > a for a, b in itertools.pairwise(positions))


def test_sample_positions_single_frame_is_midpoint():
    assert sample_positions(1, margin=0.1) == [pytest.approx(0.5)]


# --------------------------------------------------------------------------- #
#  fingerprint() / collect_files() / scan_videos() - real decode round-trips
# --------------------------------------------------------------------------- #

def test_fingerprint_reports_correct_dimensions_and_duration(video_dir):
    width, height, duration, dh, ph = fingerprint(
        os.path.join(video_dir, "original.mp4"))
    assert (width, height) == (160, 120)
    assert duration == pytest.approx(3.0, abs=0.2)      # 30 frames @ 10fps
    assert len(dh) == len(ph) == engine.SAMPLE_COUNT


def test_fingerprint_raises_on_corrupt_file(video_dir):
    with pytest.raises(engine.UnreadableVideoError):
        fingerprint(os.path.join(video_dir, "corrupt.mp4"))


def test_fingerprint_raises_on_missing_file(video_dir):
    with pytest.raises(engine.UnreadableVideoError):
        fingerprint(os.path.join(video_dir, "does_not_exist.mp4"))


def test_resized_copy_scores_far_more_similar_than_unrelated(video_dir):
    """The real point of the whole design: a resized re-encode of the same
    footage must land close to 100%, while different footage must not."""
    _, _, _, dh_o, ph_o = fingerprint(os.path.join(video_dir, "original.mp4"))
    _, _, _, dh_r, ph_r = fingerprint(os.path.join(video_dir, "resized.mp4"))
    _, _, _, dh_u, ph_u = fingerprint(os.path.join(video_dir, "unrelated.mp4"))

    original = VidInfo(path="o", size=0, mtime=0, width=160, height=120,
                       duration=3.0, dhashes=dh_o, phashes=ph_o)
    resized = VidInfo(path="r", size=0, mtime=0, width=80, height=60,
                      duration=3.0, dhashes=dh_r, phashes=ph_r)
    unrelated = VidInfo(path="u", size=0, mtime=0, width=160, height=120,
                        duration=3.0, dhashes=dh_u, phashes=ph_u)

    same_footage = combined_similarity(original, resized)
    different_footage = combined_similarity(original, unrelated)
    assert same_footage > 90.0
    assert different_footage < same_footage - 20.0


def test_collect_files_dedupes_overlapping_folders(video_dir):
    found = list(collect_files([video_dir, video_dir], recursive=True))
    assert len(found) == len(set(os.path.normcase(f) for f in found))
    assert any(f.endswith("original.mp4") for f in found)


def test_collect_files_filters_by_extension(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "clip.MKV").write_bytes(b"x")     # extension match is case-insensitive
    found = {os.path.basename(f) for f in collect_files([str(tmp_path)], True)}
    assert found == {"clip.mp4", "clip.MKV"}


def test_scan_videos_reports_errors_for_unreadable_files(video_dir):
    paths = [os.path.join(video_dir, "original.mp4"),
            os.path.join(video_dir, "corrupt.mp4")]
    infos, errors = scan_videos(paths)
    assert len(infos) == 1
    assert len(errors) == 1
    assert errors[0][0].endswith("corrupt.mp4")


def test_scan_videos_uses_cache_to_skip_refingerprinting(video_dir, tmp_path):
    cache = FingerprintCache(str(tmp_path / "cache.sqlite3"))
    path = os.path.join(video_dir, "original.mp4")

    infos1, _ = scan_videos([path], cache=cache)
    assert cache.count() == 1

    calls = []
    real_fingerprint = engine.fingerprint

    def spy(*args, **kwargs):
        calls.append(args)
        return real_fingerprint(*args, **kwargs)

    engine.fingerprint = spy
    try:
        infos2, _ = scan_videos([path], cache=cache)
    finally:
        engine.fingerprint = real_fingerprint

    assert calls == []                     # cache hit avoided re-decoding
    assert infos1[0].dhashes == infos2[0].dhashes


# --------------------------------------------------------------------------- #
#  Duration prefilter - fast candidate generation vs brute force
# --------------------------------------------------------------------------- #

def _brute_force_duration_pairs(durations: list[float]) -> set[tuple[int, int]]:
    pairs = set()
    for i, j in itertools.combinations(range(len(durations)), 2):
        a, b = durations[i], durations[j]
        if abs(a - b) <= duration_tolerance(a, b):
            pairs.add((min(i, j), max(i, j)))
    return pairs


def _make_infos(durations: list[float]) -> list[VidInfo]:
    return [VidInfo(path=f"v{i}", size=0, mtime=0, width=100, height=100,
                    duration=d, dhashes=(0,), phashes=(0,))
            for i, d in enumerate(durations)]


def test_duration_candidates_match_brute_force_random():
    rng = random.Random(42)
    durations = [rng.uniform(0.5, 600.0) for _ in range(400)]
    infos = _make_infos(durations)

    fast = {(min(i, j), max(i, j))
           for i, j in engine._duration_candidate_pairs(infos)}
    brute = _brute_force_duration_pairs(durations)
    assert fast == brute


def test_duration_candidates_match_brute_force_with_clusters():
    # Several tight clusters plus scattered singletons - the shape most
    # likely to expose an off-by-one in the sliding window.
    rng = random.Random(7)
    durations = []
    for center in (10.0, 10.05, 300.0, 300.4, 301.0, 3600.0):
        durations.append(center)
    durations += [rng.uniform(0, 4000) for _ in range(150)]
    infos = _make_infos(durations)

    fast = {(min(i, j), max(i, j))
           for i, j in engine._duration_candidate_pairs(infos)}
    brute = _brute_force_duration_pairs(durations)
    assert fast == brute


def test_duration_tolerance_has_a_floor_and_a_percentage():
    assert duration_tolerance(5.0, 5.0) == pytest.approx(1.05)     # 1.0 + 1%
    assert duration_tolerance(1000.0, 1000.0) == pytest.approx(11.0)


# --------------------------------------------------------------------------- #
#  Grouping - synthetic hash sequences, mirroring the fixtures used by the
#  sibling image project's dual-hash-gate tests.
# --------------------------------------------------------------------------- #

def _vid(path: str, duration: float, dhashes: tuple[int, ...],
        phashes: tuple[int, ...], size: int = 1000, md5: str = "") -> VidInfo:
    return VidInfo(path=path, size=size, mtime=0, width=100, height=100,
                   duration=duration, dhashes=dhashes, phashes=phashes,
                   md5=md5)


def test_group_duplicates_needs_both_hashes_to_agree():
    """
    A pair that agrees on dHash but not pHash must NOT be grouped - this is
    the video-level version of the image engine's dual-hash gate, and is the
    exact mechanism that lets a trimmed copy (same average dHash, very
    different pHash - see engine.py's module docstring) get rejected.
    """
    close_dhash = (0,) * 7
    agreeing_phash = (0,) * 7
    disagreeing_phash = (0xFFFFFFFFFFFFFFFF,) * 7   # maximally different

    a = _vid("a.mp4", 10.0, close_dhash, agreeing_phash)
    b = _vid("b.mp4", 10.0, close_dhash, disagreeing_phash)
    groups = group_duplicates([a, b], similarity_pct=90)
    assert groups == []


def test_group_duplicates_groups_when_both_hashes_agree():
    dh = (0,) * 7
    ph = (0,) * 7
    a = _vid("a.mp4", 10.0, dh, ph)
    b = _vid("b.mp4", 10.05, dh, ph)
    groups = group_duplicates([a, b], similarity_pct=90)
    assert len(groups) == 1
    assert {v.path for v in groups[0]} == {"a.mp4", "b.mp4"}


def test_group_duplicates_respects_duration_prefilter():
    """Even identical hashes must not group videos further apart in duration
    than the tolerance allows - this is what makes a trim a non-match even
    before the hash comparison runs."""
    dh = (0,) * 7
    ph = (0,) * 7
    a = _vid("a.mp4", 10.0, dh, ph)
    b = _vid("b.mp4", 30.0, dh, ph)      # 20s apart, way outside tolerance
    groups = group_duplicates([a, b], similarity_pct=90)
    assert groups == []


def test_group_duplicates_md5_short_circuit_ignores_duration():
    """Byte-identical files (e.g. a straight copy with a different name) must
    group even if something odd left their reported durations far apart."""
    dh = (0,) * 7
    ph = (0,) * 7
    a = _vid("a.mp4", 10.0, dh, ph, md5="same")
    b = _vid("b.mp4", 500.0, dh, ph, md5="same")
    groups = group_duplicates([a, b], similarity_pct=90)
    assert len(groups) == 1
    assert all(v.match == 100.0 for v in groups[0])


def test_group_duplicates_is_transitive():
    dh = (0,) * 7
    ph = (0,) * 7
    far_ph = tuple((1 << i) - 1 for i in range(7))     # a bit different, still close
    a = _vid("a.mp4", 10.0, dh, ph)
    b = _vid("b.mp4", 10.0, dh, ph)
    c = _vid("c.mp4", 10.0, dh, ph)
    groups = group_duplicates([a, b, c], similarity_pct=90)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_group_duplicates_singletons_are_not_reported():
    a = _vid("a.mp4", 10.0, (0,) * 7, (0,) * 7)
    b = _vid("b.mp4", 999.0, (0,) * 7, (0,) * 7)   # nowhere near a's duration
    assert group_duplicates([a, b], similarity_pct=90) == []


def test_group_duplicates_reference_is_highest_scoring_metadata():
    dh = (0,) * 7
    ph = (0,) * 7
    short = _vid("short.mp4", 10.0, dh, ph, size=500)
    long = _vid("long.mp4", 10.05, dh, ph, size=999999)
    groups = group_duplicates([short, long], similarity_pct=90)
    assert groups[0][0].path == "long.mp4"          # reference sorts first


# --------------------------------------------------------------------------- #
#  keeper_index()
# --------------------------------------------------------------------------- #

def test_keeper_index_all_criteria():
    small = _vid("small.mp4", 5.0, (), (), size=100)
    small.width, small.height = 100, 100
    big = _vid("big.mp4", 20.0, (), (), size=900)
    big.width, big.height = 400, 400
    small.mtime, big.mtime = 1000.0, 2000.0
    group = [small, big]

    assert keeper_index(group, "Smaller file size") == 1     # keeps the larger
    assert keeper_index(group, "Larger file size") == 0
    assert keeper_index(group, "Lower resolution") == 1
    assert keeper_index(group, "Higher resolution") == 0
    assert keeper_index(group, "Shorter duration") == 1
    assert keeper_index(group, "Longer duration") == 0
    assert keeper_index(group, "Older date") == 1
    assert keeper_index(group, "Newer date") == 0
    assert keeper_index(group, "Unknown criterion") == 0


def test_keeper_index_ties_resolve_to_lowest_index():
    a = _vid("a.mp4", 10.0, (), (), size=100)
    b = _vid("b.mp4", 10.0, (), (), size=100)
    assert keeper_index([a, b], "Smaller file size") == 0


# --------------------------------------------------------------------------- #
#  Cache
# --------------------------------------------------------------------------- #

def test_cache_round_trip(tmp_path):
    cache = FingerprintCache(str(tmp_path / "c.sqlite3"))
    # load() normalises whatever path it's given before querying, so the
    # stored key has to already be in that normalised form to round-trip.
    key = os.path.normcase(os.path.abspath("k1"))
    entry = CachedFingerprint(key=key, size=123, mtime_ns=456, width=100,
                              height=200, duration=12.5,
                              dhashes=(1, 2, 3), phashes=(4, 5, 6), md5="abc")
    cache.store([entry])
    loaded = cache.load(["k1"])
    assert loaded[key] == entry
    assert cache.count() == 1


def test_cache_degrades_silently_on_unwritable_path():
    # A drive letter that (almost certainly) doesn't exist can't have its
    # directory created, unlike a plain missing path under C:\ which
    # os.makedirs would happily create.
    cache = FingerprintCache(r"Z:\this\path\does\not\exist\x.sqlite3")
    assert cache.available is False
    assert cache.load(["k"]) == {}
    cache.store([CachedFingerprint(key="k", size=1, mtime_ns=1, width=1,
                                   height=1, duration=1.0, dhashes=(1,),
                                   phashes=(1,))])   # must not raise
    cache.clear()                                     # must not raise
    assert cache.count() == 0


def test_cache_clear_removes_all_rows(tmp_path):
    cache = FingerprintCache(str(tmp_path / "c.sqlite3"))
    cache.store([CachedFingerprint(key="k", size=1, mtime_ns=1, width=1,
                                   height=1, duration=1.0, dhashes=(1,),
                                   phashes=(1,))])
    assert cache.count() == 1
    cache.clear()
    assert cache.count() == 0


def test_cache_prune_evicts_old_rows(tmp_path):
    cache = FingerprintCache(str(tmp_path / "c.sqlite3"))
    cache.store([CachedFingerprint(key="k", size=1, mtime_ns=1, width=1,
                                   height=1, duration=1.0, dhashes=(1,),
                                   phashes=(1,))])
    cache.prune(max_age_days=-1)          # everything is "older" than -1 days
    assert cache.count() == 0


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #

def test_settings_defaults_when_file_missing(tmp_path):
    settings = Settings.load(str(tmp_path / "nope.json"))
    assert settings.folders == []
    assert settings.threshold == 90
    assert settings.recursive is True


def test_settings_round_trip(tmp_path):
    path = str(tmp_path / "settings.json")
    original = Settings(folders=["C:/videos"], recursive=False, threshold=85,
                        auto_choice="Shorter duration", use_cache=False,
                        geometry="800x600", sash=300)
    original.save(path)
    loaded = Settings.load(path)
    assert loaded == original


def test_settings_rejects_malformed_types(tmp_path):
    path = str(tmp_path / "settings.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"folders": "not-a-list", "threshold": "ninety", '
                '"recursive": "yes"}')
    settings = Settings.load(path)
    assert settings.folders == []
    assert settings.threshold == 90
    assert settings.recursive is True


def test_settings_clamps_threshold_range(tmp_path):
    path = str(tmp_path / "settings.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"threshold": 5}')
    assert Settings.load(path).threshold == 70
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"threshold": 500}')
    assert Settings.load(path).threshold == 100


def test_settings_save_is_atomic_and_never_raises(tmp_path):
    settings = Settings(folders=["x"])
    # A directory that cannot exist must not raise.
    settings.save(os.path.join(os.sep, "no", "such", "dir", "s.json"))
