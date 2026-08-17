// Service worker нужен ровно для одного: чтобы при обрыве связи на стене
// висела внятная страница, а не браузерная ошибка. Приложение на VPS, кэшировать
// состояние машин бессмысленно — оно общее и мгновенно устаревает.

// Версию поднимать при правках app.js и app.css. Это по-прежнему единственный
// способ обновить планшет сразу; забытый номер больше не оставляет старую копию
// навсегда, но стоит одного лишнего показа старого экрана — см. `fetch` ниже.
var CACHE = "booking-v12";
var SHELL = ["/offline", "/static/app.css", "/static/app.js"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) { return cache.addAll(SHELL); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (key) { return key !== CACHE; })
          .map(function (key) { return caches.delete(key); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(function () { return caches.match("/offline"); })
    );
    return;
  }

  // Отдаём из кэша сразу (на стене экран должен рисоваться и без сети), но в
  // фоне перекачиваем и кладём свежее. Так забытый номер версии стоит ровно
  // одного показа старого файла, а не вечности: раньше правка стилей просто не
  // доезжала до уже открытого планшета, и снаружи это выглядело как «поменяли
  // CSS, а на стене всё по-старому».
  if (new URL(request.url).pathname.startsWith("/static/")) {
    var fresh = fetch(request).then(function (response) {
      if (response.ok) {
        var copy = response.clone();
        return caches.open(CACHE).then(function (cache) {
          return cache.put(request, copy);
        }).then(function () { return response; });
      }
      return response;
    });
    // `waitUntil` — чтобы браузер не усыпил worker раньше, чем свежая копия
    // ляжет в кэш: ответ-то уже отдан из старой. Отказ (нет сети) гасится тут
    // же: фоновая перекачка на то и фоновая, экран уже нарисован.
    event.waitUntil(fresh.catch(function () { return null; }));
    event.respondWith(
      caches.match(request).then(function (hit) { return hit || fresh; })
    );
  }
});
