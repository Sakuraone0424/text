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

from fractions import Fraction

from app.training_spec_v5 import POINTS as NEW, V4_POINTS, V5_POINTS

# every earlier maximum a question of each type may still carry (v4 and v5), converted to the current one. A fill-in's 2 (v5) and 4 (v4)
# both become 2.5; single choice keeps 3 (a v4 single choice of 4 becomes 3: single choice is only used for practice now)
OLD_MAXIMA = {
    "single_choice": (4,),
    "fill_blank": (4, 2),
    "term": (6,),
    "short_answer": (12,),
    "essay": (18,),
}
OLD = V5_POINTS                              # the scale production is on when this runs: the "before" of the stock report

STATE_KEY = "scoring_rescale_v6"
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


def scale_exactly(points, new_total):
    """`points` in proportion to `new_total`, exactly (a fill-in's 2 -> 2.5 is a factor 1.25: 1 -> 1.25)."""
    total = sum(Fraction(str(p)) for p in points)
    if not points or total <= 0:
        raise ValueError("nothing to scale")
    scaled = [Fraction(str(p)) * Fraction(str(new_total)) / total for p in points]
    if any((x * 12).denominator != 1 for x in scaled):      # a scoring point must be a whole number, a half or an equal share (a multiple of 1/12)
        raise ValueError("rescale_impossible")
    return [_plain(float(x)) for x in scaled]


def rescale_content(content):
    """(new content, the scoring it had) for a question still on an earlier scale; None for any other question.

    Most types: the scoring points are scaled in proportion to whole numbers or halves that add up exactly. A fill-in (2 or 4 -> 2.5):
    a single blank's point is 2.5; several blanks share the 2.5 equally (the exact value is kept); other scoring points keep their
    proportions exactly."""
    kind = content.get("question_type")
    if kind not in OLD_MAXIMA or content.get("max_points") not in OLD_MAXIMA[kind] or content.get("max_points") == NEW[kind]:
        return None
    before = original_scoring(content)
    if kind == "fill_blank":
        blanks = before["blanks"]
        n = len(blanks)
        rubric = [_plain(NEW[kind] / n)] * len(before["rubric"]) if n and len(before["rubric"]) == n else scale_exactly(before["rubric"], NEW[kind])
        scaled = dict(max_points=NEW[kind], rubric=rubric, blanks=[_plain(NEW[kind] / n)] * n if n else [])
    else:
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
    """How many questions per learner, subject and type are published, marked ready and unused by that learner, at the given point
    scale. Plain counts (no per-question re-verification: that cost tens of seconds inside the worker's planning pass); this is the
    figure reported before and after rescaling, and the two must match."""
    from app import auto_generation
    from app.training_spec_v5 import SUBJECTS, QUESTION_TYPES

    out = {}
    for user in conn.execute("SELECT id,username FROM users ORDER BY id").fetchall():
        cells = {}
        for subject in SUBJECTS:
            cells[subject] = {}
            for kind in QUESTION_TYPES:
                cells[subject][kind] = conn.execute(
                    "SELECT count(*) AS n FROM auto_question_states s JOIN reviewed_questions q ON q.id=s.question_id "
                    "WHERE s.status='ready' AND q.status='published' AND q.content->>'subject'=%s AND q.content->>'question_type'=%s "
                    "AND q.content->>'max_points'=%s AND " + auto_generation.UNUSED_SQL,
                    (subject, kind, str(points[kind]), user["id"], user["id"]),
                ).fetchone()["n"]
        out[user["username"]] = cells
    return out


def retired_total(conn):
    return conn.execute("SELECT count(*) AS n FROM auto_question_states WHERE status='retired'").fetchone()["n"]


UNSCALABLE = "rescale_impossible"


def pause_single_choice_work(conn):
    """Single choice questions are no longer written (no new request, no past-exam single imported): a request still waiting is put aside
    (its past-exam item goes back to the import queue, untouched). Questions already written stay in the bank and go on being reviewed."""
    ids = [r["id"] for r in conn.execute(
        "UPDATE ai_jobs SET state='blocked',error_code='superseded_scoring' WHERE kind='generation' AND state IN ('pending','retrying') "
        "AND payload->>'question_type'='single_choice' RETURNING id").fetchall()]
    if ids:
        conn.execute("UPDATE past_exam_items SET status='imported',job_id=NULL,updated_at=clock_timestamp() WHERE job_id=ANY(%s) AND status='queued'", (ids,))
    return len(ids)


def run(conn):
    """Rescale every question still on the old scale (drafts and published ones, in flight or already used). Safe to call on
    every worker pass: a question that is already on the new scale is left alone.

    One question that cannot be converted never stops the others (each is its own savepoint): it is counted under `failed` with a
    fixed code and left as it is. A question whose scoring points cannot be shared out in half points (more points than half-points
    of score) is marked blocked / rescale_impossible so the retirement step does not void it: it waits for a person.
    Returns what happened; the first pass that converts something also records the stock before and after (STATE_KEY)."""
    from app import legacy_reuse

    # only questions still at an old maximum are loaded: the pass runs every few minutes and must stay cheap once everything is converted.
    # A fill-in converted before (v4 -> v5, it has its first scoring on record) is converted again (2 -> 2.5); the first record is kept.
    old = [(kind, str(points)) for kind, points_list in OLD_MAXIMA.items() for points in points_list]
    rows = conn.execute(
        "SELECT q.id,q.content FROM reviewed_questions q WHERE q.status IN ('draft','published') "
        "AND (q.content->>'question_type',q.content->>'max_points') IN (SELECT * FROM unnest(%s::text[],%s::text[])) "
        "AND (q.content->>'question_type'='fill_blank' OR NOT EXISTS(SELECT 1 FROM question_scoring_originals o WHERE o.question_id=q.id)) "
        "AND NOT EXISTS(SELECT 1 FROM auto_question_states s WHERE s.question_id=q.id AND s.reason=%s) ORDER BY q.id",
        ([k for k, _ in old], [p for _, p in old], UNSCALABLE),
    ).fetchall()
    todo, failed = [], {}
    for r in rows:
        try:
            pair = rescale_content(r["content"])
        except (ValueError, KeyError, TypeError) as exc:
            code = UNSCALABLE if isinstance(exc, ValueError) else "rescale_bad_content_" + type(exc).__name__
            failed[code] = failed.get(code, 0) + 1
            if code == UNSCALABLE:
                conn.execute(
                    "UPDATE auto_question_states SET status='blocked',reason=%s,updated_at=clock_timestamp() WHERE question_id=%s "
                    "AND status IN ('candidate','checking','repairing','ready','blocked')", (UNSCALABLE, r["id"]))
            continue
        if pair:
            todo.append((r["id"],) + pair)
    paused = pause_single_choice_work(conn)
    if not todo:
        out = dict(rescaled=0)
        if failed:
            out["failed"] = failed
        if paused:
            out["single_choice_requests_paused"] = paused
        return out
    first = legacy_reuse._state(conn, STATE_KEY) is None
    retired_before = retired_total(conn)
    before = stock_by_type(conn, OLD) if first else None
    by_type = {}
    for qid, content, original in todo:
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('app.rescale_scoring','1',true)")
                conn.execute("INSERT INTO question_scoring_originals(question_id,original) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                             (qid, Jsonb(original)))
                conn.execute("UPDATE reviewed_questions SET content=%s WHERE id=%s", (Jsonb(content), qid))
        except Exception as exc:  # noqa: BLE001  (one question's failure must not undo the others)
            code = "rescale_failed_" + type(exc).__name__
            failed[code] = failed.get(code, 0) + 1
            continue
        by_type[content["question_type"]] = by_type.get(content["question_type"], 0) + 1
    # a question-writing request still carrying the old maximum would only make more old-scale questions: ask again instead
    conn.execute(
        "UPDATE ai_jobs SET state='blocked',error_code='superseded_scoring' WHERE kind='generation' AND state IN ('pending','retrying') "
        "AND payload->>'max_points' IS DISTINCT FROM CASE payload->>'question_type'"
        + " " + " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in NEW.items()) + " END")
    report = dict(rescaled=sum(by_type.values()), by_type=by_type, invalidated=retired_total(conn) - retired_before)
    if failed:
        report["failed"] = failed
    if paused:
        report["single_choice_requests_paused"] = paused
    if first and report["rescaled"]:
        report.update(stock_before=before, stock_after=stock_by_type(conn, NEW))
        legacy_reuse._set_state(conn, STATE_KEY, report)
    return report
