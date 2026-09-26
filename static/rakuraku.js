// 楽楽精算/楽楽勤怠 フォーム:
//  - 認証情報はこの端末の localStorage にのみ保存
//  - 送信は SSE(stream) でステップ進捗をライブ表示し、最後にファイルを受け取って保存
//  - サーバにデータは保存しない
(function () {
  "use strict";
  var form = document.getElementById("rr-form");
  if (!form) return;
  var ns = "rr:" + (form.getAttribute("data-store") || "form") + ":";
  var remember = document.getElementById("rr-remember");
  var submit = document.getElementById("rr-submit");
  var flow = document.getElementById("rr-flow");
  var fields = Array.prototype.slice.call(form.querySelectorAll("[data-rr-store]"));
  var streamSupported = !!(window.fetch && window.ReadableStream && window.TextDecoder);

  function ls(get, key, val) {
    try {
      if (get) return window.localStorage.getItem(ns + key);
      if (val === null) window.localStorage.removeItem(ns + key);
      else window.localStorage.setItem(ns + key, val);
    } catch (e) { /* ignore */ }
    return null;
  }
  function restore() {
    var r = ls(true, "_remember");
    if (remember && r !== null) remember.checked = r === "1";
    fields.forEach(function (el) {
      var v = ls(true, el.getAttribute("data-rr-store"));
      if (v === null) return;
      if (el.type === "checkbox") el.checked = v === "1"; else el.value = v;
    });
  }
  function save() {
    var keep = !remember || remember.checked;
    ls(false, "_remember", keep ? "1" : "0");
    fields.forEach(function (el) {
      var key = el.getAttribute("data-rr-store");
      if (el.hasAttribute("data-rr-secret") && !keep) { ls(false, key, null); return; }
      ls(false, key, el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value);
    });
  }
  function clearAll() {
    fields.forEach(function (el) { ls(false, el.getAttribute("data-rr-store"), null); });
    ls(false, "_remember", null);
    form.querySelectorAll("[data-rr-secret]").forEach(function (el) { el.value = ""; });
  }

  var ICON = {
    pending: '<span class="rr-mark rr-pending"></span>',
    running: '<span class="rr-mark rr-running"></span>',
    done: '<span class="rr-mark rr-done">✓</span>',
    failed: '<span class="rr-mark rr-failed">✕</span>',
    skipped: '<span class="rr-mark rr-skipped">!</span>', // データ無しで飛ばした月 (致命ではない)
  };
  function t(name) { return (flow && flow.getAttribute("data-t-" + name)) || ""; }

  function renderSteps(steps) {
    var ol = flow.querySelector(".rr-steps");
    ol.innerHTML = "";
    steps.forEach(function (s) {
      var li = document.createElement("li");
      li.className = "rr-step";
      li.setAttribute("data-key", s.key);
      li.innerHTML = ICON.pending + '<span class="rr-step-label"></span>';
      li.querySelector(".rr-step-label").textContent = s.label;
      ol.appendChild(li);
    });
  }
  function setStep(key, status, detail) {
    var li = flow.querySelector('.rr-step[data-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]');
    if (!li) return;
    li.querySelector(".rr-mark").outerHTML = ICON[status] || ICON.pending;
    li.className = "rr-step rr-" + status;
    if (detail) {
      var d = li.querySelector(".rr-step-detail") || document.createElement("span");
      d.className = "rr-step-detail"; d.textContent = detail;
      if (!d.parentNode) li.appendChild(d);
    }
  }
  function statusText(msg) { flow.querySelector(".rr-flow-status").textContent = msg; }

  function download(file) {
    var bin = atob(file.b64), len = bin.length, bytes = new Uint8Array(len);
    for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    var url = URL.createObjectURL(new Blob([bytes], { type: file.mime || "application/octet-stream" }));
    var a = document.createElement("a");
    a.href = url; a.download = file.filename || "download";
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1500);
  }

  function handleEvent(ev, data) {
    if (ev === "steps") renderSteps(data.steps);
    else if (ev === "step") setStep(data.key, data.status, data.detail);
    else if (ev === "file") { statusText(t("done")); download(data); }
    else if (ev === "error") {
      statusText(data.message || t("error"));
      flow.classList.add("rr-has-error");
      var running = flow.querySelector(".rr-step.rr-running");
      if (running) setStep(running.getAttribute("data-key"), "failed");
    }
  }

  function setBusy(on) { if (submit) submit.disabled = on; }

  function runStream(url) {
    flow.hidden = false;
    flow.classList.remove("rr-has-error");
    flow.querySelector(".rr-steps").innerHTML = "";
    setBusy(true);
    var start = Date.now();
    var timer = setInterval(function () {
      if (!flow.classList.contains("rr-has-error") && !flow.querySelector(".rr-step.rr-failed")) {
        var s = Math.floor((Date.now() - start) / 1000);
        statusText(t("running") + "  " + t("elapsed").replace("{s}", s));
      }
    }, 500);

    fetch(url, { method: "POST", body: new FormData(form), credentials: "same-origin",
                 headers: { "Accept": "text/event-stream" } })
      .then(function (resp) {
        var reader = resp.body.getReader(), dec = new TextDecoder("utf-8"), buf = "";
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) return;
            buf += dec.decode(r.value, { stream: true });
            var parts = buf.split("\n\n"); buf = parts.pop();
            parts.forEach(function (chunk) {
              var ev = "message", data = "";
              chunk.split("\n").forEach(function (line) {
                if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
                else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
              });
              if (data) { try { handleEvent(ev, JSON.parse(data)); } catch (e) { /* ignore */ } }
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function () { statusText(t("error")); flow.classList.add("rr-has-error"); })
      .finally(function () { clearInterval(timer); setBusy(false); });
  }

  restore();
  var clearBtn = document.getElementById("rr-clear");
  if (clearBtn) clearBtn.addEventListener("click", clearAll);

  form.addEventListener("submit", function (e) {
    if (!streamSupported || !flow) return; // no-JS/older browser: normal form POST (file download)
    e.preventDefault();
    save();
    runStream(flow.getAttribute("data-stream"));
  });
})();
