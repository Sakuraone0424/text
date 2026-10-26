"""Resumable, pending-only scientific review preparation from the existing private DB."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from psycopg.types.json import Jsonb
from app.source_policy import FILES
from app.source_intake import private_json
from app.local_corpus import private_jsonl
from app.review_transcripts import questions_and_years

VERSION = "evidence-work/1.6"
RISKS = {
    "energy_cofactor": r"ATP|ADP|NADH|NADPH|NADP\+|FADH",
    "reaction_direction": r"→|⇌|↔|反应方向|氧化|还原",
    "sequence_direction": r"[35][′’'＇]|5.?端|3.?端|DNA|RNA",
    "number_unit": r"\d\s*(?:nm|μm|um|mol|kDa|Da|%|℃|°C)|×10|\d+\^",
    "structure_bond": r"结构式|化学键|磷酸二酯|糖苷键|肽键|二硫键",
    "mechanism": r"机制|机理|调控|调节",
    "method": r"实验|测定|检测|色谱|电泳|离心|显微",
    "definition": r"称为|是指|定义|概念",
    "dependency": r"依赖|以.+为前提|必须先|需要",
    "comparison": r"区别|不同之处|差异|\bvs[.．]?|相比",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def normalize(label):
    # Do not fold case, Greek symbols or molecular punctuation into equivalence.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", label)).strip()


def build(conn):
    docs = conn.execute("SELECT * FROM source_documents ORDER BY id").fetchall()
    units = conn.execute(
        "SELECT u.*,d.file_id,d.sha256,d.subject FROM source_units u JOIN source_documents d ON d.id=u.document_id ORDER BY u.id"
    ).fetchall()
    proposals = conn.execute(
        "SELECT * FROM source_mapping_proposals ORDER BY id"
    ).fetchall()
    byid = {u["id"]: u for u in units}
    current = {
        d["id"]
        for d in docs
        if d["file_id"] in FILES
        and not any(o["file_id"] == d["file_id"] and o["id"] > d["id"] for o in docs)
    }
    units = [u for u in units if u["document_id"] in current]
    activeids = {u["id"] for u in units}
    work = []

    def add(kind, subject, ids, evidence, priority):
        ids = sorted(set(ids))
        row = dict(
            algorithm=VERSION,
            kind=kind,
            subject=subject,
            source_unit_ids=ids,
            evidence=evidence,
            priority=priority,
            status="pending",
        )
        row["work_key"] = digest(row)
        work.append(row)

    def ref(u, quote=None):
        return dict(
            unit_id=u["id"],
            file_id=u["file_id"],
            source_sha256=u["sha256"],
            chapter=u["chapter"],
            section=u["section"],
            ocr_page=u["ocr_global_page"],
            ordinal=u["ordinal"],
            quote=quote if quote is not None else u["text"][:220],
            unit_text_sha256=hashlib.sha256(u["text"].encode()).hexdigest(),
        )

    chapters = defaultdict(list)
    for u in units:
        item = FILES[u["file_id"]]
        categories = [k for k, p in RISKS.items() if re.search(p, u["text"], re.I)]
        add(
            "unit_review",
            u["subject"],
            [u["id"]],
            dict(
                reference=ref(u),
                risk_flags=u["quality_flags"],
                audit_categories=categories,
                ocr_text_checked=False,
                science_checked=False,
                needs_human=True,
                six_texts_sufficient=None,
                requires_text_sufficiency_review=bool(
                    set(u["quality_flags"])
                    & {
                        "image_dependent",
                        "untranscribed_image",
                        "chemical_structure",
                        "transcription_gap_reported",
                    }
                ),
                source_role=item["kind"],
                note="Exam statements may intentionally be false; never interpret them as textbook claims.",
            ),
            90 if u["quality_flags"] else 50,
        )
        if item["kind"] == "textbook":
            chapters[(u["document_id"], u["chapter"])].append(u)
    chapter_reports = []
    for (doc, chapter), members in chapters.items():
        sections = defaultdict(list)
        for u in members:
            sections[u["section"]].append(u["id"])
        role = "body"
        if chapter == "unclassified":
            role = (
                "contents"
                if all(u["section"] == "contents" for u in members)
                else "front_matter_or_unresolved"
            )
        report = dict(
            document_id=doc,
            chapter=chapter,
            indexed_units=len(members),
            pages=sorted(
                {u["ocr_global_page"] for u in members if u["ocr_global_page"]}
            ),
            sections=dict(sections),
            proposed_role=role,
            human_reviewed=0,
            science_reviewed=0,
            reference=ref(members[0]),
            status="pending",
        )
        chapter_reports.append(report)
        add(
            "chapter_alignment",
            members[0]["subject"],
            [u["id"] for u in members],
            report,
            65 if chapter == "unclassified" else 40,
        )
    groups = defaultdict(list)
    for p in proposals:
        if p["source_unit_id"] not in activeids or (
            p["target_unit_id"] and p["target_unit_id"] not in activeids
        ):
            continue
        u = byid[p["target_unit_id"] or p["source_unit_id"]]
        groups[(u["subject"], normalize(p["knowledge_label"]))].append(p)
    concepts = []
    for (subject, label), ps in groups.items():
        anchors = sorted({p["target_unit_id"] or p["source_unit_id"] for p in ps})
        exam_ids = sorted({p["source_unit_id"] for p in ps if p["target_unit_id"]})
        originals = sorted({p["knowledge_label"] for p in ps})
        anchor_refs = []
        for anchor in anchors:
            proposal = next(
                p for p in ps if (p["target_unit_id"] or p["source_unit_id"]) == anchor
            )
            evidence = proposal["evidence"]
            quote = (
                evidence["textbook_reference"]["quote"]
                if proposal["target_unit_id"]
                else evidence["quote"]
            )
            if quote not in byid[anchor]["text"]:
                raise ValueError(
                    "Original mapping excerpt differs from its evidence unit"
                )
            anchor_refs.append(ref(byid[anchor], quote))
        concept = dict(
            name=label,
            subject=subject,
            variants=originals,
            proposal_ids=[p["id"] for p in ps],
            textbook_anchors=anchor_refs,
            exam_unit_ids=exam_ids,
            cross_book_candidate=len({byid[i]["document_id"] for i in anchors}) > 1,
            needs_name_cleanup=bool(re.match(r"^\d|^第", label)) or len(label) > 65,
            relation_type="literal_label_candidate",
            review_basis="NFKC/whitespace normalization only; identity and scientific meaning not approved",
            status="pending",
        )
        concepts.append(concept)
        priority = min(
            85,
            35
            + min(30, len(exam_ids) * 3)
            + (15 if all(not byid[i]["quality_flags"] for i in anchors) else 0),
        )
        add("concept_mapping", subject, anchors + exam_ids, concept, priority)
    # Explicit textual relationship spans, not inferred semantic graph edges.
    relationships = []
    for u in units:
        for relation, pattern in [
            ("contrast_candidate", RISKS["comparison"]),
            ("prerequisite_candidate", RISKS["dependency"]),
            (
                "alias_candidate",
                r"[\u4e00-\u9fff]{2,25}\s*[（(][A-Za-z][^）)\n]{1,90}[）)]",
            ),
        ]:
            match = re.search(pattern, u["text"], re.I)
            if not match:
                continue
            quote = u["text"][
                max(0, match.start() - 65) : min(len(u["text"]), match.end() + 120)
            ]
            edge = dict(
                relation_type=relation,
                reference=ref(u, quote),
                endpoints_unconfirmed=True,
                status="pending",
                note="Explicit wording supports a review task, not an approved equivalence, contrast or dependency.",
            )
            relationships.append(edge)
            add("relationship", u["subject"], [u["id"]], edge, 45)
    exam_reports = []
    questions = []
    for d in docs:
        if (
            d["id"] not in current
            or FILES[d["file_id"]]["kind"] != "exam_transcription"
        ):
            continue
        qs, years, parsed = questions_and_years(
            [u for u in units if u["document_id"] == d["id"]], FILES[d["file_id"]]
        )
        for q in qs:
            q.update(
                reference=ref(byid[q["unit_id"]]), algorithm="transcript-review/1.4"
            )
            add(
                "exam_question",
                d["subject"],
                q["unit_ids"],
                q,
                85 if q["concerns"] else 60,
            )
        for year in years:
            year.update(file_id=d["file_id"], source_sha256=d["sha256"])
            ids = [u["unit_id"] for u in parsed if u["year"] == year["year"]]
            add(
                "exam_year", d["subject"], ids, year, 90 if year["year"] >= 2023 else 75
            )
        questions.extend(qs)
        exam_reports.extend(years)
    duplicates = defaultdict(list)
    for q in questions:
        # Conservative same-text detection; never deletes originals or merges years.
        key = re.sub(r"^\s*\d+[.．、)）]\s*", "", q["text"])
        key = re.sub(r"\s+", "", unicodedata.normalize("NFKC", key))
        if len(key) >= 8:
            duplicates[(byid[q["unit_id"]]["subject"], key)].append(q)
    repeats = []
    for (subject, _), qs in duplicates.items():
        if len(qs) < 2:
            continue
        item = dict(
            unit_ids=[q["unit_id"] for q in qs],
            years=[q["year"] for q in qs],
            relation_type="exact_text_repeat_candidate",
            status="pending",
            not_exam_probability=True,
        )
        repeats.append(item)
        add("transcription_repeat", subject, item["unit_ids"], item, 70)
    return dict(
        work=work,
        chapters=chapter_reports,
        concepts=concepts,
        relationships=relationships,
        questions=questions,
        years=exam_reports,
        repeats=repeats,
        counts=dict(
            current_units=len(units),
            original_proposals=len(proposals),
            normalized_concept_candidates=len(concepts),
            normalized_groups_merged=sum(len(c["proposal_ids"]) - 1 for c in concepts),
            chapter_groups=sum(c["chapter"] != "unclassified" for c in chapter_reports),
            pending_work=len(work),
            relationship_candidates=len(relationships),
            structural_questions=len(questions),
            year_reports=len(exam_reports),
            repeat_groups=len(repeats),
        ),
    )


def persist(conn, work):
    valid_ids = {
        r["id"] for r in conn.execute("SELECT id FROM source_units").fetchall()
    }
    inserted = 0
    for row in work:
        if row["status"] != "pending" or not set(row["source_unit_ids"]).issubset(
            valid_ids
        ):
            raise ValueError(
                "Only pending work with existing evidence units is allowed"
            )
        if digest({k: v for k, v in row.items() if k != "work_key"}) != row["work_key"]:
            raise ValueError("Review work fingerprint mismatch")
        inserted += (
            conn.execute(
                "INSERT INTO evidence_review_work(work_key,algorithm,kind,subject,source_unit_ids,evidence,priority) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING work_key",
                (
                    row["work_key"],
                    row["algorithm"],
                    row["kind"],
                    row["subject"],
                    row["source_unit_ids"],
                    Jsonb(row["evidence"]),
                    row["priority"],
                ),
            ).fetchone()
            is not None
        )
    return inserted


def refined_mapping_work(questions, concepts):
    """Reattach old lexical suggestions to the new full question/subquestion groups."""
    by_unit = defaultdict(list)
    for concept in concepts:
        for uid in concept["exam_unit_ids"]:
            by_unit[uid].append(concept)
    work = []
    for q in questions:
        found = {}
        for uid in q["unit_ids"]:
            for concept in by_unit[uid]:
                found[(concept["subject"], concept["name"])] = concept
        for (subject, name), concept in found.items():
            evidence = dict(
                question_reference=q["reference"],
                question_unit_ids=q["unit_ids"],
                question_text=q["text"],
                year=q["year"],
                knowledge_name=name,
                textbook_anchors=concept["textbook_anchors"],
                original_proposal_ids=concept["proposal_ids"],
                relation_type="may_assess",
                assessment_role="primary_or_secondary_requires_review",
                review_basis="Original lexical suggestions reattached to full question and subquestion evidence; not scientific entailment",
                status="pending",
                original_exam_verified=False,
            )
            ids = sorted(
                set(q["unit_ids"] + [a["unit_id"] for a in concept["textbook_anchors"]])
            )
            row = dict(
                algorithm="refined-exam-mapping/1.0",
                kind="fine_exam_mapping",
                subject=subject,
                source_unit_ids=ids,
                evidence=evidence,
                priority=80,
                status="pending",
            )
            row["work_key"] = digest(row)
            work.append(row)
    return work


def progress(conn):
    from app.ocr_sources import review_snapshot
    from app.source_policy import current_source_sql
    from app.training_spec import COUNTS, SUBJECTS

    units = conn.execute(
        "SELECT u.*,d.metadata,d.sha256 FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE "
        + current_source_sql()
    ).fetchall()
    reviews = review_snapshot(conn, units)
    counts = dict(
        text_checked=sum(v.get("text", False) for v in reviews.values()),
        science_checked=sum(v.get("science", False) for v in reviews.values()),
        risk_resolved=sum(v.get("uncertainty", False) for v in reviews.values()),
    )
    counts.update(
        raw_knowledge_links=conn.execute(
            "SELECT count(*) AS n FROM knowledge_links"
        ).fetchone()["n"],
        published_questions=conn.execute(
            "SELECT count(*) AS n FROM reviewed_questions WHERE status='published'"
        ).fetchone()["n"],
    )
    # Existing knowledge_links are annotations, without a signed relation-level
    # approval ledger. They must not be reported as confirmed concepts/mappings.
    counts["confirmed_knowledge_points"] = (
        0 if counts["raw_knowledge_links"] == 0 else None
    )
    counts["trusted_exam_textbook_mappings"] = (
        0 if counts["raw_knowledge_links"] == 0 else None
    )
    counts["mapping_confirmation_status"] = (
        "requires_explicit_relation_level_reviewer_decision; source text/science reviews alone do not confirm a relation"
    )
    counts["pending_work_by_kind"] = {
        r["kind"]: r["n"]
        for r in conn.execute(
            "SELECT kind,count(*) AS n FROM evidence_review_work WHERE algorithm=%s GROUP BY kind",
            (VERSION,),
        ).fetchall()
    }
    counts["supplemental_pending_work"] = {
        r["kind"]: r["n"]
        for r in conn.execute(
            "SELECT kind,count(*) AS n FROM evidence_review_work WHERE algorithm IN ('refined-exam-mapping/1.0','analyst-dossier/1.0') GROUP BY kind"
        ).fetchall()
    }
    chapters = defaultdict(list)
    for u in units:
        if (
            u["metadata"].get("source_kind") == "textbook"
            and u["chapter"] != "unclassified"
        ):
            chapters[(u["document_id"], u["chapter"])].append(u)
    counts["indexed_textbook_chapters"] = len(chapters)
    counts["fully_text_reviewed_chapters"] = sum(
        all(reviews[u["id"]].get("text", False) for u in group)
        for group in chapters.values()
    )
    counts["fully_science_reviewed_chapters"] = sum(
        all(reviews[u["id"]].get("science", False) for u in group)
        for group in chapters.values()
    )
    counts["normalized_pending_concepts"] = counts["pending_work_by_kind"].get(
        "concept_mapping", 0
    )
    # Report source evidence reviews separately from structural re-segmentation.
    tasks = conn.execute(
        "SELECT evidence FROM evidence_review_work WHERE kind='exam_question' AND algorithm=%s",
        (VERSION,),
    ).fetchall()
    counts["text_reviewed_transcript_questions"] = sum(
        all(
            reviews.get(uid, {}).get("text", False) for uid in t["evidence"]["unit_ids"]
        )
        for t in tasks
    )
    available = {
        (r["subject"], r["kind"]): r["n"]
        for r in conn.execute(
            "SELECT content->>'subject' AS subject,content->>'question_type' AS kind,count(*) AS n FROM reviewed_questions WHERE status='published' GROUP BY 1,2"
        ).fetchall()
    }
    counts["published_quota_deficits"] = {
        s: {k: max(0, n - available.get((s, k), 0)) for k, n in COUNTS.items()}
        for s in SUBJECTS
    }
    counts["published_counts_are_not_eligibility_review"] = True
    counts["fixed_quota_available"] = all(
        n == 0 for s in counts["published_quota_deficits"].values() for n in s.values()
    )
    counts["meaning"] = (
        "Only signed source events count as reviewed; pending preparation never contributes to official frequency or training."
    )
    return counts


def run(destination, schema, target):
    from app.source_intake import isolated_db

    destination = Path(destination)
    if destination.exists() or destination.resolve().is_relative_to(
        Path(__file__).resolve().parents[2]
    ):
        raise ValueError("Use a new private directory outside checkout")
    destination.mkdir(mode=0o700, parents=True)
    with isolated_db(schema, target, readonly=True) as conn:
        data = build(conn)
    for key in (
        "work",
        "chapters",
        "concepts",
        "relationships",
        "questions",
        "years",
        "repeats",
    ):
        private_jsonl(destination / (key + ".jsonl"), data[key])
    with isolated_db(schema, target) as conn:
        inserted = persist(conn, data["work"])
        stats = progress(conn)
    with isolated_db(schema, target, readonly=True) as conn:
        saved = conn.execute(
            "SELECT * FROM evidence_review_work WHERE algorithm=%s", (VERSION,)
        ).fetchall()
        actual = {r["work_key"]: r for r in saved}
        for row in data["work"]:
            assert (
                actual[row["work_key"]]["evidence"] == row["evidence"]
                and actual[row["work_key"]]["status"] == "pending"
            )
    result = dict(
        counts=data["counts"],
        progress=stats,
        inserted=inserted,
        exact_work_readback=True,
    )
    private_json(destination / "summary.json", result)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--target", required=True, choices=("development", "staging"))
    args = parser.parse_args()
    print(json.dumps(run(args.destination, args.schema, args.target), default=str))
