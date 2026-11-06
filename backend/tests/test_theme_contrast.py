"""Both colour themes: every text/background pair that the pages use reads at 4.5:1 or better,
and no stylesheet other than tokens.css hard-codes a colour (so dark mode cannot miss a spot)."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
PAIRS = [
    ("ink", "bg"), ("ink", "card"), ("ink", "key"), ("ink", "input"), ("ink", "divider"),
    ("muted", "bg"), ("muted", "card"), ("muted", "key"), ("muted", "input"),
    ("primary", "card"), ("primary", "bg"), ("primary", "key"),
    ("primary-soft-ink", "primary-soft"), ("on-primary", "primary"), ("on-primary", "primary-press"),
    ("accent-ink", "card"), ("accent-ink", "bg"), ("accent-ink", "accent-soft"), ("accent-ink", "accent-faint"),
    ("up", "card"), ("up", "bg"), ("up", "up-soft"), ("danger", "card"), ("danger", "bg"),
    ("tag-ink", "tag"), ("disabled-ink", "disabled"), ("toast-ink", "toast-bg"),
    ("placeholder", "card"), ("chart-axis", "card"), ("chart-axis", "key"),
]


def variables(block):
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def themes():
    css = (STATIC / "tokens.css").read_text(encoding="utf-8")
    dark_at = css.index("@media (prefers-color-scheme: dark)")
    light = variables(css[:dark_at])
    dark = {**light, **variables(css[dark_at:])}
    return {"light": light, "dark": dark}


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_text_pair_has_at_least_4_5_to_1(theme):
    colours = themes()[theme]
    bad = [(fg, bg, round(ratio(colours[fg], colours[bg]), 2)) for fg, bg in PAIRS if ratio(colours[fg], colours[bg]) < 4.5]
    assert not bad, bad


def test_the_dark_palette_is_the_agreed_one():
    d = themes()["dark"]
    expected = dict(bg="#0e1014", card="#171a21", line="#262b35", divider="#222731", ink="#e8eaed", muted="#a3a9b5",
                    primary="#60a5fa", **{"primary-press": "#93c5fd", "primary-soft": "#172554", "primary-soft-ink": "#bfdbfe",
                    "key": "#111b2e", "key-line": "#1e3a5f", "accent": "#fb923c", "accent-ink": "#fdba74", "up": "#4ade80",
                    "tag": "#262b35", "tag-ink": "#cbd5e1", "disabled": "#262b35", "disabled-ink": "#8b93a1",
                    "input": "#12151b", "input-focus": "#3b82f6", "chart-bio": "#60a5fa", "chart-cell": "#fb923c", "chart-grid": "#222731"})
    assert {k: d[k].lower() for k in expected} == expected


def test_no_stylesheet_or_script_hard_codes_a_colour():
    """Colours live in tokens.css only; charts and pages take them from CSS variables."""
    pattern = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(")
    for path in list(STATIC.glob("*.css")) + list(STATIC.glob("*.js")):
        if path.name in ("tokens.css",) or path.name.startswith("review"):
            continue
        text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        found = pattern.findall(text)
        assert not found, (path.name, found[:5])
