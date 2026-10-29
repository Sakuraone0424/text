"""One-time authenticated *QA-only* six-source ZIP upload and atomic import.

The only accepted input is the user's six SHA-locked original OCR/DOCX files.
No excerpt, image, answer, secret, file path, or raw upload is returned/logged.
This application must NEVER be attached to a production deployment.
"""
import asyncio
from contextlib import closing
from hmac import compare_digest
import os
from pathlib import Path
import stat
import tempfile
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.collection_seed import LOCK, import_isolated, preflight
from app.source_policy import COLLECTION

app = FastAPI(title="Isolated source QA intake", docs_url=None, redoc_url=None, openapi_url=None)
_upload_lock = asyncio.Lock()
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 15 * 1024 * 1024
EXPECTED_EXTRA = frozenset(("PRIVATE_QA_SOURCE_LOCK.json", "README_PRIVATE_QA.txt"))


def _require_qa():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise HTTPException(status_code=503, detail="QA environment required")
    if os.environ.get("QA_IMPORT_ENABLED") != "1":
        raise HTTPException(status_code=503, detail="QA intake is disabled")
    if not os.environ.get("SOURCE_STAGING_DATABASE_URL"):
        raise HTTPException(status_code=503, detail="QA database is not configured")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    from app.source_intake import SCHEMA
    if not SCHEMA.fullmatch(schema):
        raise HTTPException(status_code=503, detail="Dedicated QA schema required")
    return schema


def _authorize(request):
    token = os.environ.get("QA_UPLOAD_TOKEN", "")
    received = request.headers.get("authorization", "")
    if len(token) < 40 or not compare_digest(received, "Bearer " + token):
        raise HTTPException(status_code=401, detail="Invalid QA upload token")


def unpack_verified_bundle(archive_path, directory, *, lock_path=LOCK):
    """Reject unexpected entries, links, oversized members, and changed originals."""
    expected = {item["relative_path"] for item in COLLECTION["files"]}
    with zipfile.ZipFile(archive_path) as archive:
        info = archive.infolist()
        names = [item.filename for item in info]
        if (
            len(names) != len(set(names))
            or set(names) != expected | EXPECTED_EXTRA
            or not all(item.file_size >= 0 for item in info)
            or sum(item.file_size for item in info) > MAX_UNCOMPRESSED_BYTES
        ):
            raise ValueError("Unexpected or oversized six-source archive")
        for member in info:
            mode = member.external_attr >> 16
            if (stat.S_IFMT(mode) == stat.S_IFLNK or member.is_dir()
                    or member.file_size > MAX_UNCOMPRESSED_BYTES):
                raise ValueError("Archive links and directories not allowed")
        if archive.testzip() is not None:
            raise ValueError("Archive integrity failed")
        root = Path(directory)
        for item in COLLECTION["files"]:
            filename = item["relative_path"]
            dest = root / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(filename) as source, dest.open("xb") as sink:
                size = 0
                while True:
                    data = source.read(65536)
                    if not data:
                        break
                    size += len(data)
                    if size > MAX_UNCOMPRESSED_BYTES:
                        raise ValueError("Source exceeds bounded size")
                    sink.write(data)
    return preflight(directory, lock_path=lock_path)


PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'">
<title>独立 QA 六源导入</title>
<style>body{font:16px -apple-system,BlinkMacSystemFont,Arial,sans-serif;max-width:650px;margin:4rem auto;padding:1rem;color:#18222c}
label{display:block;margin:1rem 0}input{max-width:100%;width:100%;padding:.65rem;box-sizing:border-box}button{padding:.8rem 1.6rem;border:0;border-radius:6px;background:#193a70;color:white}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f3f5;padding:1rem;border-radius:8px}</style>
<h2>六份锁定资料 · 独立 QA 入库</h2>
<p>仅接受六份原始文件组成的私有 ZIP；使用独立 QA 数据库，不修改生产环境。上传成功后执行六源哈希检查、数据库事务导入与独立只读读回。导入失败不会产生成功验收报告。</p>
<label>一次性上传密钥<input type="password" id="token" autocomplete="off"></label>
<label>六源私有 ZIP 文件<input type="file" id="bundle" accept=".zip,application/zip"></label>
<button id="send">核验并导入</button><pre id="result">未开始上传</pre>
<script>
document.getElementById('send').addEventListener('click', async () => {
 const out=document.getElementById('result'), key=document.getElementById('token').value;
 const file=document.getElementById('bundle').files[0];
 if(!file || !key){out.textContent='请提供密钥及ZIP文件';return}
 if(file.size>12582912){out.textContent='ZIP文件超过12MiB';return}
 out.textContent='正在验证原始文件并执行QA入库...';
 try { const response=await fetch('/import',{method:'POST',headers:{'Authorization':'Bearer '+key,'Content-Type':'application/zip'},body:file,cache:'no-store'});
 const body=await response.json();out.textContent=JSON.stringify({http:response.status,...body},null,2);
 }catch(e){out.textContent='连接或导入请求失败，请检查状态后再重试。'}
});
</script></html>"""


@app.middleware("http")
async def no_cache_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/health")
async def health():
    return {"status": "online", "scope": "qa_only", "source_imported": False}


@app.get("/", response_class=HTMLResponse)
async def upload_form():
    return PAGE


@app.get("/status")
async def status(request: Request):
    _authorize(request)
    schema = _require_qa()
    try:
        from app.source_intake import isolated_db
        from app.source_readiness import audit
        with isolated_db(schema, "staging", readonly=True) as conn:
            state = audit(conn)
        return {
            "status": "readback_available",
            "source_files": state["imported_files"],
            "integrity_passed_files": state["integrity_passed_files"],
            "all_six_byte_integrity_checks_passed":
                state["all_six_byte_integrity_checks_passed"],
            "scientific_accuracy_verified": False,
        }
    except Exception:
        return {"status": "not_verified", "source_files": 0}


@app.post("/import")
async def upload(request: Request):
    _authorize(request)
    schema = _require_qa()
    if request.headers.get("content-type", "").split(";")[0] != "application/zip":
        raise HTTPException(415, detail="application/zip required")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError:
            raise HTTPException(400, detail="Invalid content length")
        if length < 1 or length > MAX_UPLOAD_BYTES:
            raise HTTPException(413, detail="Upload exceeds 12MiB")
    if _upload_lock.locked():
        raise HTTPException(409, detail="QA import already running")
    async with _upload_lock:
        with tempfile.TemporaryDirectory(prefix="qa-six-source-") as temporary:
            folder = Path(temporary)
            archive = folder / "six.zip"
            length = 0
            with archive.open("xb") as sink:
                async for chunk in request.stream():
                    length += len(chunk)
                    if length > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, detail="Upload exceeds 12MiB")
                    sink.write(chunk)
            if not length:
                raise HTTPException(400, detail="Empty upload")
            sources = folder / "sources"
            try:
                items = await asyncio.to_thread(unpack_verified_bundle,archive,sources)
            except (ValueError, OSError, zipfile.BadZipFile, KeyError):
                raise HTTPException(422, detail="Six-source SHA-256 or archive preflight failed")
            report = folder / "private-import-readback.json"
            try:
                verified = await asyncio.to_thread(
                    import_isolated,items,schema,"staging",report,
                    initialize_schema=True
                )
            except Exception:
                # Never leak database details, private paths or source material.
                raise HTTPException(500, detail="QA import did not pass commit/readback; inspect private QA diagnostics")
            return JSONResponse({
                "status": "committed_to_isolated_schema",
                "imported_files": len(verified["imported"]),
                "ocr_page_markers": verified["ocr_page_markers"],
                "postcommit_database_readback_verified":
                    verified["database_integrity"]["independent_post_commit_readback_verified"],
                "science_review_completed": False,
                "official_exam_answers_verified": False,
            })
