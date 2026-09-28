// 楽楽精算/楽楽勤怠/ワンクリック実行 フォーム:
//  - 認証情報はこの端末の localStorage にのみ保存
//  - 送信は SSE(stream) でステップ進捗をライブ表示し、最後にファイル/結果を受け取る
//  - サーバにデータは保存しない
(function () {
  "use strict";
  var form = document.getElementById("rr-form");
  if (!form) return;
  var ns = "rr:" + (form.getAttribute("data-store") || "form") + ":";
  var remember = document.getElementById("rr-remember");
  var submit = document.getElementById("rr-submit");
  var flow = document.getElementById("rr-flow");
  var resultBox = document.getElementById("rr-result");
  var fields = Array.prototype.slice.call(form.querySelectorAll("[data-rr-store]"));
  var streamSupported = !!(window.fetch && window.ReadableStream && window.TextDecoder);

  // 保存キー: 既定はフォーム単位 (rr:<data-store>:<key>)。data-rr-ns があればそちらを使い、
  // 別画面 (例: ワンクリック実行 と /seisan) で同じ認証情報を共有する。
  function keyOf(el) {
    var own = el.getAttribute("data-rr-ns");
    return (own ? "rr:" + own + ":" : ns) + el.getAttribute("data-rr-store");
  }
  function ls(get, key, val) {
    try {
      if (get) return window.localStorage.getItem(key);
      if (val === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, val);
    } catch (e) { /* ignore */ }
    return null;
  }
  function restore() {
    var r = ls(true, ns + "_remember");
    if (remember && r !== null) remember.checked = r === "1";
    fields.forEach(function (el) {
      var v = ls(true, keyOf(el));
      if (v === null) return;
      if (el.type === "checkbox") el.checked = v === "1"; else el.value = v;
    });
  }
  function save() {
    var keep = !remember || remember.checked;
    ls(false, ns + "_remember", keep ? "1" : "0");
    fields.forEach(function (el) {
      if (el.hasAttribute("data-rr-secret") && !keep) { ls(false, keyOf(el), null); return; }
      ls(false, keyOf(el), el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value);
    });
  }
  function clearAll() {
    fields.forEach(function (el) { ls(false, keyOf(el), null); });
    ls(false, ns + "_remember", null);
    form.querySelectorAll("[data-rr-secret]").forEach(function (el) { el.value = ""; });
  }

  var ICON = {
    pending: '<span class="rr-mark rr-pending"></span>',
    running: '<span class="rr-mark rr-running"></span>',
    done: '<span class="rr-mark rr-done">✓</span>',
    failed: '<span class="rr-mark rr-failed">✕</span>',
    skipped: '<span class="rr-mark rr-skipped">!</span>', // データ無しで飛ばした月 (致命ではない)
  };
  function t(name, el) { return ((el || flow) && (el || flow).getAttribute("data-t-" + name)) || ""; }

  function stepLi(s) {
    var li = document.createElement("li");
    li.className = "rr-step";
    li.setAttribute("data-key", s.key);
    li.innerHTML = ICON.pending + '<span class="rr-step-label"></span>';
    li.querySelector(".rr-step-label").textContent = s.label;
    return li;
  }
  function findStep(key) {
    return flow.querySelector('.rr-step[data-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]');
  }
  function renderSteps(steps) {
    var ol = flow.querySelector(".rr-steps");
    ol.innerHTML = "";
    steps.forEach(function (s) { ol.appendChild(stepLi(s)); });
  }
  // 後から決まるステップ (ワンクリック実行の月別出勤簿など) を before の前に挿入する。
  function insertSteps(steps, beforeKey) {
    var ol = flow.querySelector(".rr-steps");
    var anchor = beforeKey ? findStep(beforeKey) : null;
    steps.forEach(function (s) { ol.insertBefore(stepLi(s), anchor); });
  }
  function setStep(key, status, detail) {
    var li = findStep(key);
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

  function toBlob(file) {
    var bin = atob(file.b64), len = bin.length, bytes = new Uint8Array(len);
    for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: file.mime || "application/octet-stream" });
  }
  function download(file) {
    var url = URL.createObjectURL(toBlob(file));
    var a = document.createElement("a");
    a.href = url; a.download = file.filename || "download";
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1500);
  }
  function openInTab(file) {
    var url = URL.createObjectURL(toBlob({ b64: file.b64, mime: "text/html" }));
    var a = document.createElement("a");
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
  }

  // ---- ワンクリック実行の結果パネル (データは textContent で入れる) ----
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function statClass(label) {
    if (label === "OK") return "ok";
    if (label === "NG") return "ng";
    if (label.indexOf("未確認") >= 0) return "gray";
    return "warn";
  }
  function fileButton(file, primary) {
    var b = el("button", "rr-dl" + (primary ? " rr-dl-primary" : ""));
    b.type = "button";
    var ext = (file.filename.split(".").pop() || "").toUpperCase();
    b.appendChild(el("span", "rr-dl-ico", ext));
    b.appendChild(el("span", "rr-dl-name", file.filename));
    b.addEventListener("click", function () { download(file); });
    return b;
  }
  function showLog(text) {
    if (!resultBox || !text) return;
    var d = el("details", "rr-log");
    d.appendChild(el("summary", null, t("log", resultBox)));
    d.appendChild(el("pre", null, text));
    resultBox.appendChild(d);
    resultBox.hidden = false;
  }
  function renderResult(data) {
    if (!resultBox) return;
    resultBox.innerHTML = "";
    resultBox.appendChild(el("div", "rr-result-who", t("approver", resultBox) + data.approver));
    if (data.summary && data.summary.length) {
      var stats = el("div", "rr-stats");
      data.summary.forEach(function (s) {
        var tile = el("div", "rr-stat " + statClass(s.label));
        tile.appendChild(el("div", "n", s.count));
        tile.appendChild(el("div", "l", s.text || s.label));
        stats.appendChild(tile);
      });
      resultBox.appendChild(stats);
    }
    resultBox.appendChild(el("h3", null, t("files", resultBox)));
    var files = el("div", "rr-files");
    (data.files || []).forEach(function (f) {
      var row = el("div", "rr-file-row");
      row.appendChild(fileButton(f, true));
      if (/\.html?$/i.test(f.filename)) {
        var open = el("button", "btn-ghost rr-open", t("open", resultBox));
        open.type = "button";
        open.addEventListener("click", function () { openInTab(f); });
        row.appendChild(open);
      }
      files.appendChild(row);
    });
    resultBox.appendChild(files);
    var warns = [];
    if (data.skipped && data.skipped.length) warns.push(t("skipped", resultBox).replace("{months}", data.skipped.join("、")));
    if (data.dropped && data.dropped.length) warns.push(t("dropped", resultBox).replace("{months}", data.dropped.join("、")));
    if (warns.length) {
      var w = el("div", "rr-result-warn");
      warns.forEach(function (m) { w.appendChild(el("div", null, m)); });
      resultBox.appendChild(w);
    }
    if (data.sources && data.sources.length) {
      resultBox.appendChild(el("h3", null, t("sources", resultBox)));
      var src = el("div", "rr-files rr-sources");
      data.sources.forEach(function (f) { src.appendChild(fileButton(f, false)); });
      resultBox.appendChild(src);
    }
    showLog(data.log);
    resultBox.hidden = false;
  }

  function handleEvent(ev, data) {
    if (ev === "steps") { if (data.before) insertSteps(data.steps, data.before); else renderSteps(data.steps); }
    else if (ev === "step") setStep(data.key, data.status, data.detail);
    else if (ev === "file") { statusText(t("done")); download(data); }
    else if (ev === "result") { statusText(t("done")); renderResult(data); }
    else if (ev === "error") {
      statusText(data.message || t("error"));
      flow.classList.add("rr-has-error");
      var running = flow.querySelector(".rr-step.rr-running");
      if (running) setStep(running.getAttribute("data-key"), "failed");
      showLog(data.log);
    }
  }

  function setBusy(on) { if (submit) submit.disabled = on; }

  function runStream(url) {
    flow.hidden = false;
    flow.classList.remove("rr-has-error");
    flow.querySelector(".rr-steps").innerHTML = "";
    if (resultBox) { resultBox.hidden = true; resultBox.innerHTML = ""; }
    setBusy(true);
    var start = Date.now();
    var timer = setInterval(function () {
      if (!flow.classList.contains("rr-has-error") && !flow.querySelector(".rr-step.rr-failed")) {
        var s = Math.floor((Date.now() - start) / 1000);
        statusText(t("running") + "  " + t("elapsed").replace("{s}", s));
      }
    }, 500);
    var finished = false;

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
              if (data) {
                try { handleEvent(ev, JSON.parse(data)); } catch (e) { /* ignore */ }
                if (ev === "result" || ev === "file" || ev === "error") finished = true;
              }
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function () { statusText(t("error")); flow.classList.add("rr-has-error"); })
      .finally(function () {
        clearInterval(timer); setBusy(false);
        // 接続が途中で切れた (結果もエラーも来ない) 場合もエラー表示にする
        if (!finished && !flow.classList.contains("rr-has-error")) { statusText(t("error")); flow.classList.add("rr-has-error"); }
      });
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
