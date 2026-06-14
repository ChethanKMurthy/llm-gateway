/* ============================================================================
   Landing — motion & dynamic content. Vanilla, dependency-free.
   ============================================================================ */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const cssv = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

/* ── nav shadow on scroll ─────────────────────────────────────────────── */
const nav = $("#nav");
const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 8);
addEventListener("scroll", onScroll, { passive: true }); onScroll();

/* ── scroll reveals (robust: never leave content hidden) ──────────────── */
const io = new IntersectionObserver((entries) => {
  entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
}, { threshold: 0.14, rootMargin: "0px 0px -6% 0px" });
const inView = (el) => { const r = el.getBoundingClientRect(); return r.top < innerHeight * 0.96 && r.bottom > 0; };
$$(".reveal").forEach((el) => { if (inView(el)) el.classList.add("in"); else io.observe(el); });
// safety net: if anything is still hidden after 1.6s (e.g. observer never fired), show it
setTimeout(() => $$(".reveal:not(.in)").forEach((el) => { if (inView(el)) el.classList.add("in"); }), 1600);

/* ── 3D pointer tilt ──────────────────────────────────────────────────── */
function tilt(el, max = 8) {
  if (reduceMotion) return;
  const base = el.classList.contains("hero-stage")
    ? { ry: -7, rx: 3 } : el.classList.contains("proof-shot") ? { ry: 4, rx: 0 } : { ry: 0, rx: 0 };
  el.addEventListener("pointermove", (e) => {
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    el.style.transform = `perspective(1500px) rotateY(${base.ry + px * max}deg) rotateX(${base.rx - py * max}deg)`;
  });
  el.addEventListener("pointerleave", () => { el.style.transform = ""; });
}
$$(".hero-stage, .proof-shot").forEach((el) => tilt(el));

/* ── the pipeline flow chart ──────────────────────────────────────────── */
const STAGES = [
  ["Security", "scan: secrets · injection · PII", "guard"],
  ["Classify", "intent → code · 98% confidence", "model"],
  ["Optimize", "trim dead tokens · −18%", "tokens"],
  ["Cache", "L1 exact → L2 semantic", "lookup"],
  ["Predict", "cost & p95 for each candidate", "forecast"],
  ["Route", "bandit picks the best model", "learn"],
  ["Call", "+ health-aware fallback", "provider"],
  ["Quality", "relevance · halluc · score 0.93", "judge"],
  ["Learn", "reward → router; cache; adapt", "feedback"],
];
function buildPipeline() {
  const el = $("#pipeline");
  el.innerHTML =
    `<div class="pipeline-rail"></div><div class="pipeline-packet" id="packet"></div>` +
    STAGES.map(([name, detail, tag], i) => `
      <div class="stage-row" role="listitem" data-i="${i}">
        <div class="stage-dot"></div>
        <div>
          <div class="stage-main">
            <span class="stage-num">${String(i + 1).padStart(2, "0")}</span>
            <span class="stage-name">${name}</span>
          </div>
          <span class="stage-detail">${detail}</span>
        </div>
        <span class="stage-tag">${tag}</span>
      </div>`).join("");

  const rows = $$(".stage-row", el);
  const packet = $("#packet");
  // hover lights a stage
  rows.forEach((r) => r.addEventListener("pointerenter", () => { stopAuto(); light(+r.dataset.i); }));

  let cur = 0, timer = null;
  function light(i) {
    rows.forEach((r, k) => r.classList.toggle("lit", k === i));
    const r = rows[i];
    const top = r.offsetTop + r.offsetHeight / 2;
    packet.style.top = top + "px";
    cur = i;
  }
  function step() { light((cur + 1) % rows.length); }
  function startAuto() { if (reduceMotion) { rows.forEach((r) => r.classList.add("lit")); return; } stopAuto(); timer = setInterval(step, 1100); }
  function stopAuto() { if (timer) clearInterval(timer); timer = null; }

  // start when the pipeline scrolls into view
  const pio = new IntersectionObserver((es) => {
    es.forEach((e) => { if (e.isIntersecting) { light(0); startAuto(); } else stopAuto(); });
  }, { threshold: 0.25 });
  pio.observe(el);
  $("#pipeline").addEventListener("pointerleave", startAuto);
  // expose for resize re-measure
  window.__relightPipeline = () => light(cur);
}
buildPipeline();
addEventListener("resize", () => window.__relightPipeline && window.__relightPipeline());

/* ── routing viz (intent → favoured model, cheaper than frontier) ─────── */
const ROUTING_FALLBACK = [
  ["code", "claude-sonnet-4-6", 62], ["reasoning", "claude-opus-4-8", 18],
  ["summarization", "llama-3.1-8b-instant", 96], ["qa", "llama-3.1-8b-instant", 97],
  ["translation", "gemini-2.5-flash", 92], ["chat", "claude-haiku-4-5", 88],
];
async function buildRouting() {
  let rows = ROUTING_FALLBACK;
  try {
    const pol = await fetch("/api/policy").then((r) => r.json());
    const entries = Object.entries(pol.policy || {});
    if (entries.length) {
      const order = ["code", "reasoning", "math", "summarization", "qa", "translation", "chat", "rag", "classification"];
      rows = entries.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0])).slice(0, 6)
        .map(([intent, p]) => [intent, p.best_model, Math.round(60 + p.expected_reward * 38)]);
    }
  } catch {}
  const el = $("#routing-viz");
  el.innerHTML = rows.map(([intent, model, pct]) => `
    <div class="rv-row">
      <span class="rv-intent">${intent}</span>
      <span class="rv-track"><span class="rv-fill" data-w="${pct}"></span></span>
      <span class="rv-model">${model} <span class="save">·${pct}%↓</span></span>
    </div>`).join("");
  // animate bars when visible
  const rio = new IntersectionObserver((es) => {
    es.forEach((e) => { if (e.isIntersecting) {
      $$(".rv-fill", el).forEach((f) => { f.style.width = f.dataset.w + "%"; });
      rio.disconnect();
    }});
  }, { threshold: 0.4 });
  rio.observe(el);
}
buildRouting();

/* ── capabilities index ───────────────────────────────────────────────── */
const CAPS = [
  ["Dynamic model routing", "Per-request model choice from a learned policy.", "router.py"],
  ["Multi-level cache", "L1 exact hash → L2 semantic vector search.", "cache.py"],
  ["Prompt classification", "Intent in 9 classes, full distribution.", "classifier.py"],
  ["Cost prediction", "Dollar cost per candidate before any call.", "cost.py"],
  ["Latency prediction", "p50 / p95 / p99 from a log-normal model.", "cost.py"],
  ["Smart fallback", "Health-aware candidate chain, zero downtime.", "providers.py"],
  ["Prompt fingerprinting", "Hash + embedding + intent + cost, stored.", "cache.py"],
  ["Adaptive thresholds", "Per-intent cache strictness that self-tunes.", "cache.py"],
  ["Quality scoring", "Relevance, completeness, hallucination risk.", "quality.py"],
  ["Cost dashboard", "Spend, savings, attribution, projection — live.", "console"],
  ["Security guardrails", "Injection scoring, PII redaction, secret block.", "security.py"],
  ["Token optimization", "Lossless-ish prompt compression, banked.", "optimizer.py"],
  ["Replay cache", "Generated answers cached and replayed.", "cache.py"],
  ["Observability", "OTel-shaped spans streamed over SSE.", "metrics.py"],
  ["RL router", "Thompson-sampling bandit that learns online.", "router.py"],
];
$("#cap-index").innerHTML = CAPS.map(([h, p, f]) => `
  <li class="cap-item"><div class="cap-body">
    <h3>${h} <span class="file">${f}</span></h3><p>${p}</p>
  </div></li>`).join("");

/* ── live hero stats + footer mode ────────────────────────────────────── */
(async () => {
  try {
    const s = await fetch("/api/summary").then((r) => r.json());
    if (s.total_requests > 0) {
      $("#hs-saved").textContent = Math.round(s.savings_pct) + "%";
      $("#hs-hit").textContent = Math.round(s.hit_rate * 100) + "%";
      $("#hs-p50").textContent = (s.latency.p50 < 5 ? "~1" : Math.round(s.latency.p50)) + "ms";
    } else { setStaticStats(); }
    const live = Object.values(s.mode || {}).some((m) => m === "live");
    $("#footer-mode").textContent = live ? "live providers" : "simulated fleet";
  } catch { setStaticStats(); }
})();
function setStaticStats() {
  $("#hs-saved").textContent = "90%+"; $("#hs-hit").textContent = "20–45%"; $("#hs-p50").textContent = "~1ms";
}

/* ── hero canvas: requests routing through the gateway ────────────────── */
(function heroCanvas() {
  const canvas = $("#hero-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let W, H, dpr, nodeX, packets = [], lanes = [], t = 0, raf;
  const clay = cssv("--clay"), glow = cssv("--clay-glow"), warm = "#e9d9cf";
  const onDeep3 = cssv("--on-deep-3"), green = cssv("--green");

  function resize() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = r.height;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    nodeX = W * 0.5;
    lanes = ["Anthropic", "Groq", "OpenAI", "Google"].map((name, i, a) => ({
      name, y: H * (0.22 + (i / (a.length - 1)) * 0.56), x: W * 0.9,
    }));
  }
  resize(); addEventListener("resize", resize);

  function spawn() {
    const y = H * (0.18 + Math.random() * 0.64);
    packets.push({ x: -10, y, sy: y, phase: "in", lane: null, hit: Math.random() < 0.42, life: 0 });
  }

  function draw() {
    t += 1;
    ctx.clearRect(0, 0, W, H);

    // provider lanes
    lanes.forEach((l) => {
      ctx.strokeStyle = onDeep3 + "44"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(nodeX, H / 2); ctx.lineTo(l.x, l.y); ctx.stroke();
      ctx.fillStyle = onDeep3; ctx.beginPath(); ctx.arc(l.x, l.y, 3.2, 0, 7); ctx.fill();
      ctx.font = '500 10px "JetBrains Mono", monospace'; ctx.fillStyle = onDeep3;
      ctx.textAlign = "left"; ctx.fillText(l.name, l.x + 8, l.y + 3.5);
    });

    // gateway node (rounded square, breathing)
    const pulse = 1 + Math.sin(t * 0.05) * 0.04;
    const s = 34 * pulse;
    ctx.save(); ctx.translate(nodeX, H / 2);
    ctx.shadowColor = glow; ctx.shadowBlur = 26;
    roundRect(ctx, -s / 2, -s / 2, s, s, 9); ctx.fillStyle = clay; ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = "#2a1a12"; roundRect(ctx, -7, -7, 14, 14, 4); ctx.fill();
    ctx.restore();
    ctx.font = '600 10px "JetBrains Mono", monospace'; ctx.fillStyle = warm;
    ctx.textAlign = "center"; ctx.fillText("GATEWAY", nodeX, H / 2 + 36);

    if (t % 26 === 0 && packets.length < 26) spawn();

    packets.forEach((p) => {
      p.life++;
      if (p.phase === "in") {
        p.x += (nodeX - p.x) * 0.06;
        if (Math.abs(p.x - nodeX) < 4) {
          if (p.hit) { p.phase = "return"; }
          else { p.phase = "out"; p.lane = lanes[(Math.random() * lanes.length) | 0]; p.flash = 6; }
        }
      } else if (p.phase === "out") {
        p.x += (p.lane.x - p.x) * 0.07; p.y += (p.lane.y - p.y) * 0.07;
        if (Math.abs(p.x - p.lane.x) < 5) p.phase = "back";
      } else if (p.phase === "back") {
        p.x += (-20 - p.x) * 0.05; p.y += (p.sy - p.y) * 0.05;
        if (p.x < -8) p.dead = true;
      } else if (p.phase === "return") { // cache hit: bounce straight back, fast
        p.x += (-20 - p.x) * 0.10;
        if (p.x < -8) p.dead = true;
      }
      // draw
      const c = p.phase === "return" ? glow : warm;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.hit ? 3.6 : 3, 0, 7);
      ctx.fillStyle = c; ctx.globalAlpha = 0.9; ctx.fill(); ctx.globalAlpha = 1;
      // trailing
      ctx.beginPath(); ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x - (p.phase === "in" ? 14 : 8), p.y); ctx.strokeStyle = c + "55"; ctx.lineWidth = 2; ctx.stroke();
    });
    // cache-hit ring flash at node
    packets.forEach((p) => {
      if (p.phase === "return" && p.life < 40 && Math.abs(p.x - nodeX) < 30) {
        const a = 1 - (p.life % 40) / 40;
        ctx.beginPath(); ctx.arc(nodeX, H / 2, 24 * (1 - a) + 16, 0, 7);
        ctx.strokeStyle = glow + Math.floor(a * 120).toString(16).padStart(2, "0"); ctx.lineWidth = 2; ctx.stroke();
      }
    });
    packets = packets.filter((p) => !p.dead);
    raf = requestAnimationFrame(draw);
  }
  function roundRect(c, x, y, w, h, r) { c.beginPath(); c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); }

  if (reduceMotion) {
    // static frame: a few packets + node
    for (let i = 0; i < 6; i++) spawn();
    draw(); cancelAnimationFrame(raf);
  } else {
    // pause when offscreen
    const vio = new IntersectionObserver((es) => {
      es.forEach((e) => { if (e.isIntersecting) { if (!raf) draw(); } else { cancelAnimationFrame(raf); raf = null; } });
    }, { threshold: 0.05 });
    vio.observe(canvas);
  }
})();
