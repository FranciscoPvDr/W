(function () {
  const IDLE_MS = 5 * 60 * 1000;
  const TOKEN_KEY = 'monitor_token';
  let timer = null;

  function logoutIdle() {
    if (!localStorage.getItem(TOKEN_KEY)) return;
    localStorage.clear();
    location.href = '/login?timeout=1';
  }

  function resetIdleTimer() {
    if (!localStorage.getItem(TOKEN_KEY)) return;
    clearTimeout(timer);
    timer = setTimeout(logoutIdle, IDLE_MS);
  }

  ['mousedown', 'keydown', 'scroll', 'touchstart', 'click'].forEach(function (ev) {
    document.addEventListener(ev, resetIdleTimer, { passive: true });
  });

  resetIdleTimer();
})();
