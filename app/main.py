"""FastAPI アプリ本体。 uvicorn app.main:app で起動。"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import time
from typing import Optional

import httpx
from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adapters.demo import DEMO_PRODUCTS
from .config import BASE_DIR, get_settings, validate
from .llm import LLMQuotaExceeded
from .ratelimit import RateLimiter, client_ip
from .service import PriceService, ServiceBusy, UserError
from .suggest import load_seed, suggest

log = logging.getLogger("price_judge")
settings = get_settings()
for w in validate(settings):  # N-14, N-17
    log.warning("設定の警告: %s", w)
service = PriceService(settings)
for w in service.publish_warnings():  # N-25
    log.warning("公開前の確認: %s", w)
seed = load_seed(settings.suggest_seed_path)
limiter = RateLimiter()

app = FastAPI(title="価格チェッカー", version="1.1.0", docs_url=None, redoc_url=None, openapi_url=None)
STATIC = BASE_DIR / "static"
MAX_BODY = settings.max_image_bytes + 64 * 1024

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _json(body: dict, code: int, extra: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(body, status_code=code, headers={**SECURITY_HEADERS, **(extra or {})})


def code_ok(given: str) -> bool:
    return bool(settings.access_code) and hmac.compare_digest(given.encode("utf-8", "ignore"),
                                                              settings.access_code.encode())


def _session_id(request: Request) -> str:
    sid = request.headers.get("x-session-id", "")
    return sid if re.fullmatch(r"[A-Za-z0-9-]{8,64}", sid) else ""


def _ip(request: Request) -> str:
    return client_ip(request, settings.trust_proxy, settings.client_ip_header, settings.trusted_proxy_cidrs)


# R3-5/R3-7: アクセスコードが正しければ、サーバーが署名した利用券（トークン）を発行する。
# 以後の要求はトークンで認証するため、同じ回線で誰かがコードを打ち間違えても、すでに使っている人は止まらない。
_SESSION_KEY = (os.getenv("SESSION_SECRET", "") or secrets.token_hex(32)).encode()


def issue_token(now: Optional[float] = None) -> str:
    sid = secrets.token_hex(16)
    exp = int((now or time.time()) + settings.session_hours * 3600)
    sig = hmac.new(_SESSION_KEY, f"{sid}.{exp}.{settings.access_code}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{sid}.{exp}.{sig}"


def token_sid(tok: str) -> str:
    """有効なトークンなら端末ID、無効なら空文字"""
    m = re.fullmatch(r"([0-9a-f]{32})\.(\d{1,12})\.([0-9a-f]{32})", tok or "")
    if not m or int(m.group(2)) < time.time():
        return ""
    sig = hmac.new(_SESSION_KEY, f"{m.group(1)}.{m.group(2)}.{settings.access_code}".encode(), hashlib.sha256).hexdigest()[:32]
    return m.group(1) if hmac.compare_digest(sig, m.group(3)) else ""


LOCK_MSG = "アクセスコードの誤りが続いたため、10分ほどお待ちください。"


def _fail_keys(request: Request, ip: str) -> list:
    """失敗回数を数えるキー：端末（X-Session-Id）ごと＋回線（IP）ごとの上限（R3-5）"""
    sid = _session_id(request)
    keys = [(f"codefail:ip:{ip}", settings.access_code_ip_max_failures)]
    keys.append((f"codefail:sid:{sid}", settings.access_code_max_failures) if sid
                else (f"codefail:ipdev:{ip}", settings.access_code_max_failures))
    return keys


def check_code(request: Request, ip: str, code: str):
    """(ok, error_response)。ロック中は照合せずに 429（総当たりで正解を探せないように）"""
    keys = _fail_keys(request, ip)
    if any(limiter.exceeded(k, lim, 600) for k, lim in keys):
        return False, _json({"error": LOCK_MSG}, 429, {"Retry-After": "600"})
    if code_ok(code):
        return True, None
    for k, _ in keys:
        limiter.record(k, 600)
    return False, None


@app.middleware("http")
async def guard(request: Request, call_next):
    # M-1: 本文を受信する前に Content-Length で拒否（チャンク転送は無効化）
    if request.method == "POST":
        cl = request.headers.get("content-length")
        if cl is None or not cl.isdigit():
            return _json({"error": "Content-Length が必要です"}, 411)
        if int(cl) > MAX_BODY:
            return _json({"error": "送信データが大きすぎます（画像は5MBまで）"}, 413)
    path = request.url.path
    ip = _ip(request)
    sid = ""
    authed = False
    if settings.access_code and path.startswith("/api/") and path not in ("/api/status", "/api/auth", "/api/sources"):
        sid = token_sid(request.headers.get("x-access-token", ""))
        if not sid:
            # 画面以外（API 直接利用）向けに X-Access-Code も受け付ける。この場合は回線（IP）単位で数える
            ok, err = check_code(request, ip, request.headers.get("x-access-code", ""))
            if err is not None:
                return err
            if not ok:
                return _json({"error": "アクセスコードが必要です", "need_code": True}, 401)
        authed = True
    limits = {"/api/analyze": settings.rate_analyze_per_min, "/api/identify": settings.rate_identify_per_min,
              "/api/suggest": settings.rate_suggest_per_min}
    if path in limits:
        lim = limits[path]
        if authed and sid:
            # N-13: 利用券ごと（端末ごと）に数え、共有回線のIP上限は緩める。利用券は IP ごとに発行数の上限あり（R3-7）
            ok = limiter.allow(f"{path}:sid:{sid}", lim) and limiter.allow(
                f"{path}:ip:{ip}", max(lim, settings.rate_ip_authed_per_min * lim // max(1, settings.rate_analyze_per_min)))
        else:
            ok = limiter.allow(f"{path}:ip:{ip}", lim)
        if not ok:
            return _json({"error": "アクセスが集中しています。1分ほど待ってから再度お試しください。"}, 429, {"Retry-After": "60"})
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


class AuthReq(BaseModel):
    code: str = Field("", max_length=200)


@app.post("/api/auth")
async def api_auth(req: AuthReq, request: Request):
    """アクセスコードの確認（N-7: 画面で最初に1回だけ入力）→ 利用券を発行（R3-5, R3-7）"""
    if not settings.access_code:
        return {"ok": True, "required": False}
    ip = _ip(request)
    ok, err = check_code(request, ip, req.code.strip())
    if err is not None:
        return err
    if not ok:
        return _json({"error": "アクセスコードが違います", "need_code": True}, 401)
    if not limiter.allow(f"newsess:{ip}", settings.max_sessions_per_ip, 3600):
        return _json({"error": "この回線から新しく開始された利用が多すぎます。しばらくしてからお試しください。"}, 429,
                     {"Retry-After": "600"})
    return {"ok": True, "required": True, "token": issue_token()}


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):  # L-10: エラー形式を {"error": "..."} に統一
    return JSONResponse({"error": "入力内容を確認してください（価格は1円以上の整数、商品名は300文字以内）"}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


class AnalyzeReq(BaseModel):
    query: Optional[str] = Field(None, max_length=300)
    url: Optional[str] = Field(None, max_length=2000)
    target_price: Optional[int] = Field(None, gt=0, lt=100_000_000)
    condition: str = "new"


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse(STATIC / "index.html")


@app.api_route("/terms", methods=["GET", "HEAD"])
def terms():
    return FileResponse(STATIC / "terms.html")


@app.api_route("/sites", methods=["GET", "HEAD"])
def sites():
    return FileResponse(STATIC / "sites.html")


@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
def favicon():  # N-27: 既定の /favicon.ico 要求にも応答（404 を出さない）
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    return {"ok": True}


def is_admin(request: Request, admin: str) -> bool:
    """R3-11: 管理者向けの注意を返すのは、自分のPCからの接続か ?admin=<ADMIN_TOKEN> のときだけ"""
    if settings.admin_token and admin and hmac.compare_digest(admin.encode(), settings.admin_token.encode()):
        return True
    peer = request.client.host if request.client else ""
    try:
        loop = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loop = peer == "testclient"
    # プロキシ経由（転送ヘッダあり）は自分のPCとはみなさない
    return loop and not request.headers.get("x-forwarded-for") and not request.headers.get(settings.client_ip_header or "x-none")


@app.get("/api/status")
def status(request: Request, admin: str = Query("", max_length=200)):
    st = service.status()
    if not is_admin(request, admin):
        st["warnings"] = []
    return st


@app.get("/api/sources")
def api_sources():
    """対応サイト一覧（店舗ごとに何ができるか）。秘密情報は含まない"""
    return service.sources_overview()


@app.get("/api/suggest")
def api_suggest(q: str = Query("", max_length=100)):
    # H-4: 利用者の検索語は候補に使わない。固定辞書（＋デモ時はサンプル商品名）のみ
    if settings.demo_mode:
        # N-10: デモでは、データのあるサンプル商品だけを候補にする
        cands = [(p["name"], 1000) for p in DEMO_PRODUCTS]
    else:
        cands = [(s, 10) for s in seed]
    return {"suggestions": suggest(q, cands)}


@app.post("/api/analyze")
async def api_analyze(req: AnalyzeReq):
    try:
        return await service.analyze(req.query, req.url, req.target_price, req.condition)
    except ServiceBusy as e:
        return _json({"error": str(e), "busy": True}, 503, {"Retry-After": str(e.retry_after)})
    except UserError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


def sniff_image(data: bytes) -> Optional[str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


@app.post("/api/identify")
async def api_identify(image: UploadFile = File(...)):
    data = await image.read(settings.max_image_bytes + 1)
    if len(data) > settings.max_image_bytes:
        return JSONResponse({"error": "画像が大きすぎます（5MBまで）"}, status_code=413)
    mime = sniff_image(data)
    if mime is None:
        return JSONResponse({"error": "JPEG / PNG / WebP / GIF 画像を選んでください"}, status_code=400)
    if not service.llm.enabled:
        if settings.demo_mode:
            return {"demo": True, "candidates": [{"name": p["name"], "brand": "", "category": "", "confidence": 0}
                                                 for p in DEMO_PRODUCTS[:3]],
                    "message": "デモモードのため画像は解析・送信していません。サンプル候補を表示しています。"}
        return JSONResponse({"error": "画像からの商品特定は現在利用できません（管理者がAI設定をしていません）"}, status_code=503)
    try:
        async with httpx.AsyncClient() as client:
            cands = await service.llm.identify_image(client, data, mime)
    except LLMQuotaExceeded:
        return JSONResponse({"error": "本日の画像解析の上限に達しました。商品名で検索してください。"}, status_code=429)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"画像解析に失敗しました（{type(e).__name__}）"}, status_code=502)
    return {"demo": False, "candidates": cands,
            "message": "AIによる推定です。正しい商品を選ぶか、商品名を修正してください。" if cands else "商品を特定できませんでした。"}
