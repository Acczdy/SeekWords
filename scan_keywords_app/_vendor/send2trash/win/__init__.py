# Copyright 2017 Virgil Dupras

# This software is licensed under the "BSD" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.hardcoded.net/licenses/bsd_license

from __future__ import unicode_literals
from platform import version

# if windows is vista or newer and pywin32 is available use IFileOperation
if int(version().split(".", 1)[0]) >= 6:
    # SeekWords requires the modern recycle-only operation on supported Windows.
    # Falling back to SHFileOperation may show repeated prompts or permanently
    # delete an item when the system Recycle Bin is unavailable.
    from send2trash.win.modern import send2trash
else:
    raise ImportError("SeekWords requires Windows Vista or newer for Recycle Bin operations")
