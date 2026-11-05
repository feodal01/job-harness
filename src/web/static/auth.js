// Login screen (8·0) and the account menu of the app. The app is closed behind the login screen:
// the server redirects "/" to /auth, and an open tab follows when the session ends.
(() => {
  const find = id => document.getElementById(id);
  const isLoginPage = !!find("authProviders");
  const LAST_PROVIDER = "job-search:last-provider";
  const SHOWN_PROVIDERS = 3;
  const GITHUB_MARK = "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z";
  let generation = 0, busy = false;

  const remembered = {
    get() { try { return localStorage.getItem(LAST_PROVIDER); } catch { return null; } },
    set(value) { try { localStorage.setItem(LAST_PROVIDER, value); } catch { /* private mode */ } },
  };

  function alert(message) {
    const host = find("authAlert");
    if (!host) return;
    host.textContent = message;
    host.hidden = !message;
  }

  async function request(path, body) {
    const response = await fetch("/api/v1/auth" + path, {
      method: body === undefined ? "GET" : "POST",
      cache: "no-store",
      headers: body === undefined ? {} : {"Content-Type": "application/json", "X-Job-Search-Auth": "1"},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) {
      const error = new Error(typeof value.detail === "string" ? value.detail : "Не удалось выполнить вход.");
      error.status = response.status;
      throw error;
    }
    return value;
  }

  function providerButtons() {
    return [...document.querySelectorAll("#authProviders button, #authMoreProviders button")];
  }

  async function start(provider) {
    busy = true;
    alert("");
    find("authNotice").hidden = true;
    for (const button of providerButtons()) button.disabled = true;
    try {
      const result = await request("/start", {provider});
      remembered.set(provider);
      location.assign(result.url);
    } catch (error) {
      busy = false;
      alert(error.message);
      for (const button of providerButtons()) button.disabled = false;
    }
  }

  function providerIcon(provider, main) {
    if (provider.id === "github") {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("width", "20");
      svg.setAttribute("height", "20");
      svg.setAttribute("viewBox", "0 0 16 16");
      svg.setAttribute("fill", main ? "#fff" : "currentColor");
      svg.setAttribute("aria-hidden", "true");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", GITHUB_MARK);
      svg.append(path);
      return svg;
    }
    const mono = document.createElement("span");
    mono.className = "mono";
    mono.setAttribute("aria-hidden", "true");
    mono.textContent = provider.name.slice(0, 1).toUpperCase();
    return mono;
  }

  function providerButton(provider, {main = false, last = false, short = false} = {}) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pbtn" + (main ? " main" : "");
    button.append(providerIcon(provider, main), (short ? "" : "Войти через ") + provider.name);
    if (last) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "в прошлый раз";
      button.append(tag);
    }
    button.onclick = () => start(provider.id);
    return button;
  }

  function renderProviders(providers) {
    const host = find("authProviders");
    const more = find("authMoreProviders");
    host.replaceChildren();
    more.replaceChildren();
    host.removeAttribute("aria-busy");
    if (!providers.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "Способы входа пока не подключены. Попробуйте позже.";
      host.append(empty);
      find("authMore").hidden = true;
      find("authRetry").hidden = false;
      return;
    }
    const lastId = remembered.get();
    const ordered = [...providers].sort((a, b) => (b.id === lastId) - (a.id === lastId));
    ordered.slice(0, SHOWN_PROVIDERS).forEach((provider, index) => {
      host.append(providerButton(provider, {main: index === 0, last: provider.id === lastId}));
    });
    const rest = ordered.slice(SHOWN_PROVIDERS);
    for (const provider of rest) more.append(providerButton(provider, {short: true}));
    find("authMoreLabel").textContent = "Другие способы · " + rest.length;
    find("authMore").hidden = !rest.length;
  }

  function renderAccount(status) {
    find("accountName").textContent = status.user?.name || status.user?.email || "Аккаунт";
    find("accountButton").hidden = !status.authenticated;
  }

  async function refresh() {
    if (busy) return;
    const current = ++generation;
    try {
      const status = await request("");
      if (current !== generation) return;
      if (!isLoginPage) {
        if (!status.authenticated) location.replace(status.login_url || "/auth");
        else renderAccount(status);
        return;
      }
      if (status.authenticated) {
        location.replace("/");
        return;
      }
      alert("");
      find("authRetry").hidden = true;
      find("authReset").hidden = true;
      const {providers} = await request("/providers");
      if (current !== generation) return;
      renderProviders(providers);
    } catch (error) {
      if (current !== generation || !isLoginPage) return;  // the app shows its own "service stopped" screen
      alert(error instanceof TypeError ? "Нет связи с сервисом. Проверьте, что он запущен." : error.message);
      find("authProviders").replaceChildren();
      find("authProviders").removeAttribute("aria-busy");
      find("authMore").hidden = true;
      find("authRetry").hidden = false;
      find("authReset").hidden = ![409, 503].includes(error.status);
    }
  }

  async function logout(button) {
    busy = true;
    ++generation;
    if (button) button.disabled = true;
    let logoutError = "";
    try {
      await request("/logout", {});
    } catch (error) {
      logoutError = error.message;
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
    if (!isLoginPage) {
      // The local session is gone even when Supabase did not confirm, so the app closes either way.
      location.assign("/auth");
      return;
    }
    await refresh();
    if (logoutError) alert(logoutError);
  }

  async function showVersion() {
    try {
      const response = await fetch("/api/v1/service", {cache: "no-store"});
      if (!response.ok) return;
      const {version} = await response.json();
      if (version) find("appVersion").textContent = "Поиск работы · версия " + version;
    } catch { /* the footer keeps the plain name */ }
  }

  if (isLoginPage) {
    const messages = {
      failed: "Не удалось завершить вход. Выберите способ входа и попробуйте ещё раз.",
      expired: "Время входа истекло. Выберите способ входа ещё раз.",
      cancelled: "Вход отменён. Можно выбрать другой способ или повторить попытку.",
    };
    const message = messages[new URLSearchParams(location.search).get("result")];
    if (message) {
      find("authNotice").textContent = message;
      find("authNotice").hidden = false;
    }
    history.replaceState(null, "", "/auth");
    find("authRetry").onclick = refresh;
    find("authReset").onclick = () => logout(find("authReset"));
    showVersion();
  } else {
    find("signOut").onclick = () => logout(find("signOut"));
  }
  window.addEventListener("focus", refresh);
  window.addEventListener("pageshow", event => { if (event.persisted) refresh(); });
  refresh();
})();
