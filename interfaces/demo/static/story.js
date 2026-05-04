/**
 * AIOps Demo — Storytelling Page (/demo/story)
 *
 * Cliente paralelo ao dashboard.js: assina o mesmo /demo/ws e mapeia
 * tick.phase -> narrativa local (carregada via REST). Renderiza 4 painéis
 * técnicos (Risk gauge, Compare, Latency, Prediction) + 5 elementos
 * narrativos (Header, Hero/Lead-time, Business Impact, Slack mock,
 * Story timeline).
 *
 * Protocolo de start: POST /demo/start/{scenario} (mesmo que dashboard.js).
 * O endpoint /demo/ws hoje só faz broadcast — não consome control messages.
 */

"use strict";

// ------------------------------------------------------------------ //
// Estado de módulo                                                    //
// ------------------------------------------------------------------ //
let narrative = null;            // StoryNarrative do backend (ou null em fallback)
let scenarioName = "payment-pool-exhaustion";

let ws = null;
let wsReconnectTimer = null;

let riskGauge = null;
let compareChart = null;
let latencyChart = null;

const MAX_POINTS = 60;

// Buffers temporais (eixo x: elapsed_sim_seconds para coerência com speed_factor)
const series = {
  ts:          [],   // elapsed_sim_seconds
  predictive:  [],
  lat95:       [],
  lat99:       [],
};

// Estado de transições de fase
let lastPhase = null;
let phaseStartSimTime = 0;       // sim_time (segundos) ao entrar em WARNING/ALERT
let resolvedFadeoutTimer = null;
let timelineNodeRects = {};      // cache de offsets X dos 6 nós da timeline

const PHASE_ORDER = ["PLAYING", "DEGRADING", "WARNING", "ALERT", "INCIDENT", "RESOLVED"];

// ------------------------------------------------------------------ //
// Bootstrap                                                           //
// ------------------------------------------------------------------ //
document.addEventListener("DOMContentLoaded", async () => {
  // 1. Lê cenário da query string
  const params = new URLSearchParams(location.search);
  scenarioName = params.get("scenario") || "payment-pool-exhaustion";

  // 2. Tenta carregar narrativa autorada
  try {
    const res = await fetch("/demo/api/narrative/" + encodeURIComponent(scenarioName));
    if (res.status === 404) {
      activateFallback();
    } else if (res.ok) {
      narrative = await res.json();
      renderHeader(narrative);
    } else {
      console.warn("Narrative endpoint unexpected status:", res.status);
      activateFallback();
    }
  } catch (err) {
    console.error("Falha ao buscar narrativa:", err);
    activateFallback();
  }

  // 3. Inicializa gráficos técnicos
  initCharts();
  cacheTimelineRects();
  window.addEventListener("resize", () => {
    cacheTimelineRects();
    if (lastPhase) moveTimelineCursor(lastPhase, true);
  });

  // 4. Conecta WS e inicia o cenário (POST, igual ao dashboard.js)
  connectWS();

  // Pequeno delay para garantir que o WS está aceito antes do primeiro tick.
  // Caso contrário, ticks anteriores ao on_open são perdidos.
  // speed_factor=4 = mesmo default do dashboard original (~3 min por cenário).
  setTimeout(() => {
    fetch("/demo/start/" + encodeURIComponent(scenarioName), { method: "POST" })
      .then(() => fetch("/demo/speed", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speed_factor: 4 }),
      }))
      .catch((err) => console.error("Falha ao iniciar cenário:", err));
  }, 250);
});

function activateFallback() {
  // Cenário sem narrativa autorada: degrade gracioso, mantém painéis técnicos
  narrative = null;
  document.body.classList.add("no-narrative");
  const headerEl = document.getElementById("story-header");
  if (headerEl) {
    headerEl.querySelector(".story-header-text").textContent =
      "Cenário sem narrativa autorada — visualizando apenas painéis técnicos";
  }
  ["business-impact", "slack-mock-panel", "hero-bar", "story-timeline"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  });
  // Esconde também a coluna inteira se quiser, mas mantemos para preservar o grid.
  const slackPanel = document.querySelector(".panel-slack");
  if (slackPanel) slackPanel.classList.add("hidden");
  const impactPanel = document.querySelector(".panel-impact");
  if (impactPanel) impactPanel.classList.add("hidden");
}

function renderHeader(narr) {
  const headerEl = document.getElementById("story-header");
  if (!headerEl) return;
  const text = narr.app_name + "  ·  " + narr.app_context;
  headerEl.querySelector(".story-header-text").textContent = text;
}

// ------------------------------------------------------------------ //
// WebSocket                                                           //
// ------------------------------------------------------------------ //
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/demo/ws`);

  ws.onopen = () => {
    console.log("WS conectado (story)");
    clearTimeout(wsReconnectTimer);
  };

  ws.onmessage = (evt) => {
    let tick;
    try {
      tick = JSON.parse(evt.data);
    } catch (err) {
      console.error("Frame inválido:", err);
      return;
    }
    // Story page ignora frames de comparação dual (dashboard-only).
    if (tick.comparison) return;
    handleTick(tick);
  };

  ws.onclose = () => {
    wsReconnectTimer = setTimeout(connectWS, 2000);
  };
  ws.onerror = () => ws.close();
}

// ------------------------------------------------------------------ //
// Tick handler                                                        //
// ------------------------------------------------------------------ //
function handleTick(tick) {
  const phase = tick.phase || "IDLE";
  const phaseNarr = narrative && narrative.phases ? narrative.phases[phase] : null;

  // 1. Painéis técnicos (sempre rodam)
  updateRiskGauge(tick);
  updateCompareChart(tick);
  updateLatencyChart(tick);
  updatePredictionStatus(tick);

  // 2. Narrativa (só roda se temos narrativa autorada)
  if (narrative) {
    if (phase !== lastPhase) {
      onPhaseTransition(lastPhase, phase, phaseNarr);
    }
    if (phaseNarr) {
      updateBusinessImpact(phaseNarr);
      updateHeroBar(tick, phaseNarr);
    }
    moveTimelineCursor(phase, false);
  }

  lastPhase = phase;
}

// ------------------------------------------------------------------ //
// Charts — init                                                       //
// ------------------------------------------------------------------ //
function initCharts() {
  // Risk gauge (ECharts)
  const gaugeEl = document.getElementById("risk-gauge");
  if (gaugeEl) {
    riskGauge = echarts.init(gaugeEl, "dark");
    riskGauge.setOption(buildGaugeOption(0, null));
  }

  // Risk score chart (uPlot, single-line). Comparação dual fica restrita ao dashboard.
  compareChart = new uPlot(
    buildLineOpts("compare-chart", ["Predictive"], ["#5c6bc0"], { yMax: 100 }),
    [[], []],
    document.getElementById("compare-chart")
  );

  // Latency chart (uPlot, dual-line)
  latencyChart = new uPlot(
    buildLineOpts("latency-chart", ["p95", "p99"], ["#ff780a", "#f2495c"]),
    [[], [], []],
    document.getElementById("latency-chart")
  );

  // Resize handler — uPlot precisa de setSize explícito para grid responsivo
  window.addEventListener("resize", () => {
    [compareChart, latencyChart].forEach((u) => {
      if (!u) return;
      const parent = u.root.parentElement;
      if (parent) u.setSize({ width: parent.clientWidth, height: parent.clientHeight });
    });
  });
}

function buildGaugeOption(value, eventLevel) {
  let color;
  if (eventLevel === "prediction.alert") color = "#f2495c";
  else if (eventLevel === "prediction.warning") color = "#ff780a";
  else color = "#73bf69";

  return {
    backgroundColor: "transparent",
    series: [{
      type: "gauge",
      min: 0,
      max: 100,
      radius: "92%",
      axisLine: { lineStyle: { color: [[value / 100, color], [1, "#2a2d3a"]], width: 14 } },
      pointer: { length: "62%", width: 5, itemStyle: { color } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: {
        show: true,
        formatter: "{value}",
        offsetCenter: [0, "30%"],
        fontSize: 28,
        fontWeight: 700,
        color: "#e0e0e0",
      },
      data: [{ value: Math.round(value) }],
    }],
  };
}

function buildLineOpts(containerId, names, colors, opts) {
  opts = opts || {};
  const el = document.getElementById(containerId);
  const w = el ? el.clientWidth || 400 : 400;
  const h = el ? el.clientHeight || 160 : 160;

  const seriesDef = [{}].concat(
    names.map((name, i) => ({
      label: name,
      stroke: colors[i] || "#5c6bc0",
      width: 1.8,
    }))
  );

  return {
    width: w,
    height: h,
    legend: { show: false },
    cursor: { drag: { x: false, y: false } },
    scales: {
      y: opts.yMax ? { range: [0, opts.yMax] } : {},
    },
    axes: [
      { stroke: "#555", ticks: { stroke: "#333" }, grid: { stroke: "#1e2130" } },
      { stroke: "#555", ticks: { stroke: "#333" }, grid: { stroke: "#1e2130" } },
    ],
    series: seriesDef,
    padding: [8, 12, 8, 12],
  };
}

// ------------------------------------------------------------------ //
// Charts — updates                                                    //
// ------------------------------------------------------------------ //
function updateRiskGauge(tick) {
  if (!riskGauge) return;
  const eventLevel = tick.prediction && tick.prediction.event_level;
  riskGauge.setOption(buildGaugeOption(tick.risk_score || 0, eventLevel));
}

function updateCompareChart(tick) {
  // Eixo X: elapsed_sim_seconds (numérico, respeita speed_factor)
  // sim_time é ISO string -> não usar como número.
  const t = tick.elapsed_sim_seconds != null ? tick.elapsed_sim_seconds : (series.ts.length > 0 ? series.ts[series.ts.length - 1] + 1 : 0);
  series.ts.push(t);
  series.predictive.push(tick.risk_score || 0);
  series.lat95.push(tick.metrics ? (tick.metrics.latency_p95_ms || 0) : 0);
  series.lat99.push(tick.metrics ? (tick.metrics.latency_p99_ms || 0) : 0);

  if (series.ts.length > MAX_POINTS) {
    series.ts.shift();
    series.predictive.shift();
    series.lat95.shift();
    series.lat99.shift();
  }

  if (compareChart) compareChart.setData([series.ts, series.predictive]);
}

function updateLatencyChart(tick) {
  // Buffers já populados em updateCompareChart (mesmo ciclo de tick)
  if (latencyChart) latencyChart.setData([series.ts, series.lat95, series.lat99]);
}

function updatePredictionStatus(tick) {
  const pred = tick.prediction;
  const levelEl   = document.getElementById("pred-level");
  const etaEl     = document.getElementById("pred-eta");
  const sigEl     = document.getElementById("pred-signals");
  if (!levelEl) return;

  if (!pred) {
    levelEl.textContent = "— No Prediction —";
    levelEl.className = "pred-level";
    if (etaEl) etaEl.textContent = "";
    if (sigEl) sigEl.textContent = "";
    return;
  }

  levelEl.textContent = pred.event_level || "—";
  levelEl.className = "pred-level"
    + (pred.event_level === "prediction.alert" ? " alert"
       : pred.event_level === "prediction.warning" ? " warning" : "");

  if (etaEl) etaEl.textContent = pred.eta_minutes != null ? `ETA: ~${pred.eta_minutes.toFixed(1)} min` : "";
  if (sigEl) sigEl.textContent = pred.signals_fired && pred.signals_fired.length
    ? `Signals: ${pred.signals_fired.join(", ")}` : "";
}

// ------------------------------------------------------------------ //
// Phase transitions                                                   //
// ------------------------------------------------------------------ //
function onPhaseTransition(oldPhase, newPhase, phaseNarr) {
  if (!narrative || !phaseNarr) return;

  // 1. Slack card (se a fase tem mensagem autorada)
  if (phaseNarr.slack_message) {
    appendSlackCard(phaseNarr.slack_message);
  }

  const heroBar = document.getElementById("hero-bar");

  // Helper: registra início simulado da fase para drain.
  // O dashboard manda elapsed_sim_seconds em todo tick — capturamos no próximo
  // tick via fallback de phaseStartSimTime; aqui apenas marcamos a flag.
  // (Captura efetiva é no primeiro updateHeroBar com phase==newPhase abaixo.)

  // 2. Hero bar: WARNING/ALERT -> slide-in com lead-time
  if (newPhase === "WARNING" || newPhase === "ALERT") {
    if (resolvedFadeoutTimer) { clearTimeout(resolvedFadeoutTimer); resolvedFadeoutTimer = null; }
    heroBar.classList.remove("hidden");
    heroBar.dataset.mode = newPhase === "WARNING" ? "lead" : "alert";
    document.getElementById("hero-text").textContent = phaseNarr.hero_text || "";
    phaseStartSimTime = -1; // capturado no próximo tick (ver updateHeroBar)

    if (typeof gsap !== "undefined") {
      gsap.from(heroBar, { y: -20, opacity: 0, duration: 0.4, ease: "power2.out" });
    }
  }

  // 3. Hero banner: INCIDENT/RESOLVED -> banner sólido
  if (newPhase === "INCIDENT" || newPhase === "RESOLVED") {
    if (resolvedFadeoutTimer) { clearTimeout(resolvedFadeoutTimer); resolvedFadeoutTimer = null; }
    heroBar.classList.remove("hidden");
    heroBar.dataset.mode = newPhase === "INCIDENT" ? "incident" : "resolved";
    document.getElementById("hero-text").textContent = phaseNarr.hero_text || "";

    if (typeof gsap !== "undefined") {
      gsap.fromTo(heroBar, { opacity: 0.4 }, { opacity: 1, duration: 0.6 });
    }

    // RESOLVED: agendar fade-out 10s depois
    if (newPhase === "RESOLVED") {
      resolvedFadeoutTimer = setTimeout(() => {
        if (typeof gsap !== "undefined") {
          gsap.to(heroBar, { opacity: 0, duration: 0.5, onComplete: () => heroBar.classList.add("hidden") });
        } else {
          heroBar.classList.add("hidden");
        }
      }, 10000);
    }
  }

  // 4. PLAYING/DEGRADING — esconde hero (não há lead-time nem banner)
  if (newPhase === "PLAYING" || newPhase === "DEGRADING") {
    heroBar.classList.add("hidden");
  }

  // 5. Timeline cursor anima para o nó atual
  moveTimelineCursor(newPhase, false);
}

// ------------------------------------------------------------------ //
// Slack mock                                                          //
// ------------------------------------------------------------------ //
function appendSlackCard(msg) {
  const panel = document.getElementById("slack-mock-panel");
  if (!panel) return;
  // Remove placeholder vazio na primeira inserção
  const empty = panel.querySelector(".slack-empty");
  if (empty) empty.remove();

  const card = document.createElement("div");
  card.className = `slack-card slack-card--${msg.severity || "info"}`;
  card.innerHTML = `
    <div class="slack-card-header">${escapeHtml(severityLabel(msg.severity))}</div>
    <div class="slack-card-meta">
      <span class="slack-channel">${escapeHtml(msg.channel || "")}</span>
      <span class="slack-author">${escapeHtml(msg.author || "")}</span>
      <span class="slack-ts">${escapeHtml(msg.timestamp_label || "")}</span>
    </div>
    <div class="slack-card-title">${escapeHtml(msg.title || "")}</div>
    <div class="slack-card-body">${renderMarkdownLite(msg.body || "")}</div>
  `;
  panel.appendChild(card);
  panel.scrollTop = panel.scrollHeight;

  if (typeof gsap !== "undefined") {
    gsap.from(card, { x: -30, opacity: 0, duration: 0.5, ease: "power2.out" });
  }
}

function severityLabel(sev) {
  switch (sev) {
    case "warning":  return "Warning";
    case "critical": return "Critical";
    case "success":  return "Resolved";
    case "info":     return "Info";
    default:         return "Alert";
  }
}

// Suporte mínimo a **bold** + \n -> <br> (markdown leve do spec)
function renderMarkdownLite(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ------------------------------------------------------------------ //
// Business impact                                                     //
// ------------------------------------------------------------------ //
function updateBusinessImpact(phaseNarr) {
  const wrap = document.getElementById("business-impact");
  const valueEl = document.getElementById("business-impact-value");
  if (!wrap || !valueEl) return;

  const v = phaseNarr.business_impact_per_min;
  valueEl.textContent = v > 0 ? `R$ ${formatBR(v)}/min` : "R$ 0";

  const color = phaseNarr.impact_color || "green";
  wrap.classList.remove("impact-green", "impact-amber", "impact-red", "impact-pulse");
  wrap.classList.add(`impact-${color}`);
  if (color === "red") wrap.classList.add("impact-pulse");
}

// formatBR: heurística simples sem Intl (determinismo).
//  0 -> "0", 5000 -> "5 mil", 50000 -> "50 mil", 1500000 -> "1,5 mi"
function formatBR(n) {
  if (n == null || n === 0) return "0";
  if (n >= 1_000_000) {
    const mi = n / 1_000_000;
    const s = mi.toFixed(mi % 1 === 0 ? 0 : 1).replace(".", ",");
    return `${s} mi`;
  }
  if (n >= 1000) {
    const k = Math.round(n / 1000);
    return `${k} mil`;
  }
  return String(n);
}

// ------------------------------------------------------------------ //
// Hero bar — drenagem progressiva                                     //
// ------------------------------------------------------------------ //
function updateHeroBar(tick, phaseNarr) {
  const phase = tick.phase;
  if (phase !== "WARNING" && phase !== "ALERT") return;
  const lead = phaseNarr.hero_lead_time_seconds;
  if (!lead) return;

  // Captura sim_time do primeiro tick após a transição de fase
  const simNow = tick.elapsed_sim_seconds || 0;
  if (phaseStartSimTime < 0) {
    phaseStartSimTime = simNow;
  }

  const elapsed = simNow - phaseStartSimTime;
  const remaining = Math.max(0, lead - elapsed);
  const pct = Math.max(0, Math.min(100, (remaining / lead) * 100));

  const bar = document.getElementById("hero-progress");
  const lbl = document.getElementById("hero-progress-label");
  if (bar) bar.style.width = pct + "%";
  if (lbl) lbl.textContent = `${Math.ceil(remaining)}s restantes`;
}

// ------------------------------------------------------------------ //
// Story timeline cursor                                               //
// ------------------------------------------------------------------ //
function cacheTimelineRects() {
  const tl = document.getElementById("story-timeline");
  if (!tl) return;
  const trackRect = tl.querySelector(".timeline-track").getBoundingClientRect();
  timelineNodeRects = {};
  tl.querySelectorAll(".timeline-node").forEach((node) => {
    const r = node.getBoundingClientRect();
    const x = (r.left + r.width / 2) - trackRect.left;
    timelineNodeRects[node.dataset.phase] = x;
  });
}

function moveTimelineCursor(phase, instant) {
  const cursor = document.getElementById("timeline-cursor");
  const tl = document.getElementById("story-timeline");
  if (!cursor || !tl) return;

  // Atualiza estados past/current/future dos nós
  const idx = PHASE_ORDER.indexOf(phase);
  tl.querySelectorAll(".timeline-node").forEach((n) => {
    const i = PHASE_ORDER.indexOf(n.dataset.phase);
    n.classList.remove("past", "current", "future");
    if (idx < 0)              n.classList.add("future");
    else if (i < idx)         n.classList.add("past");
    else if (i === idx)       n.classList.add("current");
    else                      n.classList.add("future");
  });

  const x = timelineNodeRects[phase];
  if (x == null) return;
  cursor.classList.add("visible");

  if (typeof gsap !== "undefined" && !instant) {
    gsap.to(cursor, { left: x + "px", duration: 0.3, ease: "power2.out" });
  } else {
    cursor.style.left = x + "px";
  }
}
