"""Reviewed 28-question workflow; deliberately independent of legacy records."""
import json
from datetime import timedelta

from psycopg.types.json import Jsonb

from app.question_quality import Audit, Blueprint, Question, audit_issues, duplicate, duplication_metrics, normalize_answer
from app.quiz_service import today_cn


class BankShortage(ValueError):
    def __init__(self, shortages):
        self.shortages = shortages
        super().__init__('Insufficient fresh, published questions; no legacy fallback')


def lock_bank(conn):
    # Publication and selection share a lock so duplicate checks cannot race.
    conn.execute('SELECT pg_advisory_xact_lock(280028)')


def create_question(conn, question: Question):
    return conn.execute(
        'INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id, status',
        (Jsonb(question.model_dump()),),
    ).fetchone()


def review_question(conn, question_id: int, audit: Audit):
    lock_bank(conn)
    row = conn.execute('SELECT * FROM reviewed_questions WHERE id=%s FOR UPDATE', (question_id,)).fetchone()
    if not row:
        raise LookupError('Question not found')
    stems = [r['stem'] for r in conn.execute(
        "SELECT content->>'stem' AS stem FROM reviewed_questions WHERE id<>%s AND status='published' "
        'UNION ALL SELECT q.stem FROM questions q WHERE NOT EXISTS '
        '(SELECT 1 FROM legacy_question_publications lp WHERE lp.question_id=q.id AND lp.reviewed_question_id=%s)', (question_id,question_id),
    ).fetchall()]
    issues = audit_issues(row['content'], audit, stems)
    result = conn.execute(
        'INSERT INTO question_audits(question_id,reviewer,content_snapshot,checks,issues,duplicate_metrics,passed) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, passed, issues, duplicate_metrics',
        (question_id, audit.reviewer, Jsonb(row['content']), Jsonb(audit.model_dump()), Jsonb(issues), Jsonb(duplication_metrics(row['content']['stem'], stems)), not issues),
    ).fetchone()
    # A failed new review withdraws a published question from future papers.
    if issues and row['status'] == 'published':
        conn.execute("UPDATE reviewed_questions SET status='withdrawn' WHERE id=%s", (question_id,))
    return result


def publish_question(conn, question_id: int):
    lock_bank(conn)
    row = conn.execute('SELECT * FROM reviewed_questions WHERE id=%s FOR UPDATE', (question_id,)).fetchone()
    if not row:
        raise LookupError('Question not found')
    audit_row = conn.execute(
        'SELECT * FROM question_audits WHERE question_id=%s ORDER BY id DESC LIMIT 1', (question_id,),
    ).fetchone()
    if not audit_row or not audit_row['passed']:
        raise ValueError('A passing expert review is required')
    stems = [r['stem'] for r in conn.execute(
        "SELECT content->>'stem' AS stem FROM reviewed_questions WHERE id<>%s AND status='published' "
        'UNION ALL SELECT q.stem FROM questions q WHERE NOT EXISTS '
        '(SELECT 1 FROM legacy_question_publications lp WHERE lp.question_id=q.id AND lp.reviewed_question_id=%s)', (question_id,question_id),
    ).fetchall()]
    issues = audit_issues(row['content'], Audit.model_validate(audit_row['checks']), stems)
    if issues or audit_row['content_snapshot'] != row['content']:
        raise ValueError('Publication blocked: stale or failed audit: ' + ', '.join(issues))
    conn.execute("UPDATE reviewed_questions SET status='published' WHERE id=%s", (question_id,))
    return {'id': question_id, 'status': 'published'}


def create_paper(conn, user_id: int, blueprint: Blueprint, day=None):
    day = day or today_cn()
    lock_bank(conn)
    spec = blueprint.model_dump()
    existing = conn.execute('SELECT * FROM papers_v2 WHERE user_id=%s AND paper_date=%s', (user_id, day)).fetchone()
    if existing:
        if existing['blueprint'] != spec:
            raise ValueError('Today already has a paper with a different blueprint')
        return load_paper(conn, user_id, existing['id'])
    candidates = conn.execute(
        "SELECT q.id, q.content FROM reviewed_questions q WHERE q.status='published' "
        'AND NOT EXISTS (SELECT 1 FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id '
        'WHERE p.user_id=%s AND i.question_id=q.id) ORDER BY q.id', (user_id,),
    ).fetchall()
    # Also reject semantically close stems already seen by this user; no revision recycling.
    seen = [r['stem'] for r in conn.execute(
        "SELECT i.snapshot->>'stem' AS stem FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id WHERE p.user_id=%s "
        'UNION ALL SELECT q.stem FROM paper_questions i JOIN papers p ON p.id=i.paper_id '
        'JOIN questions q ON q.id=i.question_id WHERE p.user_id=%s', (user_id, user_id),
    ).fetchall()]
    selected, shortages = [], []
    for subject, quotas in spec['quotas'].items():
        for kind, count in quotas.items():
            pool = []
            for row in candidates:
                content = row['content']
                if content['subject'] != subject or content['question_type'] != kind:
                    continue
                if any(duplicate(content['stem'], s) for s in seen):
                    continue
                if any(duplicate(content['stem'], r['content']['stem']) for r in selected + pool):
                    continue
                pool.append(row)
            if len(pool) < count:
                shortages.append({'subject': subject, 'question_type': kind, 'required': count, 'available': len(pool)})
            selected.extend(pool[:count])
    if shortages:
        raise BankShortage(shortages)
    paper_id = conn.execute(
        'INSERT INTO papers_v2(user_id,paper_date,blueprint) VALUES (%s,%s,%s) RETURNING id',
        (user_id, day, Jsonb(spec)),
    ).fetchone()['id']
    for pos, row in enumerate(selected, 1):
        conn.execute('INSERT INTO paper_items_v2 VALUES (%s,%s,%s,%s)', (paper_id, row['id'], pos, Jsonb(row['content'])))
    return load_paper(conn, user_id, paper_id)


def owned_paper(conn, user_id, paper_id, *, lock=False):
    row = conn.execute(
        'SELECT * FROM papers_v2 WHERE id=%s AND user_id=%s' + (' FOR UPDATE' if lock else ''), (paper_id, user_id),
    ).fetchone()
    if not row:
        raise LookupError('Paper not found')
    return row


def load_paper(conn, user_id, paper_id):
    row = owned_paper(conn, user_id, paper_id)
    items = conn.execute('SELECT * FROM paper_items_v2 WHERE paper_id=%s ORDER BY position', (paper_id,)).fetchall()
    # No solutions, excerpts or rubrics in the unsubmitted paper response.
    questions = [{
        'id': i['question_id'], 'position': i['position'],
        **{k: i['snapshot'][k] for k in ('subject', 'question_type', 'stem', 'options', 'max_points')},
    } for i in items]
    return {'paper_id': paper_id, 'date': row['paper_date'].isoformat(), 'mode': 'reviewed_28',
            'blueprint': row['blueprint'], 'count': len(items), 'questions': questions}


def schedule_review(conn, attempt_id, question_id):
    for stage in (1, 3, 7):
        conn.execute('INSERT INTO reviews_v2(attempt_id,question_id,review_stage,due_date) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (attempt_id, question_id, stage, today_cn() + timedelta(days=stage)))


def submit(conn, user_id, paper_id, answers):
    owned_paper(conn, user_id, paper_id, lock=True)
    if conn.execute('SELECT 1 FROM attempts_v2 WHERE paper_id=%s', (paper_id,)).fetchone():
        raise ValueError('This paper has already been submitted')
    items = conn.execute('SELECT * FROM paper_items_v2 WHERE paper_id=%s ORDER BY position', (paper_id,)).fetchall()
    if len(items) != 28 or set(answers) != {i['question_id'] for i in items}:
        raise ValueError('Submit exactly the 28 question IDs belonging to this paper')
    attempt_id = conn.execute('INSERT INTO attempts_v2(paper_id) VALUES (%s) RETURNING id', (paper_id,)).fetchone()['id']
    for item in items:
        qid, q = item['question_id'], item['snapshot']
        value = answers[qid]
        if not isinstance(value, str) or not value.strip() or len(value) > 20000:
            raise ValueError('Each answer must be nonempty text of at most 20000 characters')
        points = None
        if q['question_type'] == 'single_choice':
            if value.strip().upper() not in 'ABCD' or len(value.strip()) != 1:
                raise ValueError('Single choice answer must be A, B, C or D')
            points = q['max_points'] if value.strip().upper() == q['answer'] else 0
        elif q['question_type'] == 'fill_blank':
            points = q['max_points'] if normalize_answer(value) in {normalize_answer(a) for a in q['accepted_answers']} else 0
        conn.execute('INSERT INTO answers_v2(attempt_id,question_id,answer,points) VALUES (%s,%s,%s,%s)',
                     (attempt_id, qid, value, points))
        if points is not None and points < q['max_points']:
            schedule_review(conn, attempt_id, qid)
    return result(conn, user_id, paper_id)


def grade(conn, paper_id, question_id, criterion_points, feedback, reviewer):
    paper = conn.execute('SELECT * FROM papers_v2 WHERE id=%s FOR UPDATE', (paper_id,)).fetchone()
    if not paper:
        raise LookupError('Paper not found')
    row = conn.execute(
        'SELECT a.*, i.snapshot FROM attempts_v2 t JOIN answers_v2 a ON a.attempt_id=t.id '
        'JOIN paper_items_v2 i ON i.paper_id=t.paper_id AND i.question_id=a.question_id '
        'WHERE t.paper_id=%s AND a.question_id=%s', (paper_id, question_id),
    ).fetchone()
    if not row:
        raise LookupError('Submitted answer not found')
    q = row['snapshot']
    if q['question_type'] in ('single_choice', 'fill_blank') or row['points'] is not None:
        raise ValueError('Only pending subjective answers can be graded')
    if len(criterion_points) != len(q['rubric']) or any(type(p) is not int or p < 0 or p > c['points'] for p, c in zip(criterion_points, q['rubric'])):
        raise ValueError('Scores must match the snapshot rubric and stay within each criterion')
    points = sum(criterion_points)
    conn.execute('UPDATE answers_v2 SET points=%s,feedback=%s,graded_by=%s WHERE attempt_id=%s AND question_id=%s',
                 (points, Jsonb({'criterion_points': criterion_points, 'comment': feedback}), reviewer, row['attempt_id'], question_id))
    if points < q['max_points']:
        schedule_review(conn, row['attempt_id'], question_id)
    return result(conn, paper['user_id'], paper_id)


def result(conn, user_id, paper_id):
    owned_paper(conn, user_id, paper_id)
    attempt = conn.execute('SELECT * FROM attempts_v2 WHERE paper_id=%s', (paper_id,)).fetchone()
    if not attempt:
        raise LookupError('Paper has not been submitted')
    rows = conn.execute(
        'SELECT a.*,i.snapshot FROM answers_v2 a JOIN paper_items_v2 i ON i.question_id=a.question_id '
        'AND i.paper_id=%s WHERE a.attempt_id=%s ORDER BY i.position', (paper_id, attempt['id']),
    ).fetchall()
    pending = sum(r['points'] is None for r in rows)
    if not pending and attempt['finalized_at'] is None:
        conn.execute('UPDATE attempts_v2 SET finalized_at=NOW() WHERE id=%s', (attempt['id'],))
    return {'paper_id': paper_id, 'attempt_id': attempt['id'], 'status': 'pending_review' if pending else 'graded',
            'pending': pending, 'score': None if pending else sum(r['points'] for r in rows),
            'graded_points': sum(r['points'] or 0 for r in rows),
            'max_points': sum(r['snapshot']['max_points'] for r in rows),
            'answers': [{'question_id': r['question_id'], 'answer': r['answer'], 'points': r['points'],
                         'feedback': r['feedback'], 'solution': r['snapshot']['answer'],
                         'evidence': r['snapshot']['evidence'], 'rubric': r['snapshot']['rubric']} for r in rows]}


def due_reviews(conn, user_id):
    return conn.execute(
        'SELECT r.question_id,r.review_stage,r.due_date,i.snapshot FROM reviews_v2 r '
        'JOIN attempts_v2 a ON a.id=r.attempt_id JOIN papers_v2 p ON p.id=a.paper_id '
        'JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=r.question_id '
        'WHERE p.user_id=%s AND r.due_date<=%s AND r.completed_at IS NULL ORDER BY r.due_date,r.question_id', (user_id, today_cn()),
    ).fetchall()


def export_legacy(conn, question_id):
    publish_question(conn, question_id)
    old = conn.execute('SELECT question_id FROM legacy_question_publications WHERE reviewed_question_id=%s', (question_id,)).fetchone()
    if old:
        return old
    q = conn.execute('SELECT content FROM reviewed_questions WHERE id=%s', (question_id,)).fetchone()['content']
    if q['question_type'] != 'single_choice':
        raise ValueError('Only single-choice questions can be exported to the legacy bank')
    topic_id = conn.execute('INSERT INTO topics(subject,name) VALUES (%s,%s) ON CONFLICT(subject,name) DO UPDATE SET name=EXCLUDED.name RETURNING id',
                            (q['subject'], q['evidence'][0]['chapter'])).fetchone()['id']
    legacy_id = conn.execute('INSERT INTO questions(topic_id,stem,options,correct_answer,explanation,source) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id',
                             (topic_id, q['stem'], Jsonb(q['options']), q['answer'], json.dumps(q['rubric'], ensure_ascii=False),
                              json.dumps(q['evidence'], ensure_ascii=False))).fetchone()['id']
    conn.execute('INSERT INTO legacy_question_publications VALUES (%s,%s)', (legacy_id,question_id))
    return {'question_id': legacy_id}
