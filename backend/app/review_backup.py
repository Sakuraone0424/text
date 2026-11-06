"""Create an authenticated-download handoff locally from an explicit nonproduction snapshot."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile
import tempfile
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from app.source_intake import SCHEMA, private_json


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_package(archive, index, pg_bin):
    """Read-only archive check, not a claim of database restore success."""
    archive = Path(archive)
    if archive.stat().st_size != index["bytes"] or checksum(archive) != index["sha256"]:
        raise ValueError("Backup ZIP SHA or size mismatch")
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) != 3 or set(z.namelist()) != {
            "database.dump",
            "database-manifest.json",
            "RESTORE.txt",
        }:
            raise ValueError(
                "Unexpected files: credentials/configs must never be included"
            )
        if any(i.file_size > 2 * 1024**3 for i in z.infolist()):
            raise ValueError("Backup member too large")
        manifest = json.loads(z.read("database-manifest.json"))
        with tempfile.TemporaryDirectory(
            prefix="verify-private-", dir=archive.parent
        ) as temp:
            dump = Path(temp) / "database.dump"
            with dump.open("xb") as target, z.open("database.dump") as source:
                dump.chmod(0o600)
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(chunk)
            if (
                dump.stat().st_size != manifest["dump_bytes"]
                or checksum(dump) != manifest["dump_sha256"]
            ):
                raise ValueError("Inner dump checksum mismatch")
            # Decode all SQL without emitting private text to logs.
            subprocess.run(
                [str(Path(pg_bin) / "pg_restore"), "--file=/dev/null", str(dump)],
                capture_output=True,
                check=True,
            )
        if z.testzip() is not None:
            raise ValueError("ZIP CRC mismatch")
    return dict(
        zip_sha256=index["sha256"],
        dump_sha256=manifest["dump_sha256"],
        members_allowlisted=True,
        archive_fully_decoded=True,
        restored_database_tested=False,
        contains_credentials_files=False,
    )


def export(destination, schema, pg_bin, expected_database):
    destination = Path(destination).resolve()
    code_root = Path(__file__).resolve().parents[1]
    checkout = next((p for p in (code_root, *code_root.parents)
                     if (p / ".git").exists()), code_root)
    if destination.exists() or destination.is_relative_to(checkout):
        raise ValueError("Choose a new private directory outside checkout")
    if not SCHEMA.fullmatch(schema):
        raise ValueError("Isolated source_staging_* schema required")
    url = os.environ.get("SOURCE_STAGING_DATABASE_URL")
    if not url:
        raise ValueError("Explicit nonproduction SOURCE_STAGING_DATABASE_URL required")
    destination.mkdir(mode=0o700, parents=True)
    dump = destination / "database.dump"
    dump.touch(mode=0o600)
    with psycopg.connect(url, row_factory=dict_row) as c:
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        c.execute("SET TRANSACTION READ ONLY")
        if (
            c.execute("SELECT current_database() AS name").fetchone()["name"]
            != expected_database
        ):
            raise ValueError("Explicit database identity differs")
        if not c.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)
        ).fetchone():
            raise ValueError("Isolated schema missing; never create a database here")
        c.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        c.execute("SET LOCAL TIME ZONE 'UTC'")
        snapshot = c.execute(
            "SELECT pg_export_snapshot() AS snapshot,clock_timestamp() AS at"
        ).fetchone()
        from psycopg.conninfo import conninfo_to_dict

        mapping = {
            "dbname": "PGDATABASE",
            "host": "PGHOST",
            "port": "PGPORT",
            "user": "PGUSER",
            "password": "PGPASSWORD",
            "sslmode": "PGSSLMODE",
            "sslrootcert": "PGSSLROOTCERT",
            "sslcert": "PGSSLCERT",
            "sslkey": "PGSSLKEY",
            "options": "PGOPTIONS",
            "connect_timeout": "PGCONNECT_TIMEOUT",
        }
        env = {
            **os.environ,
            **{mapping[k]: v for k, v in conninfo_to_dict(url).items() if k in mapping},
        }
        subprocess.run(
            [
                str(Path(pg_bin) / "pg_dump"),
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--schema=" + schema,
                "--exclude-table-data=" + schema + ".reviewer_sessions",
                "--snapshot=" + snapshot["snapshot"],
                "--file=" + str(dump),
            ],
            env=env,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [str(Path(pg_bin) / "pg_restore"), "--file=/dev/null", str(dump)],
            check=True,
            capture_output=True,
        )
        tables = {}
        for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
            (schema,),
        ).fetchall():
            hashes = []
            if r["tablename"] == "reviewer_sessions":
                tables[r["tablename"]] = dict(
                    rows=0, sorted_jsonb_row_sha256=hashlib.sha256(b"").hexdigest()
                )
                continue
            with c.cursor(name="handoff_table_hash") as cur:
                cur.execute(
                    sql.SQL("SELECT to_jsonb(t)::text AS row FROM {} t").format(
                        sql.Identifier(r["tablename"])
                    )
                )
                for row in cur:
                    hashes.append(hashlib.sha256(row["row"].encode()).hexdigest())
            tables[r["tablename"]] = dict(
                rows=len(hashes),
                sorted_jsonb_row_sha256=hashlib.sha256(
                    "".join(sorted(hashes)).encode()
                ).hexdigest(),
            )
        manifest = dict(
            database=expected_database,
            schema=schema,
            snapshot_at=snapshot["at"].isoformat(),
            dump_sha256=checksum(dump),
            dump_bytes=dump.stat().st_size,
            archive_fully_decoded=True,
            full_restore_executed=False,
            tables=tables,
            migrations=c.execute(
                "SELECT name,sha256 FROM schema_migrations ORDER BY name"
            ).fetchall(),
            sources=c.execute(
                "SELECT file_id,sha256 FROM source_documents ORDER BY id"
            ).fetchall(),
            off_host_transfer=False,
            excluded_runtime_table_data=["reviewer_sessions"],
            authentication_sessions_restored=False,
            dump_scope="selected_isolated_schema_only",
            hash_method="UTC PostgreSQL17 to_jsonb(row)::text; SHA256 each row; sort row hex hashes; SHA256 concatenation",
        )
    private_json(destination / "database-manifest.json", manifest)
    instructions = destination / "RESTORE.txt"
    with instructions.open("x") as f:
        f.write(
            "PRIVATE BACKUP — contains textbook and exam text.\nSave in your own controlled Files location; do not publish.\nAn explicit empty independent nonproduction restore target requires separate authorization.\nDo not overwrite or recreate the current database. No --clean or --create.\nAfter archive and dump SHA verification, use compatible PostgreSQL17 pg_restore --single-transaction --exit-on-error --no-owner --no-acl on the authorized empty target.\nThen compare all table counts/content hashes, migration hashes, six source hashes, candidate revisions and decision events.\nThis archive was fully decoded but has NOT been restored into another database.\nLater reviews are absent from this snapshot; export a fresh backup after reviewing.\n"
        )
    instructions.chmod(0o600)
    archive = destination / "USTC-private-review-backup.zip"
    with archive.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as bundle:
            for path in (dump, destination / "database-manifest.json", instructions):
                bundle.write(path, arcname=path.name)
    archive.chmod(0o600)
    index = dict(
        filename=archive.name,
        sha256=checksum(archive),
        bytes=archive.stat().st_size,
        snapshot_at=manifest["snapshot_at"],
        restore_warning="Only on an explicitly authorized empty nonproduction target; never overwrite the existing database",
    )
    private_json(destination / "download-index.json", index)
    return index


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--destination", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--pg-bin", required=True)
    p.add_argument("--expected-database", required=True)
    a = p.parse_args()
    print(json.dumps(export(a.destination, a.schema, a.pg_bin, a.expected_database)))
