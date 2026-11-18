"""配布・起動スクリプト・画面の静的チェック（L-5, L-9, M-9, M-10, M-11）"""
import asyncio
import os
import shutil
import subprocess
import zipfile

import httpx

from app.adapters import page
from tests.helpers import ROOT, allow


def test_l9_package_excludes_secrets(tmp_path):
    work = tmp_path / "pj"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("dist", ".venv", "__pycache__", ".pytest_cache", "data"))
    for f in (".env", ".env.local", ".env.production"):
        (work / f).write_text("SECRET=1")
    (work / "data").mkdir()
    (work / "data" / "prices.db").write_text("x")
    subprocess.run(["sh", str(work / "scripts" / "package.sh")], check=True, capture_output=True)
    names = zipfile.ZipFile(work / "dist" / "price-judge.zip").namelist()
    assert "price-judge/.env.example" in names
    assert not any(n.endswith((".env", ".env.local", ".env.production", ".db")) for n in names)
    assert "price-judge/scripts/start.bat" in names and "price-judge/static/terms.html" in names


def test_scripts_use_python3_and_localhost():
    sh = open(os.path.join(ROOT, "scripts", "start.sh"), encoding="utf-8").read()
    assert "python3" in sh and "127.0.0.1" in sh
    assert "python3" in open(os.path.join(ROOT, "scripts", "package.sh"), encoding="utf-8").read()
    bat = open(os.path.join(ROOT, "scripts", "start.bat"), encoding="utf-8").read()
    assert "app.launcher" in bat and "app.launcher" in sh
    launcher = open(os.path.join(ROOT, "app", "launcher.py"), encoding="utf-8").read()
    assert "uvicorn" in launcher and "127.0.0.1" in launcher


def test_m10_requirements_pinned():
    for f in ("requirements.txt", "requirements-dev.txt"):
        for line in open(os.path.join(ROOT, f), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith(("#", "-r")):
                assert "==" in line, line


def test_frontend_a11y_and_history_markers():
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    for m in ("pushState", "popstate", "AbortController", "aria-pressed", "aria-activedescendant"):
        assert m in js, m
    assert 'id="camBtn"' in html and 'aria-live="polite"' in html and 'href="/terms"' in html


def test_l2_page_fetch_stops_at_byte_cap(monkeypatch):
    monkeypatch.setattr(page, "MAX_BYTES", 1000)
    sent = {"n": 0}

    async def body():
        for _ in range(100):
            sent["n"] += 1
            yield b"a" * 500

    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body()))

    async def run():
        async with httpx.AsyncClient(transport=transport) as cl:
            return await page.fetch_page(cl, "https://zozo.jp/x", allow())

    _, html = asyncio.run(run())
    assert len(html) <= 1500 and sent["n"] < 10
