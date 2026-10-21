/**
 * AIOps Agent Demo Dashboard
 * WebSocket client + chart rendering + animations (WM1/WM2/WM3) +
 * comparison mode + speed selector + postmortem export.
 */

"use strict";

// ------------------------------------------------------------------ //
// Estado global                                                        //
// ------------------------------------------------------------------ //
let ws = null;
let wsReconnectTimer = null;
let gaugeChart = null;
let cpuChart = null, memChart = null, latencyChart = null;
let gaugeLeft = null, gaugeRight = null;
let cpuLeft = null, cpuRight = null, latLeft = null, latRight = null;

const MAX_POINTS = 60;   // Máximo de pontos nos gráficos de linha

// Buffers de séries temporais
const series = {
  ts: [],
  cpu: [], mem: [], lat95: [], lat99: [],
  cpuL: [], latL: [],
  cpuR: [], latR: [],
};

// Estado de comparação
let compareMode = false;
let prevPhase = "IDLE";
let predictionFired = false;   // WM1 disparado?
let ragCardsFired = false;      // WM3 disparado?
let leadTimeInterval = null;
let leadTimeStartWall = null;
let leadTimeFinished = false;

// Risk trend tracking
let prevRiskScore = 0;
let riskTrend = "stable";   // "rising" | "falling" | "stable"
let _lastLogPhase = "IDLE"; // controle de separadores no log stream

// ------------------------------------------------------------------ //
// WebSocket                                                            //
// ------------------------------------------------------------------ //
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/demo/ws`);

  ws.onopen = () => {
    console.log("WebSocket conectado");
    clearTimeout(wsReconnectTimer);
  };

  ws.onmessage = (evt) => {
    try {
      const data = JSON.parse(evt.data);
      if (data.comparison) {
        handleCompareFrame(data);
      } else {
        handleTickFrame(data);
      }
    } catch (e) {
      console.error("Erro ao processar frame:", e);
    }
  };

  ws.onclose = () => {
    console.log("WebSocket desconectado — reconectando em 2s");
    wsReconnectTimer = setTimeout(connectWS, 2000);
  };

  ws.onerror = () => ws.close();
}

// ------------------------------------------------------------------ //
// Inicialização dos gráficos                                          //
// ------------------------------------------------------------------ //
function initCharts() {
  // Gauge (ECharts)
  gaugeChart = echarts.init(document.getElementById("gauge-chart"), "dark");
  gaugeChart.setOption(buildGaugeOption(0));

  // uPlot — CPU
  cpuChart = new uPlot(buildLineOpts("cpu-chart", ["CPU %"]), [[], []], document.getElementById("cpu-chart"));
  memChart = new uPlot(buildLineOpts("mem-chart", ["Memory %"]), [[], []], document.getElementById("mem-chart"));
  // O cone de deriva (WM2) é desenhado no hook "draw" do uPlot, depois que o
  // gráfico termina de renderizar os dados do frame.
  const latencyOpts = buildLineOpts("latency-chart", ["p95 ms", "p99 ms"], ["#ff780a", "#f2495c"]);
  latencyOpts.hooks = {
    draw: [() => { try { drawLatencyCone(); } catch (e) { /* ignora erros de canvas */ } }],
  };
  latencyChart = new uPlot(latencyOpts, [[], [], []], document.getElementById("latency-chart"));
}

function initCompareCharts() {
  if (gaugeLeft) return;  // Já inicializado

  gaugeLeft  = echarts.init(document.getElementById("gauge-left"), "dark");
  gaugeRight = echarts.init(document.getElementById("gauge-right"), "dark");
  gaugeLeft.setOption(buildGaugeOption(0));
  gaugeRight.setOption(buildGaugeOption(0));

  cpuLeft  = new uPlot(buildLineOpts("cpu-left", ["CPU %"]), [[], []], document.getElementById("cpu-left"));
  cpuRight = new uPlot(buildLineOpts("cpu-right", ["CPU %"]), [[], []], document.getElementById("cpu-right"));
  latLeft  = new uPlot(buildLineOpts("lat-left",  ["p95 ms"]), [[], []], document.getElementById("lat-left"));
  latRight = new uPlot(buildLineOpts("lat-right", ["p95 ms"]), [[], []], document.getElementById("lat-right"));
}

function buildGaugeOption(value, eventLevel) {
  // Cor baseada no prediction.event_level (não no score bruto),
  // pois o score varia por cenário mas o nível semântico é universal
  let color;
  if (eventLevel === "prediction.alert") color = "#f2495c";
  else if (eventLevel === "prediction.warning") color = "#ff780a";
  else color = "#73bf69";
  return {
    backgroundColor: "transparent",
    series: [{
      type: "gauge",
      min: 0, max: 100,
      radius: "90%",
      axisLine: { lineStyle: { color: [[value / 100, color], [1, "#2a2d3a"]], width: 12 } },
      pointer: { length: "60%", width: 4, itemStyle: { color } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: { show: false },
      data: [{ value }],
    }],
  };
}

function buildLineOpts(containerId, seriesNames, colors) {
  const defaultColors = ["#73bf69", "#ff780a", "#f2495c", "#5c6bc0"];
  const el = document.getElementById(containerId);
  const w = el ? el.offsetWidth || 260 : 260;
  const h = el ? el.offsetHeight || 130 : 130;

  const seriesDef = [{}].concat(
    seriesNames.map((name, i) => ({
      label: name,
      stroke: (colors && colors[i]) || defaultColors[i],
      width: 1.5,
    }))
  );

  return {
    width: w,
    height: h,
    legend: { show: false },
    axes: [
      { stroke: "#555", ticks: { stroke: "#333" }, grid: { stroke: "#1e2130" } },
      { stroke: "#555", ticks: { stroke: "#333" }, grid: { stroke: "#1e2130" } },
    ],
    series: seriesDef,
    padding: [4, 8, 4, 8],
  };
}

// ------------------------------------------------------------------ //
// Processamento de frames normais                                     //
// ------------------------------------------------------------------ //
function handleTickFrame(frame) {
  updateHeader(frame);

  // Calcula tendência antes de atualizar gauge
  const delta = frame.risk_score - prevRiskScore;
  if (Math.abs(delta) > 1.5) {
    riskTrend = delta > 0 ? "rising" : "falling";
  } else {
    riskTrend = "stable";
  }
  prevRiskScore = frame.risk_score;

  updateGauge(gaugeChart, "risk-value", frame.risk_score, frame.prediction && frame.prediction.event_level);
  updateTrendIndicator(riskTrend, frame.risk_score, frame.phase);
  updateLineSeries(frame);
  updateLogs("log-stream", frame.recent_logs, frame.phase);
  updatePredictionCard(frame);
  updateTimeline(frame);

  // Animações WM
  checkWM1(frame);
  checkWM3(frame);
  checkWM2(frame);

  // Postmortem disponível?
  if (frame.phase === "RESOLVED") {
    document.getElementById("postmortem-btn").classList.add("visible");
  }

  prevPhase = frame.phase;
}

// ------------------------------------------------------------------ //
// Processamento de frames de comparação                               //
// ------------------------------------------------------------------ //
function handleCompareFrame(data) {
  const left = data.left;
  const right = data.right;

  // Atualiza header com frame esquerdo (preditivo)
  updateHeader(left);

  // Painéis esquerdos
  updateGauge(gaugeLeft, "risk-left", left.risk_score, left.prediction && left.prediction.event_level);
  updateCompareLineSeries(left, cpuLeft, latLeft, series.cpuL, series.latL);
  updateLogs("logs-left", left.recent_logs);

  // Painéis direitos
  updateGauge(gaugeRight, "risk-right", right.risk_score, right.prediction && right.prediction.event_level);
  updateCompareLineSeries(right, cpuRight, latRight, series.cpuR, series.latR);
  updateLogs("logs-right", right.recent_logs);

  // Lead time counter
  if (data.lead_time_seconds !== null && data.lead_time_seconds !== undefined) {
    updateLeadTimeCounter(data.lead_time_seconds);
  }

  // Warning: inicia contador se preditivo atingiu WARNING
  if (left.phase === "WARNING" && prevPhase !== "WARNING" && !leadTimeFinished) {
    startLeadTimeCounter();
  }

  // Tradicional atingiu WARNING — para contador
  if (right.phase === "WARNING" && leadTimeInterval) {
    stopLeadTimeCounter(data.lead_time_seconds);
  }

  prevPhase = left.phase;
}

// ------------------------------------------------------------------ //
// Update helpers                                                      //
// ------------------------------------------------------------------ //
function updateHeader(frame) {
  const badge = document.getElementById("phase-badge");
  const isRecovering = frame.phase === "INCIDENT" && riskTrend === "falling";

  if (isRecovering) {
    badge.textContent = "INCIDENT ↓";
    badge.className = `phase-badge ${frame.phase} recovering`;
  } else {
    badge.textContent = frame.phase;
    badge.className = `phase-badge ${frame.phase}`;
  }

  const scenarioEl = document.getElementById("scenario-name");
  if (scenarioEl) scenarioEl.textContent = frame.scenario_name || "";
}

function updateGauge(chart, valueElId, riskScore, eventLevel) {
  if (!chart) return;
  chart.setOption(buildGaugeOption(Math.round(riskScore), eventLevel));
  const el = document.getElementById(valueElId);
  if (!el) return;

  const prev = parseInt(el.textContent) || 0;
  const current = Math.round(riskScore);
  const diff = current - prev;

  el.textContent = current;
  el.className = "risk-value"
    + (eventLevel === "prediction.alert" ? " alert"
       : eventLevel === "prediction.warning" ? " warning" : "");

  // Delta indicator (apenas painel único)
  const deltaEl = document.getElementById(valueElId + "-delta");
  if (deltaEl && diff !== 0) {
    deltaEl.textContent = (diff > 0 ? "+" : "") + diff;
    deltaEl.className = "risk-delta" + (diff < 0 ? " falling" : " rising");
    deltaEl.style.opacity = "1";
    setTimeout(() => { deltaEl.style.opacity = "0.6"; }, 500);
  } else if (deltaEl && diff === 0) {
    deltaEl.textContent = "";
  }
}

function updateTrendIndicator(trend, score, phase) {
  const el = document.getElementById("risk-trend");
  if (!el) return;
  if (score < 5) { el.textContent = ""; el.className = "risk-trend"; return; }

  if (trend === "falling" && (phase === "INCIDENT" || phase === "ALERT")) {
    el.textContent = "↓ recovering";
    el.className = "risk-trend recovering";
  } else if (trend === "rising" && score >= 30) {
    el.textContent = "↑ worsening";
    el.className = "risk-trend worsening";
  } else if (trend === "stable" && score >= 60) {
    el.textContent = "— sustained";
    el.className = "risk-trend sustained";
  } else {
    el.textContent = "";
    el.className = "risk-trend";
  }
}

function updateLineSeries(frame) {
  const ts = Date.now() / 1000;
  series.ts.push(ts);
  series.cpu.push(frame.metrics["cpu_usage"] || 0);
  series.mem.push(frame.metrics["memory_usage"] || (frame.metrics["memory_used_mb"] || 0) / 10000);
  series.lat95.push(frame.metrics["latency_p95_ms"] || 0);
  series.lat99.push(frame.metrics["latency_p99_ms"] || 0);

  // Limita tamanho do buffer
  if (series.ts.length > MAX_POINTS) {
    series.ts.shift(); series.cpu.shift(); series.mem.shift();
    series.lat95.shift(); series.lat99.shift();
  }

  if (cpuChart)     cpuChart.setData([series.ts, series.cpu]);
  if (memChart)     memChart.setData([series.ts, series.mem]);
  if (latencyChart) latencyChart.setData([series.ts, series.lat95, series.lat99]);

}

function updateCompareLineSeries(frame, cpuC, latC, cpuBuf, latBuf) {
  const ts = Date.now() / 1000;
  const tsBuf = cpuBuf._ts || (cpuBuf._ts = []);
  tsBuf.push(ts);
  cpuBuf.push(frame.metrics["cpu_usage"] || 0);
  latBuf.push(frame.metrics["latency_p95_ms"] || 0);
  if (tsBuf.length > MAX_POINTS) { tsBuf.shift(); cpuBuf.shift(); latBuf.shift(); }

  if (cpuC) cpuC.setData([tsBuf, cpuBuf]);
  if (latC) latC.setData([tsBuf, latBuf]);
}

function updateLogs(containerId, logs, phase) {
  if (!logs || !logs.length) return;
  const el = document.getElementById(containerId);
  if (!el) return;

  // Separador visual quando a fase muda (apenas no painel principal)
  if (phase && phase !== _lastLogPhase && containerId === "log-stream") {
    _lastLogPhase = phase;
    const sep = document.createElement("div");
    sep.className = "log-separator";
    sep.textContent = `── ${phase} ──`;
    el.appendChild(sep);
  }

  logs.slice(-5).forEach(log => {
    const div = document.createElement("div");
    div.className = `log-entry log-${log.level}`;
    const ts = (log.timestamp || "").substring(11, 19);
    div.textContent = `[${ts}] ${log.level} ${log.service}: ${log.message}`;
    el.appendChild(div);
  });

  // Mantém apenas os últimos 25 (espaço extra para separadores)
  while (el.children.length > 25) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

function updatePredictionCard(frame) {
  const pred = frame.prediction;
  const levelEl  = document.getElementById("pred-level");
  const etaEl    = document.getElementById("pred-eta");
  const sigEl    = document.getElementById("pred-signals");
  if (!levelEl) return;

  if (!pred) {
    levelEl.style.color = "var(--text-muted)";
    levelEl.textContent = "— No Prediction —";
    etaEl.textContent = "";
    sigEl.textContent = "";
    return;
  }

  const colors = { "prediction.alert": "#f2495c", "prediction.warning": "#ff780a" };
  levelEl.style.color = colors[pred.event_level] || "#e0e0e0";
  levelEl.textContent = pred.event_level || "—";
  etaEl.textContent = pred.eta_minutes != null ? `ETA: ~${pred.eta_minutes.toFixed(1)}min` : "";
  sigEl.textContent = pred.signals_fired ? `Signals: ${pred.signals_fired.join(", ")}` : "";

  // Sincroniza banner e eta-marker com o ETA em tempo real (evita valor congelado
  // do momento da primeira transição WARNING)
  if (pred.eta_minutes != null) {
    const etaStr = pred.eta_minutes.toFixed(1);
    const bannerEta = document.getElementById("banner-eta");
    if (bannerEta) bannerEta.textContent = etaStr;
    const etaMarker = document.getElementById("eta-marker");
    if (etaMarker && etaMarker.style.opacity !== "0") {
      etaMarker.textContent = `⏱ ETA ~${etaStr}min`;
    }
  }
}

function updateTimeline(frame) {
  // Adiciona item de timeline quando fase muda
  if (frame.phase === prevPhase) return;
  const list = document.getElementById("timeline-list");
  if (!list) return;

  const item = document.createElement("div");
  item.className = "timeline-item";
  item.innerHTML = `
    <div class="timeline-dot ${frame.phase}"></div>
    <span>${frame.phase} — ${(frame.sim_time || "").substring(11, 19)}</span>
  `;
  list.appendChild(item);
  list.scrollTop = list.scrollHeight;
}

// ------------------------------------------------------------------ //
// WM1 — Prediction Cascade Animation                                  //
// ------------------------------------------------------------------ //
function checkWM1(frame) {
  if (frame.phase === "WARNING" && prevPhase !== "WARNING" && !predictionFired) {
    predictionFired = true;
    handlePredictionWarning(frame);
  }

  // Transição ALERT → INCIDENT: predição confirmada, dismiss banner
  if (frame.phase === "INCIDENT" && prevPhase === "ALERT") {
    dismissBannerAsConfirmed();
  }

  // Reset ao voltar para IDLE/PLAYING
  if (frame.phase === "IDLE" || frame.phase === "PLAYING") {
    if (predictionFired) {
      resetWM1();
      predictionFired = false;
      riskTrend = "stable";
      prevRiskScore = 0;
      _lastLogPhase = "IDLE";
      // Limpa indicadores
      const trendEl = document.getElementById("risk-trend");
      if (trendEl) { trendEl.textContent = ""; trendEl.className = "risk-trend"; }
      const deltaEl = document.getElementById("risk-value-delta");
      if (deltaEl) { deltaEl.textContent = ""; }
    }
  }
}

function dismissBannerAsConfirmed() {
  const banner = document.getElementById("prediction-banner");
  if (!banner) return;
  // Muda texto para confirmar predição
  banner.innerHTML = "✓ Prediction confirmed — incident detected as predicted";
  banner.style.borderBottomColor = "#f2495c";
  banner.style.background = "linear-gradient(90deg, #2a0000, #f2495c22)";
  // Desliza para fora após 3 segundos
  if (typeof gsap !== "undefined") {
    gsap.to(banner, { top: "-80px", duration: 0.4, delay: 3.0 });
  } else {
    setTimeout(() => { banner.style.top = "-80px"; }, 3000);
  }
}

function handlePredictionWarning(frame) {
  const pred = frame.prediction;
  const eta  = pred && pred.eta_minutes != null ? pred.eta_minutes.toFixed(1) : "?";

  // Fallback CSS puro se GSAP não carregou
  if (typeof gsap === "undefined") {
    showBannerCSSOnly(eta);
    return;
  }

  const banner = document.getElementById("prediction-banner");
  document.getElementById("banner-eta").textContent = eta;

  const tl = gsap.timeline();

  // 1. Banner desliza de cima
  tl.to(banner, { top: "0px", duration: 0.4, ease: "power2.out" }, 0);

  // 2. Glow na borda do gauge
  tl.to("#gauge-chart", {
    boxShadow: "0 0 20px 4px #ff780a",
    duration: 0.3,
  }, 0.2);

  // 3. Zona de anomalia nos charts CPU/Memory
  tl.to("#anomaly-cpu, #anomaly-mem", {
    opacity: 1, duration: 0.5,
  }, 0.3);

  // 4. Marcador ETA na timeline
  const etaMarker = document.getElementById("eta-marker");
  if (etaMarker) {
    etaMarker.textContent = `⏱ ETA ~${eta}min`;
    tl.to(etaMarker, { opacity: 1, duration: 0.3 }, 0.5);
  }

  // 5. Cards RAG (fade in com stagger — serão inseridos pelo WM3)
  tl.to(".rag-card", { opacity: 1, y: 0, duration: 0.4, stagger: 0.15 }, 0.7);
}

function showBannerCSSOnly(eta) {
  const banner = document.getElementById("prediction-banner");
  document.getElementById("banner-eta").textContent = eta;
  banner.classList.add("visible");
}

function resetWM1() {
  if (typeof gsap !== "undefined") {
    gsap.to("#prediction-banner", { top: "-80px", duration: 0.3 });
    gsap.to("#gauge-chart", { boxShadow: "none", duration: 0.2 });
    gsap.to("#anomaly-cpu, #anomaly-mem", { opacity: 0, duration: 0.3 });
    gsap.to("#eta-marker", { opacity: 0, duration: 0.2 });
  } else {
    const banner = document.getElementById("prediction-banner");
    banner.classList.remove("visible");
  }
}

// ------------------------------------------------------------------ //
// WM2 — Latency Drift Cone Projection                                 //
// ------------------------------------------------------------------ //
let _latencyConePlugin = null;
let _coneData = null;

function checkWM2(frame) {
  const signals = frame.signals || {};
  if (signals.latency_drift_detected && signals.slope_ms_per_min) {
    const currentP95 = frame.metrics["latency_p95_ms"] || 0;
    _coneData = {
      currentP95,
      slope: signals.slope_ms_per_min,
      simNowSec: frame.elapsed_sim_seconds,
    };
  } else {
    _coneData = null;
  }
  // O cone é desenhado pelo hook "draw" do gráfico no próximo setData().
  // Não chamar redraw() aqui: ele roda antes do commit do setData() em lote
  // e deixa a escala X sem valores, o que apaga as linhas do gráfico.
}

function drawLatencyCone() {
  // Cone é desenhado via uPlot draw hook — configurado no buildLineOpts
  // para simplicidade, desenhamos sobre o canvas via over element
  if (!latencyChart || !_coneData) return;
  // uPlot: ctx é o CanvasRenderingContext2D diretamente; .over é um <div> de mouse overlay
  const ctx = latencyChart.ctx;
  if (!ctx) return;

  const { currentP95, slope } = _coneData;
  const u = latencyChart;
  const pxPerSec = u.width / (u.scales.x.max - u.scales.x.min || 1);

  const x0 = u.width - 1;  // Ponto atual (borda direita)
  const y0 = u.valToPos(currentP95, "y");

  // Minuto 2 e minuto 5 à frente
  const dt2min = 2 * 60;
  const dt5min = 5 * 60;
  const p95at2 = currentP95 + slope * 2;
  const p95at5 = currentP95 + slope * 5;

  const x2 = x0 + dt2min * pxPerSec;
  const y2 = u.valToPos(p95at2, "y");
  const x5 = x0 + dt5min * pxPerSec;
  const y5 = u.valToPos(p95at5, "y");

  ctx.save();
  // Trapézio amarelo (0–2min)
  ctx.beginPath();
  ctx.moveTo(x0, y0); ctx.lineTo(x2, y2 - 10);
  ctx.lineTo(x2, y2 + 10); ctx.lineTo(x0, y0);
  ctx.fillStyle = "rgba(245, 166, 35, 0.20)";
  ctx.fill();

  // Trapézio vermelho (2–5min)
  ctx.beginPath();
  ctx.moveTo(x2, y2 - 10); ctx.lineTo(x5, y5 - 20);
  ctx.lineTo(x5, y5 + 20); ctx.lineTo(x2, y2 + 10);
  ctx.fillStyle = "rgba(242, 73, 92, 0.20)";
  ctx.fill();
  ctx.restore();
}

// ------------------------------------------------------------------ //
// WM3 — RAG Match Cards Slide-Up                                      //
// ------------------------------------------------------------------ //
function checkWM3(frame) {
  const pred = frame.prediction;
  if (!pred || !pred.matched_history || pred.matched_history.length === 0) return;
  if (ragCardsFired) return;

  ragCardsFired = true;
  showRagCards(pred.matched_history);
}

function showRagCards(matchedHistory) {
  const container = document.getElementById("rag-cards");
  if (!container) return;
  container.innerHTML = "";  // Limpa cards anteriores

  const cards = matchedHistory.slice(0, 3);
  cards.forEach(match => {
    const card = document.createElement("div");
    card.className = "rag-card";
    const score = match.similarity_score != null ? `${Math.round(match.similarity_score * 100)}%` : "—";
    const date  = match.incident_id || match.date || "—";
    const cause = match.root_cause || match.cause || "—";
    const fix   = match.fix || "—";

    card.innerHTML = `
      <span class="match-score">${score} match</span>
      <span class="match-date">${date}</span>
      <p class="root-cause">${cause}</p>
      <p class="fix">Fix: ${fix}</p>
    `;
    container.appendChild(card);
  });

  if (typeof gsap !== "undefined") {
    gsap.from(".rag-card", { y: 100, opacity: 0, stagger: 0.15, duration: 0.4, ease: "power2.out" });
  } else {
    // Fallback: mostra diretamente
    document.querySelectorAll(".rag-card").forEach(c => {
      c.style.opacity = "1"; c.style.transform = "translateY(0)";
    });
  }
}

function hideRagCards() {
  if (typeof gsap !== "undefined") {
    gsap.to(".rag-card", { y: 100, opacity: 0, duration: 0.3, onComplete: () => {
      const container = document.getElementById("rag-cards");
      if (container) container.innerHTML = "";
    }});
  } else {
    const container = document.getElementById("rag-cards");
    if (container) container.innerHTML = "";
  }
  ragCardsFired = false;
}

// ------------------------------------------------------------------ //
// Comparison Mode                                                     //
// ------------------------------------------------------------------ //
function toggleCompare() {
  compareMode = !compareMode;
  const btn = document.getElementById("compare-btn");
  const single = document.getElementById("single-panel");
  const left   = document.getElementById("compare-left");
  const right  = document.getElementById("compare-right");
  const ltCounter = document.getElementById("lead-time-counter");
  const body = document.body;

  if (compareMode) {
    btn.classList.add("active");
    single.style.display = "none";
    left.style.display = "block";
    right.style.display = "block";
    body.classList.add("compare-mode");
    ltCounter.classList.remove("hidden");
    initCompareCharts();
    fetch("/demo/compare/enable", { method: "POST" });
    // Reinicia séries de comparação
    series.cpuL = []; series.latL = [];
    series.cpuR = []; series.latR = [];
    leadTimeFinished = false;
  } else {
    btn.classList.remove("active");
    single.style.display = "block";
    left.style.display = "none";
    right.style.display = "none";
    body.classList.remove("compare-mode");
    ltCounter.classList.add("hidden");
    fetch("/demo/compare/disable", { method: "POST" });
    if (leadTimeInterval) clearInterval(leadTimeInterval);
  }
}

// ------------------------------------------------------------------ //
// Lead Time Counter                                                   //
// ------------------------------------------------------------------ //
function startLeadTimeCounter() {
  leadTimeStartWall = Date.now();
  if (leadTimeInterval) clearInterval(leadTimeInterval);
  leadTimeInterval = setInterval(() => {
    const elapsed = (Date.now() - leadTimeStartWall) / 1000;
    updateLeadTimeCounter(elapsed);
  }, 500);
}

function stopLeadTimeCounter(finalSeconds) {
  if (leadTimeInterval) clearInterval(leadTimeInterval);
  leadTimeInterval = null;
  leadTimeFinished = true;
  if (finalSeconds != null) updateLeadTimeCounter(finalSeconds);
}

function updateLeadTimeCounter(seconds) {
  const el = document.getElementById("lead-time-counter");
  if (!el) return;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  el.textContent = `🏆 Lead time: ${m}m ${s}s ahead`;
}

// ------------------------------------------------------------------ //
// Controles de demo                                                   //
// ------------------------------------------------------------------ //
function startDemo() {
  const scenario = document.getElementById("scenario-select").value;
  const speed    = parseFloat(document.getElementById("speed-select").value);

  fetch(`/demo/start/${scenario}`, { method: "POST" })
    .then(() => fetch("/demo/speed", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed_factor: speed }),
    }))
    .then(() => {
      document.getElementById("start-btn").style.display = "none";
      document.getElementById("stop-btn").style.display  = "inline-block";
      document.getElementById("postmortem-btn").classList.remove("visible");
      // Limpa série e animações anteriores
      resetSeries();
      resetWM1();
      hideRagCards();
      prevPhase = "IDLE";
      predictionFired = false;
    });
}

function stopDemo() {
  fetch("/demo/stop", { method: "POST" }).then(() => {
    document.getElementById("start-btn").style.display = "inline-block";
    document.getElementById("stop-btn").style.display  = "none";
  });
}

function resetSeries() {
  Object.keys(series).forEach(k => { series[k] = []; });
  if (document.getElementById("log-stream"))
    document.getElementById("log-stream").innerHTML = "";
  if (document.getElementById("timeline-list"))
    document.getElementById("timeline-list").innerHTML = "";
}

// ------------------------------------------------------------------ //
// Speed Selector                                                      //
// ------------------------------------------------------------------ //
document.addEventListener("DOMContentLoaded", () => {
  const sel = document.getElementById("speed-select");
  if (sel) {
    sel.addEventListener("change", () => {
      const speed = parseFloat(sel.value);
      fetch("/demo/speed", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speed_factor: speed }),
      });
    });
  }
});

// ------------------------------------------------------------------ //
// Postmortem export                                                   //
// ------------------------------------------------------------------ //
function downloadPostmortem() {
  fetch("/demo/postmortem")
    .then(res => {
      if (res.status === 204) {
        alert("Postmortem not yet available — scenario must reach RESOLVED.");
        return null;
      }
      return res.text();
    })
    .then(text => {
      if (!text) return;
      const blob = new Blob([text], { type: "text/markdown" });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url; a.download = "postmortem.md"; a.click();
      URL.revokeObjectURL(url);
    });
}

// ------------------------------------------------------------------ //
// Bootstrap                                                           //
// ------------------------------------------------------------------ //
document.addEventListener("DOMContentLoaded", () => {
  initCharts();
  connectWS();
});
