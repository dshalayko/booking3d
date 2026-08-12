// Service worker нужен ровно для одного: чтобы при обрыве связи на стене
// висела внятная страница, а не браузерная ошибка. Приложение на VPS, кэшировать
// состояние машин бессмысленно — оно общее и мгновенно устаревает.

// Версию обязательно поднимать при правках app.js и app.css: /static/ отдаётся
// из кэша без перепроверки, и на уже открытом планшете старая копия иначе
// останется навсегда.
var CACHE = "booking-v10";
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

  if (new URL(request.url).pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then(function (hit) { return hit || fetch(request); })
    );
  }
});
