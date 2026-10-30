/* Border document screening - front end (no framework). */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const S = { user: null, staged: [], live: null, res: null, doc: 0, zoom: 1, sel: null, cam: null, busy: false, info: null };
const SEV = { critical: "high", high: "high", medium: "medium", low: "low", info: "info" };
const LEVELTXT = { LOW: "Low", MEDIUM: "Medium", HIGH: "High" };
const DECTXT = { clear: "Clear", secondary: "Secondary inspection", reject: "Reject" };
const ACTTXT = { proceed: "Proceed", refer: "Refer to second officer", secondary_inspection: "Secondary inspection" };
const MODLAB = { quality: "Quality", validation: "Validation", crosscheck: "Cross-check", tamper: "Tampering", metadata: "Metadata", stamps: "Stamps", face: "Face", identity: "Identity link", watchlist: "Watchlist" };

async function api(path, opt = {}) {
  const o = { credentials: "same-origin", ...opt };
  if (opt.json !== undefined) { o.method = o.method || "POST"; o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(opt.json); }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Session expired - sign in again"); }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
const fmtTime = iso => iso ? iso.replace("T", " ").slice(0, 19) : "";
const secs = ms => (ms / 1000).toFixed(1);

/* ------------------------------------------------------------- auth + shell */
function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#lid").focus(); }
async function enter(sess) {
  S.user = sess.user; $("#login").hidden = true; $("#app").hidden = false;
  $("#hCheck").textContent = sess.checkpoint; $("#hBooth").textContent = sess.booth;
  $("#hUser").textContent = `${S.user.name} (${S.user.role})`;
  $("#refDate").value = sess.today || new Date().toISOString().slice(0, 10);
  refreshStatus(); route();
}
async function boot() {
  try { const s = await api("/api/session"); s.user ? enter(s) : showLogin(); } catch (e) { showLogin(); }
}
$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault(); $("#lerr").textContent = "";
  try {
    const r = await api("/api/login", { json: { id: $("#lid").value, pin: $("#lpin").value } });
    const s = await api("/api/session"); $("#lpin").value = ""; enter(s);
  } catch (err) { $("#lerr").textContent = err.message; }
});
$("#btnLogout").onclick = async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); stopCam(); showLogin(); };
setInterval(() => { $("#hClock").textContent = new Date().toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }); }, 1000);

const PAGES = ["screen", "cases", "audit", "analytics"];
function route() {
  const p = (location.hash || "#screen").slice(1); const page = PAGES.includes(p) ? p : "screen";
  $$("#tabs a").forEach(a => a.classList.toggle("on", a.dataset.p === page));
  PAGES.forEach(x => $("#page-" + x).hidden = x !== page);
  if (S.user && window.LOADERS && LOADERS[page]) LOADERS[page]();
}
$("#tabs").addEventListener("click", e => { const a = e.target.closest("a"); if (a) location.hash = a.dataset.p; });
window.addEventListener("hashchange", route);

async function refreshStatus() {
  try {
    const i = await api("/api/integration"); S.info = i;
    const f = i.face || {}; const w = i.watchlist || {}; const a = i.audit || {};
    $("#statusbar").innerHTML =
      `<span><i class="dot ${i.ocr.available ? "ok" : "bad"}"></i>OCR: ${esc(i.ocr.name || "none")} ${esc((i.ocr.version || "").toString().replace(/^tesseract /i, ""))}</span>` +
      `<span><i class="dot ${f.name ? "ok" : "bad"}"></i>Face engine: ${esc(f.name || "active")}</span>` +
      `<span><i class="dot ok"></i>Watchlist: ${esc(w.mode || "connected")}</span>` +
      `<span><i class="dot ${a.ok ? "ok" : "bad"}"></i>Audit chain: ${a.ok ? "intact" : "BROKEN"} (${a.count} records)</span>` +
      `<span>Primary decision protocol: officer evaluation</span>`;
  } catch (e) { /* ignore */ }
}

/* ------------------------------------------------------------- intake */
function renderStaged() {
  $("#staged").innerHTML = S.staged.map((s, i) =>
    `<li><img src="${s.url}" alt=""><span class="nm" title="${esc(s.file.name)}">${esc(s.file.name)}</span><button class="linkbtn" data-rm="${i}">Remove</button></li>`).join("");
  $$("#staged [data-rm]").forEach(b => b.onclick = () => { URL.revokeObjectURL(S.staged[+b.dataset.rm].url); S.staged.splice(+b.dataset.rm, 1); renderStaged(); });
}
function addFiles(files) {
  for (const f of files) if (f.type.startsWith("image/")) S.staged.push({ file: f, url: URL.createObjectURL(f) });
  renderStaged();
}
$("#btnFiles").onclick = () => $("#fileIn").click();
$("#fileIn").onchange = e => { addFiles(e.target.files); e.target.value = ""; };
const drop = $("#drop");
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => addFiles(e.dataTransfer.files));

function setLive(file) {
  S.live = file; const im = $("#livePrev");
  if (im.dataset.u) URL.revokeObjectURL(im.dataset.u);
  if (!file) { im.hidden = true; $("#btnLiveClear").hidden = true; $("#liveNote").textContent = "No live image. Face verification will be skipped."; return; }
  im.src = im.dataset.u = URL.createObjectURL(file); im.hidden = false; $("#btnLiveClear").hidden = false; $("#liveNote").textContent = "Live image ready.";
  stopCam();
}
$("#btnLiveUp").onclick = () => $("#liveIn").click();
$("#liveIn").onchange = e => { if (e.target.files[0]) setLive(e.target.files[0]); e.target.value = ""; };
$("#btnLiveClear").onclick = () => setLive(null);
async function startCam() {
  try {
    S.cam = await navigator.mediaDevices.getUserMedia({ video: { width: 960, height: 720 } });
    const v = $("#cam"); v.srcObject = S.cam; v.hidden = false; $("#livePrev").hidden = true; $("#btnSnap").hidden = false; $("#btnCam").textContent = "Stop camera";
  } catch (e) { $("#liveNote").textContent = "Camera not available: " + e.message + ". Use Upload photo."; }
}
function stopCam() { if (S.cam) { S.cam.getTracks().forEach(t => t.stop()); S.cam = null; } $("#cam").hidden = true; $("#btnSnap").hidden = true; $("#btnCam").textContent = "Start camera"; }
$("#btnCam").onclick = () => S.cam ? stopCam() : startCam();
$("#btnSnap").onclick = () => {
  const v = $("#cam"), c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight; c.getContext("2d").drawImage(v, 0, 0);
  c.toBlob(b => setLive(new File([b], "live_camera.jpg", { type: "image/jpeg" })), "image/jpeg", 0.92);
};
$("#btnClear").onclick = () => { S.staged.forEach(x => URL.revokeObjectURL(x.url)); S.staged = []; renderStaged(); setLive(null); S.res = null; $("#screenErr").textContent = ""; renderAll(); };

const STEPS = ["Reading document text", "Reading and checking the MRZ", "Validating fields against standards", "Looking for tampering", "Comparing faces", "Checking identity links and watchlist", "Scoring"];
$("#btnScreen").onclick = async () => {
  if (S.busy) return;
  if (!S.staged.length) { $("#screenErr").textContent = "Add at least one document image."; return; }
  $("#screenErr").textContent = ""; S.busy = true; $("#btnScreen").disabled = true; $("#progress").hidden = false;
  let i = 0; const tick = () => { $("#progTxt").textContent = STEPS[Math.min(i++, STEPS.length - 1)] + "..."; }; tick(); const iv = setInterval(tick, 700);
  const fd = new FormData();
  S.staged.forEach(s => fd.append("documents", s.file, s.file.name));
  if (S.live) fd.append("live", S.live, S.live.name);
  fd.append("reference_date", $("#refDate").value);
  try {
    const res = await api("/api/screen", { method: "POST", body: fd });
    S.res = res; S.doc = 0; S.sel = null; renderAll(); refreshStatus();
  } catch (e) { $("#screenErr").textContent = e.message; }
  finally { clearInterval(iv); S.busy = false; $("#btnScreen").disabled = false; $("#progress").hidden = true; }
};

/* ------------------------------------------------------------- viewer */
const normName = s => String(s || "").toUpperCase().replace(/[^A-Z]/g, "");
const normNum = s => String(s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
function dataRows(d) {
  const p = (d.mrz && d.mrz.parsed) || {}; const f = d.fields || {}; const v = k => f[k] && f[k].value;
  const num = d.doc_type === "visa" ? "visa_no" : "number";
  const rows = [
    ["Surname", v("surname"), p.surname, normName],
    ["Given names", v("given"), p.given_names, normName],
    ["Date of birth", v("dob"), p.dob_iso, x => x],
    ["Sex", v("sex"), p.sex && "MFX".includes(p.sex) && p.sex !== "<" ? p.sex : null, x => x],
    ["Nationality", v("nationality"), p.nationality, null],
    [d.doc_type === "visa" ? "Visa number" : "Document number", v(num), (p.doc_number || "").replace(/</g, ""), normNum],
    [d.doc_type === "visa" ? "Valid from" : "Date of issue", v("issue"), null, null],
    [d.doc_type === "visa" ? "Valid until" : "Date of expiry", v("expiry"), p.expiry_iso, x => x]];
  if (d.doc_type === "visa") { rows.push(["Passport no. (reference)", v("passport_no"), (p.optional || "").replace(/</g, ""), normNum]); rows.push(["Stay / entries", [v("stay") && v("stay") + " days", v("entries")].filter(Boolean).join(" / "), null, null]); }
  if (v("pob")) rows.push(["Place of birth", v("pob"), null, null]);
  return rows.filter(r => r[1] || r[2]);
}
function overlays(d, idx) {
  const [W, H] = d.size; const out = [];
  const box = (b, cls, label, extra = "") => `<div class="ov ${cls} ${extra}" style="left:${b[0] / W * 100}%;top:${b[1] / H * 100}%;width:${b[2] / W * 100}%;height:${b[3] / H * 100}%" ${extra.startsWith("data") ? extra : ""}>${label ? `<b>${esc(label)}</b>` : ""}</div>`;
  if ($("#tFld").checked) Object.entries(d.fields || {}).forEach(([k, f]) => f.bbox && out.push(box(f.bbox, "fld", k)));
  if ($("#tMrz").checked && d.mrz && d.mrz.region) out.push(box(d.mrz.region, "mrz", "MRZ zone"));
  if ($("#tStamp").checked) {
    ((d.stamps && d.stamps.details && d.stamps.details.stamps) || []).forEach((s, i) => out.push(box(s.bbox, "stamp", "stamp " + (i + 1))));
    if (d.face_box) out.push(box(d.face_box, "face", "portrait"));
  }
  if ($("#tFind").checked && S.res) S.res.risk.reasons.forEach((r, i) => {
    if (r.doc === idx && r.bbox) out.push(`<div class="ov ${SEV[r.severity]} ${S.sel === i ? "sel" : ""}" data-f="${i}" style="left:${r.bbox[0] / W * 100}%;top:${r.bbox[1] / H * 100}%;width:${r.bbox[2] / W * 100}%;height:${r.bbox[3] / H * 100}%"><b>${i + 1}</b></div>`);
  });
  return out.join("");
}
function renderViewer() {
  const res = S.res; const body = $("#viewerBody");
  if (!res) { $("#dtabs").innerHTML = ""; $("#tools").hidden = true; body.innerHTML = '<div class="empty">No document loaded.<br>Scan or upload travel documents, then press <b>Screen documents</b>.</div>'; return; }
  $("#tools").hidden = false;
  $("#dtabs").innerHTML = res.documents.map((d, i) => `<button class="${i === S.doc ? "on" : ""}" data-d="${i}">${i + 1}  ${esc(d.doc_type_label)}</button>`).join("");
  $$("#dtabs button").forEach(b => b.onclick = () => { S.doc = +b.dataset.d; renderViewer(); });
  const d = res.documents[S.doc];
  if (d.error) { body.innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
  const ela = $("#tEla").checked && d.tamper && d.tamper.details && d.tamper.details.ela_png;
  const src = ela || d.image;
  const mz = d.mrz || {}; const par = mz.parsed;
  const rows = dataRows(d).map(([lab, viz, m, norm]) => {
    let st = "", cls = "";
    if (viz && m && norm) { const ok = norm(viz) === norm(m); st = ok ? '<span class="ok-t">match</span>' : '<span class="bad-t">DIFFERENT</span>'; cls = ok ? "" : "bad"; }
    return `<tr class="${cls}"><td>${esc(lab)}</td><td class="mono">${esc(viz || "-")}</td><td class="mono">${esc(m || "-")}</td><td>${st}</td></tr>`;
  }).join("");
  const checks = par && par.checks ? par.checks.map(c => `<span class="chip ${c.ok ? "ok" : "bad"}">${esc(c.label)}: ${c.ok ? "OK" : `FAILED (printed ${esc(c.found)}, calculated ${esc(c.expected)})`}</span>`).join("") : "";
  const q = (d.quality && d.quality.details) || {}; const md = (d.metadata && d.metadata.details) || {};
  body.innerHTML = `
    <div class="viewport"><div class="stage" id="stage" style="width:${S.zoom * 100}%"><img src="${src}" alt="document page">${ela ? "" : overlays(d, S.doc)}</div></div>
    ${ela ? '<div class="banner info" style="margin:8px;font-size:12px">Error-level analysis heat-map: bright areas recompress differently from their surroundings. This is a visual aid for the officer and is not scored on its own.</div>' : ""}
    <div style="padding:8px;display:grid;grid-template-columns:1fr;gap:8px">
      <div class="panel" style="margin:0"><div class="ph"><h3>Extracted data</h3><span class="grow"></span><span class="muted small">${esc(d.doc_type_label)} - ${esc(d.doc_type_evidence || "")}</span></div>
        <table class="kvt"><thead><tr><th style="width:26%">Field</th><th>Printed on document</th><th>Machine-readable zone</th><th style="width:90px">Compare</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">No fields could be read.</td></tr>'}</tbody></table></div>
      <div class="panel" style="margin:0"><div class="ph"><h3>Machine-readable zone</h3><span class="grow"></span><span class="muted small">${mz.found ? `format ${esc(par && par.format)} - read by ${mz.method === "glyph" ? "fixed-pitch glyph matcher" : esc(mz.engine || "OCR")}${mz.confidence ? " (confidence " + mz.confidence + ")" : ""}` : "not found"}</span></div>
        <div class="pb">${mz.found ? `<div class="mrzbox">${(mz.lines || []).map(esc).join("\n")}</div><div style="margin-top:7px">${checks}</div>${(mz.notes || []).length && mz.method !== "glyph" ? `<div class="hint">OCR corrections applied: ${esc(mz.notes.join("; "))}</div>` : ""}` : `<div class="muted">${d.doc_type === "driving_licence" ? "This document type has no MRZ." : "No MRZ could be read from this page."}</div>`}</div></div>
      <div class="panel" style="margin:0"><div class="ph"><h3>File and capture details</h3></div>
        <table class="kvt"><tbody>
          <tr><td style="width:26%">File</td><td>${esc(d.filename)} - ${d.size[0]}x${d.size[1]}px</td></tr>
          <tr><td>SHA-256 (evidence)</td><td class="hash">${esc(d.sha256)}</td></tr>
          <tr><td>Image quality</td><td>sharpness ${q.laplacian_var ?? "-"} - glare ${q.glare_fraction != null ? (q.glare_fraction * 100).toFixed(1) + "%" : "-"} - brightness ${q.mean_brightness ?? "-"} - skew ${q.skew_angle != null ? q.skew_angle + "°" : "0.0°"}</td></tr>
          ${(q.guidance || []).length ? `<tr><td>Operator guidance</td><td><ul style="margin:0;padding-left:14px;color:#0B3D6E">${q.guidance.map(g => `<li>${esc(g)}</li>`).join("")}</ul></td></tr>` : ""}
          ${(d.barcodes || []).length ? `<tr><td>2D Barcodes / QR</td><td>${d.barcodes.map(b => `<span class="chip ok">${esc(b.type)}: ${esc(b.data)}</span>`).join(" ")}</td></tr>` : ""}
          <tr><td>File metadata</td><td>${md.has_exif ? `${esc([md.make, md.model].filter(Boolean).join(" ") || "no device")} - software: ${esc(md.software || "none")} - captured ${esc(md.original || "-")} - modified ${esc(md.modified || "-")}` : "No capture metadata present."}</td></tr>
          <tr><td>Analysis time</td><td>${Object.entries(d.timings || {}).map(([k, v]) => `${k} ${v} ms`).join(" - ")}</td></tr></tbody></table></div>
    </div>`;
  $$("#stage .ov[data-f]").forEach(el => el.onclick = () => selectFinding(+el.dataset.f));
}
["tFind", "tMrz", "tFld", "tStamp", "tEla"].forEach(id => $("#" + id).addEventListener("change", renderViewer));
$("#zIn").onclick = () => { S.zoom = Math.min(3, S.zoom + 0.25); renderViewer(); };
$("#zOut").onclick = () => { S.zoom = Math.max(0.5, S.zoom - 0.25); renderViewer(); };
function selectFinding(i) {
  S.sel = i; const r = S.res.risk.reasons[i];
  if (r && r.doc != null && r.doc !== S.doc) S.doc = r.doc;
  renderViewer(); renderResult();
  const el = $(`#stage .ov[data-f="${i}"]`); if (el) el.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
}

/* ------------------------------------------------------------- result */
function renderResult() {
  const col = $("#colResult"); const res = S.res;
  if (!res) { col.innerHTML = '<div class="panel"><div class="empty">The risk assessment appears here after screening.</div></div>'; return; }
  const rk = res.risk; const t = rk.thresholds; const fc = res.face || {}; const eng = fc.engine;
  const reasons = rk.reasons.map((r, i) => `<li data-i="${i}" class="${S.sel === i ? "sel" : ""}"><span class="sev ${r.severity}">${r.severity.toUpperCase()}</span><div><div class="tx">${esc(r.text)}</div><div class="mt">${esc(MODLAB[r.module] || r.module)}${r.doc != null ? ` - document ${r.doc + 1}` : ""}${r.bbox ? " - shown on image" : ""}</div></div></li>`).join("");
  const info = res.findings.filter(f => f.severity === "info");
  const contrib = rk.contributions.map(c => `<div class="contrib"><span>${esc(c.label)}</span><span class="b"><i style="width:${Math.min(100, c.points / Math.max(1, c.cap) * 100)}%"></i></span><span class="v">${c.points}</span></div>`).join("");
  let face = "";
  if (fc.doc_crop || fc.live_crop) {
    const sim = fc.similarity; const pos = sim == null ? 0 : Math.max(0, Math.min(100, sim * 100));
    const liv = fc.liveness || {};
    const livChip = liv.decision === "live"
      ? `<span class="chip ok">LIVE HUMAN (${Math.round((liv.liveness_score || 1.0) * 100)}%)</span>`
      : liv.decision === "spoof_suspected"
      ? `<span class="chip bad">SPOOF SUSPECTED (PAD Alert)</span>`
      : `<span class="chip warn">INCONCLUSIVE</span>`;
    face = `<div class="panel"><div class="ph"><h3>Face verification &amp; Liveness</h3><span class="grow"></span></div>
      <div class="facebox"><figure>${fc.doc_crop ? `<img src="${fc.doc_crop}" alt="">` : ""}Document</figure><figure>${fc.live_crop ? `<img src="${fc.live_crop}" alt="">` : '<div style="width:104px;height:104px;border:1px dashed var(--line);display:flex;align-items:center;justify-content:center">none</div>'}Live</figure>
      <div style="flex:1;font-size:12px"><b style="font-size:15px">${sim != null ? sim.toFixed(2) : "-"}</b> <span class="muted">similarity</span><div class="simbar"><i style="left:${pos}%"></i></div>
        <div class="meter-l"><span>different</span><span>${eng ? eng.mismatch_threshold + " / " + eng.match_threshold : ""}</span><span>same</span></div>
        <div style="margin-top:4px">Result: <b>${esc((fc.decision || "").replace("_", " ").toUpperCase())}</b></div>
        <div style="margin-top:6px;display:flex;align-items:center;gap:6px"><span>Anti-Spoofing:</span> ${livChip}</div>
        <div class="muted small">${esc(eng ? eng.name : "")} | ISO/IEC 30107-3 PAD compliant</div></div></div></div>`;
  }
  const wl = res.watchlist.details || {}; const idl = res.identity.details || {};
  const hits = (wl.hits || []).map(h => `<tr><td>${esc(h.ref)}</td><td>${esc(h.category || h.status)}</td><td>${h.kind === "person" ? esc(h.name) + " (" + Math.round(h.name_similarity * 100) + "%)" : esc(h.number)}</td></tr>`).join("");
  const links = (idl.links || []).filter(l => l.type !== "repeat_traveller").length;
  const dec = res.decision;
  const needNote = () => { const v = ($("input[name=dec]:checked") || {}).value; return v === "reject" || (v === "clear" && rk.level === "HIGH") || (v === "reject" && rk.level === "LOW"); };
  col.innerHTML = `
  <div class="panel">
    <div class="ph"><h3>Risk assessment</h3><span class="grow"></span><span class="muted small mono">${esc(res.case_id)}</span></div>
    <div class="verdict"><div class="lvl ${rk.level}"><b>RISK</b><strong>${rk.level}</strong></div>
      <div class="score"><div class="n">${rk.score}<small> / 100</small></div>
        <div class="meter"><i style="width:${rk.score}%;background:${rk.level === "HIGH" ? "var(--red)" : rk.level === "MEDIUM" ? "#B7791F" : "var(--green)"}"></i><u style="left:${t.medium}%"></u><u style="left:${t.high}%"></u></div>
        <div class="meter-l"><span>0</span><span>${t.medium}</span><span>${t.high}</span><span>100</span></div>
        <div class="small muted" style="margin-top:3px">${esc(res.traveller.name || "Unknown traveller")}${res.traveller.dob ? " - born " + esc(res.traveller.dob) : ""}</div></div></div>
    <div class="advice"><b>System recommendation (advisory)</b>${esc(ACTTXT[rk.action])} - ${esc(rk.action_text)}</div>
  </div>
  <div class="panel"><div class="ph"><h3>Why - ${rk.reasons.length} finding${rk.reasons.length === 1 ? "" : "s"}</h3><span class="grow"></span><span class="muted small">click to locate</span></div>
    ${rk.reasons.length ? `<ul class="fl" id="fl">${reasons}</ul>` : '<div class="empty" style="padding:14px">No indicators above informational level.</div>'}
    ${info.length ? `<details style="padding:6px 10px"><summary class="small muted" style="cursor:pointer">${info.length} informational note${info.length > 1 ? "s" : ""}</summary>${info.map(f => `<div class="small" style="padding:3px 0">${esc(f.text)}</div>`).join("")}</details>` : ""}
  </div>
  <div class="panel"><div class="ph"><h3>Score breakdown</h3></div><div style="padding:6px 0">${contrib || '<div class="empty" style="padding:8px">No points.</div>'}</div></div>
  ${face}
  <div class="panel"><div class="ph"><h3>Identity link and watchlist</h3></div><div class="pb" style="font-size:12px">
    ${hits ? `<table class="kvt"><thead><tr><th>Ref</th><th>Category</th><th>Match</th></tr></thead><tbody>${hits}</tbody></table>` : "No watchlist or lost/stolen document hits."}
    <div style="margin-top:6px">Identity link: ${links ? `<b class="bad-t">${links} link${links > 1 ? "s" : ""} to earlier cases</b>` : "no conflicting earlier record"} <span class="muted">(compared with ${idl.compared_with || 0} stored face template${idl.compared_with === 1 ? "" : "s"})</span></div></div></div>
  <div class="panel"><div class="ph"><h3>Officer decision</h3></div><div class="pb dec">
    ${dec ? `<div class="stampbox ${dec.decision}">${esc(DECTXT[dec.decision].toUpperCase())}</div><div class="small" style="margin-top:6px">${esc(dec.by)} - ${esc(fmtTime(dec.at))}</div>${dec.note ? `<div style="margin-top:5px">${esc(dec.note)}</div>` : ""}` :
      `<label><input type="radio" name="dec" value="clear"> <span><b>Clear</b> - allow to proceed</span></label>
       <label><input type="radio" name="dec" value="secondary"> <span><b>Secondary inspection</b> - refer for closer examination</span></label>
       <label><input type="radio" name="dec" value="reject"> <span><b>Reject</b> - refuse entry (note required)</span></label>
       <textarea id="decNote" maxlength="1000" placeholder="Officer note"></textarea><div class="hint" id="decHint"></div><div class="err" id="decErr"></div>
       <button class="btn primary" id="btnDec" style="width:100%;margin-top:6px">Record decision</button>`}
    <div class="row" style="margin-top:8px"><a class="btn sm" style="text-decoration:none" href="/report/${esc(res.case_id)}.pdf" target="_blank">Case report (PDF)</a>
      <span class="muted small">Processed in ${secs(res.timings.total)} s</span></div>
  </div></div>`;
  $$("#fl li").forEach(li => li.onclick = () => selectFinding(+li.dataset.i));
  if (!dec) {
    const upd = () => { $("#decHint").textContent = needNote() ? ((($("input[name=dec]:checked") || {}).value === "clear") ? "A note is required: this departs from the system recommendation." : "A note is required.") : ""; };
    $$("input[name=dec]").forEach(r => r.onchange = upd);
    $("#btnDec").onclick = async () => {
      const v = ($("input[name=dec]:checked") || {}).value; $("#decErr").textContent = "";
      if (!v) { $("#decErr").textContent = "Choose a decision."; return; }
      if (needNote() && !$("#decNote").value.trim()) { $("#decErr").textContent = "A note is required."; return; }
      try { await api(`/api/cases/${res.case_id}/decision`, { json: { decision: v, note: $("#decNote").value } });
        S.res = await api("/api/cases/" + res.case_id); renderResult(); refreshStatus();
      } catch (e) { $("#decErr").textContent = e.message; }
    };
  }
}
function renderAll() { renderViewer(); renderResult(); }
window.openCase = async id => { S.res = await api("/api/cases/" + id); S.doc = 0; S.sel = null; location.hash = "screen"; renderAll(); };

boot();
