"use strict";
/* Case queue, audit log, analytics, rules, integration. */
window.LOADERS = {};

/* ---------------- case queue */
let CASES = [];
async function loadCases() { CASES = (await api("/api/cases")).cases; drawCases(); }
function drawCases() {
  const q = $("#cSearch").value.toLowerCase(), lv = $("#cLevel").value, dc = $("#cDec").value;
  const rows = CASES.filter(c => (!lv || c.risk_level === lv) && (!dc || (dc === "pending" ? !c.decision : c.decision === dc)) &&
    (!q || [c.id, c.traveller, c.screened_by, c.doc_summary].join(" ").toLowerCase().includes(q)));
  $("#cBody").innerHTML = rows.map(c => `<tr class="click" data-id="${esc(c.id)}"><td class="mono">${esc(c.id)}</td><td>${esc(fmtTime(c.created_at))}</td><td>${esc(c.traveller || "-")}</td>
    <td>${esc(c.doc_summary)}</td><td><span class="lv ${c.risk_level}">${c.risk_level}</span></td><td>${c.risk_score}</td><td>${esc(ACTTXT[c.action] || c.action)}</td>
    <td>${c.decision ? esc(DECTXT[c.decision]) + `<div class="muted small">${esc(c.decided_by)}</div>` : '<span class="muted">pending</span>'}</td><td>${esc(c.screened_by)}</td><td>${secs(c.processing_ms)}</td></tr>`).join("") ||
    '<tr><td colspan="10" class="empty">No cases match.</td></tr>';
  $$("#cBody tr.click").forEach(tr => tr.onclick = () => openCase(tr.dataset.id));
}
["cSearch", "cLevel", "cDec"].forEach(id => $("#" + id).addEventListener("input", drawCases));
$("#cRefresh").onclick = loadCases;
LOADERS.cases = loadCases;

/* ---------------- audit log */
async function loadAudit() {
  const r = await api("/api/audit?limit=300"); const v = r.verify;
  $("#auditBanner").innerHTML = v.ok
    ? `<div class="banner ok"><b>Chain intact.</b> ${v.count} records verified from the genesis block. Head hash <span class="hash">${esc(v.head || "")}</span></div>`
    : `<div class="banner bad"><b>INTEGRITY FAILURE.</b> Record #${v.first_bad_id}: ${esc(v.reason)}. Records after it can no longer be trusted.</div>`;
  $("#aBody").innerHTML = r.rows.map(a => `<tr><td>${a.id}</td><td>${esc(fmtTime(a.ts))}</td><td>${esc(a.actor)}</td><td>${esc(a.role)}</td><td><b>${esc(a.action)}</b></td>
    <td class="mono">${esc(a.case_id || "")}</td><td class="small">${esc(summ(a))}</td><td class="hash">${esc(a.prev_hash.slice(0, 12))}</td><td class="hash">${esc(a.hash.slice(0, 12))}</td></tr>`).join("");
  refreshStatus();
}
function summ(a) {
  const d = a.details || {}; if (typeof d !== "object") return String(d);
  if (a.action === "SCREEN") return `${d.documents ? d.documents.length : 0} doc(s), risk ${d.risk_level} ${d.risk_score}, recommended ${d.recommended}, ${d.ms} ms`;
  if (a.action === "DECISION") return `${d.decision}${d.override ? " (override)" : ""}${d.note ? ": " + d.note : ""}`;
  if (a.action === "RULES_CHANGED") return "risk rules updated";
  return Object.entries(d).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ").slice(0, 120);
}
$("#aRefresh").onclick = loadAudit;
$("#aVerify").onclick = async () => { await api("/api/audit/verify"); await loadAudit(); };
LOADERS.audit = loadAudit;

/* ---------------- analytics */
async function loadAnalytics() {
  const a = await api("/api/analytics");
  const tot = Math.max(1, a.total); const colr = { LOW: "var(--green)", MEDIUM: "#B7791F", HIGH: "var(--red)" };
  const hb = (label, v, max, color) => `<div class="hb"><span>${esc(label)}</span><span class="b"><i style="width:${v / Math.max(1, max) * 100}%;background:${color}"></i></span><span style="text-align:right">${v}</span></div>`;
  const maxH = Math.max(1, ...a.per_hour.map(h => h.count));
  $("#anBody").innerHTML = `
  <div class="tiles">
    <div class="tile"><b>Cases screened</b><strong>${a.total}</strong></div>
    <div class="tile"><b>Average risk score</b><strong>${a.avg_score}</strong></div>
    <div class="tile"><b>Average time</b><strong>${secs(a.avg_ms)} s</strong></div>
    <div class="tile"><b>95th percentile time</b><strong>${secs(a.p95_ms)} s</strong></div>
    <div class="tile"><b>Officer overrides</b><strong>${a.overrides}<small class="muted" style="font-size:12px"> / ${a.decided}</small></strong></div>
  </div>
  <div class="grid2">
    <div class="panel"><div class="ph"><h3>Risk bands</h3></div><div class="pb">${["LOW", "MEDIUM", "HIGH"].map(k => hb(LEVELTXT[k], a.levels[k], tot, colr[k])).join("")}</div></div>
    <div class="panel"><div class="ph"><h3>Officer decisions</h3></div><div class="pb">${["clear", "secondary", "reject", "pending"].map(k => hb(DECTXT[k] || "Pending", a.decisions[k] || 0, tot, "var(--navy)")).join("")}</div></div>
    <div class="panel"><div class="ph"><h3>Most frequent findings</h3></div><div class="pb">${a.top_codes.map(c => hb(c.code, c.count, a.top_codes[0].count, "#46586B")).join("") || '<span class="muted">No data yet.</span>'}</div></div>
    <div class="panel"><div class="ph"><h3>Screenings per hour of day</h3></div><div class="pb">${a.per_hour.length ? `<div class="vbars">${a.per_hour.map(h => `<div style="height:${h.count / maxH * 100}%"><span>${h.count}</span></div>`).join("")}</div><div class="vlab">${a.per_hour.map(h => `<span>${h.hour}h</span>`).join("")}</div>` : '<span class="muted">No data yet.</span>'}</div></div>
  </div>
  <div class="hint" style="margin-top:6px">Operational statistics recorded for duty checkpoint. Override = officer decision differs from advisory recommendation.</div>
  ${S.user.role === "supervisor" ? '<div style="margin-top:10px"><button class="btn sm danger" id="btnReset">Clear local station cache</button></div>' : ""}`;
  const rb = $("#btnReset");
  if (rb) rb.onclick = async () => { if (confirm("Clear local station screening cache and re-initialize audit genesis?")) { await api("/api/demo/reset", { method: "POST" }); S.res = null; renderAll(); loadAnalytics(); refreshStatus(); } };
}
LOADERS.analytics = loadAnalytics;
