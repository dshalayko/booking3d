// Ванильный JS без зависимостей: htmx и Tailwind по CDN отвалились бы ровно в
// тот момент, когда пропадает интернет, — а именно тогда экран и должен
// оставаться читаемым. Здесь всего четыре вещи: опрос статусов, счётчики
// времени, клавиатура PIN и авто-возврат на главный экран.

(function () {
  "use strict";

  // --- тексты --------------------------------------------------------------
  // Приезжают из app/texts.py в data-texts на <body> (см. base.html): своих
  // формулировок в браузере нет, иначе перевод пришлось бы искать и здесь.

  var T = {};
  try {
    T = JSON.parse(document.body.dataset.texts || "{}");
  } catch (error) {
    T = {};
  }

  function fmt(template, values) {
    return String(template || "").replace(/\{(\w+)\}/g, function (match, key) {
      return key in values ? values[key] : match;
    });
  }

  // --- опрос статусов ------------------------------------------------------

  var board = document.querySelector("[data-poll]");
  var banner = document.getElementById("offline-banner");
  var failures = 0;

  function setOffline(isOffline) {
    if (banner) banner.hidden = !isOffline;
  }

  function poll() {
    if (!board) return;
    fetch(board.dataset.poll, { headers: { "X-Requested-With": "poll" } })
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.text();
      })
      .then(function (html) {
        // Не перерисовываем, пока человек держит палец на кнопке.
        if (!document.querySelector(".btn:active")) board.innerHTML = html;
        failures = 0;
        setOffline(false);
        renderTimes();
      })
      .catch(function () {
        failures += 1;
        if (failures >= 2) setOffline(true);
      });
  }

  if (board) {
    var interval = (parseInt(board.dataset.interval, 10) || 10) * 1000;
    setInterval(poll, interval);
  }

  // --- счётчики времени ----------------------------------------------------

  function humanize(totalMinutes) {
    var minutes = Math.abs(totalMinutes);
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    if (hours && rest) return fmt(T.unit_hours_minutes, { hours: hours, minutes: rest });
    if (hours) return fmt(T.unit_hours, { hours: hours });
    return fmt(T.unit_minutes, { minutes: rest });
  }

  function renderTimes() {
    var now = Date.now();

    document.querySelectorAll("[data-eta]").forEach(function (node) {
      var eta = Date.parse(node.dataset.eta);
      if (isNaN(eta)) return;
      var minutes = Math.round((eta - now) / 60000);
      node.textContent =
        minutes > 0 ? fmt(T.eta_left, { left: humanize(minutes) }) : T.eta_over;
    });

    document.querySelectorAll("[data-since]").forEach(function (node) {
      var since = Date.parse(node.dataset.since);
      if (isNaN(since)) return;
      node.textContent = fmt(T.done_ago, { ago: humanize(Math.round((now - since) / 60000)) });
    });
  }

  renderTimes();
  setInterval(renderTimes, 30000);

  // --- клавиатура PIN ------------------------------------------------------

  document.querySelectorAll("[data-keypad]").forEach(function (pad) {
    var value = pad.querySelector("[data-keypad-value]");
    var display = pad.querySelector("[data-keypad-display]");

    function draw() {
      var filled = value.value.length;
      display.textContent = "•".repeat(filled) + "·".repeat(Math.max(0, 4 - filled));
      display.dataset.filled = filled ? "1" : "0";
    }

    pad.addEventListener("click", function (event) {
      var key = event.target.closest("[data-key]");
      if (!key) return;
      var code = key.dataset.key;
      if (code === "clear") value.value = "";
      else if (code === "back") value.value = value.value.slice(0, -1);
      else if (value.value.length < 4) value.value += code;
      draw();
    });

    draw();
  });

  // Не отправлять форму с недовведённым PIN — иначе человек получит отказ
  // и решит, что система сломалась.
  document.querySelectorAll("form[data-guard]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var pin = form.querySelector("[data-keypad-value]");
      if (pin && pin.value.length !== 4) {
        event.preventDefault();
        var display = form.querySelector("[data-keypad-display]");
        if (display) {
          display.style.color = "#e74c3c";
          setTimeout(function () { display.style.color = ""; }, 700);
        }
      }
    });
  });

  // --- авто-возврат и автоскрытие -----------------------------------------

  var seconds = parseInt(document.body.dataset.autoreturn, 10);
  if (seconds) {
    var timer;
    function restart() {
      clearTimeout(timer);
      timer = setTimeout(function () { location.href = "/"; }, seconds * 1000);
    }
    ["click", "touchstart", "keydown"].forEach(function (name) {
      document.addEventListener(name, restart, { passive: true });
    });
    restart();
  }

  document.querySelectorAll("[data-autohide]").forEach(function (node) {
    setTimeout(function () { node.remove(); }, parseInt(node.dataset.autohide, 10) * 1000);
  });

  // --- офлайн-заглушка -----------------------------------------------------

  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }
})();
