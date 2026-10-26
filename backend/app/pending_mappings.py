"""Persist draft associations with exact version/unit/quote checks, never approval."""

from psycopg.types.json import Jsonb


def persist(conn, proposals):
    from app.source_policy import current_source_sql

    rows = conn.execute(
        "SELECT u.*,d.file_id,d.sha256 FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE "
        + current_source_sql()
    ).fetchall()
    units = {(r["file_id"], r["sha256"], r["ordinal"]): r for r in rows}

    def resolve(ref):
        row = units.get((ref["file_id"], ref["source_sha256"], ref["ordinal"]))
        if not row or row["ocr_global_page"] != ref["ocr_page"]:
            raise ValueError("Draft mapping source identity mismatch")
        return row

    inserted = 0
    for p in proposals:
        if p["status"] != "pending":
            raise ValueError("Only pending proposals may be imported")
        source = resolve(p["source"])
        target = resolve(p["target"]) if p["target"] else None
        evidence = p["evidence"]
        quote = evidence.get("quote")
        if target:
            quote = evidence["textbook_reference"]["quote"]
        if not quote or quote not in (target or source)["text"]:
            raise ValueError("Draft mapping quote is not in its exact source unit")
        if p["knowledge_label"] not in (target or source)["text"]:
            raise ValueError("Draft knowledge label missing from source")
        result = conn.execute(
            "INSERT INTO source_mapping_proposals(source_unit_id,target_unit_id,knowledge_label,method,evidence) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
            (
                source["id"],
                target["id"] if target else None,
                p["knowledge_label"],
                p["method"],
                Jsonb(evidence),
            ),
        ).fetchone()
        inserted += result is not None
    return inserted


if __name__ == "__main__":
    import argparse
    import json
    from app.source_files import read_bounded
    from app.source_intake import isolated_db

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--schema", required=True)
    parser.add_argument("--target", required=True, choices=("development", "staging"))
    args = parser.parse_args()
    data = [
        json.loads(line)
        for line in read_bounded(args.path, 100 * 1024 * 1024)
        .decode("utf-8")
        .splitlines()
        if line.strip()
    ]
    with isolated_db(args.schema, args.target) as conn:
        count = persist(conn, data)
    print(f"{count} pending proposals inserted; no authoritative mappings or approvals")
