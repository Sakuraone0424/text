"""Real HTTPS fixtures. Private CA trusted only inside disposable browser processes.

Never skip a requested browser or disable certificate verification. No production data.
"""
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time
import uuid

import httpx
from playwright.sync_api import sync_playwright
import pytest


class QAContext(dict):
    def __repr__(self):
        # pytest must never print a QA password or DSN when an assertion fails.
        return "<isolated HTTPS QA context; credentials redacted>"


def run(*args):
    subprocess.run(list(map(str, args)), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def https_app(live_app, tmp_path):
    client, dsn, blueprint = live_app
    root = tmp_path / 'https-qa'; root.mkdir(mode=0o700)
    run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1', '-subj', '/CN=Ephemeral synthetic QA CA', '-keyout', root/'ca.key', '-out', root/'ca.crt')
    run('openssl', 'req', '-new', '-newkey', 'rsa:2048', '-noenc', '-subj', '/CN=localhost', '-keyout', root/'server.key', '-out', root/'server.csr')
    (root/'server.ext').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n')
    run('openssl', 'x509', '-req', '-in', root/'server.csr', '-CA', root/'ca.crt', '-CAkey', root/'ca.key', '-CAcreateserial', '-days', '1', '-extfile', root/'server.ext', '-out', root/'server.crt')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    origin = f'https://localhost:{port}'
    password = uuid.uuid4().hex
    env = {k:v for k,v in os.environ.items() if not k.startswith(('RAILWAY_', 'QUIZ_', 'DEEPSEEK_', 'AI_', 'NOTION_')) and k not in {'SESSION_SECRET', 'APP_PASSWORD', 'DATABASE_URL'}}
    env.update(DATABASE_URL=dsn, QUIZ_USERNAME='test-owner', APP_PASSWORD=password, SESSION_SECRET=uuid.uuid4().hex, QUIZ_PUBLIC_ORIGIN=origin)
    for k in ('QUIZ_WORKER_ENABLED', 'AI_AUTOMATION_ENABLED', 'AI_QUEUE_AUTOPLAN', 'AI_FULL_CORPUS_ENABLED', 'QUIZ_DAILY_PREP_ENABLED', 'DEEPSEEK_API_AUTHORIZED', 'DEEPSEEK_PRIVATE_DATA_AUTHORIZED', 'NOTION_SYNC_ENABLED'):
        env[k] = '0'
    tls = ssl.create_default_context(cafile=str(root/'ca.crt'))
    log = (root/'server.log').open('w'); process = None
    def start():
        nonlocal process
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--ssl-keyfile', str(root/'server.key'), '--ssl-certfile', str(root/'server.crt'), '--no-access-log', '--timeout-graceful-shutdown', '3'], cwd=Path(__file__).resolve().parents[1], env=env, stdout=log, stderr=log)
        with httpx.Client(verify=tls, trust_env=False, timeout=2) as h:
            for _ in range(100):
                try:
                    if h.get(origin+'/health').status_code == 200: return
                except httpx.HTTPError: pass
                if process.poll() is not None: raise RuntimeError('Synthetic HTTPS server exited')
                time.sleep(.1)
        raise RuntimeError('Synthetic HTTPS server timeout')
    def restart():
        process.terminate(); process.wait(timeout=10); start()
    try:
        start()
        with httpx.Client(base_url=origin, verify=tls, trust_env=False, headers={'Origin':origin, 'Connection':'close'}, timeout=20) as h:
            assert h.post('/auth/login', json={'password':password}).status_code == 200
            yield QAContext(client=h, dsn=dsn, blueprint=blueprint, origin=origin, password=password, root=root, restart=restart)
    finally:
        if process and process.poll() is None:
            process.terminate(); process.wait(timeout=10)
        log.close()
        # Includes QA keys, CA, browser certificate databases and session profiles.
        shutil.rmtree(root, ignore_errors=True)


def trusted_browser(pw, kind, root):
    """Process-local trust, preserving the host CA bundle. Never alter the host trust DB."""
    certdir = root/'system-certs'
    if not certdir.exists():
        shutil.copytree('/etc/ssl/certs', certdir, symlinks=True)
        # Replace, rather than follow, the copied bundle symlink.
        bundle = certdir/'ca-certificates.crt'
        if bundle.is_symlink(): bundle.unlink()
        bundle.write_bytes(Path('/etc/ssl/certs/ca-certificates.crt').read_bytes()+(root/'ca.crt').read_bytes())
        shutil.copyfile(root/'ca.crt', certdir/'synthetic-qa.crt')
        run('openssl', 'rehash', certdir)
    if kind == 'firefox':
        profile = root/'firefox-profile'; profile.mkdir()
        run('certutil', '-N', '--empty-password', '-d', 'sql:'+str(profile))
        run('certutil', '-A', '-d', 'sql:'+str(profile), '-n', 'ephemeral-qa', '-t', 'C,,', '-i', root/'ca.crt')
        return pw.firefox.launch_persistent_context(str(profile), headless=True, viewport={'width':390,'height':844}, ignore_https_errors=False)
    browser_type = getattr(pw, kind)
    executable = (os.environ.get('QUIZ_BROWSER_EXECUTABLE') if kind=='chromium' else os.environ.get('QUIZ_WEBKIT_EXECUTABLE')) or browser_type.executable_path
    # Bubblewrap changes only filesystem mounts in this browser's process tree.
    # Network namespace/proxy settings are preserved; nothing is imported globally.
    # Hosted Ubuntu denies unprivileged UID maps. In that disposable CI runner only,
    # allow sudo to establish mount isolation, then drop back to the original UID
    # before the browser starts. No sysctl/AppArmor changes or global CA imports.
    privileged_mount = os.environ.get('QUIZ_QA_PRIVILEGED_MOUNTS') == '1'
    if privileged_mount:
        preserved = 'HOME,USER,LOGNAME,HTTP_PROXY,HTTPS_PROXY,ALL_PROXY,NO_PROXY,http_proxy,https_proxy,all_proxy,no_proxy,SSL_CERT_FILE,SSL_CERT_DIR'
        argv = ['sudo', '-n', '-C', '5', '--preserve-env='+preserved, 'bwrap']
    else:
        argv = ['bwrap', '--unshare-user']
    argv += ['--ro-bind', '/', '/', '--bind', '/tmp', '/tmp', '--proc', '/proc', '--dev', '/dev']
    for directory in {str(root), str(Path.cwd())}:
        argv += ['--bind', directory, directory]
    argv += ['--ro-bind', str(certdir), '/etc/ssl/certs']
    if kind == 'chromium':
        nss = root/'chromium-nss'; nss.mkdir()
        run('certutil', '-N', '--empty-password', '-d', 'sql:'+str(nss))
        run('certutil', '-A', '-d', 'sql:'+str(nss), '-n', 'ephemeral-qa', '-t', 'C,,', '-i', root/'ca.crt')
        legacy = Path.home()/'.pki/nssdb'
        if legacy.is_dir(): argv += ['--bind', str(nss), str(legacy)]
        else:
            data = root/'xdg-data'; (data/'pki').mkdir(parents=True)
            (data/'pki/nssdb').symlink_to(nss, target_is_directory=True)
            argv += ['--setenv', 'XDG_DATA_HOME', str(data)]
    import shlex
    wrapper = root/(kind+'-trusted')
    command = [str(executable)]
    if privileged_mount:
        command = ['/usr/bin/setpriv', '--reuid', str(os.getuid()), '--regid', str(os.getgid()), '--clear-groups', '--no-new-privs'] + command
    wrapper.write_text('#!/bin/sh\nexec '+shlex.join(argv+['--']+command)+' "$@"\n'); wrapper.chmod(0o700)
    browser = browser_type.launch(headless=True, executable_path=str(wrapper))
    return browser.new_context(viewport={'width':390,'height':844}, has_touch=True, ignore_https_errors=False)


@pytest.fixture(params=['chromium','firefox','webkit'])
def https_browser(request, https_app):
    with sync_playwright() as pw:
        context = trusted_browser(pw, request.param, https_app['root'])
        try: yield context
        finally:
            browser = context.browser
            context.close()
            if browser: browser.close()
