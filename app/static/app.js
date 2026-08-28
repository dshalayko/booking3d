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

  // --- обновление после деплоя ---------------------------------------------
  // Планшет на стене — это вкладка, открытая неделями: новые стили и скрипт
  // сами по себе до неё не доезжают, а ходить к каждому экрану ногами — это
  // ровно то, чего не делают. Поэтому у отданного браузеру есть номер версии
  // (app/assets.py): он приходит с каждым ответом сервера, и разошёлся с тем, с
  // которым страница загрузилась, — значит на сервере уже новое.

  var version = document.body.dataset.version || "";
  var updating = false;

  function update() {
    if (updating) return;
    updating = true;
    // Кэш service worker'а чистим до перезагрузки: иначе страница поднимется
    // уже новая, а стили возьмёт из старой копии — и обновление съест само
    // себя, потому что версия-то уже совпала.
    var cleared = window.caches
      ? caches.keys().then(function (keys) {
          return Promise.all(keys.map(function (key) { return caches.delete(key); }));
        })
      : Promise.resolve();
    cleared.catch(function () {}).then(function () { location.reload(); });
  }

  function poll() {
    if (!board) return;
    fetch(board.dataset.poll, { headers: { "X-Requested-With": "poll" } })
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        var fresh = response.headers.get("X-App-Version");
        // Палец на кнопке — перезагрузку отложим до следующего опроса, как и
        // перерисовку ниже: человек в этот момент что-то нажимает.
        if (version && fresh && fresh !== version && !document.querySelector(".btn:active")) {
          update();
          return null;
        }
        return response.text();
      })
      .then(function (html) {
        if (html === null) return;  // страница уже перезагружается
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

  // --- «Как получить PIN» --------------------------------------------------
  // Кнопка под клавиатурой показывает QR на бота (разметка в _keypad.html).
  // Нативный <dialog>: он сам затемняет фон и закрывается по Esc. Где его нет
  // (Safari до 15.4), остаётся атрибут `open` — карточка появится в потоке
  // страницы, без затемнения, но с тем же кодом и той же кнопкой закрытия.

  document.querySelectorAll("[data-pin-help]").forEach(function (button) {
    var dialog = button.parentNode.querySelector("[data-pin-help-dialog]");
    if (!dialog) return;

    function close() {
      if (dialog.close) dialog.close();
      else dialog.removeAttribute("open");
    }

    button.addEventListener("click", function () {
      if (dialog.showModal) dialog.showModal();
      else dialog.setAttribute("open", "");
    });

    dialog.addEventListener("click", function (event) {
      // Клик мимо карточки закрывает: на планшете это первое, что пробуют,
      // а Esc с сенсорного экрана не нажать. Цель клика по затемнению — сам
      // <dialog>: карточка внутри него отдельным элементом.
      if (event.target === dialog || event.target.closest("[data-pin-help-close]")) close();
    });
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

  // --- не давать экрану гаснуть -------------------------------------------
  // Погасший планшет — это неработающее табло: пока никто его не разбудил,
  // статусов на стене нет. «Автоблокировка → Никогда» в настройках iPad от
  // этого спасает не всегда: режим энергосбережения возвращает блокировку
  // через 30 секунд, и настройку легко потерять при сбросе устройства.
  // Wake Lock система снимает при каждом уходе страницы из видимости
  // (блокировка, переключение вкладки), поэтому берём его заново
  // по visibilitychange, а не один раз при загрузке.
  // Нужен https — по http (и в Safari до 16.4) запрос просто не выполнится,
  // и экран останется на настройках iPad.

  var wakeLock = null;

  function keepAwake() {
    if (!navigator.wakeLock || document.visibilityState !== "visible") return;
    if (wakeLock && !wakeLock.released) return;
    navigator.wakeLock.request("screen")
      .then(function (lock) {
        wakeLock = lock;
        lock.addEventListener("release", function () { wakeLock = null; });
      })
      .catch(function () {});
  }

  document.addEventListener("visibilitychange", keepAwake);
  // Ещё и по первому касанию: если в момент загрузки запрос отклонили
  // (страница открылась в фоне), жест — второй надёжный шанс.
  ["click", "touchend"].forEach(function (name) {
    document.addEventListener(name, keepAwake, { passive: true });
  });
  keepAwake();

  // --- офлайн-заглушка -----------------------------------------------------

  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }

  // --- Telegram Mini App ---------------------------------------------------
  // На страницах /app рядом лежит telegram-web-app.js (base.html), и через него
  // Telegram отдаёт подпись открытия. Весь код — под проверкой объекта: на
  // киоске скрипта нет, и ничего из этого не выполняется.

  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    // В try целиком: вне настоящего клиента Telegram (страницу открыли в обычном
    // браузере, а скрипт всё равно загрузился) эти методы бросают
    // WebAppMethodUnsupported. Без try исключение уносит с собой весь остальной
    // файл — вместе с бутстрапом ниже, который как раз и объясняет человеку,
    // что приложение нужно открыть из бота.
    try {
      tg.ready();
      tg.expand();

      // Системная кнопка «назад» вместо своей: в Telegram она на привычном
      // месте, и лишняя кнопка в шапке только спорила бы с ней.
      if (tg.BackButton && history.length > 1) {
        tg.BackButton.show();
        tg.BackButton.onClick(function () { history.back(); });
      }

      // BackButton — навигация внутри приложения. Закрытие должно быть
      // отдельным явным действием: иначе после нескольких переходов человек
      // листает историю назад и не понимает, как вернуться в чат.
      var closeButton = document.querySelector("[data-tg-close]");
      var closeBar = document.querySelector("[data-tg-close-bar]");
      if (closeButton && closeBar && typeof tg.close === "function") {
        closeBar.hidden = false;
        closeButton.hidden = false;
        closeButton.addEventListener("click", function () {
          try {
            tg.close();
          } catch (error) {
            /* старый клиент Telegram — системное меню остаётся доступно */
          }
        });
      }
    } catch (error) {
      /* не в Telegram — просто веб-страница */
    }
  }

  // Бутстрап: подпись живёт в JS-объекте, серверу её нужно передать явно.
  // Открыли не из Telegram — подписи нет, и вместо формы показываем объяснение.
  var bootstrap = document.querySelector("[data-tg-bootstrap]");
  if (bootstrap) {
    var initData = tg && tg.initData;
    if (initData) {
      bootstrap.querySelector("[data-tg-init-data]").value = initData;
      bootstrap.submit();
    } else {
      var loading = document.querySelector("[data-tg-loading]");
      var outside = document.querySelector("[data-tg-outside]");
      if (loading) loading.hidden = true;
      if (outside) outside.hidden = false;
    }
  }
})();
