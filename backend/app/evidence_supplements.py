"""Append-only DOCX evidence indexes. Content is data, never executable code.

Separate from source_units so original import and reviewed identities stay intact.
No function in this module publishes questions or signs human decisions.
"""

import hashlib
import io
import re
from docx import Document
from docx.table import Table
from lxml import etree
from psycopg.types.json import Jsonb
from app.candidate_review import digest

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def paragraphs(container, path="body"):
    for i, item in enumerate(container.iter_inner_content(), 1):
        position = f"{path}/{i}"
        if isinstance(item, Table):
            seen = set()
            for ri, row in enumerate(item.rows, 1):
                for ci, cell in enumerate(row.cells, 1):
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        yield from paragraphs(cell, f"{position}/row{ri}/cell{ci}")
        else:
            yield position, item


def math_text(node):
    """Explicit fractions/scripts; no OCR evaluation or scientific correction."""
    kind = etree.QName(node).localname
    if kind == "t":
        return node.text or ""
    if kind.endswith("Pr"):
        return ""
    children = [
        math_text(c) for c in node if not etree.QName(c).localname.endswith("Pr")
    ]
    if kind == "f" and len(children) == 2:
        return f"({children[0]})/({children[1]})"
    if kind == "func" and len(children) == 2:
        return f"{children[0]}({children[1]})"
    if kind == "d":
        props = node.find(M + "dPr")

        def delimiter(name, default):
            el = props.find(M + name) if props is not None else None
            return el.get(M + "val", default) if el is not None else default

        return (
            delimiter("begChr", "(")
            + delimiter("sepChr", "|").join(children)
            + delimiter("endChr", ")")
        )
    if kind in ("sSub", "sSup") and len(children) == 2:
        return children[0] + ("_{" if kind == "sSub" else "^{") + children[1] + "}"
    return "".join(children)


def extract(raw):
    """Resolve only embedded relationships, never fetch external targets."""
    doc = Document(io.BytesIO(raw))
    for position, paragraph in paragraphs(doc):
        seen = set()
        for node in paragraph._p.iter():
            if node.tag == M + "oMath":
                payload = etree.tostring(node)
                yield dict(
                    position=position,
                    kind="omml",
                    payload=payload,
                    search_text=math_text(node),
                    context=dict(paragraph=paragraph.text),
                )
            for attr in ("embed", "id"):
                rid = node.get(R + attr)
                if not rid or rid in seen or rid not in doc.part.rels:
                    continue
                rel = doc.part.rels[rid]
                if rel.is_external or not rel.reltype.endswith("/image"):
                    continue
                seen.add(rid)
                yield dict(
                    position=position,
                    kind="image",
                    payload=rel.target_part.blob,
                    search_text="",
                    context=dict(
                        paragraph=paragraph.text,
                        relationship_id=rid,
                        part=str(rel.target_part.partname),
                    ),
                )


def append_supplement(conn, document_id, source_sha, item, parent_unit_id):
    row = conn.execute(
        "SELECT d.sha256,v.source_content,u.document_id,u.section FROM source_documents d "
        "JOIN source_versions v ON v.document_id=d.id JOIN source_units u ON u.id=%s "
        "WHERE d.id=%s AND v.source_format='docx' AND v.source_sha256=%s",
        (parent_unit_id, document_id, source_sha),
    ).fetchone()
    if not row or row["document_id"] != document_id or row["sha256"] != source_sha:
        raise ValueError("Source or parent identity mismatch")
    raw = bytes(row["source_content"])
    if hashlib.sha256(raw).hexdigest() != source_sha:
        raise ValueError("Source bytes changed")
    match = next(
        (
            x
            for x in extract(raw)
            if all(
                x[k] == item[k]
                for k in ("position", "kind", "payload", "search_text", "context")
            )
        ),
        None,
    )
    if not match:
        raise ValueError("Supplement not present in original DOCX")
    parent_position = re.sub(r"/line\d+$", "", row["section"].split("|")[0])
    # Formula-only paragraphs follow the text introducing the equation.
    if parent_position != item["position"]:
        a, b = (
            re.fullmatch(r"body/(\d+)", parent_position),
            re.fullmatch(r"body/(\d+)", item["position"]),
        )
        if (
            item["kind"] != "omml"
            or not a
            or not b
            or not 0 < int(b[1]) - int(a[1]) <= 2
        ):
            raise ValueError("Parent is not the located paragraph or formula context")
    payload_sha = hashlib.sha256(item["payload"]).hexdigest()
    key = digest([document_id, source_sha, item["position"], item["kind"], payload_sha])
    context = dict(item["context"], parent_position=parent_position)
    previous = conn.execute(
        "SELECT * FROM source_supplements WHERE supplement_key=%s", (key,)
    ).fetchone()
    if previous:
        if (
            previous["parent_unit_id"] != parent_unit_id
            or previous["context"] != context
        ):
            raise ValueError("Existing supplement binding immutable")
        index_formula(conn, key, item)
        return key
    conn.execute(
        "INSERT INTO source_supplements(supplement_key,document_id,source_sha256,parent_unit_id,position,kind,payload_sha256,payload,search_text,context) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            key,
            document_id,
            source_sha,
            parent_unit_id,
            item["position"],
            item["kind"],
            payload_sha,
            item["payload"],
            item["search_text"],
            Jsonb(context),
        ),
    )
    index_formula(conn, key, item)
    return key


def index_formula(conn, key, item):
    if item["kind"] != "omml":
        return
    text = item["search_text"]
    sha = hashlib.sha256(text.encode()).hexdigest()
    old = conn.execute(
        "SELECT rendering_sha256 FROM formula_search_renderings WHERE supplement_key=%s AND renderer_version='omml-search/2'",
        (key,),
    ).fetchone()
    if old:
        if old["rendering_sha256"] != sha:
            raise ValueError("Rendering version immutable; increment version")
        return
    conn.execute(
        "INSERT INTO formula_search_renderings(supplement_key,renderer_version,search_text,rendering_sha256) VALUES (%s,'omml-search/2',%s,%s)",
        (key, text, sha),
    )


def assess(conn, key, candidate_key, conclusion, analysis):
    required = {
        "stem",
        "answer",
        "rubric",
        "science",
        "style",
        "media",
        "duplicates",
        "uncertainties",
    }
    if set(analysis) != required or any(not v for v in analysis.values()):
        raise ValueError("Every assessment dimension and limitation must be explicit")
    packet = conn.execute(
        "SELECT * FROM candidate_review_packets WHERE candidate_key=%s",
        (candidate_key,),
    ).fetchone()
    if not packet:
        raise ValueError("Missing immutable packet")
    from app.auto_quality import evidence

    # Revalidate exact current source bytes, citations and style identities; no AI round creation.
    evidence(conn, packet["question_id"])
    record = dict(
        candidate_key=candidate_key,
        revision_sha256=packet["revision_sha256"],
        conclusion=conclusion,
        analysis=analysis,
    )
    sha = digest(record)
    old = conn.execute(
        "SELECT assessment_sha256 FROM candidate_science_assessments WHERE assessment_key=%s",
        (key,),
    ).fetchone()
    if old:
        if old["assessment_sha256"] != sha:
            raise ValueError("Existing assessment immutable")
        return
    conn.execute(
        "INSERT INTO candidate_science_assessments(assessment_key,candidate_key,revision_sha256,assessor,conclusion,analysis,assessment_sha256) VALUES (%s,%s,%s,'Codex AI-assisted',%s,%s,%s)",
        (
            key,
            candidate_key,
            packet["revision_sha256"],
            conclusion,
            Jsonb(analysis),
            sha,
        ),
    )


def annotate(conn, key, uid, kind, analysis):
    from app.candidate_review import snapshot_unit

    unit = snapshot_unit(conn, uid)
    record = dict(
        unit_id=uid,
        unit_sha256=unit["unit_sha256"],
        source_sha256=unit["source_sha256"],
        kind=kind,
        analysis=analysis,
    )
    old = conn.execute(
        "SELECT unit_id,unit_sha256,source_sha256,kind,analysis FROM source_text_annotations WHERE annotation_key=%s",
        (key,),
    ).fetchone()
    if old:
        if old != record:
            raise ValueError("Annotation identity or analysis changed; use new key")
        return
    conn.execute(
        "INSERT INTO source_text_annotations(annotation_key,unit_id,unit_sha256,source_sha256,kind,analysis) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            key,
            uid,
            record["unit_sha256"],
            record["source_sha256"],
            kind,
            Jsonb(analysis),
        ),
    )


def search_formulas(conn, query, limit=20):
    """Use a caller-authorized connection; return locators, never science approval."""
    if not query.strip() or not 1 <= limit <= 100:
        raise ValueError("Nonempty query and limit 1..100 required")
    return conn.execute(
        "SELECT s.supplement_key,s.document_id,s.source_sha256,s.position,s.parent_unit_id,"
        "s.payload_sha256,s.scientific_status,r.search_text,r.renderer_version "
        "FROM source_supplements s JOIN formula_search_renderings r USING(supplement_key) "
        "JOIN source_documents d ON d.id=s.document_id "
        "WHERE r.renderer_version='omml-search/2' AND d.sha256=s.source_sha256 "
        "AND strpos(lower(r.search_text),lower(%s))>0 ORDER BY s.document_id,s.position LIMIT %s",
        (query, limit),
    ).fetchall()
