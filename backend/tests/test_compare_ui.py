"""The blind comparison page (我的 → 出题对比) and the executor settings (模型与费用), at iPhone width. Answers are given inside the page."""
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_motion_ui import FULL, serve  # noqa: F401
from test_ui_screens import engine, open_page  # noqa: F401


def question(stem, answer="A"):
    return {"subject": "biochemistry", "question_type": "single_choice", "stem": stem, "options": {"A": "DNA连接酶", "B": "拓扑异构酶", "C": "解旋酶", "D": "引发酶"},
            "answer": answer, "accepted_answers": [], "max_points": 3, "rubric": [{"description": "选出DNA连接酶", "points": 3}],
            "evidence": [{"chapter": "第六章 核酸", "excerpt": "DNA连接酶封闭缺口"}]}


def pair_set(rated=False, reveal=None):
    side = lambda s, label, stem: {"side": s, "label": label, "question": question(stem),
                                   "rating": {"realism": 4, "correct": 5, "rubric": 3, "note": "还行"} if rated else None}
    data = {"set_id": 7, "created_at": "2026-11-13T01:00:00Z", "waiting": 0, "complete": bool(reveal), "revealed": bool(reveal),
            "pairs": [{"position": 1, "subject": "biochemistry", "question_type": "single_choice", "status": "rate",
                       "sides": [side("A", "甲", "哪种酶封闭冈崎片段之间的缺口？"), side("B", "乙", "冈崎片段之间的缺口由什么酶连接？")]},
                      {"position": 2, "subject": "cell_biology", "question_type": "term", "status": "one_side_only"}]}
    if reveal:
        data["reveal"] = reveal
    return data


LIST = {"sets": [{"set_id": 7, "created_at": "2026-11-13T01:00:00Z", "pairs": 2, "waiting": 0, "complete": False}]}
REVEAL = {"executors": {"claude": {"name": "Claude（订阅）", "tasks": 2, "program_check_rate": 0.5, "review_rate": 1.0, "review_pending": 0, "realism": 4.0, "correct": 5.0, "rubric": 3.0},
                        "deepseek": {"name": "DeepSeek", "tasks": 2, "program_check_rate": 1.0, "review_rate": 0.5, "review_pending": 1, "realism": 3.0, "correct": 4.0, "rubric": 4.0}},
          "pairs": [{"position": 1, "question_type": "single_choice", "A": "Claude（订阅）", "B": "DeepSeek"}]}


def open_compare(engine, live_app, set_data):
    client, _, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    serve(page, **{"/v2/compare/7/rate": {"rated": True}, "/v2/compare/7": set_data, "/v2/compare": LIST, "/v2/stats/overview": FULL})
    page.goto(base + "/training#/compare")
    page.reload()
    page.wait_for_selector("#compare-view:not([hidden])")
    errors.clear()
    return page, errors


def test_the_page_shows_two_questions_as_jia_and_yi_and_says_nothing_about_who_wrote_them(engine, live_app):
    page, errors = open_compare(engine, live_app, pair_set())
    expect(page.locator("#compare-view h1")).to_have_text("出题对比")
    expect(page.locator("#compare-set .cmp-pair").first.locator("h3")).to_have_text(["甲", "乙"])
    shown = page.locator("#compare-set").inner_text().lower()
    for leak in ("claude", "deepseek", "订阅"):
        assert leak not in shown, leak
    assert "只有一边出成了题" in shown or "只有一边" in page.locator("#compare-set").inner_text()
    assert page.locator("#compare-set .cmp-reveal").count() == 0                       # not revealed yet
    # three scores and a note per side; the controls are finger-sized and do not make the page scroll sideways
    assert page.locator("#compare-set form.cmp-rating").count() == 2
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert not errors, errors


def test_rating_needs_all_three_scores_and_sends_them(engine, live_app):
    page, errors = open_compare(engine, live_app, pair_set())
    form = page.locator("#compare-set form.cmp-rating").first
    form.locator("button[type=submit]").click()
    expect(form.locator("button[type=submit]")).to_contain_text("三项都要打分")
    form.locator("select[name=realism]").select_option("4")
    form.locator("select[name=correct]").select_option("5")
    form.locator("select[name=rubric]").select_option("3")
    form.locator("input[name=note]").fill("很像真题")
    form.locator("button[type=submit]").click()
    page.wait_for_function("window.__calls['/v2/compare/7/rate'] === 1")
    assert not errors, errors


def test_after_everything_is_rated_the_sources_and_pass_rates_are_shown(engine, live_app):
    page, errors = open_compare(engine, live_app, pair_set(rated=True, reveal=REVEAL))
    box = page.locator("#compare-set .cmp-reveal")
    expect(box).to_be_visible()
    text = box.inner_text()
    assert "Claude（订阅）" in text and "DeepSeek" in text and "50%" in text and "100%" in text and "1 道审核中" in text
    assert "甲 = Claude（订阅）" in text and "乙 = DeepSeek" in text
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert not errors, errors


def test_the_executor_settings_are_on_the_model_page(engine, live_app):
    client, _, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base)
    routing = {"routing": {"generation": "claude_first", "review": "deepseek", "grading": "deepseek", "fallback_hours": 4},
               "today": {}, "token_configured": True, "grading_trigger_configured": False}
    serve(page, **{"/v2/model/executors": routing, "/v2/stats/overview": FULL})
    page.goto(base + "/training#/model")
    page.reload()
    page.wait_for_selector("#executor-generation")
    expect(page.locator("#executor-generation")).to_have_value("claude_first")
    expect(page.locator("#executor-review")).to_have_value("deepseek")
    expect(page.locator("#executor-hours")).to_have_value("4")
    page.locator("#executor-review").select_option("claude")
    page.locator("#executor-save").click()
    expect(page.locator("#executor-note")).to_contain_text("已保存")
    page.wait_for_function("window.__calls['/v2/model/executors'] >= 2")
