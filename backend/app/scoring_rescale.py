"""Moving every question to the new point scale WITHOUT retiring any of them.

The first specification change retired every question written under the old scale (superseded_scoring). This one does not:
a question keeps its stem, answer, evidence and rubric text; only its numbers change.

  single choice 4 -> 3, fill-in 4 -> 2 (a fill-in's single scoring point, and each blank, follow),
  term 6 -> 5, short answer 12 -> 15, essay 18 -> 25: the scoring points are scaled in proportion, each rounded to a whole
  number or a half, and together they equal the new maximum exactly.

What the question was reviewed as is kept in question_scoring_originals: its quality review is bound to the content as it was
reviewed, so auto_quality reads a rescaled question back in its original scoring when it checks that review (the review judges
science, answer and evidence, none of which changed). The change is announced to the database with app.rescale_scoring; the
guard trigger lets nothing but the scoring fields differ.
"""

import copy
import math

from psycopg.types.json import Jsonb

from app.training_spec_v5 import POINTS as NEW, V4_POINTS as OLD

STATE_KEY = "scoring_rescale_v5"
NOTE = "分值已按比例换算"


def _plain(value):
    """5.0 -> 5 (stored and compared as whole numbers when they are whole)."""
    return int(value) if float(value) == int(value) else float(value)


def scale_points(points, new_total):
    """`points` scaled in proportion so they add up to `new_total`: each a whole number or a half (never below 0.5), the
    largest remainders deciding who gets the spare halves. [3, 3] -> 5 gives [2.5, 2.5]; [2, 2, 2] -> 5 gives [1.5, 1.5, 2]."""
    total = sum(points)
    if not points or total <= 0:
        raise ValueError("nothing to scale")
    units = int(round(new_total * 2))
    exact = [p * units / total for p in points]
    base = [max(1, math.floor(x)) for x in exact]
    diff = units - sum(base)
    while diff > 0:
        for i in sorted(range(len(base)), key=lambda i: exact[i] - base[i], reverse=True):
            if diff == 0:
                break
            base[i] += 1
            diff -= 1
    while diff < 0:
        movable = sorted((i for i in range(len(base)) if base[i] > 1), key=lambda i: exact[i] - base[i])
        if not movable:
            raise ValueError("cannot reach the new total")
        base[movable[0]] -= 1
        diff += 1
    return [_plain(u / 2) for u in base]


def original_scoring(content):
    return dict(max_points=content["max_points"], rubric=[c["points"] for c in content["rubric"]],
                blanks=[b["points"] for b in content.get("blanks") or []])


def with_scoring(content, scoring):
    """A copy of the content carrying the given scoring (nothing else changes)."""
    out = copy.deepcopy(content)
    out["max_points"] = scoring["max_points"]
    for criterion, points in zip(out["rubric"], scoring["rubric"]):
        criterion["points"] = points
    for blank, points in zip(out.get("blanks") or [], scoring.get("blanks") or []):
        blank["points"] = points
    return out


def rescale_content(content):
    """(new content, the scoring it had) for a question still on the old scale; None for any other question."""
    kind = content.get("question_type")
    if kind not in OLD or content.get("max_points") != OLD[kind]:
        return None
    before = original_scoring(content)
    scaled = dict(max_points=NEW[kind], rubric=scale_points(before["rubric"], NEW[kind]),
                  blanks=scale_points(before["blanks"], NEW[kind]) if before["blanks"] else [])
    return with_scoring(content, scaled), before


def read_in_original_scoring(conn, qid, content):
    """The content as it was reviewed (its scoring before rescaling), for checking the review bound to it."""
    row = conn.execute("SELECT original FROM question_scoring_originals WHERE question_id=%s", (qid,)).fetchone()
    return with_scoring(content, row["original"]) if row else content


def rescaled_ids(conn, ids):
    ids = list(ids)
    if not ids:
        return set()
    return {r["question_id"] for r in conn.execute(
        "SELECT question_id FROM question_scoring_originals WHERE question_id=ANY(%s)", (ids,)).fetchall()}


def stock_by_type(conn, points):
    """Questions that could go on a paper right now (published, verified, never used by that learner), per learner, subject
    and type, counted at the given point scale: the figure reported before and after rescaling."""
    from app import auto_generation
    from app.training_spec_v5 import SUBJECTS, QUESTION_TYPES

    out = {}
    for user in conn.execute("SELECT id,username FROM users ORDER BY id").fetchall():
        out[user["username"]] = {s: {k: auto_generation.eligible_reserve_count(conn, user["id"], s, k, points=points)
                                     for k in QUESTION_TYPES} for s in SUBJECTS}
    return out


def retired_total(conn):
    return conn.execute("SELECT count(*) AS n FROM auto_question_states WHERE status='retired'").fetchone()["n"]


def run(conn):
    """Rescale every question still on the old scale (drafts and published ones, in flight or already used). Safe to call on
    every worker pass: a question that is already on the new scale is left alone. Returns what happened; the first pass that
    does something also records the stock before and after in legacy_reuse_state (STATE_KEY)."""
    from app import legacy_reuse

    rows = conn.execute(
        "SELECT q.id,q.content FROM reviewed_questions q WHERE q.status IN ('draft','published') "
        "AND NOT EXISTS(SELECT 1 FROM question_scoring_originals o WHERE o.question_id=q.id) ORDER BY q.id"
    ).fetchall()
    todo = [(r["id"],) + pair for r in rows if (pair := rescale_content(r["content"]))]
    if not todo:
        return dict(rescaled=0)
    first = legacy_reuse._state(conn, STATE_KEY) is None
    retired_before = retired_total(conn)
    before = stock_by_type(conn, OLD) if first else None
    conn.execute("SELECT set_config('app.rescale_scoring','1',true)")
    by_type = {}
    for qid, content, original in todo:
        conn.execute("INSERT INTO question_scoring_originals(question_id,original) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (qid, Jsonb(original)))
        conn.execute("UPDATE reviewed_questions SET content=%s WHERE id=%s", (Jsonb(content), qid))
        by_type[content["question_type"]] = by_type.get(content["question_type"], 0) + 1
    conn.execute("SELECT set_config('app.rescale_scoring','',true)")
    # a question-writing request still carrying the old maximum would only make more old-scale questions: ask again instead
    conn.execute(
        "UPDATE ai_jobs SET state='blocked',error_code='superseded_scoring' WHERE kind='generation' AND state IN ('pending','retrying') "
        "AND payload->>'max_points' IS DISTINCT FROM CASE payload->>'question_type' "
        "WHEN 'single_choice' THEN '3' WHEN 'fill_blank' THEN '2' WHEN 'term' THEN '5' WHEN 'short_answer' THEN '15' WHEN 'essay' THEN '25' END")
    report = dict(rescaled=len(todo), by_type=by_type, invalidated=retired_total(conn) - retired_before)
    if first:
        report.update(stock_before=before, stock_after=stock_by_type(conn, NEW))
        legacy_reuse._set_state(conn, STATE_KEY, report)
    return report
