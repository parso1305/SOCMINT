"""P5 (D7): exports and local databases are never committable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("git") or not (ROOT / ".git").exists(), reason="needs a git checkout")
@pytest.mark.parametrize("path", ["data/exports/some-investigation-1-json/posts.json", "data/verify.db",
                                  "data/verify.db-wal", "data/socmint.db", "data/demo.db", ".env"])
def test_sensitive_paths_are_git_ignored(path):
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, check=False)
    assert r.returncode == 0, f"{path} is not git-ignored"
