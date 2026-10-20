"""Opt-in smoke test: recycles only files created inside its temporary fixture."""
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_keywords_app.system_files import delete_path


if __name__ == "__main__":
    assert os.name == "nt"
    with tempfile.TemporaryDirectory(prefix="SeekWords-recycle-test-") as folder:
        root = Path(folder)
        ordinary = root / "seekwords-synthetic.txt"
        ordinary.write_text("SeekWords temporary deletion test", encoding="utf-8")
        assert delete_path(ordinary) == "系统删除"
        assert not ordinary.exists()
        readonly = root / "seekwords-readonly-synthetic.txt"
        readonly.write_text("SeekWords temporary readonly deletion test", encoding="utf-8")
        readonly.chmod(stat.S_IREAD)
        assert delete_path(readonly, allow_readonly=True) == "系统删除"
        assert not readonly.exists()
    print("Native Windows recycle-only deletion passed for ordinary and readonly synthetic files")
