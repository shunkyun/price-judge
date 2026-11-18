"use strict";
(function () {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CAT = { all: "総合", electronics: "家電", clothes: "服", shoes: "靴", sports: "スポーツ", outdoor: "アウトドア", goods: "生活雑貨", drug: "ドラッグ・化粧品", music: "音楽" };
  const READ = { ok: ["○", "読める（確認済み）"], unverified: ["△", "robots.txt は許可・構造化データ未確認"], blocked: ["×", "このサーバーから読めない"], unchecked: ["－", "未確認"], api: ["API", "APIで読む"] };
  function auto(s, d) {
    const out = [];
    if (s.id === "rakuten") out.push("楽天市場 全体" + (d.rakuten_enabled ? "" : "（APIキー未設定）"));
    if (s.id === "yahoo") out.push("Yahoo!ショッピング 全体" + (d.yahoo_enabled ? "" : "（APIキー未設定）"));
    if (s.id === "amazon_jp") out.push("なし（Keepa 設定時、ASIN 指定で価格履歴のみ）");
    if (s.rakuten_shop) out.push(`楽天市場店（${esc(s.rakuten_shop.code)}）`);
    if (s.yahoo_store) out.push(`Yahoo!店（${esc(s.yahoo_store.id)}）`);
    return out.length ? out.map(esc).join("<br>") : "なし（URL指定時のみ）";
  }
  fetch("/api/sources", { headers: { Accept: "application/json" } }).then((r) => r.json()).then((d) => {
    document.getElementById("shopMax").textContent = d.shop_search_max;
    document.getElementById("apiState").textContent = d.demo_mode
      ? "現在はデモモード（架空のサンプルデータ）です。自動検索は APIキーを設定すると有効になります。"
      : `楽天API：${d.rakuten_enabled ? "有効" : "未設定"}／Yahoo! API：${d.yahoo_enabled ? "有効" : "未設定"}`;
    const dates = [...new Set(d.sources.map((s) => s.verified_at).filter(Boolean))];
    document.getElementById("verified").textContent = dates.join("、") || "—";
    const rows = d.sources.map((s) => {
      const rd = READ[(s.url_read || {}).status] || ["？", "不明"];
      const api = s.official_api && s.official_api.available ? "あり" : "なし";
      const apiNote = s.official_api && (s.official_api.available || s.kind === "marketplace") ? s.official_api.note : "";
      const note = [apiNote, (s.url_read || {}).note, s.note].filter(Boolean).map(esc).join("<br>");
      const dom = s.domains.map((x) => x + (s.path_prefixes && s.path_prefixes.length ? s.path_prefixes[0] : "")).join(", ");
      return `<tr data-id="${esc(s.id)}"><td><b>${esc(s.name)}</b><div class="k">${esc(dom)}</div></td>
        <td>${(s.categories || []).map((c) => esc(CAT[c] || c)).join("・")}</td>
        <td>${auto(s, d)}</td><td>${api}</td>
        <td title="${esc(rd[1])}"><b>${esc(rd[0])}</b> <span class="k">${esc(rd[1])}</span></td>
        <td class="k">${note}</td></tr>`;
    }).join("");
    document.querySelector("#sitesTable tbody").innerHTML = rows;
  }).catch(() => {
    document.querySelector("#sitesTable tbody").innerHTML = '<tr><td colspan="6">一覧を読み込めませんでした</td></tr>';
  });
})();
