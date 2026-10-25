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
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    trusted = trusted_records(conn, now)
    result = []
    for subject, start in [("biochemistry", 2004), ("cell_biology", 1998)]:
        for year in range(start, 2027):
            row = found.get(
                (subject, year),
                {
                    "subject": subject,
                    "year": year,
                    "questions": 0,
                    "status": "original_not_acquired",
                },
            )
            raw = conn.execute(
                "SELECT e.*,u.verified,d.original_content IS NOT NULL AS acquired FROM exam_records e JOIN source_units u ON u.id=e.source_unit_id JOIN source_documents d ON d.id=u.document_id WHERE e.subject=%s AND e.year=%s",
                (subject, year),
            ).fetchall()
            effective = [
                r
                for r in trusted
                if r["subject"] == subject
                and r["year"] == year
                and r["trusted"]
                and r["mapping_verified"]
                and r["source_kind"] == "real_exam"
            ]
            row.update(
                original_acquired=any(r["acquired"] for r in raw),
                text_extracted=bool(raw),
                transcription_verified=any(
                    r["verified"] and r["original_verified"] for r in raw
                ),
                has_reference_answer=any(bool(r["answer"]) for r in raw),
                has_official_answer_evidence=any(
                    r["answer_kind"] == "official"
                    and r["official_answer_evidence_unit"] is not None
                    for r in raw
                ),
                textbook_mapping_verified=bool(effective),
                included_in_reliable_frequency=bool(effective),
                reliable_record_ids=[r["id"] for r in effective],
                scope_keys=sorted({r["scope_key"] for r in effective}),
                missing_year_is_unknown=not raw,
            )
            result.append(row)
    return result


def frequencies(conn):
    from app.dynamic_model import canonical_exam, fractions

    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    grouped = {}
    for row in trusted_records(conn, now):
        if (
            not row["trusted"]
            or not row["mapping_verified"]
            or row["source_kind"] != "real_exam"
        ):
            continue
        key = (row["scope_key"], row["subject"], row["year"], canonical_exam(row))
        contribution = fractions(
            row["knowledge_tags"], row["tag_contributions"], row["primary_tags"]
        )
        if key in grouped and grouped[key]["contribution"] != contribution:
            grouped[key]["conflict"] = True
        else:
            grouped.setdefault(
                key,
                {
                    "row": row,
                    "contribution": contribution,
                    "ids": [],
                    "conflict": False,
                },
            )
        grouped[key]["ids"].append(row["id"])
    results = {}
    for item in grouped.values():
        if item["conflict"]:
            continue
        row = item["row"]
        for tag, weight in item["contribution"].items():
            key = (row["scope_key"], row["subject"], row["category"], tag)
            result = results.setdefault(
                key,
                {
                    "scope_key": row["scope_key"],
                    "subject": row["subject"],
                    "category": row["category"],
                    "question_type": row["question_type"],
                    "tag": tag,
                    "appearances": 0,
                    "contribution": 0.0,
                    "years": set(),
                    "record_ids": [],
                },
            )
            result["appearances"] += 1
            result["contribution"] += weight
            result["years"].add(row["year"])
            result["record_ids"].extend(item["ids"])
    return [
        {**r, "years": sorted(r["years"]), "record_ids": sorted(r["record_ids"])}
        for _, r in sorted(results.items())
    ]


def trusted_records(conn, as_of):
    from app.dynamic_model import fractions

    rows = conn.execute(
        "SELECT e.*,u.verified AND e.original_verified AND d.subject=e.subject AND d.original_content IS NOT NULL AND d.original_sha256 IS NOT NULL AS trusted,d.imported_at AS source_available_at,d.id AS source_document_id,d.original_sha256 AS original_sha256 "
        "FROM exam_records e JOIN source_units u ON u.id=e.source_unit_id JOIN source_documents d ON d.id=u.document_id WHERE e.indexed_at<=%s AND d.imported_at<=%s",
        (as_of, as_of),
    ).fetchall()
    annotations = {
        r["record_id"]: r
        for r in conn.execute(
            "SELECT DISTINCT ON(record_id) * FROM exam_annotations WHERE record_id=ANY(%s) AND registered_at<=%s ORDER BY record_id,registered_at DESC,id DESC",
            ([r["id"] for r in rows], as_of),
        ).fetchall()
    }
    scopes = {
        r["scope_key"]: r
        for r in conn.execute(
            "SELECT s.* FROM exam_scopes s JOIN source_units u ON u.id=s.source_unit_id WHERE u.verified AND s.registered_at<=%s",
            (as_of,),
        ).fetchall()
    }
    unit_ids = sorted({uid for r in rows for uid in r["textbook_units"]})
    units = {
        r["id"]: r
        for r in conn.execute(
            "SELECT u.id,(u.verified AND d.original_content IS NOT NULL) AS verified,d.subject FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=ANY(%s) AND d.imported_at<=%s",
            (unit_ids, as_of),
        ).fetchall()
    }
    links = {}
    for r in conn.execute(
        "SELECT unit_id,tag FROM knowledge_links WHERE unit_id=ANY(%s)", (unit_ids,)
    ).fetchall():
        links.setdefault(r["unit_id"], set()).add(r["tag"])
    for row in rows:
        annotation = annotations.get(row["id"])
        if annotation:
            row.update(annotation["metadata"])
        row["annotation_id"] = annotation["id"] if annotation else None
        row["available_at"] = max(
            row["indexed_at"],
            annotation["registered_at"] if annotation else row["indexed_at"],
        )
        scope = scopes.get(row["scope_key"])
        if not scope or scope["subject"] != row["subject"]:
            row["trusted"] = False
        if row["mapping_verified"]:
            mapped = set()
            for uid in row["textbook_units"]:
                unit = units.get(uid)
                if (
                    not unit
                    or not unit["verified"]
                    or unit["subject"] != row["subject"]
                ):
                    row["trusted"] = False
                mapped.update(links.get(uid, set()))
            if (
                not row["knowledge_tags"]
                or not row["textbook_units"]
                or not set(row["knowledge_tags"]).issubset(mapped)
            ):
                row["trusted"] = False
            try:
                fractions(
                    row["knowledge_tags"], row["tag_contributions"], row["primary_tags"]
                )
            except ValueError:
                row["trusted"] = False
    return rows


def register_scope(conn, data):
    unit = conn.execute(
        "SELECT u.verified,d.subject FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s",
        (data["source_unit_id"],),
    ).fetchone()
    if not unit or not unit["verified"] or unit["subject"] != data["subject"]:
        raise ValueError(
            "Scope applicability requires a verified source of the same subject"
        )
    conn.execute(
        "INSERT INTO exam_scopes(scope_key,subject,paper_code,syllabus,source_unit_id,reviewer,note) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        tuple(
            data[k]
            for k in (
                "scope_key",
                "subject",
                "paper_code",
                "syllabus",
                "source_unit_id",
                "reviewer",
                "note",
            )
        ),
    )
    return {"scope_key": data["scope_key"]}


def validate_classification(conn, record, metadata):
    from app.dynamic_model import fractions

    scope = conn.execute(
        "SELECT subject FROM exam_scopes WHERE scope_key=%s",
        (metadata.get("scope_key"),),
    ).fetchone()
    if not scope or scope["subject"] != record["subject"]:
        raise ValueError("A reviewed applicable scope of the same subject is required")
    fractions(
        record["knowledge_tags"],
        metadata.get("tag_contributions"),
        metadata.get("primary_tags"),
    )
    if metadata.get("mapping_verified"):
        if not record["knowledge_tags"] or not record.get("textbook_units"):
            raise ValueError("Knowledge mapping requires verified textbook units")
        mapped = set()
        for uid in record["textbook_units"]:
            row = conn.execute(
                "SELECT u.verified,d.subject FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s",
                (uid,),
            ).fetchone()
            if not row or not row["verified"] or row["subject"] != record["subject"]:
                raise ValueError("Knowledge mapping unit is unavailable")
            mapped.update(
                r["tag"]
                for r in conn.execute(
                    "SELECT tag FROM knowledge_links WHERE unit_id=%s", (uid,)
                ).fetchall()
            )
        if not set(record["knowledge_tags"]).issubset(mapped):
            raise ValueError("Every exam knowledge tag needs a verified textbook link")


def classify(conn, record_id, metadata, reviewer, note):
    row = conn.execute(
        "SELECT * FROM exam_records WHERE id=%s", (record_id,)
    ).fetchone()
    if not row:
        raise LookupError("Exam record not found")
    validate_classification(conn, row, metadata)
    conn.execute(
        "INSERT INTO exam_annotations(record_id,metadata,reviewer,note) VALUES (%s,%s,%s,%s)",
        (record_id, Jsonb(metadata), reviewer, note),
    )
    return {"classified": True}


def register_sample(conn, data):
    from app.dynamic_model import canonical_exam, fractions

    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    unit = conn.execute(
        "SELECT u.verified,u.document_id,d.subject,d.original_sha256 FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s",
        (data["source_unit_id"],),
    ).fetchone()
    scope = conn.execute(
        "SELECT subject FROM exam_scopes WHERE scope_key=%s", (data["scope_key"],)
    ).fetchone()
    if (
        not unit
        or not unit["verified"]
        or not scope
        or unit["subject"] != scope["subject"]
    ):
        raise ValueError(
            "Complete-paper source must be verified for the applicable subject"
        )
    rows = [
        r
        for r in trusted_records(conn, now)
        if r["scope_key"] == data["scope_key"]
        and r["year"] == data["year"]
        and r["trusted"]
        and r["mapping_verified"]
        and r["source_kind"] == "real_exam"
    ]
    mappings = {}
    for row in rows:
        identity = canonical_exam(row)
        mapped = fractions(
            row["knowledge_tags"], row["tag_contributions"], row["primary_tags"]
        )
        if identity in mappings and mappings[identity] != mapped:
            raise ValueError(
                "Complete-paper mapping conflict must be resolved before verification"
            )
        mappings[identity] = mapped
    matching = {
        canonical_exam(r)
        for r in rows
        if r["source_document_id"] == unit["document_id"]
        or unit["original_sha256"]
        and r["original_sha256"] == unit["original_sha256"]
    }
    if len(matching) != data["expected_questions"]:
        raise ValueError(
            "Complete-paper source does not cover the mapped original questions"
        )
    if len({canonical_exam(r) for r in rows}) != data["expected_questions"]:
        raise ValueError(
            "Complete mapped sample count differs from verified distinct questions"
        )
    conn.execute(
        "INSERT INTO exam_samples(scope_key,year,source_unit_id,expected_questions,reviewer,note) VALUES (%s,%s,%s,%s,%s,%s)",
        tuple(
            data[k]
            for k in (
                "scope_key",
                "year",
                "source_unit_id",
                "expected_questions",
                "reviewer",
                "note",
            )
        ),
    )
    return {"sample_verified": True}


def style_issues(conn, content):
    refs = content.get("style_reference_ids", [])
    if not refs:
        return ["missing_exam_style_source"]
    for ref in refs:
        row = conn.execute(
            "SELECT e.*,(u.verified AND d.original_content IS NOT NULL) AS verified FROM exam_records e JOIN source_units u ON u.id=e.source_unit_id JOIN source_documents d ON d.id=u.document_id WHERE e.id=%s",
            (ref,),
        ).fetchone()
        if not row:
            return ["unverified_or_mismatched_exam_style_source"]
        annotation = conn.execute(
            "SELECT metadata FROM exam_annotations WHERE record_id=%s ORDER BY registered_at DESC,id DESC LIMIT 1",
            (ref,),
        ).fetchone()
        if annotation:
            row.update(annotation["metadata"])
        scope = conn.execute(
            "SELECT s.subject,u.verified FROM exam_scopes s JOIN source_units u ON u.id=s.source_unit_id WHERE s.scope_key=%s",
            (row["scope_key"],),
        ).fetchone()
        if (
            not row["verified"]
            or not row["original_verified"]
            or row["source_kind"] != "real_exam"
            or not scope
            or not scope["verified"]
            or scope["subject"] != content["subject"]
            or row["subject"] != content["subject"]
            or row["question_type"] != content["question_type"]
        ):
            return ["unverified_or_mismatched_exam_style_source"]
    return []
