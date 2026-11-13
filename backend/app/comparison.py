"""Blind comparison of the two executors on identical writing tasks (我的 → 出题对比).

Ten tasks are drawn from the current gaps (6 fill-in, 2 term, 1 short answer, 1 essay, the two subjects mixed). Each is
built ONCE, exactly as it would be sent to DeepSeek, and queued twice: one copy pinned to DeepSeek, one to a Claude scheduled task. Both
results go through the same program checks and the same review (DeepSeek, so the review is the same for both). The page shows the two
questions of a pair as 甲 / 乙 in random order, without saying who wrote which; after everything is rated it reveals the sources and the
pass rates. Questions that pass review join the bank like any other.
"""
import secrets

from app.external_executor import CLAUDE, DEEPSEEK

SLOTS = ("fill_blank",) * 6 + ("term",) * 2 + ("short_answer", "essay")
SUBJECTS = ("biochemistry", "cell_biology")
SIDES = ("A", "B")
LABEL = {"A": "甲", "B": "乙"}
EXECUTOR_NAME = {"deepseek": "DeepSeek", "claude": "Claude（订阅）"}


def _question_of_job(conn, job_id):
    row = conn.execute("SELECT question_id FROM candidate_review_packets WHERE candidate_key LIKE %s ORDER BY question_id DESC LIMIT 1",
                       (f"auto-generated-{job_id}-%",)).fetchone()
    if row:
        return row["question_id"]
    repaired = conn.execute(
        "SELECT (r.result->>'repaired_question')::bigint AS q FROM ai_jobs r WHERE r.kind='repair' AND r.payload->>'repair_of_job'=%s "
        "AND r.result ? 'repaired_question' ORDER BY r.id DESC LIMIT 1", (str(job_id),)).fetchone()
    return repaired["q"] if repaired else None


def start(conn, uid):
    """Queue the paired tasks. Returns {set_id, pairs, skipped}: a slot with no gap or nothing writable is skipped, never faked."""
    from app import auto_generation
    from app.ai_jobs import enqueue

    gaps = {(s, k) for s, k, _ in auto_generation.bucket_gaps(conn, uid)}
    auto_generation.past_exam_import.index_item_chapters(conn)
    set_id = conn.execute("INSERT INTO comparison_sets(user_id) VALUES (%s) RETURNING id", (uid,)).fetchone()["id"]
    used_units, built, skipped = [], 0, 0
    for position, kind in enumerate(SLOTS, 1):
        order = SUBJECTS if position % 2 else SUBJECTS[::-1]
        request = None
        for subject in order:
            if (subject, kind) not in gaps:
                continue
            request = auto_generation.build_gap_request(conn, uid, subject, kind, avoid_units=used_units)
            if request:
                break
        if not request:
            skipped += 1
            continue
        key, payload = request
        used_units += [u["unit_id"] for u in payload["evidence"][:1]]
        jobs = {}
        for who in ("deepseek", "claude"):
            job = enqueue(conn, uid, f"compare:{set_id}:{position}:{who}", "generation", payload)
            conn.execute("UPDATE ai_jobs SET executor=%s WHERE id=%s", (DEEPSEEK if who == "deepseek" else CLAUDE, job["id"]))
            jobs[who] = job["id"]
        conn.execute(
            "INSERT INTO comparison_pairs(set_id,position,subject,question_type,deepseek_job,claude_job,a_is) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (set_id, position, payload["subject"], payload["question_type"], jobs["deepseek"], jobs["claude"], secrets.choice(("deepseek", "claude"))))
        built += 1
    return dict(set_id=set_id, pairs=built, skipped=skipped)


def _side_state(conn, job_id):
    job = conn.execute("SELECT state,dispatched_at,result,error_code FROM ai_jobs WHERE id=%s", (job_id,)).fetchone()
    failure = ((job["result"] or {}).get("dispatch_failure") or {}).get("code") if job else None
    qid = _question_of_job(conn, job_id) if job else None
    review = None
    if qid:
        row = conn.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()
        review = row["status"] if row else None
    if qid:
        resolved, passed_checks = True, True
    elif job and (job["state"] == "blocked" or (job["state"] == "succeeded" and job["dispatched_at"] and failure != "repair_requested")):
        resolved, passed_checks = True, False
    else:
        resolved, passed_checks = False, False          # not written yet, or its one repair is still on the way
    return dict(question_id=qid, resolved=resolved, program_checks_passed=passed_checks, review=review)


def _content(conn, qid):
    c = conn.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
    return dict(
        subject=c["subject"], question_type=c["question_type"], stem=c["stem"], options=c.get("options") or {}, answer=c["answer"],
        accepted_answers=c.get("accepted_answers") or [], max_points=c["max_points"],
        rubric=[dict(description=r["description"], points=r["points"]) for r in c["rubric"]],
        evidence=[dict(chapter=e.get("chapter"), excerpt=e.get("excerpt")) for e in (c.get("evidence") or [])[:3]],
    )


def _pairs(conn, set_id):
    return conn.execute("SELECT * FROM comparison_pairs WHERE set_id=%s ORDER BY position", (set_id,)).fetchall()


def _sides(conn, pair):
    """{'A': side state, 'B': side state} with 'who' (the executor behind the label) kept INSIDE the server-side dict."""
    who = {"A": pair["a_is"], "B": "claude" if pair["a_is"] == "deepseek" else "deepseek"}
    job = {"deepseek": pair["deepseek_job"], "claude": pair["claude_job"]}
    return {side: dict(_side_state(conn, job[who[side]]), who=who[side]) for side in SIDES}


def view(conn, uid, set_id):
    """What the page may see. Nothing before the reveal says who wrote what: no executor, no job, no model."""
    owner = conn.execute("SELECT * FROM comparison_sets WHERE id=%s AND user_id=%s", (set_id, uid)).fetchone()
    if not owner:
        raise LookupError("comparison_not_found")
    pairs, rated_all, waiting, items = _pairs(conn, set_id), True, 0, []
    ratings = {(r["pair_id"], r["side"]): r for r in conn.execute(
        "SELECT * FROM comparison_ratings WHERE pair_id IN (SELECT id FROM comparison_pairs WHERE set_id=%s)", (set_id,)).fetchall()}
    detail = []
    for pair in pairs:
        sides = _sides(conn, pair)
        detail.append((pair, sides))
        resolved = all(s["resolved"] for s in sides.values())
        comparable = all(s["question_id"] for s in sides.values())
        if not resolved:
            waiting += 1
        entry = dict(position=pair["position"], question_type=pair["question_type"], subject=pair["subject"],
                     status="waiting" if not resolved else "rate" if comparable else "one_side_only")
        if comparable:
            entry["sides"] = [dict(side=side, label=LABEL[side], question=_content(conn, sides[side]["question_id"]),
                                   rating=({k: ratings[(pair["id"], side)][k] for k in ("realism", "correct", "rubric", "note")}
                                           if (pair["id"], side) in ratings else None)) for side in SIDES]
            if any((pair["id"], side) not in ratings for side in SIDES):
                rated_all = False
        items.append(entry)
    complete = waiting == 0 and rated_all and bool(pairs)
    out = dict(set_id=set_id, created_at=owner["created_at"], pairs=items, waiting=waiting, complete=complete, revealed=bool(complete))
    if complete:
        out["reveal"] = _reveal(detail, ratings)
        conn.execute("UPDATE comparison_sets SET revealed_at=COALESCE(revealed_at,clock_timestamp()) WHERE id=%s", (set_id,))
    return out


def _reveal(detail, ratings):
    stats = {w: dict(name=EXECUTOR_NAME[w], tasks=0, program_checks_passed=0, reviewed=0, review_passed=0, review_pending=0,
                     realism=[], correct=[], rubric=[]) for w in ("deepseek", "claude")}
    pairs_out = []
    for pair, sides in detail:
        shown = {}
        for side, s in sides.items():
            st = stats[s["who"]]
            st["tasks"] += 1
            st["program_checks_passed"] += 1 if s["program_checks_passed"] else 0
            if s["question_id"]:
                if s["review"] == "ready":
                    st["reviewed"] += 1
                    st["review_passed"] += 1
                elif s["review"] in ("blocked", "retired"):
                    st["reviewed"] += 1
                else:
                    st["review_pending"] += 1
            r = ratings.get((pair["id"], side))
            if r:
                for k in ("realism", "correct", "rubric"):
                    st[k].append(r[k])
            shown[side] = EXECUTOR_NAME[s["who"]]
        pairs_out.append(dict(position=pair["position"], question_type=pair["question_type"], A=shown["A"], B=shown["B"]))
    out = {}
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    for w, st in stats.items():
        out[w] = dict(name=st["name"], tasks=st["tasks"],
                      program_check_rate=round(st["program_checks_passed"] / st["tasks"], 3) if st["tasks"] else None,
                      review_rate=round(st["review_passed"] / st["reviewed"], 3) if st["reviewed"] else None,
                      review_pending=st["review_pending"], realism=mean(st["realism"]), correct=mean(st["correct"]), rubric=mean(st["rubric"]))
    return dict(executors=out, pairs=pairs_out)


def rate(conn, uid, set_id, position, side, realism, correct, rubric, note):
    if side not in SIDES:
        raise ValueError("side_must_be_A_or_B")
    for value in (realism, correct, rubric):
        if type(value) is not int or not 1 <= value <= 5:
            raise ValueError("scores_are_1_to_5")
    pair = conn.execute("SELECT p.* FROM comparison_pairs p JOIN comparison_sets s ON s.id=p.set_id WHERE s.id=%s AND s.user_id=%s AND p.position=%s",
                        (set_id, uid, position)).fetchone()
    if not pair:
        raise LookupError("comparison_pair_not_found")
    sides = _sides(conn, pair)
    if not all(s["question_id"] for s in sides.values()):
        raise ValueError("pair_not_ready_to_rate")
    conn.execute(
        "INSERT INTO comparison_ratings(pair_id,side,realism,correct,rubric,note) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (pair_id,side) DO UPDATE SET realism=EXCLUDED.realism,correct=EXCLUDED.correct,rubric=EXCLUDED.rubric,note=EXCLUDED.note,rated_at=clock_timestamp()",
        (pair["id"], side, realism, correct, rubric, (note or "").strip()[:300] or None))
    return dict(rated=True)


def listing(conn, uid):
    out = []
    for s in conn.execute("SELECT id,created_at FROM comparison_sets WHERE user_id=%s ORDER BY id DESC LIMIT 20", (uid,)).fetchall():
        v = view(conn, uid, s["id"])
        out.append(dict(set_id=s["id"], created_at=s["created_at"], pairs=len(v["pairs"]), waiting=v["waiting"], complete=v["complete"]))
    return out
