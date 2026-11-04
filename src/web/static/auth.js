(() => {
  const find = id => document.getElementById(id);
  const isLoginPage = !!find("authProviders");
  let generation = 0, busy = false;

  function alert(message) {
    const host = find("authAlert");
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

  async function start(provider) {
    busy = true;
    alert("");
    for (const button of find("authProviders").querySelectorAll("button")) button.disabled = true;
    try {
      const result = await request("/start", {provider});
      location.assign(result.url);
    } catch (error) {
      busy = false;
      alert(error.message);
      for (const button of find("authProviders").querySelectorAll("button")) button.disabled = false;
    }
  }

  function renderStatus(status) {
    const user = status.user;
    find("accountName").textContent = user?.name || "";
    find("accountName").hidden = !status.authenticated;
    find("signOut").hidden = !status.authenticated;
    if (!isLoginPage) {
      find("accountButton").hidden = !status.authenticated;
      find("authLink").hidden = status.authenticated;
      find("authLink").href = status.login_url || "/auth";
      return;
    }
    find("authTitle").textContent = status.authenticated ? "Вы вошли" : "Вход в Поиск работы";
    find("authDescription").textContent = status.authenticated
      ? "Общий каталог компаний доступен этому приложению и ассистенту."
      : "Войдите, чтобы пользоваться общим каталогом компаний.";
    find("authAccount").hidden = !status.authenticated;
    find("authProviders").hidden = status.authenticated;
    if (status.authenticated) find("authNotice").hidden = true;
  }

  async function refresh() {
    if (busy) return;
    const current = ++generation;
    try {
      const status = await request("");
      if (current !== generation) return;
      renderStatus(status);
      alert("");
      if (!isLoginPage) return;
      find("authRetry").hidden = true;
      find("authReset").hidden = true;
      if (status.authenticated) return;
      const {providers} = await request("/providers");
      if (current !== generation) return;
      const host = find("authProviders");
      host.replaceChildren();
      for (const provider of providers) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Войти через " + provider.name;
        button.onclick = () => start(provider.id);
        host.append(button);
      }
      if (!providers.length) {
        host.textContent = "Способы входа пока не подключены. Попробуйте позже.";
        find("authRetry").hidden = false;
      }
    } catch (error) {
      if (current !== generation) return;
      alert(error instanceof TypeError ? "Нет связи с сервисом. Проверьте, что он запущен." : error.message);
      if (isLoginPage) {
        find("authProviders").replaceChildren();
        find("authRetry").hidden = false;
        find("authReset").hidden = ![409, 503].includes(error.status);
      }
    }
  }

  async function logout() {
    busy = true;
    ++generation;
    find("signOut").disabled = true;
    let logoutError = "";
    try {
      const status = await request("/logout", {});
      renderStatus(status);
      alert("");
    } catch (error) {
      logoutError = error.message;
    } finally {
      busy = false;
      find("signOut").disabled = false;
    }
    await refresh();
    if (logoutError) alert(logoutError);
  }
  find("signOut").onclick = logout;
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
    find("authReset").onclick = logout;
  }
  window.addEventListener("focus", refresh);
  window.addEventListener("pageshow", event => { if (event.persisted) refresh(); });
  refresh();
})();
