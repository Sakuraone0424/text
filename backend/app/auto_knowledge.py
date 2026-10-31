"""Two-round evidence-bound knowledge graph; lexical similarity never approves a merge."""

from pydantic import Field, StrictBool, StrictInt
from psycopg.types.json import Jsonb
from app.question_quality import StrictModel
from app.candidate_review import snapshot_unit
from app.ai_jobs import enqueue

RELATIONS = (
    "definition",
    "mechanism",
    "synonym",
    "parent",
    "prerequisite",
    "confusable",
    "experiment",
    "metabolism",
    "cross_chapter",
    "cross_textbook",
)


class Concept(StrictModel):
    name: str = Field(min_length=2, max_length=200)
    relation_type: str = Field(min_length=1, max_length=30)
    target: str = Field(max_length=200)
    excerpt: str = Field(min_length=5, max_length=2000)
    rationale: str = Field(min_length=20, max_length=3000)


class Extraction(StrictModel):
    concepts: list[Concept] = Field(max_length=20)
    uncertain: StrictBool


class Confirmation(StrictModel):
    index: StrictInt
    supported: StrictBool
    excerpt: str = Field(min_length=5, max_length=2000)
    rationale: str = Field(min_length=30, max_length=3000)


class Verification(StrictModel):
    confirmations: list[Confirmation] = Field(max_length=20)


def schedule(conn, uid, unit_id):
    u = snapshot_unit(conn, unit_id)
    if (
        u["source_kind"] != "textbook"
        or u["chapter"] == "unclassified"
        or set(u["quality_flags"])
        & {
            "corrupt_ocr",
            "untranscribed_image",
        }
    ):
        conn.execute(
            "INSERT INTO auto_knowledge_events(unit_id,unit_sha256,relation,status,run_key) VALUES(%s,%s,%s,'blocked',%s) ON CONFLICT DO NOTHING",
            (
                unit_id,
                u["unit_sha256"],
                Jsonb(
                    dict(
                        reason="unresolvable_ocr_or_image_dependency",
                        flags=u["quality_flags"],
                    )
                ),
                f"quarantine:{unit_id}:{u['unit_sha256']}",
            ),
        )
        return None
    return enqueue(
        conn,
        uid,
        f"knowledge:{unit_id}:{u['unit_sha256']}:extract",
        "textbook_analysis",
        dict(
            knowledge_unit_id=unit_id,
            phase="extract",
            evidence=u,
            schema=Extraction.model_json_schema(),
            instructions="Extract only explicitly evidenced concepts and relationships. Return ONLY the Extraction schema object with concepts and uncertain, never echo the input packet. Copy each excerpt character-for-character from evidence.text, including punctuation, letter case and whitespace; do not normalize or paraphrase a quote. Each Concept must have name length >=2, excerpt length >=5 copied exactly from the supplied unit, and rationale length >=20 with a meaningful source-grounded explanation. The list must have at most 20 objects. If a valid scientific name or quote cannot satisfy these invariants, omit the concept rather than pad or invent it. Each rationale must meet the schema minimum length. Names that look similar are not synonyms unless the text supports identity. Use relation types "
            + ",".join(RELATIONS)
            + ". No outside sources. Mark uncertain rather than guessing missing formulas or image content.",
        ),
    )


def accept(conn, job):
    if job["state"] != "succeeded" or job["result"].get("real_api") is not True:
        raise ValueError("actual_knowledge_call_required")
    p = job["payload"]
    u = snapshot_unit(conn, p["knowledge_unit_id"])
    if u != p["evidence"]:
        raise ValueError("stale_knowledge_source")
    if p["phase"] == "extract":
        result = Extraction.model_validate(job["result"]["response"])
        if result.uncertain or not result.concepts:
            raise ValueError("uncertain_knowledge")
        concepts = []
        for i, item in enumerate(result.concepts):
            if item.relation_type not in RELATIONS or item.excerpt not in u["text"]:
                raise ValueError("unfounded_knowledge_relation")
            relation = {
                **item.model_dump(),
                "index": i,
                "subject": u["subject"],
                "chapter": u["chapter"],
                "section": u["section"],
                "ocr_page": u["ocr_page"],
                "file_id": u["file_id"],
                "file_sha256": u["source_sha256"],
                "label": "AI自动核验",
            }
            conn.execute(
                "INSERT INTO auto_knowledge_events(unit_id,unit_sha256,job_id,relation,status,run_key) VALUES(%s,%s,%s,%s,'candidate',%s) ON CONFLICT DO NOTHING",
                (
                    u["unit_id"],
                    u["unit_sha256"],
                    job["id"],
                    Jsonb(relation),
                    f"knowledge:{job['id']}:{i}:extract",
                ),
            )
            concepts.append(relation)
        return enqueue(
            conn,
            job["user_id"],
            job["job_key"] + ":verify",
            "knowledge_extraction",
            dict(
                knowledge_unit_id=u["unit_id"],
                phase="verify",
                evidence=u,
                candidates=concepts,
                schema=Verification.model_json_schema(),
                instructions="Independently inspect each proposed relationship as an untrusted candidate. Use only original text, not previous AI rationale as proof. Reject similar-but-different molecules and unsupported synonyms. Return one confirmation per index, exact original excerpt, scientific rationale. OCR risks or incomplete evidence must reject.",
            ),
        )
    result = Verification.model_validate(job["result"]["response"])
    expected = set(range(len(p["candidates"])))
    if {x.index for x in result.confirmations} != expected or len(
        result.confirmations
    ) != len(expected):
        raise ValueError("incomplete_concept_review")
    for item in result.confirmations:
        relation = p["candidates"][item.index]
        if item.excerpt not in u["text"]:
            raise ValueError("invented_confirmation_quote")
        status = (
            "ready"
            if item.supported
            and not u["quality_flags"]
            and relation["relation_type"] not in ("cross_chapter", "cross_textbook")
            else "blocked"
        )
        conn.execute(
            "INSERT INTO auto_knowledge_events(unit_id,unit_sha256,job_id,relation,status,run_key) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                u["unit_id"],
                u["unit_sha256"],
                job["id"],
                Jsonb(
                    {
                        **relation,
                        "review_excerpt": item.excerpt,
                        "review_rationale": item.rationale,
                    }
                ),
                status,
                f"knowledge:{job['id']}:{item.index}:verify",
            ),
        )


def plan_small_batch(conn, uid):
    pending = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE user_id=%s AND kind IN ('textbook_analysis','knowledge_extraction') AND state IN ('pending','running','retrying')",
        (uid,),
    ).fetchone()["n"]
    if pending >= 4:
        return
    from app.source_policy import current_source_sql

    rows = conn.execute(
        "SELECT u.id FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE "
        + current_source_sql()
        + " AND d.metadata->>'source_kind'='textbook' AND u.chapter NOT IN ('unclassified','','contents') AND u.quality_flags='[]'::jsonb AND length(u.text)>=100 AND NOT EXISTS(SELECT 1 FROM auto_knowledge_events k WHERE k.unit_id=u.id) AND NOT EXISTS(SELECT 1 FROM ai_jobs j WHERE j.payload->>'knowledge_unit_id'=u.id::text) ORDER BY (SELECT count(*) FROM ai_jobs j WHERE j.payload->'evidence'->>'chapter'=u.chapter),u.ordinal LIMIT 2"
    ).fetchall()
    for r in rows:
        schedule(conn, uid, r["id"])
