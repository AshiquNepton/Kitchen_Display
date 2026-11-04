const B = document.body, $ = s => document.querySelector(s);
const REFRESH = +B.dataset.refresh * 1000, WARN = +B.dataset.warn * 60, LATE = +B.dataset.late * 60, DELAYED = +B.dataset.delayed * 60;
const TYPES = ["All", "Dine In", "Take Away", "Delivery", "Online"];
const TYPE_IC = {"All": "grid", "Dine In": "utensils", "Take Away": "bag", "Delivery": "truck", "Online": "globe"};
const NAME_IC = {"Dine In": "table"};  // everything else shows a customer (user) icon
let loaded = false, orders = [], items = [], tab = "pending", type = "All", query = "", fetchedAt = performance.now(), current = null;
let knownPendingInvs = new Set(), knownOrderCounts = new Map(), skipSoundOnNextLoad = false;

// ---- Category filter state (per screen / device) ----
let allCategories = [];
let selectedCategories = [];

try {
  const cd = document.getElementById("cat-data");
  if (cd && cd.textContent) allCategories = JSON.parse(cd.textContent);
} catch (e) {}

let serverStationCats = [];
try {
  const sc = document.getElementById("station-cat-data");
  if (sc && sc.textContent) serverStationCats = JSON.parse(sc.textContent).map(Number).filter(Boolean);
} catch (e) {}

// Priority 1: URL parameters support (e.g. ?categories=1275 or ?cat=1275)
let hasUrlCat = false;
try {
  const urlParams = new URLSearchParams(window.location.search);
  const param = urlParams.get("categories") || urlParams.get("cat");
  if (param !== null) {
    selectedCategories = param.split(",").map(Number).filter(Boolean);
    try { localStorage.setItem("kds_categories", JSON.stringify(selectedCategories)); } catch (e) {}
    hasUrlCat = true;
  }
} catch (e) {}

// Priority 2: localStorage (client preference on this screen)
if (!hasUrlCat) {
  try {
    const saved = localStorage.getItem("kds_categories");
    if (saved !== null) {
      selectedCategories = JSON.parse(saved).map(Number).filter(Boolean);
    } else if (serverStationCats.length) {
      // Priority 3: Server default station categories from kds_config.json
      selectedCategories = serverStationCats;
      try { localStorage.setItem("kds_categories", JSON.stringify(selectedCategories)); } catch (e) {}
    }
  } catch (e) {
    if (serverStationCats.length) selectedCategories = serverStationCats;
  }
}

function catQuery() {
  return selectedCategories.length ? `categories=${selectedCategories.join(",")}` : "";
}

function updateCatBtn() {
  const btn = $("#cat-btn"), label = $("#cat-label");
  if (!btn || !label) return;
  const isAll = !selectedCategories.length;
  if (isAll) {
    label.textContent = "All Categories";
    btn.classList.remove("filtered");
    btn.title = "Category Filter: Showing all valid categories. Click to select specific categories.";
  } else if (selectedCategories.length === 1) {
    const c = allCategories.find(x => x.id === selectedCategories[0]);
    const name = c ? c.name : `Cat #${selectedCategories[0]}`;
    label.textContent = name;
    btn.classList.add("filtered");
    btn.title = `Category Filter: Showing ${name} only. Click to change.`;
  } else {
    label.textContent = `${selectedCategories.length} Categories`;
    btn.classList.add("filtered");
    btn.title = `Category Filter: Showing ${selectedCategories.length} categories. Click to change.`;
  }
}

updateCatBtn();

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const csrf = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1];
const p2 = n => String(n).padStart(2, "0");
const fmt = s => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return (h ? h + ":" : "") + p2(m) + ":" + p2(s % 60); };
const clock = iso => iso ? new Date(iso).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
const state = o => o.kds_done || (o.total > 0 && o.done === o.total) ? "done" : o.done > 0 ? "partial" : "pending";
const notes = t => t ? `<div class="notes" dir="auto">${esc(t).replace(/(Size|Addon|Notes):/g, "<br><em>$1:</em>").replace(/^<br>/, "")}</div>` : "";

async function api(url, body) {
  const r = await fetch(url, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify(body)});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail ? `${d.error}: ${d.detail}` : (d.error || r.statusText));
  return d;
}

function card(o, n) {
  const st = state(o);
  return `<article class="card ${st}" data-inv="${o.inv}">
    <div class="top">
      <span class="left">
        <span class="num" title="Queue position">#${n}</span>
        <b class="card-inv-no">Inv #${o.inv}</b>
        <span class="badge">${ic(TYPE_IC[o.type] || "grid")}${esc(o.type)}</span>
      </span>
      ${st === "done" ? `<span class="ok">${ic("check-circle")}Done</span>` : `<span class="tm">${ic("clock")}<span class="timer" data-e="${o.elapsed}"></span></span>`}
    </div>
    <div class="card-cust-row">
      <span class="card-cust">${esc(o.name || o.type)}</span>
      <span class="card-time-badge">${clock(o.ordered_at)}</span>
    </div>
    <div class="bar"><i style="width:${o.total ? o.done / o.total * 100 : 0}%"></i></div>
    <div class="meta">${ic("list")}${o.done}/${o.total} items done<span class="late-flag">${ic("alert")}Delayed</span></div>
  </article>`;
}

function render() {
  const pend = orders.filter(o => !o.kds_done), comp = orders.filter(o => o.kds_done).reverse();
  $("#c-pending").textContent = pend.length; $("#c-completed").textContent = comp.length;
  const list = tab === "pending" ? pend : comp, q = query.toLowerCase();
  // queue position within the whole tab (oldest = 1); when an order leaves the list the rest renumber
  const pos = new Map(list.map((o, i) => [o.inv, i + 1]));
  $("#types").innerHTML = TYPES.map(t => `<button data-type="${t}" title="${t}" class="${t === type ? "on" : ""}"><span class="bt">${ic(TYPE_IC[t])}<b>${t === "All" ? list.length : list.filter(o => o.type === t).length}</b></span><small>${t}</small></button>`).join("");
  const shown = list.filter(o => (type === "All" || o.type === type) && (!q || `${o.inv} ${o.day_ref} ${o.name} ${o.waiter}`.toLowerCase().includes(q)));
  $("#grid").innerHTML = shown.length ? shown.map(o => card(o, pos.get(o.inv))).join("") : `<p class="empty">No ${tab} orders${type !== "All" ? " for " + type : ""}.</p>`;
  fillIcons($("#grid"));
  renderSummary(pend);
  tick();
}

function renderSummary(pend) {
  const body = $("#summary-body");
  const badge = $("#summary-badge");
  if (!body) return;

  // 1. Calculate active uncompleted items total
  let totalPendingItems = 0;
  pend.forEach(o => {
    (o.items || []).forEach(it => {
      if (!it.done) {
        totalPendingItems += (it.qty || 1);
      }
    });
  });

  if (badge) {
    badge.textContent = `${totalPendingItems} item${totalPendingItems === 1 ? "" : "s"}`;
  }

  // If no pending orders at all
  if (!pend.length) {
    body.innerHTML = `
      <div class="sum-card current empty-card">
        <div class="sum-card-head">
          <span class="sum-tag current-tag">${ic("check-circle")} ALL DONE</span>
        </div>
        <div class="sum-no-order">Kitchen queue clear · No active orders</div>
      </div>
    `;
    return;
  }

  // Render ALL pending invoices with their items ALWAYS DISPLAYED (no chip-clicking required!)
  let html = "";
  pend.forEach((o, idx) => {
    const isTop = idx === 0;
    const itList = o.items || [];
    const itemsHtml = itList.length ? itList.map(it => `
      <div class="sum-item-row ${it.done ? "item-done" : ""}" data-inv="${o.inv}" data-sl="${it.slno || ""}" title="Click to toggle done">
        <div class="sum-item-main">
          <span class="sum-item-qty">&times;${it.qty}</span>
          <div class="sum-item-info">
            <b class="sum-item-name" dir="auto">${esc(it.name)}</b>
            ${it.arabic ? `<small class="sum-item-ar" dir="rtl">${esc(it.arabic)}</small>` : ""}
            ${it.attribute ? `<div class="sum-item-attr">${ic("tag")}[Attribute: ${esc(it.attribute)}]</div>` : ""}
            ${notes(it.notes)}
          </div>
        </div>
        <span class="sum-item-status ${it.done ? "done" : "pend"}">${ic(it.done ? "check-circle" : "clock")}</span>
      </div>
    `).join("") : `<div class="sum-no-order">No items in invoice</div>`;

    html += `
      <div class="sum-invoice-card ${isTop ? "top-order" : ""}" data-inv="${o.inv}">
        <div class="sum-invoice-header">
          <div class="sum-inv-title-wrap">
            <span class="sum-inv-badge">#${idx + 1}</span>
            <span class="sum-inv-time">${clock(o.ordered_at)}</span>
            <span class="sum-inv-count">${o.done}/${o.total}</span>
          </div>
          <div class="sum-inv-actions-inline">
            <button class="sum-btn-done" data-inv="${o.inv}" type="button" title="Complete Order #${idx + 1}">
              ${ic("check-circle")} Complete
            </button>
            <button class="sum-btn-open" title="Open in Dialog" data-inv="${o.inv}" type="button">${ic("maximize")}</button>
          </div>
        </div>
        <div class="sum-invoice-items">
          ${itemsHtml}
        </div>
      </div>
    `;
  });

  body.innerHTML = html;
  fillIcons(body);

  // Clicking an invoice card or any item in the summary opens the modal of items listed to mark complete
  body.querySelectorAll(".sum-invoice-card").forEach(card => {
    card.onclick = e => {
      if (e.target.closest(".sum-btn-done")) return;
      const inv = +card.dataset.inv;
      if (inv) openOrder(inv);
    };
  });

  // Clicking "Complete" button directly in the header completes this invoice
  body.querySelectorAll(".sum-btn-done").forEach(btn => {
    btn.onclick = async e => {
      e.stopPropagation();
      const inv = +btn.dataset.inv;
      if (!inv) return;
      btn.disabled = true;
      try {
        const res = await api(`/api/orders/${inv}/complete/`, {
          categories: selectedCategories,
        });
        const o = orders.find(x => x.inv === inv);
        if (o) {
          o.total = res.total;
          o.done = res.done;
          o.kds_done = res.kds_done;
          (o.items || []).forEach(i => i.done = true);
        }
        render();
      } catch (err) {
        btn.disabled = false;
        alert("Could not complete order: " + err.message);
      }
    };
  });

  // Clicking "Open in Dialog" opens the full modal
  body.querySelectorAll(".sum-btn-open").forEach(btn => {
    btn.onclick = e => {
      e.stopPropagation();
      const inv = +btn.dataset.inv;
      if (inv) openOrder(inv);
    };
  });
}

function tick() {
  const now = new Date();
  $("#clock").textContent = `${p2(now.getHours())}:${p2(now.getMinutes())}:${p2(now.getSeconds())}`;
  const dt = (performance.now() - fetchedAt) / 1000;
  document.querySelectorAll(".timer").forEach(e => {
    const s = +e.dataset.e + dt;
    e.textContent = fmt(s);
    e.className = "timer" + (s >= LATE ? " late" : s >= WARN ? " warn" : "");
    const c = e.closest(".card");  // pending orders past the saved delay limit turn red
    if (c) c.classList.toggle("delayed", DELAYED > 0 && s >= DELAYED);
  });
}

function setError(e) {
  const box = $("#err");
  if (!e) { box.hidden = true; return; }
  const noService = e instanceof TypeError;  // fetch itself failed: the KDS service is not reachable
  $("#err-title").textContent = noService ? "Can't reach the KDS service" : "Can't connect to the database";
  $("#err-msg").textContent = noService ? "The KDS program is not running or the network is down." : (e.message || "");
  $("#err-hint").textContent = `Retrying automatically every ${REFRESH / 1000} seconds.`;
  box.classList.toggle("big", !loaded);  // first load failed: show it large in the middle of the screen
  box.hidden = false;
  if (!loaded) { $("#grid").innerHTML = ""; $("#c-pending").textContent = $("#c-completed").textContent = "0"; }
}

// ---- High-Intensity Kitchen Notification Sound System ----
let audioCtx = null;
let soundEnabled = true;
try {
  const savedSound = localStorage.getItem("kds_sound");
  if (savedSound !== null) soundEnabled = (savedSound === "true");
} catch (e) {}

function getAudioContext() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      audioCtx = new AudioContextClass();
    }
  }
  if (audioCtx && audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }
  return audioCtx;
}

function unlockAudio() {
  const ctx = getAudioContext();
  if (ctx && ctx.state === "suspended") {
    ctx.resume().catch(() => {});
  }
}
["pointerdown", "touchstart", "click", "keydown"].forEach(evt => {
  document.addEventListener(evt, unlockAudio, { passive: true });
});

function playTone(ctx, outNode, freq, harmonicFreq, startTime, duration, vol) {
  const osc1 = ctx.createOscillator();
  const gain1 = ctx.createGain();
  osc1.type = "triangle";
  osc1.frequency.setValueAtTime(freq, startTime);

  gain1.gain.setValueAtTime(0.001, startTime);
  gain1.gain.exponentialRampToValueAtTime(vol * 0.75, startTime + 0.008);
  gain1.gain.setValueAtTime(vol * 0.75, startTime + duration - 0.02);
  gain1.gain.exponentialRampToValueAtTime(0.001, startTime + duration);

  osc1.connect(gain1);
  gain1.connect(outNode);
  osc1.start(startTime);
  osc1.stop(startTime + duration);

  if (harmonicFreq) {
    const osc2 = ctx.createOscillator();
    const gain2 = ctx.createGain();
    osc2.type = "sine";
    osc2.frequency.setValueAtTime(harmonicFreq, startTime);

    gain2.gain.setValueAtTime(0.001, startTime);
    gain2.gain.exponentialRampToValueAtTime(vol * 0.45, startTime + 0.008);
    gain2.gain.setValueAtTime(vol * 0.45, startTime + duration - 0.02);
    gain2.gain.exponentialRampToValueAtTime(0.001, startTime + duration);

    osc2.connect(gain2);
    gain2.connect(outNode);
    osc2.start(startTime);
    osc2.stop(startTime + duration);
  }
}

let lastSoundPlayTime = 0;
function playNewOrderSound(isManualTest = false) {
  if (!isManualTest && !soundEnabled) return;
  const nowMs = Date.now();
  if (!isManualTest && nowMs - lastSoundPlayTime < 2500) return;
  lastSoundPlayTime = nowMs;

  const btn = $("#sound-btn");
  if (btn) {
    btn.classList.add("ringing");
    setTimeout(() => btn.classList.remove("ringing"), 1400);
  }

  let played = false;
  try {
    const ctx = getAudioContext();
    if (ctx) {
      if (ctx.state === "suspended") {
        ctx.resume().then(() => {
          executeChime(ctx);
        }).catch(() => {
          fallbackAudio();
        });
      } else {
        executeChime(ctx);
      }
      played = true;
    }
  } catch (err) {
    console.warn("Web Audio alert error:", err);
  }

  if (!played) {
    fallbackAudio();
  }
}

function executeChime(ctx) {
  const compressor = ctx.createDynamicsCompressor();
  compressor.threshold.setValueAtTime(-12, ctx.currentTime);
  compressor.knee.setValueAtTime(24, ctx.currentTime);
  compressor.ratio.setValueAtTime(12, ctx.currentTime);
  compressor.attack.setValueAtTime(0.002, ctx.currentTime);
  compressor.release.setValueAtTime(0.2, ctx.currentTime);

  const masterGain = ctx.createGain();
  masterGain.gain.setValueAtTime(0.95, ctx.currentTime);
  masterGain.connect(compressor);
  compressor.connect(ctx.destination);

  const start = ctx.currentTime + 0.02;

  // Burst 1: High-intensity ascending chime
  playTone(ctx, masterGain, 1175, 2350, start + 0.00, 0.12, 0.9);   // D6
  playTone(ctx, masterGain, 1397, 2794, start + 0.14, 0.12, 0.9);   // F6
  playTone(ctx, masterGain, 1760, 3520, start + 0.28, 0.24, 1.0);   // A6

  // Burst 2: Penetrating high urgency repeat
  const b2 = start + 0.62;
  playTone(ctx, masterGain, 1175, 2350, b2 + 0.00, 0.12, 0.9);      // D6
  playTone(ctx, masterGain, 1397, 2794, b2 + 0.14, 0.12, 0.9);      // F6
  playTone(ctx, masterGain, 2093, 4186, b2 + 0.28, 0.32, 1.0);      // C7
}

function fallbackAudio() {
  try {
    const snd = new Audio("/static/kds/alert.wav");
    snd.volume = 1.0;
    snd.play().catch(e => console.warn("Audio fallback play failed:", e));
  } catch (e) {}
}

function updateSoundBtn() {
  const btn = $("#sound-btn");
  if (!btn) return;
  btn.innerHTML = ic(soundEnabled ? "bell" : "bell-off");
  btn.title = soundEnabled 
    ? "Kitchen Notification Sound: ACTIVE (Click to test sound, right-click to mute)"
    : "Kitchen Notification Sound: MUTED (Click to unmute and test)";
  btn.classList.toggle("muted", !soundEnabled);
}

async function load() {
  const rb = $("#retry"); rb.disabled = true;
  try {
    const qCat = catQuery();
    const url = "/api/orders/" + (qCat ? `?${qCat}` : "");
    const data = await api(url);
    const newOrders = data.orders || [];

    // Notify chefs with high-intensity sound when a new order appears
    if (loaded && !skipSoundOnNextLoad) {
      let hasNewPending = false;
      const newInvList = [];
      for (const o of newOrders) {
        if (!o.kds_done) {
          if (!knownPendingInvs.has(o.inv)) {
            hasNewPending = true;
            newInvList.push(o.inv);
          } else if (knownOrderCounts.has(o.inv) && o.total > knownOrderCounts.get(o.inv)) {
            hasNewPending = true;
            newInvList.push(o.inv);
          }
        }
      }
      if (hasNewPending) {
        playNewOrderSound();
        setTimeout(() => {
          newInvList.forEach(inv => {
            const el = $(`#grid .card[data-inv="${inv}"]`);
            if (el) el.classList.add("new-order-pulse");
          });
        }, 50);
      }
    }
    skipSoundOnNextLoad = false;

    orders = newOrders;
    knownPendingInvs = new Set(orders.filter(o => !o.kds_done).map(o => o.inv));
    knownOrderCounts = new Map(orders.map(o => [o.inv, o.total]));

    loaded = true; setError(null);
    fetchedAt = performance.now();
    $("#status").className = "dot ok"; $("#status").title = "Connected";
    render();
    const o = current && orders.find(x => x.inv === current.inv);
    if (o) $("#m-timer").dataset.e = o.elapsed;
  } catch (e) {
    $("#status").className = "dot bad"; $("#status").title = "Connection problem: " + e.message;
    setError(e);
  } finally { rb.disabled = false; }
}

function renderItems() {
  const done = items.filter(i => i.done).length, all = items.length && done === items.length;
  $("#m-items").innerHTML = items.map(i => `<label class="row ${i.done ? "done" : ""}" data-sl="${i.slno}">
    <input type="checkbox" ${i.done ? "checked" : ""}>
    <div class="nm">
      <b dir="auto">${esc(i.name)}</b>
      ${i.arabic ? `<small dir="rtl">${esc(i.arabic)}</small>` : ""}
      ${i.cat_name ? `<span class="cat-pill-tag">${esc(i.cat_name)}</span>` : ""}
      ${i.attribute ? `<div class="item-attr-tag">${ic("tag")}[Attribute: ${esc(i.attribute)}]</div>` : ""}
      ${notes(i.notes)}
    </div>
    <span class="qty">&times; ${i.qty}</span><span class="rate">${i.rate}</span></label>`).join("");
  $("#m-sum").textContent = `${done}/${items.length} done: ${all ? "KDS completed" : done ? "partially completed" : "pending"}`;
  $("#m-sum").className = all ? "done" : done ? "partial" : "pending";
}

async function openOrder(inv) {
  current = orders.find(o => o.inv === inv);
  $("#m-title").textContent = `${current.type} ${current.name ? "· " + current.name + " " : ""}· Inv #${inv}`;
  const t = $("#m-timer"); t.dataset.e = current.elapsed; t.hidden = current.kds_done;
  $("#m-items").textContent = "Loading…"; $("#modal").hidden = false;
  try {
    const qCat = catQuery();
    const url = `/api/orders/${inv}/items/` + (qCat ? `?${qCat}` : "");
    items = (await api(url)).items;
    renderItems();
  }
  catch (e) { $("#m-items").textContent = "Could not load items: " + e.message; }
}

function closeModal() {
  const m = $("#modal");
  if (m) m.hidden = true;
  current = null;
  load();
}

$("#m-items").addEventListener("change", async e => {
  const cb = e.target, row = cb.closest(".row"); cb.disabled = true;
  try {
    const res = await api(`/api/orders/${current.inv}/items/${row.dataset.sl}/`, {
      done: cb.checked,
      categories: selectedCategories,
    });
    items.find(i => i.slno == row.dataset.sl).done = cb.checked;
    renderItems();
    if (res && current) {
      current.total = res.total;
      current.done = res.done;
      current.kds_done = res.kds_done;
      const curIt = (current.items || []).find(i => i.slno == row.dataset.sl);
      if (curIt) curIt.done = cb.checked;
      render();
    }
  } catch (err) { cb.checked = !cb.checked; cb.disabled = false; alert("Could not save: " + err.message); }
});
$("#m-all").onclick = async () => {
  try {
    const res = await api(`/api/orders/${current.inv}/complete/`, {
      categories: selectedCategories,
    });
    items.forEach(i => i.done = true);
    renderItems();
    if (res && current) {
      current.total = res.total;
      current.done = res.done;
      current.kds_done = res.kds_done;
      (current.items || []).forEach(i => i.done = true);
      render();
    }
  }
  catch (err) { alert("Could not save: " + err.message); }
};

const closeBtn = $("#m-close");
if (closeBtn) closeBtn.onclick = closeModal;
const xBtn = $("#m-x");
if (xBtn) xBtn.onclick = closeModal;

const modalEl = $("#modal");
if (modalEl) {
  modalEl.addEventListener("click", e => {
    if (e.target === modalEl) closeModal();
  });
}

document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });
$("#grid").addEventListener("click", e => {
  const c = e.target.closest(".card");
  if (!c) return;
  const inv = +c.dataset.inv;
  if (inv) openOrder(inv);
});
$("#tabs").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; tab = b.dataset.tab; document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x === b)); render(); });
$("#types").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { type = b.dataset.type; render(); } });
$("#q").addEventListener("input", e => { query = e.target.value; render(); });

$("#retry").onclick = load;

// ---- Print receipt ----
async function printReceipt() {
  if (!current) return;
  const btn = $("#m-print");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Printing…";
  }
  try {
    const result = await api(`/api/orders/${current.inv}/print/`, {
      categories: selectedCategories,
    });
    if (result.ok) {
      if (btn) btn.textContent = "✓ Sent";
      setTimeout(() => {
        if (btn) {
          btn.innerHTML = `${ic("printer")}Print`;
          btn.disabled = false;
        }
      }, 2000);
    } else {
      alert("Print failed: " + (result.error || "Unknown error"));
      if (btn) {
        btn.innerHTML = `${ic("printer")}Print`;
        btn.disabled = false;
      }
    }
  } catch (err) {
    alert("Print error: " + err.message);
    if (btn) {
      btn.innerHTML = `${ic("printer")}Print`;
      btn.disabled = false;
    }
  }
}

const printBtn = $("#m-print");
if (printBtn) printBtn.onclick = printReceipt;

// ---- Category filter modal ----
const catModal = $("#cat-modal");
const catBtn = $("#cat-btn");
const catChecklist = $("#cat-checklist");
const catSearch = $("#cat-search");
const catSummary = $("#cat-summary");

function getCheckedCatIds() {
  if (!catChecklist) return [];
  return Array.from(catChecklist.querySelectorAll("input[type='checkbox']:checked")).map(cb => +cb.value);
}

function updateCatModalSummary() {
  if (!catSummary) return;
  const checked = getCheckedCatIds();
  const total = allCategories.length;
  if (!checked.length) {
    catSummary.textContent = "All categories active (showing all items)";
  } else if (checked.length === 1) {
    const c = allCategories.find(x => x.id === checked[0]);
    catSummary.textContent = `1 category selected (${c ? c.name : checked[0]})`;
  } else {
    catSummary.textContent = `${checked.length} of ${total} categories selected`;
  }
}

async function openCatModal() {
  if (!catModal) return;
  if (!allCategories.length) {
    try {
      const res = await api("/api/categories/");
      if (res.categories) allCategories = res.categories;
    } catch (e) {
      console.warn("Could not load categories:", e);
    }
  }

  if (catChecklist) {
    catChecklist.innerHTML = allCategories.map(c => {
      const isChecked = selectedCategories.length ? selectedCategories.includes(c.id) : true;
      return `<label class="cat-card ${isChecked ? "active" : ""}" data-name="${esc(c.name.toLowerCase())}">
        <input type="checkbox" value="${c.id}" ${isChecked ? "checked" : ""}>
        <div class="cat-info">
          <span class="cat-name" title="${esc(c.name)}">${esc(c.name)}</span>
          <span class="cat-count">${c.count} items</span>
        </div>
      </label>`;
    }).join("");

    catChecklist.querySelectorAll("input[type='checkbox']").forEach(cb => {
      cb.onchange = () => {
        const card = cb.closest(".cat-card");
        if (card) card.classList.toggle("active", cb.checked);
        updateCatModalSummary();
      };
    });
  }

  if (catSearch) catSearch.value = "";
  updateCatModalSummary();
  catModal.hidden = false;
  fillIcons(catModal);
}

function closeCatModal() {
  if (catModal) catModal.hidden = true;
}

if (catBtn) catBtn.onclick = openCatModal;
const catX = $("#cat-x"), catCancel = $("#cat-cancel");
if (catX) catX.onclick = closeCatModal;
if (catCancel) catCancel.onclick = closeCatModal;
if (catModal) {
  catModal.addEventListener("click", e => {
    if (e.target === catModal) closeCatModal();
  });
}

const catSelectAll = $("#cat-select-all");
if (catSelectAll) {
  catSelectAll.onclick = () => {
    catChecklist.querySelectorAll("input[type='checkbox']").forEach(cb => {
      cb.checked = true;
      const card = cb.closest(".cat-card");
      if (card) card.classList.add("active");
    });
    updateCatModalSummary();
  };
}

const catClearAll = $("#cat-clear-all");
if (catClearAll) {
  catClearAll.onclick = () => {
    catChecklist.querySelectorAll("input[type='checkbox']").forEach(cb => {
      cb.checked = false;
      const card = cb.closest(".cat-card");
      if (card) card.classList.remove("active");
    });
    updateCatModalSummary();
  };
}

if (catSearch) {
  catSearch.oninput = () => {
    const q = catSearch.value.trim().toLowerCase();
    catChecklist.querySelectorAll(".cat-card").forEach(card => {
      card.hidden = q && !card.dataset.name.includes(q);
    });
  };
}

const catApply = $("#cat-apply");
if (catApply) {
  catApply.onclick = async () => {
    const checked = getCheckedCatIds();
    selectedCategories = checked;
    try {
      localStorage.setItem("kds_categories", JSON.stringify(selectedCategories));
    } catch (e) {}
    try {
      await api("/api/station-categories/", { categories: selectedCategories });
    } catch (e) {
      console.warn("Could not save station categories to server:", e);
    }
    updateCatBtn();
    closeCatModal();
    skipSoundOnNextLoad = true;
    load();
  };
}

// ---- header tools: kitchen alert sound test/toggle, on-screen keyboard, full screen ----
const soundBtn = $("#sound-btn");
if (soundBtn) {
  soundBtn.onclick = () => {
    if (!soundEnabled) {
      soundEnabled = true;
      try { localStorage.setItem("kds_sound", "true"); } catch (e) {}
      updateSoundBtn();
    }
    playNewOrderSound(true);
  };
  soundBtn.oncontextmenu = e => {
    e.preventDefault();
    soundEnabled = !soundEnabled;
    try { localStorage.setItem("kds_sound", String(soundEnabled)); } catch (e) {}
    updateSoundBtn();
    if (soundEnabled) playNewOrderSound(true);
  };
  updateSoundBtn();
}

$("#kbd-btn").onclick = () => KBD.visible ? KBD.close() : KBD.open("num", $("#q"));
const fsBtn = $("#fs-btn");
fsBtn.onclick = () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {});
// Start in full screen by default: browsers only allow it after a touch/click, so use the first one
// (skipped when the window is already full screen, e.g. the KDS app window or F11).
const isFull = () => document.fullscreenElement || (innerWidth >= screen.width - 2 && innerHeight >= screen.height - 2);
function autoFull(e) {
  document.removeEventListener("pointerdown", autoFull, true); document.removeEventListener("keydown", autoFull, true);
  if (!isFull() && !e.target.closest("#fs-btn")) document.documentElement.requestFullscreen().catch(() => {});
}
document.addEventListener("pointerdown", autoFull, true); document.addEventListener("keydown", autoFull, true);
document.addEventListener("fullscreenchange", () => {
  const on = !!document.fullscreenElement;
  fsBtn.innerHTML = ic(on ? "minimize" : "maximize"); fsBtn.title = on ? "Exit full screen" : "Full screen";
});

// ---- admin lock for the settings page (password = today's date as YYMMDD) ----
const lock = $("#lock"), pw = $("#pw");
function openLock() { pw.value = ""; $("#pw-err").hidden = true; lock.hidden = false; KBD.open("num", pw); }
function closeLock() { lock.hidden = true; KBD.close(); }
$("#gear").onclick = openLock;
$("#lock-x").onclick = closeLock;
$("#lock-form").addEventListener("submit", async e => {
  e.preventDefault();
  try { await api("/api/unlock/", {password: pw.value}); location.href = "/setup/"; }
  catch (err) { $("#pw-err").hidden = false; pw.value = ""; pw.focus(); }
});
pw.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); $("#lock-form").requestSubmit(); } });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !lock.hidden) closeLock(); });

// ---- Close application ----
const appCloseBtn = $("#app-close");
if (appCloseBtn) {
  appCloseBtn.onclick = async () => {
    if (confirm("Close Kitchen Display application?")) {
      try {
        await api("/api/close/", {});
      } catch (err) {
        console.warn("Server close request:", err);
      }
      window.close();
      setTimeout(() => {
        document.body.innerHTML = `
          <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#344a5f;color:#fff;font-family:sans-serif;text-align:center;padding:20px;">
            <img src="/static/kds/icon.png" alt="KDS" style="width:64px;height:64px;margin-bottom:16px;border-radius:8px;">
            <h2 style="margin:0 0 8px 0;font-size:22px;">Kitchen Display Closed</h2>
            <p style="color:#b0d0f7;margin:0 0 16px 0;font-size:15px;">The application has been stopped. You can safely close this window.</p>
            <button onclick="location.reload()" style="padding:8px 18px;background:#0689ff;color:#fff;border:0;border-radius:4px;font-size:14px;cursor:pointer;font-weight:600;">Reopen KDS</button>
          </div>`;
      }, 300);
    }
  };
}

load(); setInterval(load, REFRESH); setInterval(tick, 1000);
