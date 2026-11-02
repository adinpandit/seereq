"use strict";

/* SeeReq frontend. All model output is rendered as escaped text — never as raw HTML.
   The canonical requirement model and baseline are owned by the server; the browser stores and sends them back. */

const DOCS = {
  bpd: { short: "BPD", name: "Business Process Design" },
  fsd: { short: "FSD", name: "Functional Specification Document" },
  csd: { short: "CSD", name: "Configuration Specification Document" },
};
const DOC_ORDER = ["bpd", "fsd", "csd"];
const REQ_ID = "BR-001";
const STORAGE_KEY = "seereq-session-v2";
const MAX_RECORD_SECONDS = 30;

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function freshState() {
  return {
    language: "hinglish",
    requirement: "",
    model: null,             // canonical requirement model (from /api/analyze)
    version: 0,              // bumps whenever the model changes
    plan: null,              // requirement baseline (from /api/baseline)
    planVersion: -1,         // model version the baseline was built from
    draftAnswers: {},        // question ID -> text for pending questions
    docs: {},                // type -> {markdown, version, fingerprint, revision, issues}
    report: null,            // traceability check result
    activeTab: "bpd",
    demo: false,
  };
}

let state = freshState();
let live = false;
let busy = null;             // label of the running operation, or null
let progress = "";           // what a long operation is doing right now
const docBusy = {};          // type -> true while generating
const docErrors = {};        // type -> message
let captureError = "", answerError = "", docsError = "";

/* ---------------------------------------------------------------- persistence */

function save() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_) { /* storage unavailable */ }
}
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) state = Object.assign(freshState(), JSON.parse(raw));
  } catch (_) { /* ignore corrupt or unavailable storage */ }
}

/* ---------------------------------------------------------------- api */

async function api(path, body, timeoutMs = 240000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const isAudio = body instanceof Blob;
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": isAudio ? (body.type || "audio/webm") : "application/json" },
      body: isAudio ? body : JSON.stringify(body),
      signal: ctrl.signal,
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON error */ }
    if (!res.ok) throw new Error(data.error || `Request failed (HTTP ${res.status}).`);
    return data;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("The request timed out. Please try again.");
    if (e instanceof TypeError) throw new Error("Could not reach the SeeReq server. Is it still running?");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/* ---------------------------------------------------------------- status */

const docsCurrent = () => DOC_ORDER.every((t) => state.docs[t] && state.docs[t].version === state.version);

function overallStatus() {
  if (!state.model) return "initial";
  if (docsCurrent()) return "generated";
  return state.model.ready ? "ready" : "clarification";
}

function renderStepper() {
  const order = ["initial", "clarification", "ready", "generated"];
  const status = overallStatus();
  const idx = order.indexOf(status);
  const asked = state.model && state.model.clarifications.length > 0;
  document.querySelectorAll("#stepper li").forEach((li) => {
    const i = order.indexOf(li.dataset.step);
    li.classList.toggle("current", i === idx);
    li.classList.toggle("done", i < idx && !(li.dataset.step === "clarification" && !asked));
  });
}

/* ---------------------------------------------------------------- toast & banner */

let toastTimer;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2400);
}

function renderBanner() {
  const el = $("#banner");
  if (state.demo) {
    el.className = "banner demo";
    el.innerHTML = `<div><strong>Demo data — not a live AI analysis.</strong> You are viewing a predefined sample for the demonstration scenario. It is not based on anything you typed or said.</div>
      <button class="btn btn-small" id="exit-demo" type="button">Exit demo</button>`;
    el.hidden = false;
    $("#exit-demo").onclick = () => { state = freshState(); save(); renderAll(); };
    return;
  }
  if (!live) {
    el.className = "banner";
    el.innerHTML = `<div><strong>Sarvam AI is not configured.</strong> Add <code>SARVAM_API_KEY</code> to <code>.env</code> and restart the server to enable live transcription, analysis and document generation.</div>
      <button class="btn btn-small" id="load-demo" type="button" ${window.SEEREQ_DEMO ? "" : "disabled"}>View demo sample</button>`;
    el.hidden = false;
    $("#load-demo").onclick = loadDemo;
    return;
  }
  el.hidden = true;
}

function loadDemo() {
  const d = window.SEEREQ_DEMO;
  if (!d) return;
  state = Object.assign(freshState(), JSON.parse(JSON.stringify(d.state)), { demo: true });
  save();
  renderAll();
}

/* ---------------------------------------------------------------- capture panel */

const requirementChanged = () => !!state.model && state.requirement.trim() !== state.model.original_requirement;

function renderCapture() {
  const req = $("#requirement");
  if (req.value !== state.requirement) req.value = state.requirement;
  $("#req-count").textContent = `${state.requirement.length} / 4000`;
  const note = $("#req-note");
  const changed = requirementChanged();
  note.textContent = changed ? "Edited since last analysis — re-analyze to update." : "";
  note.className = changed ? "changed" : "muted";

  document.querySelectorAll("#lang button").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.lang === state.language));
    b.disabled = !!busy || state.demo;
  });

  req.disabled = state.demo;
  document.querySelectorAll(".chip").forEach((c) => (c.disabled = state.demo));
  const btn = $("#analyze-btn");
  btn.disabled = !!busy || state.demo || !live;
  btn.innerHTML = busy === "analyze"
    ? `<span class="spinner"></span> ${esc(progress || "Analyzing your requirement…")}`
    : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h12M12 6l6 6-6 6"/></svg> ${state.model ? (changed ? "Re-analyze requirement" : "Re-run analysis") : "Analyze requirement"}`;
  showError("#capture-error", captureError);
  renderVoice();
}

function showError(id, msg) {
  const el = $(id);
  if (!el) return;
  el.textContent = msg || "";
  el.hidden = !msg;
}

/* Analyze: answers === undefined re-runs the analysis; an object submits the current round. */
async function analyze(answers) {
  if (busy) return;
  const requirement = state.requirement.trim();
  if (!requirement) { captureError = "Please describe a requirement first — speak or type it above."; renderCapture(); $("#requirement").focus(); return; }
  captureError = answerError = "";
  busy = "analyze";
  progress = answers ? "Updating the requirement model with your answers…" : "Analyzing your requirement…";
  renderAll();
  try {
    const body = { requirement, language: state.language, model: state.model };
    if (answers) body.answers = answers;
    const data = await api("/api/analyze", body);
    state.model = data.model;
    state.draftAnswers = {};
    state.version += 1;
    save();
  } catch (e) {
    if (answers) answerError = `${e.message} Your answers are kept — try again.`;
    else captureError = `${e.message} Your requirement is kept — try again.`;
  } finally {
    busy = null;
    progress = "";
    renderAll();
  }
}

/* ---------------------------------------------------------------- voice */

const recorder = { media: null, chunks: [], stream: null, target: null, timer: null, started: 0, lastInsert: null, state: "idle", error: "" };
const canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

function pickMime() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return types.find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
}

async function startRecording(target) {
  if (recorder.state !== "idle" || busy) return;
  recorder.error = "";
  recorder.target = target;
  try {
    recorder.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    recorder.error = e.name === "NotAllowedError" || e.name === "SecurityError"
      ? "Microphone access was blocked. Allow microphone access for this site in your browser settings, or type your requirement instead."
      : e.name === "NotFoundError" ? "No microphone was found. Connect a microphone or type your requirement instead."
      : "Could not start the microphone. You can type your requirement instead.";
    renderAll();
    return;
  }
  const mime = pickMime();
  recorder.media = new MediaRecorder(recorder.stream, mime ? { mimeType: mime } : undefined);
  recorder.chunks = [];
  recorder.media.ondataavailable = (e) => e.data.size && recorder.chunks.push(e.data);
  recorder.media.onstop = onRecordingStopped;
  recorder.media.start();
  recorder.state = "recording";
  recorder.started = Date.now();
  recorder.timer = setInterval(() => {
    if ((Date.now() - recorder.started) / 1000 >= MAX_RECORD_SECONDS) stopRecording();
    renderVoice();
  }, 250);
  renderAll();
}

function stopRecording() {
  if (recorder.state !== "recording") return;
  clearInterval(recorder.timer);
  recorder.media.stop();
  recorder.stream.getTracks().forEach((t) => t.stop());
  recorder.state = "transcribing";
  renderAll();
}

async function onRecordingStopped() {
  const blob = new Blob(recorder.chunks, { type: (recorder.media.mimeType || "audio/webm").split(";")[0] });
  const target = recorder.target;
  try {
    if (blob.size < 1000) throw new Error("The recording was too short. Please try again.");
    const data = await api(`/api/transcribe?language=${encodeURIComponent(state.language)}`, blob, 90000);
    insertTranscript(target, data.transcript);
  } catch (e) {
    recorder.error = `Transcription failed: ${e.message} You can type instead.`;
  } finally {
    recorder.state = "idle";
    renderAll();
  }
}

function insertTranscript(target, text) {
  if (target === "requirement") {
    const before = state.requirement.trim();
    state.requirement = (before ? before + " " : "") + text;
    recorder.lastInsert = { target, text, before: state.requirement.slice(0, before.length) };
    save();
  } else {
    const before = (state.draftAnswers[target] || "").trim();
    state.draftAnswers[target] = (before ? before + " " : "") + text;
    recorder.lastInsert = { target, text };
    save();
  }
}

function retryRecording() {
  // Remove the last transcription from the requirement and record again.
  const last = recorder.lastInsert;
  if (last && last.target === "requirement" && state.requirement.endsWith(last.text)) {
    state.requirement = state.requirement.slice(0, -last.text.length).trim();
    save();
  }
  recorder.lastInsert = null;
  startRecording("requirement");
}

function renderVoice() {
  const box = $("#voice"), mic = $("#mic"), title = $("#voice-title"), sub = $("#voice-sub");
  const stop = $("#stop-btn"), retry = $("#retry-btn");
  const onMain = recorder.target === "requirement";
  box.classList.toggle("recording", recorder.state === "recording" && onMain);
  box.classList.toggle("unavailable", !canRecord || !live || state.demo);
  stop.hidden = !(recorder.state === "recording" && onMain);
  retry.hidden = !(recorder.state === "idle" && recorder.lastInsert && recorder.lastInsert.target === "requirement");
  mic.disabled = !canRecord || !live || state.demo || !!busy || recorder.state !== "idle";
  mic.setAttribute("aria-label", recorder.state === "recording" ? "Recording" : "Start recording");
  const langName = { hinglish: "Hinglish", hindi: "Hindi", english: "English" }[state.language];

  if (!canRecord) {
    title.textContent = "Voice input not supported in this browser";
    sub.textContent = "Your browser cannot record audio. Type your requirement below instead.";
  } else if (!live || state.demo) {
    title.textContent = "Voice input unavailable";
    sub.textContent = state.demo ? "Voice is disabled while viewing demo data." : "Transcription needs SARVAM_API_KEY on the server. Type your requirement below.";
  } else if (recorder.state === "recording" && onMain) {
    const s = Math.floor((Date.now() - recorder.started) / 1000);
    title.innerHTML = `Listening (${esc(langName)})…`;
    sub.textContent = `0:${String(s).padStart(2, "0")} / 0:${MAX_RECORD_SECONDS} — tap Stop when you're done`;
  } else if (recorder.state === "transcribing" && onMain) {
    title.innerHTML = `<span class="spinner"></span> Transcribing audio…`;
    sub.textContent = "Sarvam AI is converting your speech to text.";
  } else if (recorder.error && onMain) {
    title.textContent = "Recording problem";
    sub.textContent = recorder.error;
  } else if (recorder.lastInsert && recorder.lastInsert.target === "requirement") {
    title.textContent = "Transcribed — review and edit below";
    sub.textContent = "Tap the mic to add more, or Retry to discard the last recording.";
  } else {
    title.textContent = `Tap to speak (${langName})`;
    sub.textContent = `Up to ${MAX_RECORD_SECONDS} seconds per recording · transcribed by Sarvam AI`;
  }
}

/* ---------------------------------------------------------------- analysis panel */

const STATUS_CHIP = {
  confirmed: ["CONFIRMED", "lbl-confirmed"], proposed: ["PROPOSED", "lbl-proposed"],
  assumption: ["ASSUMPTION", "lbl-assumption"], open: ["OPEN", "lbl-open"],
};
const chip = (status) => { const [t, c] = STATUS_CHIP[status] || [status.toUpperCase(), "lbl-assumption"]; return `<span class="lbl ${c}">${t}</span>`; };
const sourceText = (d) => d.source === "original_requirement" ? "Original requirement" : d.source === "clarification" ? `Answer ${d.source_ref}` : "AI inference";

function renderAnalysis() {
  const body = $("#analysis-body");
  const m = state.model;
  if (busy === "analyze" && !m) {
    body.innerHTML = `<div class="loading"><span class="spinner"></span>Analyzing your requirement and preparing clarification questions…</div>`;
    return;
  }
  if (!m) {
    body.innerHTML = `<div class="empty"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6M10 3v5l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"/></svg>
      <p>SeeReq analyzes your requirement like a functional consultant: business objective, process area, decisions and open items — then asks only the questions that matter.</p></div>`;
    return;
  }

  const status = overallStatus();
  const active = m.open_items.filter((o) => o.status === "open");
  const blocking = active.filter((o) => o.blocking && o.owner === "business");
  const statusText = {
    clarification: ["Clarification required", m.readiness_reason || "Answer the questions below. You can also generate drafts now — open items will be flagged."],
    ready: ["Ready for documentation", (m.readiness_reason || "Essential business decisions are captured.") + " This is a recommendation — review the decisions before generating."],
    generated: ["Documentation generated", "All three documents reflect the current requirement model. Review them before sharing."],
  }[status];

  let html = "";
  if (busy === "analyze") html += `<div class="loading" style="margin-bottom:14px"><span class="spinner"></span>${esc(progress)}</div>`;
  html += `<div class="original"><div class="k">Original requirement · ${REQ_ID}</div><div class="v">“${esc(m.original_requirement)}”</div>
    <div class="en"><span class="muted">English interpretation:</span> ${esc(m.interpretation_en)}</div></div>`;
  html += `<div class="status-card ${status}"><div><strong>${statusText[0]}</strong>${esc(statusText[1])}</div></div>`;
  html += `<div class="facts">
    <div class="fact wide"><div class="k">Business objective</div><div class="v">${esc(m.business_objective)}</div></div>
    <div class="fact"><div class="k">Process area <span class="tag-proposed-inline">PROPOSED</span></div><div class="v">${esc(m.process_area)}</div></div>
    <div class="fact"><div class="k">Expected system behavior</div><div class="v">${esc(m.expected_system_behavior || "Requires confirmation")}</div></div>
  </div>`;
  if (m.contradictions.length) html += `<div class="inline-error"><strong>Contradictions:</strong> ${m.contradictions.map(esc).join(" · ")}</div>`;

  // ---- pending questions
  const qs = m.pending_questions;
  const disabled = state.demo || !live || !!busy;
  if (qs.length) {
    html += `<div class="section-title">Clarification questions <span class="count">${qs.length}</span><span class="muted" style="font-weight:500;font-size:12.5px">Round ${m.round + 1}</span></div><div class="questions">`;
    for (const q of qs) {
      const rec = recorder.target === q.id;
      html += `<div class="question"><div class="q-top"><span class="q-num">${esc(q.id)}</span><span class="q-text">${esc(q.question)}</span>${q.topic ? `<span class="q-topic">${esc(q.topic)}</span>` : ""}</div>
        <div class="q-en">${q.question_en !== q.question ? esc(q.question_en) : ""}${q.addresses.length ? ` <span class="q-resolves">Resolves ${q.addresses.map(esc).join(", ")}</span>` : ""}</div>
        <div class="answer-row"><textarea rows="1" data-answer="${esc(q.id)}" maxlength="2000" placeholder="Your answer (Hindi, Hinglish or English) — leave blank to skip" ${disabled ? "disabled" : ""}>${esc(state.draftAnswers[q.id] || "")}</textarea>
        <button type="button" class="mic-small ${recorder.state === "recording" && rec ? "recording" : ""}" data-mic="${esc(q.id)}" aria-label="Answer by voice" title="Answer by voice" ${!canRecord || disabled || (recorder.state !== "idle" && !rec) ? "disabled" : ""}>
          ${recorder.state === "transcribing" && rec ? `<span class="spinner"></span>` : `<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>`}
        </button></div></div>`;
    }
    html += `</div>`;
    if (recorder.error && recorder.target !== "requirement") html += `<div class="inline-error">${esc(recorder.error)}</div>`;
    html += `<div id="answer-error" class="inline-error" ${answerError ? "" : "hidden"}>${esc(answerError)}</div>
      <div class="analysis-actions"><button class="btn btn-primary" id="submit-answers" type="button" ${disabled ? "disabled" : ""}>${busy === "analyze" ? `<span class="spinner"></span> Updating…` : "Submit answers &amp; update model"}</button></div>
      <div class="hint muted">Blank answers are recorded as skipped and never treated as confirmation. Technical SAP decisions are not asked here — they are open items for the consultant.</div>`;
  } else {
    html += `<div class="section-title">Clarification questions <span class="count">0</span></div><p class="muted" style="margin:0;font-size:14px">No further business questions. ${active.length ? "Remaining open items are listed below." : ""}</p>`;
  }

  // ---- decision register
  const live_ = m.decisions.filter((d) => d.status !== "superseded");
  html += `<div class="section-title">Decision register <span class="count">${live_.length}</span></div><div class="register">`;
  html += live_.map((d) => `<div class="reg-row"><span class="reg-id">${esc(d.id)}</span><span class="reg-text">${esc(d.description)}</span><span class="reg-meta"><span class="src">${esc(sourceText(d))}</span>${chip(d.status)}</span></div>`).join("") || `<p class="muted">No decisions yet.</p>`;
  html += `</div>`;
  const superseded = m.decisions.filter((d) => d.status === "superseded");
  if (superseded.length) html += `<details class="list"><summary>Superseded decisions <span class="count">${superseded.length}</span></summary><ul>${superseded.map((d) => `<li>${esc(d.id)} — ${esc(d.description)} (replaced by ${esc(d.superseded_by)})</li>`).join("")}</ul></details>`;

  // ---- open items
  html += `<div class="section-title">Open items <span class="count">${active.length}</span>${blocking.length ? `<span class="lbl lbl-unknown">${blocking.length} blocking</span>` : ""}</div><div class="register">`;
  html += active.map((o) => `<div class="reg-row"><span class="reg-id">${esc(o.id)}</span><span class="reg-text">${esc(o.description)}${o.why_it_matters ? `<span class="why">${esc(o.why_it_matters)}</span>` : ""}</span><span class="reg-meta"><span class="src">${o.owner === "consultant" ? "SAP consultant" : "Business"}</span>${o.blocking ? `<span class="lbl lbl-unknown">BLOCKING</span>` : chip("open")}</span></div>`).join("") || `<p class="muted">No open items.</p>`;
  html += `</div>`;
  const resolved = m.open_items.filter((o) => o.status === "resolved");
  if (resolved.length) html += `<details class="list"><summary>Resolved during clarification <span class="count">${resolved.length}</span></summary><ul>${resolved.map((o) => `<li>${esc(o.id)} — ${esc(o.description)} <span class="muted">(answer ${esc(o.resolved_by)})</span></li>`).join("")}</ul></details>`;
  if (m.notes.length) html += `<details class="list"><summary>Analysis notes <span class="count">${m.notes.length}</span></summary><ul>${m.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></details>`;

  // ---- conversation history
  if (m.clarifications.length) {
    const answered = m.clarifications.filter((c) => c.status === "answered").length;
    html += `<div class="history"><div class="section-title">Conversation history <span class="count">${answered} answered · ${m.clarifications.length - answered} skipped</span></div>`;
    html += m.clarifications.map((c) => `<div class="turn"><div class="tq">${esc(c.id)}. ${esc(c.question)}</div><div class="ta ${c.status === "answered" ? "" : "skipped"}">${c.status === "answered" ? "→ " + esc(c.answer) : "Skipped — remains open"}</div></div>`).join("");
    html += `</div>`;
  }
  body.innerHTML = html;

  body.querySelectorAll("textarea[data-answer]").forEach((ta) => {
    autosize(ta);
    ta.addEventListener("input", () => { state.draftAnswers[ta.dataset.answer] = ta.value; autosize(ta); save(); });
  });
  body.querySelectorAll("[data-mic]").forEach((b) => {
    b.addEventListener("click", () => (recorder.state === "recording" ? stopRecording() : startRecording(b.dataset.mic)));
  });
  const submit = $("#submit-answers");
  if (submit) submit.addEventListener("click", submitAnswers);
}

function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 220) + "px"; }

function submitAnswers() {
  if (busy) return;
  const answers = {};
  for (const q of state.model.pending_questions) answers[q.id] = (state.draftAnswers[q.id] || "").trim();
  if (!Object.values(answers).some(Boolean)) {
    answerError = "Answer at least one question — or generate the documents now with these questions recorded as open items.";
    renderAnalysis();
    return;
  }
  analyze(answers);
}

/* ---------------------------------------------------------------- documents */

const planCurrent = () => state.plan && state.planVersion === state.version;

async function ensureBaseline() {
  if (planCurrent()) return true;
  progress = "Building the requirement baseline…";
  renderAll();
  const data = await api("/api/baseline", { model: state.model });
  state.plan = data.plan;
  state.planVersion = state.version;
  save();
  return true;
}

async function runCheck() {
  progress = "Running traceability check…";
  renderAll();
  const docs = {};
  DOC_ORDER.forEach((t) => { if (state.docs[t]) docs[t] = state.docs[t].markdown; });
  const data = await api("/api/check", { model: state.model, plan: state.plan, docs });
  state.report = { ...data.report, version: state.version };
  save();
}

function guardRequirement() {
  if (requirementChanged()) {
    docsError = "The requirement was edited after the last analysis. Re-analyze it first so the documents match.";
    renderDocs();
    return false;
  }
  return true;
}

async function generateAll() {
  if (busy || !state.model || !guardRequirement()) return;
  docsError = "";
  busy = "generate";
  try {
    await ensureBaseline();
    for (const type of DOC_ORDER) {
      state.activeTab = type;
      if (!(await generateOne(type))) break;
    }
    await runCheck();
  } catch (e) {
    docsError = e.message + " Your requirement and answers are kept.";
  } finally {
    busy = null;
    progress = "";
    renderAll();
  }
}

async function generateOne(type) {
  docBusy[type] = true;
  delete docErrors[type];
  progress = `Generating ${DOCS[type].short}…`;
  renderAll();
  try {
    const revision = ((state.docs[type] && state.docs[type].revision) || 0) + 1;
    const data = await api("/api/generate", { doc_type: type, model: state.model, plan: state.plan, revision });
    state.docs[type] = { markdown: data.markdown, version: state.version, fingerprint: state.plan.fingerprint, revision, issues: data.issues };
    save();
    return true;
  } catch (e) {
    docErrors[type] = e.message;
    return false;
  } finally {
    delete docBusy[type];
    renderAll();
  }
}

async function regenerate() {
  if (busy || !state.model || !guardRequirement()) return;
  docsError = "";
  busy = "generate";
  try {
    const rebuilt = !planCurrent();
    await ensureBaseline();
    if (rebuilt) toast("Requirement baseline rebuilt — regenerate the other documents too.");
    await generateOne(state.activeTab);
    await runCheck();
  } catch (e) {
    docsError = e.message;
  } finally {
    busy = null;
    progress = "";
    renderAll();
  }
}

function fileName(type) {
  return `SeeReq_${type.toUpperCase()}_${REQ_ID}.md`.replace(/[^A-Za-z0-9_.-]/g, "_");
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast("Copied to clipboard");
}

function renderReport() {
  const el = $("#check-panel");
  const r = state.report;
  if (!r || !DOC_ORDER.some((t) => state.docs[t])) { el.hidden = true; return; }
  el.hidden = false;
  const stale = r.version !== state.version;
  const errors = r.issues.filter((i) => i.level === "error");
  const warnings = r.issues.filter((i) => i.level === "warning");
  const label = stale ? ["Outdated", "warn"] : r.status === "pass" ? ["Passed", "ok"] : r.status === "warn" ? ["Passed with warnings", "warn"] : ["Failed", "bad"];
  el.innerHTML = `<details ${r.status === "fail" ? "open" : ""}><summary>
      <span class="check-pill ${label[1]}">${label[0]}</span>
      <strong>Traceability check</strong>
      <span class="muted">${esc(r.checked.join(", "))} · baseline ${esc(r.fingerprint)} · ${r.open_items} open item(s) · ${errors.length} error(s), ${warnings.length} warning(s)</span>
    </summary>
    ${r.issues.length ? `<ul class="issues">${r.issues.map((i) => `<li class="${i.level}"><span class="lvl">${i.level === "error" ? "Error" : "Warning"}</span><span class="scope">${esc(i.doc)}</span>${esc(i.message)}</li>`).join("")}</ul>`
      : `<p class="muted" style="margin:8px 0 0">All documents use the same requirement, baseline and IDs; every confirmed decision is traced.</p>`}
  </details>`;
}

function renderDocs() {
  const anyDoc = DOC_ORDER.some((t) => state.docs[t]);
  const gen = $("#generate-btn");
  gen.disabled = !state.model || !!busy || state.demo || !live;
  gen.innerHTML = busy === "generate" ? `<span class="spinner"></span> ${esc(progress || "Working…")}`
    : anyDoc ? "Regenerate all documents"
    : overallStatus() === "clarification" ? "Generate drafts (open items flagged)" : "Generate BPD, FSD &amp; CSD";
  $("#download-all").disabled = !anyDoc;
  showError("#docs-error", docsError);
  renderReport();

  document.querySelectorAll("#tabs button").forEach((b) => {
    const t = b.dataset.tab, d = state.docs[t];
    b.setAttribute("aria-selected", String(t === state.activeTab));
    const tag = b.querySelector(".tab-state");
    if (docBusy[t]) { tag.className = "tab-state busy"; tag.textContent = "Generating"; }
    else if (docErrors[t]) { tag.className = "tab-state err"; tag.textContent = "Failed"; }
    else if (d && d.version !== state.version) { tag.className = "tab-state stale"; tag.textContent = "Outdated"; }
    else if (d) { tag.className = "tab-state ok"; tag.textContent = state.demo ? "Demo" : `Draft 0.${d.revision || 1}`; }
    else { tag.className = "tab-state"; tag.textContent = ""; }
  });

  const type = state.activeTab, doc = state.docs[type], view = $("#doc-view");
  $("#doc-toolbar").hidden = !doc;
  if (docErrors[type]) showError("#doc-error", `${DOCS[type].short} generation failed: ${docErrors[type]} Your requirement and answers are kept — try Regenerate.`);
  else showError("#doc-error", "");
  if (doc) {
    const issues = (doc.issues || []).length;
    $("#doc-meta").textContent = `${fileName(type)} · ${doc.markdown.length.toLocaleString()} characters${issues ? ` · ${issues} check note(s)` : ""}${state.demo ? " · demo sample" : ""}`;
    $("#regen-btn").disabled = !!busy || state.demo || !live;
    $("#regen-btn").innerHTML = docBusy[type] ? `<span class="spinner"></span> Regenerating…` : "Regenerate";
  }

  if (docBusy[type]) {
    view.innerHTML = `<div class="loading"><span class="spinner"></span>Generating ${DOCS[type].short} — ${DOCS[type].name}… this can take up to a minute.</div>`;
  } else if (doc) {
    const stale = doc.version !== state.version
      ? `<div class="stale-note"><strong>Outdated:</strong> the requirement model changed after this document was generated. Regenerate to reflect the latest decisions.</div>` : "";
    const demo = state.demo ? `<div class="stale-note" style="background:#f3effc;color:#3b2a6b"><strong>Demo data:</strong> predefined sample document — not generated from your input.</div>` : "";
    view.innerHTML = demo + stale + renderMarkdown(doc.markdown);
    renderDiagrams(view);
  } else if (docErrors[type]) {
    view.innerHTML = "";
  } else if (busy === "generate") {
    view.innerHTML = `<div class="loading"><span class="spinner"></span>${esc(progress || "Working…")}</div>`;
  } else {
    view.innerHTML = `<div class="doc-empty">${state.model
      ? `Generate the documents when the decision register looks right. All three are built from the same requirement baseline:<br><br><b>BPD</b> — the business process · <b>FSD</b> — testable system behaviour · <b>CSD</b> — configuration activities to validate`
      : "Analyze a requirement first. SeeReq will then draft a BPD, FSD and CSD from the requirement and your answers."}</div>`;
  }
}

/* ---------------------------------------------------------------- safe markdown renderer */

const LABELS = [
  [/CONSULTANT VALIDATION REQUIRED/g, "lbl-proposed"],
  [/\bCONFIRMED\b(?! BY)/g, "lbl-confirmed"],
  [/\bPROPOSED\b/g, "lbl-proposed"],
  [/\bASSUMPTION\b/g, "lbl-assumption"],
  [/\bOPEN\b/g, "lbl-open"],
  [/\bBLOCKING\b/g, "lbl-unknown"],
  [/⚠ Not traced — review/g, "lbl-unknown"],
  [/\bRequires confirmation\b/g, "lbl-open"],
];

function inline(text) {
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/&lt;br\s*\/?&gt;/gi, "<br>");
  // Status labels, skipping anything inside tags or already wrapped.
  s = s.split(/(<[^>]+>)/).map((part) => {
    if (part.startsWith("<")) return part;
    for (const [re, cls] of LABELS) part = part.replace(re, (m) => `\u0000${cls}\u0001${m}\u0002`);
    return part.replace(/\u0000([^\u0001]+)\u0001([^\u0002]+)\u0002/g, '<span class="lbl $1">$2</span>');
  }).join("");
  return s;
}

function splitRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

function renderMarkdown(md) {
  const lines = md.replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^```\s*(\w*)\s*$/);
    if (fence) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(fence[1] === "mermaid"
        ? `<div class="diagram"><pre class="mermaid-src">${esc(buf.join("\n"))}</pre></div>`
        : `<pre class="code">${esc(buf.join("\n"))}</pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(splitRow(lines[i++]));
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${buf.filter((b) => b.trim()).map((b) => `<p>${inline(b)}</p>`).join("")}</blockquote>`);
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        let item = lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) item += " " + lines[i++].trim();
        const check = item.match(/^\[( |x|X)\]\s+(.*)$/);
        items.push(check ? `<li class="check">${inline(check[2])}</li>` : `<li>${inline(item)}</li>`);
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*>|\s*\||\s*([-*+]|\d+[.)])\s+|\s*(---|\*\*\*|___)\s*$)/.test(lines[i])) buf.push(lines[i++]);
    if (!buf.length) buf.push(lines[i++]);
    out.push(`<p>${buf.map(inline).join("<br>")}</p>`);
  }
  return out.join("\n");
}

let diagramSeq = 0;
async function renderDiagrams(root) {
  const nodes = root.querySelectorAll("pre.mermaid-src");
  if (!nodes.length) return;
  if (!window.mermaid) {
    nodes.forEach((n) => n.insertAdjacentHTML("afterend", `<p class="muted diagram-note">Diagram preview unavailable offline — the Mermaid source is shown and included in the export.</p>`));
    return;
  }
  for (const node of nodes) {
    try {
      const { svg } = await window.mermaid.render(`seereq-diagram-${++diagramSeq}`, node.textContent);
      const holder = document.createElement("div");
      holder.className = "diagram-svg";
      holder.innerHTML = svg;  // generated by Mermaid with securityLevel "strict"
      node.replaceWith(holder);
    } catch (_) {
      node.insertAdjacentHTML("afterend", `<p class="muted diagram-note">The diagram could not be rendered; its Mermaid source is shown above.</p>`);
    }
  }
}

/* ---------------------------------------------------------------- wiring */

function renderAll() {
  renderBanner();
  renderStepper();
  renderCapture();
  renderAnalysis();
  renderDocs();
  $("#new-btn").disabled = !!busy;
}

function bind() {
  $("#requirement").addEventListener("input", (e) => {
    state.requirement = e.target.value;
    captureError = "";
    save();
    renderCapture();
  });
  $("#requirement").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyze(); });
  $("#analyze-btn").addEventListener("click", () => analyze());
  $("#mic").addEventListener("click", () => startRecording("requirement"));
  $("#stop-btn").addEventListener("click", stopRecording);
  $("#retry-btn").addEventListener("click", retryRecording);
  document.querySelectorAll("#lang button").forEach((b) => b.addEventListener("click", () => { state.language = b.dataset.lang; save(); renderAll(); }));
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    state.requirement = c.textContent.trim();
    state.language = c.dataset.example;
    captureError = "";
    save();
    renderAll();
    $("#requirement").focus();
  }));
  $("#generate-btn").addEventListener("click", generateAll);
  $("#regen-btn").addEventListener("click", regenerate);
  $("#copy-btn").addEventListener("click", () => state.docs[state.activeTab] && copyText(state.docs[state.activeTab].markdown));
  $("#download-btn").addEventListener("click", () => state.docs[state.activeTab] && download(fileName(state.activeTab), state.docs[state.activeTab].markdown));
  $("#download-all").addEventListener("click", () => {
    const parts = DOC_ORDER.filter((t) => state.docs[t]).map((t) => state.docs[t].markdown);
    download(`SeeReq_All_${REQ_ID}.md`, parts.join("\n\n---\n\n"));
  });
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => { state.activeTab = b.dataset.tab; save(); renderDocs(); }));
  $("#new-btn").addEventListener("click", () => {
    if (busy) return;
    if ((state.model || state.requirement) && !confirm("Start a new requirement? The current analysis and documents will be cleared.")) return;
    state = freshState();
    recorder.lastInsert = null;
    recorder.error = "";
    captureError = answerError = docsError = "";
    save();
    renderAll();
  });
}

async function init() {
  if (window.mermaid) {
    try { window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral", flowchart: { htmlLabels: true } }); } catch (_) { /* preview falls back to source */ }
  }
  load();
  bind();
  try {
    const res = await fetch("/api/status");
    live = (await res.json()).live === true;
  } catch (_) {
    live = false;
  }
  const badge = $("#ai-status");
  badge.textContent = live ? "Sarvam AI connected" : "AI not configured";
  badge.className = "badge " + (live ? "live" : "off");
  renderAll();
}

init();
