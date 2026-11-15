"""The browser scripts are served as written (no build step), so they are checked here: every file parses, the lint rules that catch
real mistakes pass (a name that does not exist, a key written twice), the page-free decisions have unit tests, and every script the
page loads is also in the offline cache list."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.mark.parametrize("script", sorted(p.name for p in STATIC.glob("*.js")))
def test_every_script_parses(script):
    done = subprocess.run([NODE, "--check", str(STATIC / script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-800:]


def test_the_page_free_decisions_pass_their_unit_tests():
    tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
    assert tests
    done = subprocess.run([NODE, "--test", *tests], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    assert re.search(r"# fail 0\b", done.stdout) and not re.search(r"# pass 0\b", done.stdout)


def test_the_lint_rules_that_catch_real_mistakes_pass():
    eslint = ROOT / "node_modules" / ".bin" / "eslint"
    if not eslint.exists():
        if os.environ.get("CI"):
            pytest.fail("CI must install the lint tool (npm ci in backend/) before the tests")
        pytest.skip("run `npm ci` in backend/ to lint the scripts")
    done = subprocess.run([str(eslint), "app/static"], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stdout[-3000:]          # warnings do not fail; errors do


def test_every_script_and_style_the_page_loads_is_in_the_offline_cache_list():
    html = (STATIC / "training.html").read_text()
    worker = (STATIC / "training-sw.js").read_text()
    loaded = set(re.findall(r'(?:src|href)="(/static/[^"]+\.(?:js|css))"', html))
    assert loaded
    missing = sorted(path for path in loaded if f'"{path}"' not in worker)
    assert missing == [], missing
