"use strict";

/* Kivi frontend. All model output is rendered as escaped text — never as raw HTML. */

const DOCS = {
  bpd: { short: "BPD", name: "Business Process Design" },
  fsd: { short: "FSD", name: "Functional Specification Document" },
  csd: { short: "CSD", name: "Configuration Specification Document" },
};
const DOC_ORDER = ["bpd", "fsd", "csd"];
const REQ_ID = "BR-001";
const STORAGE_KEY = "kivi-session-v1";
const MAX_RECORD_SECONDS = 30;

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function freshState() {
  return {
    language: "hinglish",
    requirement: "",
    analyzedRequirement: "",
    analysis: null,
    serverStatus: null,      // "clarification" | "ready" from the server
    history: [],             // [{question, question_en, topic, answer, round}]
    round: 0,
    draftAnswers: {},        // index -> text for the current round's questions
    version: 0,              // bumps whenever the analysed context changes
    docs: {},                // type -> {markdown, version, truncated}
    activeTab: "bpd",
    demo: false,
  };
}

let state = freshState();
let live = false;
let busy = null;             // label of the running operation, or null
const docBusy = {};          // type -> true while generating
const docErrors = {};        // type -> message

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

async function api(path, body, timeoutMs = 200000) {
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
    if (e instanceof TypeError) throw new Error("Could not reach the Kivi server. Is it still running?");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/* ---------------------------------------------------------------- status */

function overallStatus() {
  if (!state.analysis) return "initial";
  const current = DOC_ORDER.every((t) => state.docs[t] && state.docs[t].version === state.version);
  if (current) return "generated";
  return state.serverStatus === "ready" ? "ready" : "clarification";
}

function renderStepper() {
  const order = ["initial", "clarification", "ready", "generated"];
  const status = overallStatus();
  let idx = order.indexOf(status);
  document.querySelectorAll("#stepper li").forEach((li) => {
    const i = order.indexOf(li.dataset.step);
    li.classList.toggle("current", i === idx);
    // "ready" can be reached without passing "clarification" when no questions were needed
    li.classList.toggle("done", i < idx && !(li.dataset.step === "clarification" && state.round === 0 && status !== "clarification"));
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
      <button class="btn btn-small" id="load-demo" type="button">View demo sample</button>`;
    el.hidden = false;
    $("#load-demo").onclick = loadDemo;
    return;
  }
  el.hidden = true;
}

function loadDemo() {
  const d = window.KIVI_DEMO;
  state = Object.assign(freshState(), JSON.parse(JSON.stringify(d.state)), { demo: true });
  save();
  renderAll();
}

/* ---------------------------------------------------------------- capture panel */

function renderCapture() {
  const req = $("#requirement");
  if (req.value !== state.requirement) req.value = state.requirement;
  $("#req-count").textContent = `${state.requirement.length} / 4000`;
  const note = $("#req-note");
  const changed = state.analysis && state.requirement.trim() !== state.analyzedRequirement;
  note.textContent = changed ? "Edited since last analysis — re-analyze to update." : "";
  note.className = changed ? "changed" : "muted";

  document.querySelectorAll("#lang button").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.lang === state.language));
    b.disabled = !!busy || state.demo;
  });

  const locked = !!busy || state.demo || !live;
  req.disabled = state.demo;
  document.querySelectorAll(".chip").forEach((c) => (c.disabled = state.demo));
  const btn = $("#analyze-btn");
  btn.disabled = locked;
  const reanalyze = state.analysis && !changed;
  btn.innerHTML = busy === "analyze"
    ? `<span class="spinner"></span> Analyzing your requirement…`
    : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h12M12 6l6 6-6 6"/></svg> ${state.analysis ? (reanalyze ? "Re-run analysis" : "Re-analyze requirement") : "Analyze requirement"}`;
  renderVoice();
}

function showError(id, msg) {
  const el = $(id);
  el.textContent = msg || "";
  el.hidden = !msg;
}

async function analyze() {
  if (busy) return;
  const requirement = state.requirement.trim();
  if (!requirement) { showError("#capture-error", "Please describe a requirement first — speak or type it above."); $("#requirement").focus(); return; }
  showError("#capture-error", "");
  busy = "analyze";
  renderAll();
  try {
    const data = await api("/api/analyze", { requirement, language: state.language, history: state.history.map(turnPayload) });
    state.analysis = data.analysis;
    state.serverStatus = data.status;
    state.analyzedRequirement = requirement;
    state.draftAnswers = {};
    state.version += 1;
    save();
  } catch (e) {
    showError("#capture-error", e.message);
  } finally {
    busy = null;
    renderAll();
  }
}

const turnPayload = (t) => ({ question_en: t.question_en, topic: t.topic, answer: t.answer });

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

const LIST_SECTIONS = [
  ["confirmed_facts", "Confirmed facts", "var(--ok)"],
  ["business_rules", "Business rules", "var(--ok)"],
  ["ambiguities", "Ambiguities", "var(--warn)"],
  ["missing_information", "Missing information", "var(--warn)"],
  ["contradictions", "Contradictions", "var(--bad)"],
  ["unresolved_items", "Unresolved items", "#5a3fa8"],
  ["assumptions", "Assumptions", "#8a8f99"],
  ["proposed_recommendations", "Proposed recommendations", "var(--accent)"],
  ["consultant_investigation", "For consultant investigation", "var(--accent)"],
];

function renderAnalysis() {
  const body = $("#analysis-body");
  const a = state.analysis;
  if (busy === "analyze" && !a) {
    body.innerHTML = `<div class="loading"><span class="spinner"></span>Analyzing your requirement and preparing clarification questions…</div>`;
    return;
  }
  if (!a) {
    body.innerHTML = `<div class="empty"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6M10 3v5l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"/></svg>
      <p>Kivi analyzes your requirement like a functional consultant: business objective, process area, gaps and ambiguities — then asks only the questions that matter.</p></div>`;
    return;
  }

  const status = overallStatus();
  const statusText = {
    clarification: ["Clarification required", "Answer the questions below in Hindi, Hinglish or English. You can also generate draft documents now — open points will be flagged."],
    ready: ["Ready for documentation", (a.readiness_reason || "Essential business decisions are captured.") + " This is an AI recommendation — review the analysis before generating."],
    generated: ["Documentation generated", "All three documents reflect the current analysis. Review them before sharing."],
  }[status];

  const answered = state.history.filter((t) => t.answer).length;
  let html = "";
  if (busy === "analyze") html += `<div class="loading" style="margin-bottom:14px"><span class="spinner"></span>Updating the analysis with your answers…</div>`;
  html += `<div class="original"><div class="k">Original requirement · ${REQ_ID}</div><div class="v">“${esc(state.analyzedRequirement)}”</div>
    <div class="en"><span class="muted">English interpretation:</span> ${esc(a.interpretation_en)}</div></div>`;
  html += `<div class="status-card ${status}"><div><strong>${statusText[0]}</strong>${esc(statusText[1])}</div></div>`;
  html += `<div class="facts">
    <div class="fact wide"><div class="k">Business objective</div><div class="v">${esc(a.business_objective)}</div></div>
    <div class="fact"><div class="k">Process area <span class="tag-proposed-inline">PROPOSED</span></div><div class="v">${esc(a.process_area)}</div></div>
    <div class="fact"><div class="k">Expected system behavior</div><div class="v">${esc(a.expected_system_behavior || "Requires confirmation")}</div></div>
  </div>`;

  const qs = a.clarification_questions || [];
  if (qs.length) {
    html += `<div class="section-title">Clarification questions <span class="count">${qs.length}</span>${state.round ? `<span class="muted" style="font-weight:500;font-size:12.5px">Round ${state.round + 1}</span>` : ""}</div><div class="questions">`;
    qs.forEach((q, i) => {
      html += `<div class="question"><div class="q-top"><span class="q-num">Q${i + 1}</span><span class="q-text">${esc(q.question)}</span>${q.topic ? `<span class="q-topic">${esc(q.topic)}</span>` : ""}</div>
        ${q.question_en && q.question_en !== q.question ? `<div class="q-en">${esc(q.question_en)}</div>` : `<div class="q-en"></div>`}
        <div class="answer-row"><textarea rows="1" data-answer="${i}" maxlength="2000" placeholder="Your answer (Hindi, Hinglish or English) — leave blank to skip" ${state.demo || busy ? "disabled" : ""}>${esc(state.draftAnswers[i] || "")}</textarea>
        <button type="button" class="mic-small ${recorder.state === "recording" && recorder.target === String(i) ? "recording" : ""}" data-mic="${i}" aria-label="Answer by voice" title="Answer by voice" ${!canRecord || !live || state.demo || busy || (recorder.state !== "idle" && recorder.target !== String(i)) ? "disabled" : ""}>
          ${recorder.state === "transcribing" && recorder.target === String(i) ? `<span class="spinner"></span>` : `<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>`}
        </button></div></div>`;
    });
    html += `</div>`;
    if (recorder.error && recorder.target !== "requirement") html += `<div class="inline-error">${esc(recorder.error)}</div>`;
    html += `<div id="answer-error" class="inline-error" hidden></div>
      <div class="analysis-actions"><button class="btn btn-primary" id="submit-answers" type="button" ${state.demo || !live || busy ? "disabled" : ""}>${busy === "analyze" ? `<span class="spinner"></span> Updating analysis…` : "Submit answers &amp; update analysis"}</button></div>
      <div class="hint muted">Unanswered questions are recorded as skipped. Technical SAP decisions are not asked here — they are listed for the consultant.</div>`;
  } else {
    html += `<div class="section-title">Clarification questions <span class="count">0</span></div><p class="muted" style="margin:0;font-size:14px">No further business questions. Remaining technical points are listed for consultant investigation.</p>`;
  }

  html += `<div class="lists">`;
  for (const [key, label, color] of LIST_SECTIONS) {
    const items = a[key] || [];
    if (!items.length) continue;
    const open = ["missing_information", "ambiguities", "unresolved_items", "contradictions"].includes(key) ? "open" : "";
    html += `<details class="list" ${open}><summary><span class="swatch" style="background:${color}"></span>${label} <span class="count">${items.length}</span></summary><ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>`;
  }
  html += `</div>`;

  if (state.history.length) {
    html += `<div class="history"><div class="section-title">Conversation history <span class="count">${answered} answered</span></div>`;
    state.history.forEach((t, i) => {
      html += `<div class="turn"><div class="tq">${i + 1}. ${esc(t.question)}</div><div class="ta ${t.answer ? "" : "skipped"}">${t.answer ? "→ " + esc(t.answer) : "Skipped"}</div></div>`;
    });
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
  const qs = state.analysis.clarification_questions || [];
  const answers = qs.map((_, i) => (state.draftAnswers[i] || "").trim());
  if (!answers.some(Boolean)) {
    showError("#answer-error", "Answer at least one question — or use “Generate” to draft documents with these questions flagged as open.");
    return;
  }
  state.round += 1;
  qs.forEach((q, i) => state.history.push({ question: q.question, question_en: q.question_en, topic: q.topic, answer: answers[i], round: state.round }));
  state.draftAnswers = {};
  save();
  analyze();
}

/* ---------------------------------------------------------------- documents */

async function generateAll() {
  if (busy || !state.analysis) return;
  if (state.requirement.trim() !== state.analyzedRequirement) {
    showError("#doc-error", "The requirement was edited after the last analysis. Re-analyze it first so the documents match.");
    return;
  }
  showError("#doc-error", "");
  busy = "generate";
  const related = {};
  for (const type of DOC_ORDER) {
    state.activeTab = type;
    const ok = await generateOne(type, related);
    if (!ok) break;
    related[type] = state.docs[type].markdown;
  }
  busy = null;
  renderAll();
}

async function generateOne(type, related) {
  docBusy[type] = true;
  delete docErrors[type];
  renderAll();
  try {
    const data = await api("/api/generate", {
      doc_type: type,
      requirement: state.analyzedRequirement,
      language: state.language,
      history: state.history.map(turnPayload),
      analysis: state.analysis,
      related,
    });
    state.docs[type] = { markdown: data.markdown, version: state.version, truncated: data.truncated };
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
  if (busy || !state.analysis) return;
  if (state.requirement.trim() !== state.analyzedRequirement) {
    showError("#doc-error", "The requirement was edited after the last analysis. Re-analyze it first so the document matches.");
    return;
  }
  const type = state.activeTab;
  const related = {};
  DOC_ORDER.filter((t) => t !== type && state.docs[t]).forEach((t) => (related[t] = state.docs[t].markdown));
  busy = "generate";
  await generateOne(type, related);
  busy = null;
  renderAll();
}

function fileName(type) {
  return `Kivi_${type.toUpperCase()}_${REQ_ID}.md`.replace(/[^A-Za-z0-9_.-]/g, "_");
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

function renderDocs() {
  const anyDoc = DOC_ORDER.some((t) => state.docs[t]);
  const gen = $("#generate-btn");
  gen.disabled = !state.analysis || !!busy || state.demo || !live;
  const running = DOC_ORDER.find((t) => docBusy[t]);
  gen.innerHTML = busy === "generate" && running
    ? `<span class="spinner"></span> Generating ${DOCS[running].short}…`
    : anyDoc ? "Regenerate all documents"
    : overallStatus() === "clarification" ? "Generate drafts (open questions flagged)" : "Generate BPD, FSD &amp; CSD";
  $("#download-all").disabled = !anyDoc;

  document.querySelectorAll("#tabs button").forEach((b) => {
    const t = b.dataset.tab, d = state.docs[t];
    b.setAttribute("aria-selected", String(t === state.activeTab));
    const tag = b.querySelector(".tab-state");
    if (docBusy[t]) { tag.className = "tab-state busy"; tag.textContent = "Generating"; }
    else if (docErrors[t]) { tag.className = "tab-state err"; tag.textContent = "Failed"; }
    else if (d && d.version !== state.version) { tag.className = "tab-state stale"; tag.textContent = "Outdated"; }
    else if (d) { tag.className = "tab-state ok"; tag.textContent = state.demo ? "Demo" : "Draft"; }
    else { tag.className = "tab-state"; tag.textContent = ""; }
  });

  const type = state.activeTab, doc = state.docs[type], view = $("#doc-view");
  $("#doc-toolbar").hidden = !doc;
  if (docErrors[type]) showError("#doc-error", `${DOCS[type].short} generation failed: ${docErrors[type]} Your requirement and answers are kept — try Regenerate.`);
  else if (busy) showError("#doc-error", "");
  if (doc) {
    $("#doc-meta").textContent = `${fileName(type)} · ${doc.markdown.length.toLocaleString()} characters${state.demo ? " · demo sample" : ""}`;
    $("#copy-btn").disabled = $("#download-btn").disabled = false;
    $("#regen-btn").disabled = !!busy || state.demo || !live;
    $("#regen-btn").innerHTML = docBusy[type] ? `<span class="spinner"></span> Regenerating…` : "Regenerate";
  }

  if (docBusy[type]) {
    view.innerHTML = `<div class="loading"><span class="spinner"></span>Generating ${DOCS[type].short} — ${DOCS[type].name}… this can take up to a minute.</div>`;
  } else if (doc) {
    const stale = doc.version !== state.version
      ? `<div class="stale-note"><strong>Outdated:</strong> the analysis changed after this document was generated. Regenerate it to reflect the latest requirement state.</div>` : "";
    const demo = state.demo ? `<div class="stale-note" style="background:#f3effc;color:#3b2a6b"><strong>Demo data:</strong> predefined sample document — not generated from your input.</div>` : "";
    view.innerHTML = demo + stale + renderMarkdown(doc.markdown);
  } else if (docErrors[type]) {
    view.innerHTML = "";
  } else {
    view.innerHTML = `<div class="doc-empty">${state.analysis
      ? `Generate the documents when the analysis looks right. Each document has a distinct purpose:<br><br><b>BPD</b> — the business process · <b>FSD</b> — testable system behavior · <b>CSD</b> — configuration decisions to validate`
      : "Analyze a requirement first. Kivi will then draft a BPD, FSD and CSD from the requirement and your answers."}</div>`;
  }
}

/* ---------------------------------------------------------------- safe markdown renderer */

const LABELS = [
  [/CONFIRMED BY USER/g, "lbl-confirmed"],
  [/PROPOSED (?:—|-|–) REQUIRES CONSULTANT VALIDATION/g, "lbl-proposed"],
  [/UNKNOWN (?:—|-|–) INVESTIGATION REQUIRED/g, "lbl-unknown"],
  [/\bOPEN QUESTION\b/g, "lbl-open"],
  [/\bASSUMPTION\b/g, "lbl-assumption"],
  [/\bRequires confirmation\b/g, "lbl-open"],
];

function inline(text) {
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/&lt;br\s*\/?&gt;/gi, "<br>");
  for (const [re, cls] of LABELS) s = s.replace(re, (m) => `<span class="lbl ${cls}">${m}</span>`);
  s = s.replace(/<strong>(<span class="lbl [^"]+">[^<]+<\/span>)<\/strong>/g, "$1");
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
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*>|\s*\||\s*([-*+]|\d+[.)])\s+|\s*(---|\*\*\*|___)\s*$)/.test(lines[i])) buf.push(lines[i++]);
    if (!buf.length) buf.push(lines[i++]);
    out.push(`<p>${buf.map(inline).join("<br>")}</p>`);
  }
  return out.join("\n");
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
    showError("#capture-error", "");
    save();
    renderCapture();
  });
  $("#requirement").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyze(); });
  $("#analyze-btn").addEventListener("click", analyze);
  $("#mic").addEventListener("click", () => startRecording("requirement"));
  $("#stop-btn").addEventListener("click", stopRecording);
  $("#retry-btn").addEventListener("click", retryRecording);
  document.querySelectorAll("#lang button").forEach((b) => b.addEventListener("click", () => { state.language = b.dataset.lang; save(); renderAll(); }));
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    state.requirement = c.textContent.trim();
    state.language = c.dataset.example;
    showError("#capture-error", "");
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
    download(`Kivi_All_${REQ_ID}.md`, parts.join("\n\n<div style=\"page-break-after: always\"></div>\n\n---\n\n"));
  });
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => { state.activeTab = b.dataset.tab; save(); renderDocs(); }));
  $("#new-btn").addEventListener("click", () => {
    if (busy) return;
    if ((state.analysis || state.requirement) && !confirm("Start a new requirement? The current analysis and documents will be cleared.")) return;
    state = freshState();
    recorder.lastInsert = null;
    recorder.error = "";
    save();
    showError("#capture-error", "");
    showError("#doc-error", "");
    renderAll();
  });
}

async function init() {
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
