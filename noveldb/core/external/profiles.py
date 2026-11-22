"""Deleting an Enter Browser profile.

A profile is a numbered browser data folder ("browser_data" for #1, "browser_data_3" for #3)
holding its sign-in sessions. Deleting one removes that folder; the profiles after it move up one
number, so the list stays 1, 2, 3 with no gap, and what the app noted per profile (the account
shown, a saved sign-in) moves with them.
"""

from __future__ import annotations

import os
import shutil
import uuid
from typing import List, Optional

from noveldb.core.external import profile_accounts, saved_logins


class ProfileInUse(OSError):
    """A browser still has one of the profiles open (Enter Browser, or a download)."""


def _name(number: int) -> str:
    return "browser_data" if number == 1 else f"browser_data_{number}"


def _roots(base: Optional[str]) -> List[str]:
    if base:
        return [base]
    # Where external_scraper keeps the profiles: the app's data folder, else a folder in the home.
    return [profile_accounts.folder(), os.path.join(os.path.expanduser("~"), ".NpiaDownloader")]


def delete(number: int, count: int, base: Optional[str] = None) -> None:
    """Delete profile `number` of `count`; the profiles after it take the number before theirs.

    Raises `ProfileInUse` (and changes nothing) when a folder cannot be moved because a browser
    has it open, and ValueError for the only profile or a number that is not one.
    """
    number, count = int(number), int(count)
    if count < 2 or not 1 <= number <= count:
        raise ValueError("there is no such profile to delete")
    tag = uuid.uuid4().hex[:8]
    for root in _roots(base):
        # Every folder is set aside first: if one is in use nothing has changed yet.
        aside = []
        try:
            for index in range(number, count + 1):
                source = os.path.join(root, _name(index))
                if os.path.isdir(source):
                    held = f"{source}.moving-{tag}"
                    os.rename(source, held)
                    aside.append((index, held, source))
        except OSError as error:
            for _, held, source in reversed(aside):
                try:
                    os.rename(held, source)
                except OSError:
                    pass
            raise ProfileInUse(f"profile #{index} is in use") from error
        for index, held, _ in aside:
            if index == number:
                shutil.rmtree(held, ignore_errors=True)
            else:
                os.rename(held, os.path.join(root, _name(index - 1)))
    if number == 1:
        # An empty first profile would be filled again from a folder an old build left beside
        # the program; a profile that exists, even empty, is left alone.
        first = os.path.join(_roots(base)[0], _name(1))
        try:
            os.makedirs(first, exist_ok=True)
            if not os.listdir(first):
                with open(os.path.join(first, "nd_new_profile"), "w", encoding="utf-8") as handle:
                    handle.write("")
        except OSError:
            pass
    profile_accounts.profile_deleted(number, base)
    saved_logins.profile_deleted(number, base)
