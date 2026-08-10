import subprocess
import sys
from pathlib import Path


def test_portable_upstream_core_has_no_drift():
    result = subprocess.run(
        [sys.executable, "tools/sync_upstream.py", "--check"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
