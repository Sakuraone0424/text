"""Every reason code the backend can produce has a Chinese line on the progress page (so nothing reads 原因未归类)."""
import re
from pathlib import Path

from app import waste_report

APP = Path(__file__).resolve().parent.parent / "app"
JS = (APP / "static" / "progress.js").read_text()
TABLE = JS[JS.index("const REASON"):JS.index("function el(")]
TRANSLATED = set(re.findall(r"^\s*([A-Za-z_]+):\s*\"", TABLE, re.M))


def codes_raised(*modules):
    found = set()
    for name in modules:
        text = (APP / f"{name}.py").read_text()
        found |= set(re.findall(r'raise \w*(?:Error|Needed)\(\s*"([a-z_]{4,60})"', text))
        found |= set(re.findall(r'_block\(conn, [^,]+, "([a-z_]+)"\)', text))
    return found


def test_every_code_in_the_waste_report_and_the_pipeline_is_translated():
    wanted = set(waste_report.HANDLING) | set(waste_report.ISSUE_CATEGORY) | set(waste_report.AXIS_CATEGORY)
    wanted |= codes_raised("auto_quality", "past_exam_import", "ai_jobs", "auto_generation", "auto_worker")
    missing = sorted(c for c in wanted if c not in TRANSLATED)
    assert not missing, f"no Chinese text for: {missing}"


def test_an_unknown_code_is_shown_as_itself_and_filed_under_other_not_as_unclassified():
    assert "原因未归类" not in JS
    assert "knownReason" in JS and '"其他"' in JS
