"""Source-backed historical exam records. Never infer unavailable years or official answers."""

import hashlib
from psycopg.types.json import Jsonb
from app.question_quality import stem_key

CATEGORIES = {
    "basic_concept",
    "structure_function",
    "metabolism",
    "mechanism",
    "comparison",
    "experiment",
    "synthesis",
}


def add(conn, record):
    unit = conn.execute(
        "SELECT * FROM source_units WHERE id=%s", (record["source_unit_id"],)
    ).fetchone()
    if not unit:
        raise ValueError("Original transcript unit is missing")
    if record["stem"] not in unit["text"]:
        raise ValueError("Exam stem does not match original source text")
    if record["category"] not in CATEGORIES:
        raise ValueError("Unknown exam analysis category")
    if record.get("original_verified") and not unit["verified"]:
        raise ValueError("Original must be verified before asserting exam verification")
    if record.get("complete_paper") and not record.get("original_verified"):
        raise ValueError("Complete-paper status requires original verification")
    if record.get("answer_kind") == "official" and not record.get(
        "official_answer_evidence_unit"
    ):
        raise ValueError("Official answer requires a separate verified source unit")
    if record.get("answer_kind") == "official":
        answer = conn.execute(
            "SELECT text,verified FROM source_units WHERE id=%s",
            (record["official_answer_evidence_unit"],),
        ).fetchone()
        if (
            not answer
            or not answer["verified"]
            or not record.get("answer")
            or record["answer"] not in answer["text"]
        ):
            raise ValueError("Official answer source not verified")
    key = hashlib.sha256(stem_key(record["stem"]).encode()).hexdigest()
    return conn.execute(
        "INSERT INTO exam_records(source_unit_id,original_question_id,subject,year,question_type,stem,options,knowledge_tags,category,answer,official_answer_evidence_unit,answer_kind,rubric,textbook_units,original_verified,complete_paper,canonical_key) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(source_unit_id,original_question_id) DO NOTHING RETURNING id",
        (
            record["source_unit_id"],
            record["original_question_id"],
            record["subject"],
            record["year"],
            record["question_type"],
            record["stem"],
            Jsonb(record.get("options", {})),
            Jsonb(record.get("knowledge_tags", [])),
            record["category"],
            record.get("answer"),
            record.get("official_answer_evidence_unit"),
            record.get("answer_kind", "missing"),
            Jsonb(record.get("rubric", [])),
            Jsonb(record.get("textbook_units", [])),
            record.get("original_verified", False),
            record.get("complete_paper", False),
            key,
        ),
    ).fetchone()


def coverage(conn):
    rows = conn.execute(
        "SELECT subject,year,COUNT(DISTINCT canonical_key) AS questions,bool_or(complete_paper AND original_verified) AS complete_original,"
        "bool_or(original_verified) AS original_verified,bool_or(answer IS NULL) AS missing_answers,"
        "bool_or(textbook_units='[]'::jsonb) AS missing_textbook_evidence FROM exam_records GROUP BY subject,year"
    ).fetchall()
    found = {(r["subject"], r["year"]): r for r in rows}
    return [
        found.get(
            (subject, year),
            {
                "subject": subject,
                "year": year,
                "questions": 0,
                "status": "original_not_acquired",
            },
        )
        for subject, start in [("biochemistry", 2004), ("cell_biology", 1998)]
        for year in range(start, 2027)
    ]


def frequencies(conn):
    # Different sources of one question in one year count once; repeat years remain distinct observations.
    return conn.execute(
        "SELECT subject,category,tag,COUNT(DISTINCT (year,canonical_key)) AS appearances "
        "FROM exam_records CROSS JOIN LATERAL jsonb_array_elements_text(knowledge_tags) AS tag "
        "GROUP BY subject,category,tag ORDER BY appearances DESC,subject,tag"
    ).fetchall()


def style_issues(conn, content):
    refs = content.get("style_reference_ids", [])
    if not refs:
        return ["missing_exam_style_source"]
    for ref in refs:
        row = conn.execute(
            "SELECT e.*,u.verified FROM exam_records e JOIN source_units u ON u.id=e.source_unit_id WHERE e.id=%s",
            (ref,),
        ).fetchone()
        if (
            not row
            or not row["verified"]
            or not row["original_verified"]
            or row["subject"] != content["subject"]
            or row["question_type"] != content["question_type"]
        ):
            return ["unverified_or_mismatched_exam_style_source"]
    return []
