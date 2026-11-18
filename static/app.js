"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const yen = (v) => v == null ? "—" : Math.round(v).toLocaleString("ja-JP") + "円";
const jst = (iso) => { if (!iso) return "—"; const d = new Date(iso); return d.toLocaleString("ja-JP", {timeZone: "Asia/Tokyo", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"}) + " JST"; };
let STATUS = null;
let state = { query: "", url: null, target: null, condition: "new", view: "total", last: null };
let inflight = null;

// N-13: 端末ごとのランダムID（個人を特定しない）。共有回線でも端末ごとに回数を数えるため
function sessionId() {
  let id = sessionStorage.getItem("sid");
  if (!id) {
    const a = new Uint8Array(16); crypto.getRandomValues(a);
    id = Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
    sessionStorage.setItem("sid", id);
  }
  return id;
}
async function api(path, opts = {}) {
  // R3-5: コードそのものは保存せず、サーバーが発行した利用券（トークン）だけを送る
  const tok = sessionStorage.getItem("accessToken");
  const headers = Object.assign({ "X-Session-Id": sessionId() }, opts.headers || {}, tok ? { "X-Access-Token": tok } : {});
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  const j = await r.json().catch(() => ({ error: "通信エラーが発生しました" }));
  if (r.status === 401 && j.need_code) {
    // N-7: prompt は使わず、画面内の入力欄を1回だけ表示する
    sessionStorage.removeItem("accessToken");
    showGate("アクセスコードを入力してください");
  }
  if (!r.ok) throw new Error(typeof j.error === "string" ? j.error : "エラーが発生しました");
  return j;
}
const needGate = () => !!(STATUS && STATUS.access_code_required && !sessionStorage.getItem("accessToken"));
function showGate(msg) {
  $("#gate").hidden = false; $("#searchForm").hidden = true; $("#dash").hidden = true; $("#home").hidden = false;
  $("#gateMsg").textContent = msg || ""; $("#gateCode").focus();
}
$("#gateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const c = $("#gateCode").value.trim();
  if (!c) return;
  try {
    const r = await fetch("/api/auth", { method: "POST", headers: { "Content-Type": "application/json", "X-Session-Id": sessionId() }, body: JSON.stringify({ code: c }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.token) { $("#gateMsg").textContent = j.error || "確認できませんでした"; return; }
    sessionStorage.setItem("accessToken", j.token);
    $("#gate").hidden = true; $("#searchForm").hidden = false; $("#gateCode").value = "";
    routeFromUrl();
  } catch { $("#gateMsg").textContent = "通信エラーが発生しました"; }
});

/* ---------- 起動 ---------- */
(async () => {
  try {
    // R3-11: 管理者向けの注意は、自分のPCで開いたときか ?admin=<ADMIN_TOKEN> を付けたときだけ表示される
    const adm = new URLSearchParams(location.search).get("admin");
    STATUS = await api("/api/status" + (adm ? "?admin=" + encodeURIComponent(adm) : ""));
    $("#demoBanner").hidden = !STATUS.demo_mode;
    $("#creditRakuten").hidden = !STATUS.credits.includes("rakuten");
    $("#creditYahoo").hidden = !STATUS.credits.includes("yahoo");
    if (STATUS.warnings && STATUS.warnings.length) {
      $("#adminWarn").hidden = false;
      $("#adminWarn").textContent = "管理者向けの注意：" + STATUS.warnings.join(" ／ ");
    }
    if (STATUS.llm_enabled) {
      $("#aiNotice").hidden = false;
      $("#aiNotice").textContent = `写真を選ぶと、商品の特定のため画像が外部のAIサービス（${STATUS.llm_provider_host}）に送信されます。人物や個人情報が写った画像は使わないでください。`;
    }
  } catch { /* noop */ }
  if (needGate()) showGate(); else routeFromUrl();
})();
window.addEventListener("popstate", routeFromUrl);
function routeFromUrl() {
  const p = new URLSearchParams(location.search);
  const q = p.get("q");
  if (needGate()) return showGate();
  if (q) {
    $("#q").value = q;
    startSearch(q, false, p.get("cond") === "used" ? "used" : "new", parseInt(p.get("tp") || "", 10) || null);
  }
  else showHome();
}
function showHome() { if (inflight) inflight.abort(); $("#dash").hidden = true; $("#home").hidden = false; $("#q").focus(); }

/* ---------- 予測変換（固定辞書のみ） ---------- */
let sugTimer = null, sugIdx = -1, sugItems = [];
$("#q").addEventListener("input", () => {
  clearTimeout(sugTimer);
  const v = $("#q").value.trim();
  if (!v || /^https?:/.test(v) || needGate()) return hideSug();
  sugTimer = setTimeout(async () => {
    try { showSug((await api("/api/suggest?q=" + encodeURIComponent(v))).suggestions); } catch { hideSug(); }
  }, 180);
});
$("#q").addEventListener("keydown", (e) => {
  if ($("#sugg").hidden) return;
  if (e.key === "ArrowDown") { sugIdx = Math.min(sugIdx + 1, sugItems.length - 1); paintSug(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { sugIdx = Math.max(sugIdx - 1, -1); paintSug(); e.preventDefault(); }
  else if (e.key === "Enter" && sugIdx >= 0) { $("#q").value = sugItems[sugIdx]; hideSug(); }
  else if (e.key === "Escape") hideSug();
});
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) hideSug(); });
function showSug(list) {
  sugItems = list || []; sugIdx = -1;
  if (!sugItems.length) return hideSug();
  $("#sugg").innerHTML = sugItems.map((s, i) => `<li role="option" id="sug-${i}" aria-selected="false" data-i="${i}">${esc(s)}</li>`).join("");
  $("#sugg").hidden = false; $("#q").setAttribute("aria-expanded", "true");
  $("#sugg").querySelectorAll("li").forEach((li) => li.addEventListener("mousedown", (e) => {
    e.preventDefault(); $("#q").value = sugItems[+li.dataset.i]; hideSug(); submit();
  }));
}
function paintSug() {
  $("#sugg").querySelectorAll("li").forEach((li, i) => { li.classList.toggle("on", i === sugIdx); li.setAttribute("aria-selected", String(i === sugIdx)); });
  if (sugIdx >= 0) $("#q").setAttribute("aria-activedescendant", "sug-" + sugIdx); else $("#q").removeAttribute("aria-activedescendant");
}
function hideSug() { $("#sugg").hidden = true; $("#q").setAttribute("aria-expanded", "false"); $("#q").removeAttribute("aria-activedescendant"); }

/* ---------- 画像から特定 ---------- */
$("#camBtn").addEventListener("click", () => {
  if (STATUS && STATUS.llm_enabled && !STATUS.demo_mode &&
      !confirm(`画像は商品特定のため外部のAIサービス（${STATUS.llm_provider_host}）に送信されます。よろしいですか？\n（人物・個人情報が写った画像は使わないでください）`)) return;
  $("#img").click();
});
$("#img").addEventListener("change", async () => {
  const f = $("#img").files[0]; if (!f) return;
  const panel = $("#imgPanel"); panel.hidden = false;
  const src = URL.createObjectURL(f);
  if (f.size > 5 * 1024 * 1024) { panel.innerHTML = `<div class="msg">画像が大きすぎます（5MBまで）</div>`; $("#img").value = ""; return; }
  panel.innerHTML = `<img src="${src}" alt="選択した画像"><div>画像から商品を推定しています…</div>`;
  const fd = new FormData(); fd.append("image", f);
  try {
    const j = await api("/api/identify", { method: "POST", body: fd });
    const chips = j.candidates.map((c) => `<button type="button" class="chip" data-n="${esc(c.name)}">${esc(c.name)}${c.confidence ? `（確度${Math.round(c.confidence * 100)}%）` : ""}</button>`).join("");
    panel.innerHTML = `<img src="${src}" alt="選択した画像"><div><div>${esc(j.message)}</div><div><strong>この商品ですか？</strong>（選ぶと検索します。違う場合は上の欄に商品名を入力してください）</div><div class="chips">${chips}</div></div>`;
    panel.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.n; submit(); }));
  } catch (e) {
    panel.innerHTML = `<img src="${src}" alt="選択した画像"><div class="msg">${esc(e.message)}</div>`;
  }
  $("#img").value = "";
});

/* ---------- 検索 ---------- */
$("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); hideSug(); submit(); });
$("#back").addEventListener("click", () => { history.pushState(null, "", "/"); showHome(); });
function submit() {
  const v = $("#q").value.trim();
  if (!v) { $("#homeMsg").textContent = "商品名またはURLを入力してください"; return; }
  $("#homeMsg").textContent = "";
  startSearch(v, true);
}
function startSearch(v, push, condition = "new", target = null) {
  const isUrl = /^https?:/.test(v);
  state = { query: isUrl ? "" : v, url: isUrl ? v : null, target, condition, view: "total", raw: v };
  if (push) pushRoute();
  run();
}
// L3-2: 中古への切替・価格入力も履歴に残し、「戻る」で1つ前の状態に戻れるようにする
function pushRoute() {
  const p = new URLSearchParams({ q: state.raw });
  if (state.condition === "used") p.set("cond", "used");
  if (state.target) p.set("tp", String(state.target));
  const adm = new URLSearchParams(location.search).get("admin");
  if (adm) p.set("admin", adm);
  history.pushState(null, "", "/?" + p.toString());
}
async function run() {
  if (inflight) inflight.abort();
  const ctrl = new AbortController(); inflight = ctrl;
  $("#home").hidden = true; $("#dash").hidden = false;
  $("#title").textContent = state.query || state.url;
  $("#loading").hidden = false; $("#content").innerHTML = "";
  // N-11: 待ち時間の目安と経過秒数を表示
  const t0 = Date.now(), wait = STATUS && STATUS.queue_wait_sec > 1 ? `（混雑中：待ち時間の目安 約${Math.ceil(STATUS.queue_wait_sec)}秒）` : "";
  const tick = () => { $("#loading").textContent = `信頼できるサイトから価格を集めています…${wait} ${Math.floor((Date.now() - t0) / 1000)}秒経過`; };
  tick(); const timer = setInterval(tick, 1000);
  try {
    const r = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
      body: JSON.stringify({ query: state.query || null, url: state.url, target_price: state.target, condition: state.condition }) });
    if (ctrl !== inflight) return;
    state.last = r;
    if (r.display_name && !state.query) state.query = r.display_name;
    render(r);
  } catch (e) {
    if (e.name === "AbortError") return;
    $("#content").innerHTML = `<div class="card c12" style="margin-top:14px"><p class="msg">${esc(e.message)}</p></div>`;
  } finally { clearInterval(timer); if (ctrl === inflight) $("#loading").hidden = true; }
}

/* ---------- ダッシュボード ---------- */
function render(r) {
  $("#title").textContent = r.display_name + (r.demo_mode ? "（サンプル）" : "");
  const st = r.stats, v = r.verdict, ok = !["insufficient", "reference", "choose_variant"].includes(v), ref = v === "reference" ? r.reference_stats : null;
  const notes = (r.notes || []).map((n) => `<div class="note">${esc(n)}</div>`).join("");
  const conf = r.confidence ? `　信頼度：<strong>${esc(r.confidence)}</strong>` : "";
  const pr = r.percentile_rank != null ? `<div class="k">安い方から ${r.percentile_rank}% の位置</div>` : "";
  const seg = (id, cur, opts, label) => `<span class="seg" id="${id}" role="group" aria-label="${label}">${opts.map(([val, txt]) => `<button type="button" data-v="${val}" aria-pressed="${cur === val}" class="${cur === val ? "on" : ""}">${txt}</button>`).join("")}</span>`;
  const html = `
  <div class="grid">
    <div class="card c12">
      <div class="k">${r.target_price ? `判定した価格：${yen(r.target_price)}（${esc(r.basis_label)}）` : "判定（基準：" + esc(r.basis_label) + "）"}</div>
      <div class="verdict v-${esc(v)}">${esc(r.verdict_label)}</div>
      ${pr}
      <div class="k">有効サンプル <strong>${r.sample_count}件</strong>（必要 ${r.min_samples}件以上）／ 取得元 ${r.source_count}サイト（必要 ${r.min_sources}以上）／ ${r.seller_count}店舗（必要 ${r.min_sellers}以上）${conf}</div>
      <ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${notes}
      ${(r.hints || []).length ? `<div class="note"><strong>見つけやすくするヒント</strong><ul>${r.hints.map((h) => `<li>${esc(h)}</li>`).join("")}</ul></div>` : ""}
      <div class="ctrl">
        ${ok ? `<label>検討中の総支払額（送料・手数料込み） <input id="tp" type="number" min="1" inputmode="numeric" placeholder="例 15000" value="${r.target_price ?? ""}"> 円</label>
        <button type="button" class="btn" id="tpGo">この価格を判定</button>` : ""}
        ${seg("condSeg", r.condition, [["new", "新品"], ["used", "中古"]], "新品か中古か")}
      </div>
    </div>
    ${ok && st ? `
    <div class="card c3"><div class="k">中央値</div><div class="num">${yen(st.median)}</div></div>
    <div class="card c3"><div class="k">適正価格帯（中央50%）</div><div class="num">${yen(st.q1)}〜${yen(st.q3)}</div></div>
    <div class="card c3"><div class="k">最安</div><div class="num">${yen(st.min)}</div></div>
    <div class="card c3"><div class="k">最高</div><div class="num">${yen(st.max)}</div></div>` : ""}
    ${ref ? `
    <div class="card c12 ref-card"><div class="ref-label">参考値（判定ではありません）</div>
      <p class="k">比較できる取得元が1サイトのみのため、高い・安いの判定は行わず、取得できた価格を参考として示します（有効サンプル ${ref.n}件・${r.seller_count}店舗）。</p>
      <div class="ref-grid"><div><div class="k">参考中央値</div><div class="num">${yen(ref.median)}</div></div>
      <div><div class="k">参考価格帯（中央50%）</div><div class="num">${yen(ref.q1)}〜${yen(ref.q3)}</div></div>
      <div><div class="k">最安〜最高</div><div class="num">${yen(ref.min)}〜${yen(ref.max)}</div></div></div></div>` : ""}
    ${(r.variant_groups || []).length ? `
    <div class="card c12"><h3>容量を選んでください</h3>
      <p class="k">容量によって価格が大きく異なるため、容量を選ぶとその容量だけで判定します。</p>
      <div class="variants">${r.variant_groups.map((g) => `<button type="button" class="btn" data-variant="${esc(g.label)}">${esc(g.label)}（${g.count}件）</button>`).join("")}</div></div>` : ""}
    <div class="card c6"><h3>価格の推移</h3>${r.trend_hidden ? `<p class="k">${esc(r.trend_hidden)}</p>` : trendChart(r.trend)}
      ${r.trend_note && !r.trend_hidden ? `<p class="k trend-note">${esc(r.trend_note)}</p>` : ""}</div>
    <div class="card c6"><h3>現在の価格分布</h3>
      ${ok ? seg("viewSeg", state.view, [["total", "総支払額"], ["effective", "実質価格（参考）"]], "分布の表示基準") : ""}
      ${ok ? distChart(r, state.view) : `<p class="k">${ref ? "判定を行わないため、分布グラフは表示しません（参考値は上に表示）。" : "データ不足のため、価格帯・分布は表示しません。"}</p>`}
    </div>
    <div class="card c12"><h3>出典（実際に取得した価格）</h3>${table(r.observations, r, false)}
      <details><summary>判定から除外したデータ ${r.excluded_count}件（理由つき）</summary>${table(r.excluded, r, true)}</details>
      <details><summary>取得状況</summary>${fetchReport(r.fetch_reports)}</details>
    </div>
  </div>`;
  $("#content").innerHTML = html;
  if ($("#tpGo")) {
    $("#tpGo").addEventListener("click", () => { const n = parseInt($("#tp").value, 10); state.target = n > 0 ? n : null; pushRoute(); run(); });
    $("#tp").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#tpGo").click(); });
  }
  $("#condSeg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { state.condition = b.dataset.v; state.target = null; pushRoute(); run(); }));
  document.querySelectorAll("[data-variant]").forEach((b) => b.addEventListener("click", () => {
    const v = `${state.query || state.raw} ${b.dataset.variant}`;
    $("#q").value = v; startSearch(v, true, state.condition);
  }));
  const vs = $("#viewSeg");
  if (vs) vs.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { state.view = b.dataset.v; render(state.last); }));
}

function badge(o, r) {
  const st = r.stats;
  if (!st || ["insufficient", "reference", "choose_variant"].includes(r.verdict) || o.total_payable == null) return "";
  if (o.total_payable < st.q1) return `<span class="badge b-cheap">安い</span>`;
  if (o.total_payable <= st.q3) return `<span class="badge b-fair">適正</span>`;
  return `<span class="badge b-high">高い</span>`;
}
function link(o) {
  if (o.is_demo) return `${esc(o.title)} <span class="k">［サンプル・リンクなし］</span>`;
  if (!/^https:\/\//.test(o.url || "")) return esc(o.title);  // L-3: https 以外はリンク化しない
  return `<a href="${esc(o.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(o.title)}</a>`;
}
function table(rows, r, excluded) {
  if (!rows || !rows.length) return `<p class="k">データなし</p>`;
  const head = `<tr><th>取得元</th><th>商品名（出典リンク）</th><th class="r opt">税込価格</th><th class="r opt">送料</th><th class="r">総支払額</th><th class="r opt">ポイント</th><th class="r opt">実質価格</th><th class="opt">取得日時</th><th>${excluded ? "除外理由" : "判定"}</th></tr>`;
  const body = rows.map((o) => `<tr>
    <td>${esc(o.source_name)}${o.seller && o.seller !== o.source_name ? `<div class="k">${esc(o.seller)}</div>` : ""}</td>
    <td>${link(o)}</td>
    <td class="r opt">${yen(o.price)}</td>
    <td class="r opt">${o.shipping == null ? `<span style="color:var(--warn)">送料不明</span>` : o.shipping === 0 ? "無料" : yen(o.shipping)}</td>
    <td class="r"><strong>${o.total_payable == null ? "—" : yen(o.total_payable)}</strong></td>
    <td class="r opt" title="${esc(o.point_note)}">${o.point_amount == null ? "—" : o.point_amount.toLocaleString("ja-JP") + "pt" + (o.point_computed ? `<div class="calc">※倍率からの計算値</div>` : "")}</td>
    <td class="r opt">${yen(o.effective_price)}</td>
    <td class="opt">${jst(o.fetched_at)}</td>
    <td>${excluded ? esc(o.excluded_reason) : badge(o, r)}</td></tr>`).join("");
  return `<div class="tbl-wrap"><table>${head}${body}</table></div>`;
}
function fetchReport(list) {
  if (!list || !list.length) return `<p class="k">自動検索に使える取得元がありません（APIキー未設定）</p>`;
  return `<ul>${list.map((x) => `<li>${esc(x.source_name)}：${x.status === "ok" ? x.count + "件取得" : esc(x.message || x.status)}</li>`).join("")}</ul>`;
}

/* ---------- グラフ（SVG・外部ライブラリなし） ---------- */
const COLORS = ["#2456d6", "#e0861a", "#11865b", "#8a3ffc"];
function trendChart(series) {
  const pts = (series || []).flatMap((s) => s.points);
  if (pts.length < 2) return `<p class="k">まだ推移データが十分にありません。検索されるたびに価格が蓄積され、推移が表示されるようになります。</p>`;
  const W = 520, H = 220, L = 64, R = 10, T = 10, B = 28;
  const ts = pts.map((p) => Date.parse(p.date)), vs = pts.map((p) => p.median);
  const x0 = Math.min(...ts), x1 = Math.max(...ts), y0 = Math.min(...vs) * 0.97, y1 = Math.max(...vs) * 1.03;
  const X = (t) => L + (W - L - R) * (t - x0) / ((x1 - x0) || 1), Y = (v) => T + (H - T - B) * (1 - (v - y0) / ((y1 - y0) || 1));
  let g = "";
  for (let i = 0; i <= 4; i++) { const v = y0 + (y1 - y0) * i / 4; g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#eee"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${Math.round(v).toLocaleString()}</text>`; }
  g += `<text x="${L}" y="${H - 6}">${esc(new Date(x0).toLocaleDateString("ja-JP"))}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${esc(new Date(x1).toLocaleDateString("ja-JP"))}</text>`;
  const lines = series.map((s, i) => {
    const dash = s.kind === "total" ? "" : ` stroke-dasharray="5 3"`;
    const d = s.points.map((p, j) => `${j ? "L" : "M"}${X(Date.parse(p.date)).toFixed(1)},${Y(p.median).toFixed(1)}`).join("");
    const dots = s.points.length < 40 ? s.points.map((p) => `<circle cx="${X(Date.parse(p.date))}" cy="${Y(p.median)}" r="2.5" fill="${COLORS[i % 4]}"><title>${esc(p.date)} ${yen(p.median)}（${p.n}件）</title></circle>`).join("") : "";
    return `<path d="${d}" fill="none" stroke="${COLORS[i % 4]}" stroke-width="2"${dash}/>${dots}`;
  }).join("");
  const legend = series.map((s, i) => `<span><i style="background:${COLORS[i % 4]}"></i>${esc(s.name)}</span>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="価格推移グラフ">${g}${lines}</svg><div class="legend">${legend}</div>`;
}
function distChart(r, view) {
  const obs = r.observations || [];
  const pick = (o) => view === "effective" ? o.effective_price : o.total_payable;
  const vals = obs.map(pick).filter((v) => v != null);
  const st = view === "effective" ? r.effective_stats : r.stats;
  if (view === "effective" && !st) return `<p class="k">ポイント情報が明示された出品が${r.min_samples}件未満のため、実質価格の分布は表示しません（判定は常に総支払額で行います）。</p>`;
  if (!vals.length) return `<p class="k">表示できるデータがありません</p>`;
  const W = 520, H = 190, L = 14, R = 14, T = 20, B = 30;
  const showTarget = view === "total" && r.target_price;
  const all = vals.concat(showTarget ? [r.target_price] : []);
  const lo = Math.min(...all) * 0.97, hi = Math.max(...all) * 1.03;
  const X = (v) => L + (W - L - R) * (v - lo) / ((hi - lo) || 1);
  let s = `<rect x="${X(st.q1)}" y="${T}" width="${Math.max(1, X(st.q3) - X(st.q1))}" height="${H - T - B}" fill="#e7eefc"/><line x1="${X(st.median)}" x2="${X(st.median)}" y1="${T}" y2="${H - B}" stroke="#2456d6" stroke-dasharray="4 3"/><text x="${X(st.median)}" y="${T - 6}" text-anchor="middle">中央値</text>`;
  const counts = {};
  obs.forEach((o) => {
    const v = pick(o); if (v == null) return;
    const bx = Math.round(X(v) / 6); counts[bx] = (counts[bx] || 0) + 1;
    const y = H - B - 8 - (counts[bx] - 1) * 9;
    s += `<circle cx="${X(v)}" cy="${Math.max(T + 4, y)}" r="4" fill="${COLORS[r.sources_used.indexOf(o.source_id) % 4] || "#2456d6"}" opacity=".8"><title>${esc(o.source_name)} ${yen(v)}</title></circle>`;
  });
  if (showTarget) s += `<line x1="${X(r.target_price)}" x2="${X(r.target_price)}" y1="${T}" y2="${H - B}" stroke="#c4361d" stroke-width="2"/><text x="${X(r.target_price)}" y="${H - B + 24}" text-anchor="middle" style="fill:#c4361d">判定価格</text>`;
  s += `<text x="${L}" y="${H - B + 12}">${Math.round(lo).toLocaleString()}円</text><text x="${W - R}" y="${H - B + 12}" text-anchor="end">${Math.round(hi).toLocaleString()}円</text>`;
  const PLATFORM = { rakuten: "楽天市場", yahoo: "Yahoo!ショッピング" };
  const legend = r.sources_used.map((sid, i) => { const o = obs.find((x) => x.source_id === sid); return `<span><i style="background:${COLORS[i % 4]}"></i>${esc(PLATFORM[sid] || (o ? o.source_name : sid))}</span>`; }).join("") + `<span><i style="background:#e7eefc;height:10px"></i>中央50%</span>`;
  const note = view === "effective" ? `<p class="k">参考表示：ポイントが明示された${vals.length}件のみ。判定は総支払額で行っています。</p>` : "";
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="価格分布">${s}</svg><div class="legend">${legend}</div>${note}`;
}

/* ---------- 対象サイト一覧 ---------- */
$("#srcLink").addEventListener("click", (e) => {
  e.preventDefault();
  const list = (STATUS && STATUS.trusted_sources) || [];
  $("#srcList").innerHTML = `<p><a href="/sites">対応サイト一覧（店舗ごとにできること）</a></p><h3>価格を取得する信頼サイト</h3><ul>${list.map((s) => `<li>${esc(s.name)}（${esc(s.domains.join(", "))}）${s.auto_search ? "：自動検索" : s.mall_search ? "：楽天市場店・Yahoo!店で自動検索" : "：URL指定時のみ"}</li>`).join("")}</ul>
    <h3>取得元の状態</h3><ul>${((STATUS && STATUS.adapters) || []).map((a) => `<li>${esc(a.name)}：${a.enabled ? "有効" : "無効（" + esc(a.reason) + "）"}</li>`).join("")}</ul>`;
  $("#srcDialog").showModal();
});
