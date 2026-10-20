Send2Trash 2.1.0 (BSD-3-Clause)
Source: https://pypi.org/project/Send2Trash/2.1.0/
Upstream: https://github.com/arsenetar/send2trash
Wheel SHA256: 0da2f112e6d6bb22de6aa6daa7e144831a4febf2a87261451c4ad849fe9a873c
Bundled to avoid requiring a separate install.
Local changes: Windows legacy and modern backends request a native warning before non-recyclable deletion (FOF_WANTNUKEWARNING); legacy backend treats user cancellation as failure.
Modern backend releases its Shell COM references on the worker thread before CoUninitialize.
Modern backend maps an aborted operation with zero HRESULT to ERROR_CANCELLED (1223).

Content-reader dependencies (unmodified wheels from official PyPI):
olefile 0.47, BSD, https://pypi.org/project/olefile/0.47/
xlrd 2.0.2, BSD, https://pypi.org/project/xlrd/2.0.2/
pypdf 6.21.1, BSD-3-Clause, https://pypi.org/project/pypdf/6.21.1/
striprtf 0.0.33, BSD-3-Clause, https://pypi.org/project/striprtf/0.0.33/
Their .dist-info directories contain upstream metadata and license files.
All source launchers and the Windows executable use these bundled dependencies.
