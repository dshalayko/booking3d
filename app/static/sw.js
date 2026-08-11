// Service worker нужен ровно для одного: чтобы при обрыве связи на стене
// висела внятная страница, а не браузерная ошибка. Приложение на VPS, кэшировать
// состояние принтеров бессмысленно — оно общее и мгновенно устаревает.

var CACHE = "booking-v1";
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
