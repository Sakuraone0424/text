"""Every dependency names an exact version: a rebuild installs what was tested, not whatever is newest that day."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt", "requirements-harness.txt"])
def test_every_line_is_an_exact_version(name):
    loose = []
    for line in (ROOT / name).read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-r "):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?==[A-Za-z0-9_.]+", line):
            loose.append(line)
    assert loose == [], loose


def test_the_two_images_install_the_same_pinned_files():
    for dockerfile in (ROOT / "Dockerfile", ROOT.parent / "deploy" / "railway-automation" / "Dockerfile"):
        text = dockerfile.read_text()
        assert "-r requirements.txt" in text and "pip install" in text
