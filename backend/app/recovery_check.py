"""Explicit isolated recovery: read-only preflight/readback, opt-in atomic restore.

Never creates/drops databases, removes schemas or prints connection strings/dump text.
"""

from contextlib import contextmanager
from pathlib import Path
import hashlib, json, os, subprocess, tempfile, zipfile
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from app.deployment_target import (
    TARGET,
    SOURCE_SCHEMA,
    verify_database_url,
    verify_database_connection,
)


def package(archive, expected_sha):
    archive = Path(archive)
    digest = hashlib.sha256()
    with archive.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha:
        raise ValueError("package_sha_mismatch")
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) != 3 or set(z.namelist()) != {
            "database.dump",
            "database-manifest.json",
            "RESTORE.txt",
        }:
            raise ValueError("unexpected_package_members")
        if any(x.file_size > 2 * 1024**3 for x in z.infolist()):
            raise ValueError("package_member_too_large")
        manifest = json.loads(z.read("database-manifest.json"))
        if manifest.get("schema") != SOURCE_SCHEMA:
            raise ValueError("unexpected_source_schema")
    return manifest


@contextmanager
def target_connection():
    from uuid import UUID

    if os.environ.get("RECOVERY_READ_AUTHORIZED") != "1":
        raise PermissionError("Recovery target inspection authorization required")
    if any(
        os.environ.get("RECOVERY_TARGET_" + k + "_ID") != TARGET[k]
        for k in ("PROJECT", "ENVIRONMENT")
    ):
        raise PermissionError("Recovery target project/environment mismatch")
    try:
        dbservice = str(UUID(os.environ.get("RECOVERY_TARGET_DATABASE_SERVICE_ID", "")))
        if dbservice in TARGET.values():
            raise ValueError()
    except ValueError:
        raise PermissionError("Separate verified database service required")
    url = os.environ.get("RECOVERY_DATABASE_URL", "")
    verify_database_url(url)
    with psycopg.connect(url, row_factory=dict_row) as c:
        c.execute("SET TRANSACTION READ ONLY")
        verify_database_connection(c)
        version = c.execute("SHOW server_version_num").fetchone()["server_version_num"]
        if int(version) // 10000 != 17:
            raise ValueError("PostgreSQL17_target_required_for_this_dump_client")
        c.execute("SET LOCAL TIME ZONE 'UTC'")
        yield c


def non_system_objects(conn):
    return conn.execute(
        "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname<>'information_schema' AND n.nspname !~ '^pg_' AND c.relkind IN ('r','p','S','v','m','f') UNION ALL SELECT n.nspname,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname<>'information_schema' AND n.nspname !~ '^pg_' UNION ALL SELECT n.nspname,t.typname FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname<>'information_schema' AND n.nspname !~ '^pg_' UNION ALL SELECT 'extension',extname FROM pg_extension WHERE extname<>'plpgsql'"
    ).fetchall()


def require_empty(conn):
    if non_system_objects(conn):
        raise ValueError("target_not_empty_no_restore_permitted")


def verify_restored(conn, manifest):
    schema = manifest["schema"]
    expected = manifest["tables"]
    actual = {
        r["tablename"]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,)
        )
    }
    if actual != set(expected):
        raise ValueError("restored_table_inventory_mismatch")
    checked = 0
    for table, want in expected.items():
        hashes = []
        with conn.cursor(name="independent_restore_readback") as cur:
            cur.itersize = (
                1 if table in ("source_versions", "source_documents") else 500
            )
            cur.execute(
                sql.SQL("SELECT to_jsonb(t)::text AS row FROM {}.{} t").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            for row in cur:
                hashes.append(hashlib.sha256(row["row"].encode()).hexdigest())
        actual = dict(
            rows=len(hashes),
            sorted_jsonb_row_sha256=hashlib.sha256(
                "".join(sorted(hashes)).encode()
            ).hexdigest(),
        )
        if actual != want:
            raise ValueError("restored_table_content_mismatch:" + table)
        checked += 1
    migrations = conn.execute(
        sql.SQL("SELECT name,sha256 FROM {}.schema_migrations ORDER BY name").format(
            sql.Identifier(schema)
        )
    ).fetchall()
    if migrations != manifest["migrations"]:
        raise ValueError("restored_migration_history_mismatch")
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for row in migrations:
        if (
            hashlib.sha256((sql_dir / row["name"]).read_bytes()).hexdigest()
            != row["sha256"]
        ):
            raise ValueError("repository_migration_sha_mismatch")
    sources = conn.execute(
        sql.SQL("SELECT file_id,sha256 FROM {}.source_documents ORDER BY id").format(
            sql.Identifier(schema)
        )
    ).fetchall()
    if sources != manifest["sources"]:
        raise ValueError("restored_source_identity_mismatch")
    with conn.cursor(name="original_byte_readback") as cur:
        cur.itersize = 1
        cur.execute(
            sql.SQL(
                "SELECT source_content,source_sha256 FROM {}.source_versions"
            ).format(sql.Identifier(schema))
        )
        for r in cur:
            if (
                hashlib.sha256(bytes(r["source_content"])).hexdigest()
                != r["source_sha256"]
            ):
                raise ValueError("restored_original_bytes_mismatch")
    return dict(
        tables_verified=checked,
        migrations_verified=len(migrations),
        source_files_verified=len(sources),
        independent_readback=True,
        api_write_restart_tested=False,
    )


def restore(archive, manifest, pg_bin):
    if any(
        os.environ.get(k) != "1"
        for k in ("RECOVERY_WRITE_AUTHORIZED", "RECOVERY_PRIVATE_TRANSFER_AUTHORIZED")
    ):
        raise PermissionError(
            "Explicit restore and private transfer authorization required"
        )
    version = subprocess.run(
        [str(Path(pg_bin) / "pg_restore"), "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    import re

    if not re.search(r"PostgreSQL\) 17\.", version):
        raise ValueError("PostgreSQL17_client_required")
    # Repeat immediately before restore; no override for a partially populated target.
    with target_connection() as c:
        require_empty(c)
    with tempfile.TemporaryDirectory(prefix="approved-private-restore-") as directory:
        dump = Path(directory) / "database.dump"
        digest = hashlib.sha256()
        with (
            zipfile.ZipFile(archive) as z,
            dump.open("xb") as out,
            z.open("database.dump") as source,
        ):
            dump.chmod(0o600)
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                out.write(chunk)
        if (
            digest.hexdigest() != manifest["dump_sha256"]
            or dump.stat().st_size != manifest["dump_bytes"]
        ):
            raise ValueError("dump_sha_or_size_mismatch")
        info = conninfo_to_dict(os.environ["RECOVERY_DATABASE_URL"])
        mapping = {
            "host": "PGHOST",
            "port": "PGPORT",
            "dbname": "PGDATABASE",
            "user": "PGUSER",
            "password": "PGPASSWORD",
            "sslmode": "PGSSLMODE",
            "sslrootcert": "PGSSLROOTCERT",
        }
        # No inherited PGSERVICE/PGOPTIONS/alternate target fallback.
        env = {
            k: os.environ[k]
            for k in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
            if k in os.environ
        }
        env.update({dest: info[src] for src, dest in mapping.items() if src in info})
        env["PGCONNECT_TIMEOUT"] = "15"
        try:
            subprocess.run(
                [
                    str(Path(pg_bin) / "pg_restore"),
                    "--dbname",
                    info["dbname"],
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    str(dump),
                ],
                env=env,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            raise ValueError(
                "restore_failed_target_preserved_no_automatic_cleanup"
            ) from None
    # A new connection, not pg_restore's exit status, determines readback success.
    with target_connection() as c:
        return verify_restored(c, manifest)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["package", "empty-target", "verify-restored", "restore"]
    )
    parser.add_argument("--archive", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--pg-bin", default="/usr/bin")
    args = parser.parse_args()
    manifest = package(args.archive, args.sha256)
    if args.action == "package":
        result = dict(
            package_sha_verified=True,
            tables=len(manifest["tables"]),
            migrations=len(manifest["migrations"]),
            restored=False,
        )
    elif args.action == "restore":
        result = restore(args.archive, manifest, args.pg_bin)
    else:
        with target_connection() as c:
            if args.action == "empty-target":
                require_empty(c)
                result = dict(empty_target_verified=True, restored=False)
            else:
                result = verify_restored(c, manifest)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print chained SQL, provider errors, URLs, passwords or dump excerpts.
        print(json.dumps(dict(status="failed", error_type=type(exc).__name__)))
        raise SystemExit(1)
