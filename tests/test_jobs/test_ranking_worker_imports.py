"""Worker imports must stay independent of pytest's already-loaded modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_registered_job_modules_import_in_a_fresh_interpreter():
    root = Path(__file__).resolve().parents[2]
    script = """
import importlib
import pkgutil
import src.jobs
for module in pkgutil.iter_modules(src.jobs.__path__, 'src.jobs.'):
    importlib.import_module(module.name)
print('all registered job imports OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "all registered job imports OK" in result.stdout
