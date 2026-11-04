/* On-screen keyboard: numpad by default, "More" opens the full keyboard (English / Arabic). */
const KBD = (() => {
  const ROWS = {
    en: [["q","w","e","r","t","y","u","i","o","p"], ["a","s","d","f","g","h","j","k","l"], ["z","x","c","v","b","n","m"]],
    ar: [["ض","ص","ث","ق","ف","غ","ع","ه","خ","ح","ج","د"], ["ش","س","ي","ب","ل","ا","ت","ن","م","ك","ط"], ["ئ","ء","ؤ","ر","لا","ى","ة","و","ز","ظ"]],
  };
  const DIGITS = ["1","2","3","4","5","6","7","8","9","0"];
  const el = document.createElement("div");
  el.id = "kbd"; el.hidden = true; document.body.appendChild(el);
  let mode = "num", lang = "en", caps = false, target = document.querySelector("#q");

  const k = (label, act, cls = "") => `<button type="button" class="k ${cls}" data-k="${act}">${label}</button>`;
  const ch = c => k(caps && lang === "en" ? c.toUpperCase() : c, "c:" + c);

  function draw() {
    if (mode === "num") {
      el.className = "m-num";
      el.innerHTML = `<div class="kb-num">${["1","2","3"].map(ch).join("")}${k("C", "clear", "fn")}
        ${["4","5","6"].map(ch).join("")}${k("More", "more", "fn")}
        ${["7","8","9"].map(ch).join("")}${k(ic("enter"), "enter", "go")}
        ${k(ic("backspace"), "bs", "bs")}${k("0", "c:0", "zero")}${k(ic("x"), "close", "x")}</div>`;
    } else {
      el.className = "m-full";
      el.innerHTML = `<div class="kb-full">
        <div class="kr">${DIGITS.map(ch).join("")}</div>
        ${ROWS[lang].map(r => `<div class="kr">${r.map(ch).join("")}</div>`).join("")}
        <div class="kr">${k("123", "num", "fn")}${lang === "en" ? k(ic("up"), "caps", "fn" + (caps ? " on" : "")) : ""}
          ${k(lang === "en" ? "ع" : "EN", "lang", "fn")}${k("&nbsp;", "c: ", "space")}
          ${k(ic("backspace"), "bs", "bs")}${k(ic("enter"), "enter", "go")}${k(ic("x"), "close", "x")}</div></div>`;
    }
    document.body.style.paddingBottom = el.hidden ? "" : el.offsetHeight + "px";
  }

  function put(t) {
    const t0 = target; if (!t0) return;
    t0.focus();
    const s = t0.selectionStart ?? t0.value.length, e = t0.selectionEnd ?? s;
    if (t0.maxLength > 0 && s === e && t0.value.length >= t0.maxLength) return;
    t0.setRangeText(t, s, e, "end");
    t0.dispatchEvent(new Event("input", {bubbles: true}));
  }
  function back() {
    const t0 = target; if (!t0) return;
    t0.focus();
    const s = t0.selectionStart ?? t0.value.length, e = t0.selectionEnd ?? s;
    t0.setRangeText("", s === e ? Math.max(0, s - 1) : s, e, "end");
    t0.dispatchEvent(new Event("input", {bubbles: true}));
  }

  el.addEventListener("mousedown", e => e.preventDefault());  // keep focus in the text box
  el.addEventListener("click", e => {
    const b = e.target.closest("[data-k]"); if (!b) return;
    const a = b.dataset.k;
    if (a.startsWith("c:")) { put(a.slice(2)); if (caps && lang === "en") { caps = false; draw(); } }
    else if (a === "bs") back();
    else if (a === "clear") { if (target) { target.value = ""; target.dispatchEvent(new Event("input", {bubbles: true})); } }
    else if (a === "enter") target && target.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
    else if (a === "more") { mode = "full"; draw(); }
    else if (a === "num") { mode = "num"; draw(); }
    else if (a === "caps") { caps = !caps; draw(); }
    else if (a === "lang") { lang = lang === "en" ? "ar" : "en"; caps = false; draw(); }
    else if (a === "close") close();
  });
  document.addEventListener("focusin", e => {
    if (e.target.matches("input:not([type=checkbox]):not([type=radio]),textarea")) target = e.target;
  });

  function open(m = "num", t) {
    if (t) target = t;
    mode = m; el.hidden = false; draw(); target && target.focus();
  }
  function close() { el.hidden = true; document.body.style.paddingBottom = ""; }
  return {open, close, toggle: () => el.hidden ? open("num") : close(), get visible() { return !el.hidden; }};
})();
