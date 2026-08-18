// Админка не является экраном-киоском и не должна жить под его service worker.
// Регистрация могла остаться в браузере от открытой ранее доски и продолжает
// управлять всем origin, включая /admin. Снимаем её без перезагрузки страницы:
// следующий переход уже пойдёт напрямую в сеть.
(function () {
  "use strict";

  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.getRegistration("/")
    .then(function (registration) {
      if (registration) return registration.unregister();
    })
    .catch(function () {});
})();
