"""
Tests for the pure half of drag-and-drop: turning a drop payload into folders.

The Win32 half needs a real Explorer window and is verified by hand, but this
part is ordinary path logic and is exactly the sort of thing that reads fine
and behaves wrong - the same reasoning that gave ``shellops`` its own test.
"""

from __future__ import annotations

import os

from dupvideo.dnd import dropped_folders


def test_a_dropped_folder_comes_through_unchanged(tmp_path):
    folder = tmp_path / "holiday"
    folder.mkdir()
    assert dropped_folders([str(folder)]) == [os.path.normpath(str(folder))]


def test_a_dropped_file_stands_for_its_folder(tmp_path):
    picture = tmp_path / "beach.jpg"
    picture.write_bytes(b"x")
    assert dropped_folders([str(picture)]) == [os.path.normpath(str(tmp_path))]


def test_a_folder_and_its_own_files_collapse_to_one_entry(tmp_path):
    """Selecting a folder *and* some of its contents is an easy drag to make."""
    folder = tmp_path / "album"
    folder.mkdir()
    for name in ("a.jpg", "b.jpg"):
        (folder / name).write_bytes(b"x")
    payload = [str(folder), str(folder / "a.jpg"), str(folder / "b.jpg")]
    assert dropped_folders(payload) == [os.path.normpath(str(folder))]


def test_several_folders_keep_the_order_they_were_dropped_in(tmp_path):
    names = ["one", "two", "three"]
    for name in names:
        (tmp_path / name).mkdir()
    payload = [str(tmp_path / name) for name in names]
    assert dropped_folders(payload) == [os.path.normpath(p) for p in payload]


def test_duplicates_collapse_case_insensitively(tmp_path):
    folder = tmp_path / "Pictures"
    folder.mkdir()
    spellings = [str(folder), str(folder).upper(), str(folder).lower()]
    assert len(dropped_folders(spellings)) == 1


def test_a_trailing_separator_is_not_a_different_folder(tmp_path):
    folder = tmp_path / "shots"
    folder.mkdir()
    assert len(dropped_folders([str(folder), str(folder) + os.sep])) == 1


def test_files_from_two_folders_yield_both_folders(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "a.jpg").write_bytes(b"x")
    (right / "b.jpg").write_bytes(b"x")
    assert dropped_folders([str(left / "a.jpg"), str(right / "b.jpg")]) == [
        os.path.normpath(str(left)), os.path.normpath(str(right))]


def test_paths_that_do_not_exist_are_dropped_not_guessed(tmp_path):
    missing = tmp_path / "gone" / "deeper" / "file.jpg"
    assert dropped_folders([str(missing)]) == []


def test_an_empty_payload_is_not_an_error():
    assert dropped_folders([]) == []
    assert dropped_folders(["", "   "[:0]]) == []


def test_spaces_in_a_path_survive(tmp_path):
    folder = tmp_path / "my holiday photos"
    folder.mkdir()
    assert dropped_folders([str(folder)]) == [os.path.normpath(str(folder))]
