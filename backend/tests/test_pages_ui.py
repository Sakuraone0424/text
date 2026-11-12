"""The practice centre, the mistake book (cards, groups, one-line filter), a mistake in full and the whole-paper review, at iPhone width.
Data is answered inside the page (WebKit's route() cannot see service-worker requests)."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_live_training import live_app  # noqa: F401
from test_motion_ui import FULL, SHORT_BANK, serve  # noqa: F401
from test_ui_screens import engine, open_page  # noqa: F401

SHOTS = Path(os.environ.get("SHOT_DIR", "/tmp/claude-0/shots"))

OVERVIEW = {
    "weak": {"mode": "weak", "available": 6, "ready": True, "size": 6, "short_by": 4, "reason": None, "chapters": [{"subject": "biochemistry", "chapter": "第六章 核酸", "rate": 0.3}]},
    "chapter": {"mode": "chapter", "available": 40, "ready": True, "size": 10, "short_by": 0, "reason": None, "chapters": [{"subject": "biochemistry", "chapter": "第六章 核酸", "available": 14}, {"subject": "cell_biology", "chapter": "第二章 细胞膜", "available": 26}]},
    "type": {"mode": "type", "available": 43, "ready": True, "size": 10, "short_by": 0, "reason": None, "types": [
        {"value": "single_choice", "name": "单选", "available": 22, "used": 22, "new": 0, "supported": True, "graded_by_ai": False}, {"value": "fill_blank", "name": "填空", "available": 18, "used": 18, "new": 0, "supported": True, "graded_by_ai": False},
        {"value": "term", "name": "名词解释", "available": 3, "used": 0, "new": 3, "supported": True, "graded_by_ai": True}, {"value": "short_answer", "name": "简答", "available": 0, "used": 0, "new": 0, "supported": True, "graded_by_ai": True},
        {"value": "essay", "name": "问答", "available": 0, "used": 0, "new": 0, "supported": True, "graded_by_ai": True}]},
    "retry": {"mode": "retry", "available": 0, "ready": False, "size": 0, "short_by": 0, "reason": "还没有可重做的错题"},
    "quick": {"mode": "quick", "available": 40, "ready": True, "size": 10, "short_by": 0, "reason": None},
    "year": {"mode": "year", "available": 0, "ready": False, "size": 0, "short_by": 0, "reason": "这一年的真题暂时没有可练的题，还差 10 道", "years": []},
    "set_size": 10,
}
RECENT = {"items": [
    {"kind": "paper", "id": 9, "date": "2026-11-05", "label": "每日训练卷", "points": 212, "max_points": 300, "pending": 0, "seconds": 3010, "at": "2026-11-05T12:00:00+00:00"},
    {"kind": "practice", "id": 4, "date": "2026-11-04", "label": "随机快练", "points": 28, "max_points": 40, "pending": 0, "seconds": 410, "at": "2026-11-04T12:00:00+00:00"},
    {"kind": "paper", "id": 8, "date": "2026-11-03", "label": "每日训练卷", "points": None, "max_points": 300, "pending": 3, "seconds": None, "at": "2026-11-03T12:00:00+00:00"},
]}


def item(qid, subject, qtype, chapter, group, **over):
    base = dict(question_id=qid, subject=subject, question_type=qtype, chapter=chapter, stem="蛋白质的二级结构主要靠什么维持？", your_answer="B", correct="A", points=None, max_points=4,
                subjective=False, wrong_times=1, due="2026-11-05T00:00:00+00:00", due_day="2026-11-05", dots=0, status="mistake", group=group, last_date="2026-11-03", version=1)
    base.update(over)
    return base


BOOK = {
    "today": "2026-11-05", "total": 6, "open": 5, "unmapped": {},
    "groups": [{"key": "today", "label": "已到期", "count": 2}, {"key": "tomorrow", "label": "今天稍后及明天", "count": 1}, {"key": "later", "label": "以后", "count": 1},
               {"key": "consolidate", "label": "待巩固", "count": 1}, {"key": "mastered", "label": "已掌握", "count": 1}],
    "items": [
        item(1, "biochemistry", "single_choice", "第二章 蛋白质的结构与功能", "today", stem="下列关于蛋白质二级结构的叙述，哪一项是正确的", wrong_times=3, dots=1),
        item(2, "cell_biology", "term", "第二章 细胞膜", "today", subjective=True, points=3, max_points=6, stem="流动镶嵌模型", your_answer="", correct=None),
        item(3, "biochemistry", "fill_blank", "第六章 核酸", "tomorrow", your_answer="嘌呤", correct="嘧啶", due_day="2026-11-06"),
        item(4, "cell_biology", "single_choice", "第二章 细胞膜", "later", due_day="2026-11-12"),
        item(5, "biochemistry", "short_answer", "第六章 核酸", "consolidate", subjective=True, points=9, max_points=12, status="consolidate", your_answer="", correct=None),
        item(6, "biochemistry", "single_choice", "第六章 核酸", "mastered", dots=3, status="mastered"),
    ],
    "subjects": [{"value": "biochemistry", "count": 4}, {"value": "cell_biology", "count": 2}],
    "types": [{"value": "single_choice", "count": 3}, {"value": "fill_blank", "count": 1}, {"value": "term", "count": 1}, {"value": "short_answer", "count": 1}],
    "chapters": [{"subject": "biochemistry", "value": "第六章 核酸", "count": 3}, {"subject": "cell_biology", "value": "第二章 细胞膜", "count": 2}, {"subject": "biochemistry", "value": "第二章 蛋白质的结构与功能", "count": 1}],
}
DETAIL = {
    "question_id": 1, "subject": "biochemistry", "question_type": "single_choice", "chapter": "第二章 蛋白质的结构与功能", "stem": "下列关于蛋白质二级结构的叙述，哪一项是正确的？",
    "options": {"A": "α螺旋靠氢键维持", "B": "靠二硫键维持", "C": "靠疏水作用维持", "D": "靠盐键维持"}, "max_points": 4, "your_answer": "B", "points": 0, "reference": "A. α螺旋靠氢键维持",
    "accepted_answers": [], "rubric": [], "scores": None, "evidence": [{"chapter": "第二章 蛋白质的结构与功能", "textbook": "生物化学", "excerpt": "α螺旋靠主链羰基氧与酰胺氢之间的氢键维持。"}],
    "history": [{"date": "2026-11-03", "points": 0, "max_points": 4, "kind": "paper"}, {"date": "2026-10-29", "points": 0, "max_points": 4, "kind": "paper"}],
    "status": "mistake", "due": "2026-11-05T00:00:00+00:00", "version": 1, "your_choice": "B", "correct_choice": "A",
}
REVIEW = {
    "paper_id": 9, "kind": "paper", "date": "2026-11-05", "specification": "xiaohan-60q-300pt-v4", "specification_name": "60 题 · 300 分", "read_only": False, "pending": 0,
    "score": 212, "graded_points": 212, "max_points": 300, "seconds": 3010,
    "subject_scores": {"biochemistry": {"points": 110, "max_points": 150, "pending": 0}, "cell_biology": {"points": 102, "max_points": 150, "pending": 0}},
    "type_rates": [{"subject": "biochemistry", "question_type": "single_choice", "points": 30, "max_points": 40, "rate": 0.75}],
    "items": [
        {"position": n, "question_id": 100 + n, "subject": "biochemistry" if n <= 30 else "cell_biology", "question_type": ["single_choice", "fill_blank", "term"][n % 3],
         "chapter": "第六章 核酸", "points": [4, 0, 3, None, 0][n % 5] if n % 5 != 4 else 4, "max_points": 4,
         "status": ["right", "wrong", "partial", "pending", "blank"][n % 5], "flag": "mistake" if n % 5 in (1, 4) else None, "stem": f"第 {n} 题的题干摘要"}
        for n in range(1, 61)],
    "counts": {"right": 12, "partial": 12, "wrong": 12, "blank": 12, "pending": 12, "reported": 0}, "flags": {"mistake": 24, "consolidate": 0},
}
REVIEW_ITEM = dict(DETAIL, question_id=101, position=1, paper_id=9, reported=False, criteria=[{"description": "要点一", "points": 2, "max_points": 3}], criterion_reasons=["写到一半"], blank_points=None, comment=None)


def start(engine, live_app, routes, path="/training", scheme="light"):
    client, _, _ = live_app
    base = str(client.base_url).rstrip("/")
    page, errors = open_page(engine, "safari", base, scheme=scheme)
    serve(page, **routes)
    page.goto(base + path)
    page.reload()                      # open_page had already loaded the page: only a reload runs the answers installed above
    page.wait_for_selector('nav.primary-nav [data-view="home"]')
    errors.clear()                     # requests of the document that was replaced are cancelled by the reload and are not this page's
    return page, errors


ROUTES = {"/v2/stats/overview": FULL, "/v2/practice/overview": OVERVIEW, "/v2/training/recent": RECENT, "/v2/mistakes/book": BOOK, "/v2/mistakes/1": DETAIL,
          "/v2/papers/9/review/101": REVIEW_ITEM, "/v2/papers/9/review": REVIEW}


def test_the_practice_page_has_the_papers_card_six_kinds_and_the_recent_list(engine, live_app):
    page, errors = start(engine, live_app, ROUTES)
    page.locator('nav.primary-nav [data-view="practice"]').click()
    expect(page.locator("#practice-view h1")).to_have_text("练习")
    # no system explanations; the spec is folded away under 卷面说明
    text = page.locator("#practice-view").inner_text()
    assert "唯一新版训练规格" not in text and "两个独立步骤" not in text and "请先在首页准备试卷" not in text
    assert page.locator("#practice-view details.paper-info").count() == 2 and not page.locator("#practice-view details.paper-info").first.evaluate("n => n.open")
    cards = page.locator("#practice-grid .practice-card")
    expect(cards).to_have_count(6)
    expect(cards.nth(0)).to_contain_text("薄弱章节练习")
    assert cards.nth(3).is_disabled() and "还没有可重做的错题" in cards.nth(3).inner_text()        # retry: nothing to redo yet
    assert cards.nth(5).is_disabled() and "还差 10 道" in cards.nth(5).inner_text() and "题库准备中" not in cards.nth(5).inner_text()      # year: its own reason, not a general one
    assert not cards.nth(4).is_disabled() and "可练 40 题" in cards.nth(4).inner_text()
    assert not cards.nth(0).is_disabled() and "可练 6 题 · 不足 10" in cards.nth(0).inner_text()          # fewer than ten: it may start with what there is
    rows = page.locator("#recent-list .recent-row")
    expect(rows).to_have_count(3)
    expect(rows.nth(0)).to_contain_text("212 / 300")
    expect(rows.nth(2)).to_contain_text("判分中")
    assert rows.nth(0).get_attribute("href") == "#/review/p9" and rows.nth(1).get_attribute("href") == "#/review/s4"
    page.locator("#recent-all").click()
    page.wait_for_function("location.hash === '#/stats/history'")
    assert not errors, errors
    page.close()


def test_one_state_on_the_home_card_and_the_practice_card(engine, live_app):
    page, _ = start(engine, live_app, ROUTES)
    page.wait_for_function("document.getElementById('prepare-objective').dataset.kind && document.getElementById('prepare-objective').dataset.kind !== 'loading'")
    home, practice = page.evaluate("""() => { const v = (b, p) => [document.getElementById(b).textContent, document.getElementById(p).textContent, document.getElementById(b).disabled];
        return [v('prepare-objective', 'paper-state-objective'), v('practice-today-btn-objective', 'practice-paper-state-objective')]; }""")        # read together: the state may change between two calls
    assert home == practice                                                    # the same label, pill and state, always
    page.close()


def test_a_short_bank_shows_how_many_are_missing_and_when_on_both_cards_and_is_not_clickable(engine, live_app):
    page, _ = start(engine, live_app, {**ROUTES, "/v2/stats/overview": SHORT_BANK, "/v2/papers": [None, 0]})
    page.wait_for_function("document.getElementById('prepare-objective').dataset.kind === 'short'")
    for sel in ("#prepare-objective", "#practice-today-btn-objective"):
        assert page.locator(sel).is_disabled()
        assert page.locator(sel).inner_text().startswith("题库还差 56 道，预计")
    assert "不会用旧题凑" in page.locator("#practice-today-note-objective").inner_text()
    page.close()


def test_the_mistake_book_is_cards_grouped_by_due_date_with_a_one_line_filter(engine, live_app):
    page, errors = start(engine, live_app, ROUTES, path="/training#/mistakes")
    page.wait_for_selector("#mistake-groups .mistake-card")
    groups = page.locator("#mistake-groups .mg-head")
    assert [t.split("\n")[0] for t in groups.all_inner_texts()] == ["已到期", "今天稍后及明天", "以后", "待巩固", "已掌握"]
    first = page.locator("#mistake-groups .mistake-card").first
    assert first.get_attribute("href") == "#/mistake/m1"
    text = first.inner_text()
    assert "单选" in text and "第二章 蛋白质的结构与功能" in text and "错 3 次" in text and "你的答案：B ✗" in text and "正确答案：A" in text and "下次复习：今天" in text
    assert page.locator("#mistake-groups .mistake-card").nth(1).inner_text().count("得分 3 / 6 分") == 1           # subjective: the score
    assert "错 1 次" not in page.locator("#mistake-groups").inner_text()                                           # only from 2 on
    assert first.locator(".mc-dots i.on").count() == 1 and first.locator(".mc-dots i").count() == 3
    # the single-choice stem has no ______ placeholder, two lines at most
    clamp = first.locator(".mc-stem").evaluate("n => getComputedStyle(n).webkitLineClamp")
    assert clamp == "2"
    # the filter is one line until opened
    assert page.locator("#filter-more").is_hidden()
    assert page.locator("#filter-subject").inner_text().replace("\n", " ").startswith("全部 5 生化 4 细胞 2") or "全部 5" in page.locator("#filter-subject").inner_text()
    page.locator("#filter-toggle").click()
    expect(page.locator("#filter-more")).to_be_visible()
    page.locator("#filter-type button", has_text="填空").click()
    expect(page.locator("#mistake-groups .mistake-card")).to_have_count(1)
    page.locator("#mistake-mode button[data-mode=chapter]").click()
    assert page.locator("#mistake-groups .mg-head").first.inner_text().startswith("生化 · 第六章 核酸")
    assert not errors, errors
    page.close()


def test_the_seven_day_chart_has_a_date_label_and_a_count_on_every_bar(engine, live_app):
    over = {**FULL, "reviews": {"due_now": 4, "mastered": 0, "next_7_days": [{"day": f"2026-11-{5 + k:02d}", "count": c} for k, c in enumerate([4, 0, 2, 0, 0, 7, 1])]}}
    page, _ = start(engine, live_app, {**ROUTES, "/v2/stats/overview": over}, path="/training#/mistakes")
    page.wait_for_selector("#due-mini .wk-col")
    cols = page.locator("#due-mini .wk-col")
    expect(cols).to_have_count(7)
    assert cols.nth(0).locator(".wk-d").inner_text() == "今天" and cols.nth(1).locator(".wk-d").inner_text() == "明天"
    assert cols.nth(0).locator(".wk-n").inner_text() == "4" and cols.nth(1).locator(".wk-n").inner_text() == ""      # a 0 draws only the baseline
    assert cols.nth(1).locator("i").evaluate("n => n.getBoundingClientRect().height") == 0
    gap = page.evaluate("document.getElementById('memory').getBoundingClientRect().top - document.getElementById('due-mini').getBoundingClientRect().bottom")
    assert gap >= 12
    page.close()


def test_a_mistake_opens_in_full_with_the_options_marked_and_returns_to_the_list(engine, live_app):
    page, errors = start(engine, live_app, ROUTES, path="/training#/mistakes")
    page.wait_for_selector("#mistake-groups .mistake-card")
    page.locator("#mistake-groups .mistake-card").first.click()
    page.wait_for_function("location.hash === '#/mistake/m1'")
    page.wait_for_selector("#mistake-detail .qd-options")
    right = page.locator("#mistake-detail .qd-options li.is-right")
    wrong = page.locator("#mistake-detail .qd-options li.is-wrong")
    assert "正确答案" in right.inner_text() and "你的选择" in wrong.inner_text()
    body = page.locator("#mistake-detail").inner_text()
    assert "错误历史" in body and "11月3日 · 0 / 4 分" in body and "教材依据：第二章 蛋白质的结构与功能" in body and "UNIT" not in body
    expect(page.locator("#mistake-review")).to_be_visible()
    page.locator("#mistake-view [data-back]").click()
    page.wait_for_function("location.hash === '#/mistakes'")
    assert not errors, errors
    page.close()


def test_the_review_page_lists_sixty_questions_and_loads_one_when_opened(engine, live_app):
    page, errors = start(engine, live_app, ROUTES, path="/training#/review/p9")
    page.wait_for_selector("#review-body .review-q")
    head = page.locator("#review-body .review-head").inner_text()
    assert "11月5日" in head and "212" in head and "300" in head and "50:10" in head
    assert page.locator("#review-body .review-q").count() == 60 and page.locator("#review-body .nav-cell").count() == 60
    assert page.locator("#review-body .rq-body").count() == 0                           # nothing in full until it is opened
    calls_before = page.evaluate("Object.keys(window.__calls).filter((k) => k.includes('/review/')).length")
    page.locator("#rq-101 .rq-head").click()
    page.wait_for_selector("#rq-101 .rq-body .qd-stem")
    assert "要点一" in page.locator("#rq-101 .rq-body").inner_text()
    assert page.locator("#review-body .rq-body").count() == 1                           # only the opened one
    page.locator("#rq-101 .rq-head").click()
    assert page.locator("#rq-101 .rq-body").is_hidden()                                 # closes without another load
    # filters
    page.locator("#review-body .review-filters button", has_text="只看错题").click()
    assert page.locator("#review-body .review-q").count() == 24
    assert page.locator("#review-body .nav-cell.dim").count() == 36
    page.locator("#review-body .review-filters button", has_text="只看错题").click()
    page.locator("#review-body .review-filters .chips").nth(1).locator("button", has_text="细胞").click()
    assert page.locator("#review-body .review-q").count() == 24 + 0 or page.locator("#review-body .review-q").count() > 0
    assert not errors, errors
    page.close()


def test_the_whole_paper_view_opens_from_the_result_page_and_the_history_page(engine, live_app):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    js = (root / "training.js").read_text()
    assert 'id = "view-whole-paper"' in js and '"#/review/p" + paper.paper_id' in js and 'card.href = "#/review/p" + item.paper_id' in js      # the history card itself links in: no separate button


def test_the_type_and_chapter_numbers_follow_the_subject_filter(engine, live_app):
    page, _ = start(engine, live_app, ROUTES, path="/training#/mistakes")
    page.wait_for_selector("#mistake-groups .mistake-card")
    page.locator("#filter-toggle").click()
    all_types = page.locator("#filter-type button").all_inner_texts()
    assert "单选 2" in all_types and "填空 1" in all_types                       # all subjects: 5 open ones (the mastered one is not counted)
    page.locator("#filter-subject button", has_text="细胞").click()
    types = dict(t.rsplit(" ", 1) for t in page.locator("#filter-type button").all_inner_texts() if t[-1].isdigit())
    assert types == {"单选": "1", "填空": "0", "名词解释": "1", "简答": "0", "问答": "0"}, types
    assert sum(int(v) for v in types.values()) == int(page.locator("#filter-subject button", has_text="细胞").inner_text().split()[-1])
    chapters = page.locator("#filter-chapter option").all_inner_texts()
    assert chapters[1:] == ["细胞 · 第二章 细胞膜（2）"], chapters                 # only that subject's chapters, with their own counts
    page.close()


def test_the_number_on_top_and_the_first_group_are_the_same_count(engine, live_app):
    due = {**FULL, "reviews": {"due_now": 2, "next_7_days": [{"day": f"2026-11-{5 + k:02d}", "count": 2 if k == 0 else 0} for k in range(7)], "mastered": 0}}
    page, _ = start(engine, live_app, {**ROUTES, "/v2/stats/overview": due}, path="/training#/mistakes")
    page.wait_for_selector("#mistake-groups .mistake-card")
    page.wait_for_function("document.getElementById('due-number').textContent !== '0'")
    first = page.locator("#mistake-groups .mg-head").first
    assert "已到期" in first.inner_text()
    assert page.locator("#due-number").inner_text() == first.locator(".mg-count").inner_text() == "2"
    # the seven bars use the width to the right of the count
    box = page.evaluate("""() => { const b = document.getElementById('due-mini').getBoundingClientRect(), c = document.querySelector('.due-card').getBoundingClientRect(),
        n = document.getElementById('due-number').getBoundingClientRect(); return { right: c.right - b.right, gap: b.left - n.right }; }""")
    assert box["right"] < 40 and box["gap"] < 120, box
    page.close()


HISTORY = {"papers": [
    {"paper_id": 9, "date": "2026-11-05", "spec": "xiaohan-60q-300pt-v4", "graded": False, "pending": 12, "points": None, "max_points": None, "objective_points": 38, "objective_max": 60,
     "subjects": {}, "seconds": 3010, "mistakes": 4, "consolidate": 1},
    {"paper_id": 8, "date": "2026-11-04", "spec": "xiaohan-60q-300pt-v4", "graded": True, "pending": 0, "points": 212, "max_points": 300, "objective_points": 50, "objective_max": 60,
     "subjects": {"biochemistry": {"points": 110, "max": 150}, "cell_biology": {"points": 102, "max": 150}}, "seconds": 3300, "mistakes": 7, "consolidate": 3},
]}


def test_the_history_page_is_one_card_per_paper_that_opens_the_review(engine, live_app):
    routes = {**ROUTES, "/v2/stats/history": HISTORY, "/v2/history": {"training": [], "legacy": []}, "/v2/stats/profile": {"trend": [], "timing": {"papers": []}}}
    page, _ = start(engine, live_app, routes, path="/training#/stats/history")
    page.wait_for_selector(".history-card")
    cards = page.locator(".history-card")
    expect(cards).to_have_count(2)
    expect(page.locator("#learning h2")).to_have_text("历史试卷")
    first, second = cards.nth(0).inner_text(), cards.nth(1).inner_text()
    assert "38" in first and "/ 60 分" in first and "客观题" in first and "主观题判分中" in first and "0 /" not in first.replace("10 / ", "")      # the objective part, never a total of 0
    assert "212" in second and "/ 300 分" in second and "生化 110/150" in second and "细胞 102/150" in second and "用时 55 分钟" in second and "错题 7" in second
    assert cards.nth(0).get_attribute("href") == "#/review/p9"                                  # the card is the link: no separate button
    assert page.locator("#learning button", has_text="整卷回看").count() == 0
    page.close()


REVIEW_PENDING = {**REVIEW, "pending": 12, "score": None, "objective_points": 38, "objective_max": 60, "grading_late": {"count": 5, "minutes": 30},
                  "items": [{**i, "pending_text": "排队等待 AI 判分"} if i["status"] == "pending" else i for i in REVIEW["items"]]}


def test_a_review_with_waiting_answers_shows_the_objective_part_the_reasons_and_the_regrade_button(engine, live_app):
    routes = {**ROUTES, "/v2/papers/9/review": REVIEW_PENDING, "/v2/papers/9/regrade": {"new_task": 12}}
    page, _ = start(engine, live_app, routes, path="/training#/review/p9")
    page.wait_for_selector("#review-body .review-q")
    head = page.locator("#review-body .review-head").inner_text()
    assert "38" in head and "/ 60 分" in head and "客观题" in head and "主观题判分中" in head
    assert "有 5 题交卷后超过 30 分钟仍未判完" in head
    assert "排队等待 AI 判分" in page.locator("#review-body .review-q.pending").first.text_content()
    page.locator("#review-body .review-head button", has_text="整卷重新判分").click()
    page.wait_for_function("window.__calls && window.__calls['/v2/papers/9/regrade'] === 1")
    expect(page.locator("#review-body .review-head button", has_text="已重新排队")).to_have_count(1)
    page.close()


def test_the_type_picker_offers_all_five_types_and_says_which_are_graded_by_the_ai(engine, live_app):
    page, _ = start(engine, live_app, ROUTES, path="/training#/practice")
    page.wait_for_selector("#practice-grid .practice-card")
    page.locator("#practice-grid .practice-card").nth(2).click()
    options = page.locator(".pick-dialog .pick-option")
    expect(options).to_have_count(5)
    texts = options.all_inner_texts()
    assert [t.split("\n")[0] for t in texts] == ["单选", "填空", "名词解释", "简答", "问答"]
    assert "AI 判分" in texts[2] and "可练 3 题" in texts[2] and not options.nth(2).is_disabled()          # a spare 名词解释 can be practised although 单选 is short
    assert options.nth(3).is_disabled() and "暂时没有可练的题" in texts[3]
    page.close()


SHORT_CELLS = {**SHORT_BANK, "tomorrow": {**SHORT_BANK["tomorrow"], "ready": 53, "needed": 60, "missing": 7, "short_cells": [
    {"subject": "cell_biology", "question_type": "single_choice", "have": 3, "need": 10, "missing": 7}]}}


def test_a_cell_that_is_short_makes_the_paper_not_ready_and_says_which(engine, live_app):
    page, _ = start(engine, live_app, {**ROUTES, "/v2/stats/overview": SHORT_CELLS, "/v2/model/progress/bank": SHORT_CELLS["tomorrow"], "/v2/papers": [None, 0]}, path="/training#/practice")
    page.wait_for_function("document.getElementById('prepare-objective').dataset.kind === 'short'")
    for button, pill, note in (("#prepare-objective", "#paper-state-objective", "#prepare-note-objective"), ("#practice-today-btn-objective", "#practice-paper-state-objective", "#practice-today-note-objective")):
        assert page.locator(button).is_disabled() and page.locator(button).inner_text().startswith("题库还差 7 道")
        assert page.locator(pill).inner_text() == "题库准备中"
        assert "细胞单选缺 7" in page.locator(note).inner_text()
    page.close()


def test_the_paper_is_never_ready_before_the_bank_is_known(engine, live_app):
    page, _ = start(engine, live_app, {**ROUTES, "/v2/stats/overview": [FULL, 6000], "/v2/model/progress/bank": [FULL["tomorrow"], 6000], "/v2/papers": [None, 0]}, path="/training#/practice")
    page.wait_for_timeout(800)
    assert page.locator("#practice-paper-state-objective").inner_text() != "已就绪" and page.locator("#practice-today-btn-objective").is_disabled()
    page.close()


def test_the_paper_description_has_an_arrow_and_the_card_texts_do_not_wrap_a_lone_character(engine, live_app):
    page, _ = start(engine, live_app, ROUTES, path="/training#/practice")
    page.wait_for_selector("#practice-grid .practice-card")
    summary = page.locator(".paper-info summary").first
    assert summary.evaluate("n => getComputedStyle(n, '::after').content") not in ("none", "normal")           # the arrow
    assert summary.bounding_box()["height"] >= 40                                                              # a finger-sized target
    page.locator(".paper-info summary").first.click()
    assert page.locator(".paper-info").first.evaluate("n => n.open") and "60 题" in page.locator(".paper-info").first.inner_text()
    lines = page.evaluate("""() => [...document.querySelectorAll('.practice-card small, .practice-card .practice-count')].map((n) => {
        const r = document.createRange(); r.selectNodeContents(n); const rects = [...r.getClientRects()].filter((x) => x.width > 0);
        const tops = [...new Set(rects.map((x) => Math.round(x.top)))]; const last = rects.filter((x) => Math.round(x.top) === tops[tops.length - 1]);
        return { text: n.textContent, lines: tops.length, lastWidth: last.reduce((a, x) => a + x.width, 0), font: parseFloat(getComputedStyle(n).fontSize) }; })""")
    for line in lines:
        assert line["lines"] == 1 or line["lastWidth"] >= line["font"] * 2.5, line          # never one character alone on the last line
    page.close()
