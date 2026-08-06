"""
The one piece of ``shellops`` that is pure logic, and the one worth pinning.

"Show in Explorer" used to open the user's Documents folder for every path
containing a space, because ``subprocess`` quoted the whole ``/select,<path>``
argument and Explorer cannot parse that form.  Nothing about the old code
looked wrong in review - it only misbehaved against a real Explorer window - so
the *shape* of the command line is asserted here instead.
"""

from __future__ import annotations

import subprocess

from dupvideo.shellops import reveal_command


def test_the_path_is_quoted_but_the_switch_is_not() -> None:
    command = reveal_command(r"C:\Some Folder\a b.mp4")
    assert command == 'explorer.exe /select,"C:\\Some Folder\\a b.mp4"'


def test_a_path_without_spaces_is_quoted_the_same_way() -> None:
    command = reveal_command(r"C:\clips\a.mp4")
    assert command == 'explorer.exe /select,"C:\\clips\\a.mp4"'


def test_the_argument_list_form_is_not_reintroduced() -> None:
    """
    Pin the actual regression: this is what building the call as a list
    produced, and Explorer opens Documents when handed it.
    """
    path = r"C:\Some Folder\a b.mp4"
    broken = subprocess.list2cmdline(["explorer.exe", f"/select,{path}"])
    assert broken == 'explorer.exe "/select,C:\\Some Folder\\a b.mp4"'
    assert reveal_command(path) != broken
