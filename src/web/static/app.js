const $ = id => document.getElementById(id);
let servicePaused = false, pollTimer = null;
let validationAttempted = false, validationFields = [], validationId = 0;
const validationSummary = "Проверьте поля, выделенные красным.";
let briefs = [], sources = [], catalogReady = false, submitting = false;
let route = {}, viewVersion = 0, requestVersion = 0, refreshController = null,
  renderedCards = null, renderedPagination = null, renderedHome = null, renderedFolders = null,
  pageItems = [], searchTimer = null, toastTimer = null, detailVersion = 0,
  selectedVacancy = null, settingsSearch = {}, initialLimit = "", initialPages = "",
  historyRun = null, lastEvent = 0, events = [];
let readerItems = [], readerIndex = -1, readerDrafts = new Map(), pageInfo = null,
  readerPage = 1, readerTotal = 0, readerTransition = false,
  readerController = null, readerChangedPage = false, readerEnds = new Set();
const pageSize = 50;
const typeNames = {aggregator:"агрегатор",career_site:"карьерный сайт",ats:"ATS",telegram:"Telegram"};
const statusNames = {blocked:"Заблокирован",skipped:"Пропущен",queued:"В очереди",running:"Идёт поиск",complete:"Завершён",partial:"Частичный результат",failed:"Ошибка",cancelled:"Остановлен",interrupted:"Прерван"};
const conditionNames = {query:"Профиль вакансии",profile:"Профиль вакансии",locations:"Места работы",minimum_work_format:"Формат работы",employment:"Занятость",experience_years:"Опыт",salary:"Зарплата",published_after:"Дата публикации",relocation_support:"Помощь с релокацией",excluded_companies:"Исключённые компании",excluded_text:"Исключённые фразы"};
    function el(tag, text, cls) {
      let e = document.createElement(tag);
      if (text !== undefined) e.textContent = text;
      if (cls) e.className = cls;
      return e;
    }
    function link(text, url) {
      const a = el("a", text);
      try {
        if (["http:", "https:"].includes(new URL(url).protocol)) {
          a.href = url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
        }
      } catch {}
      return a;
    }
    function appendCompanySite(parent, company) {
      if (!company?.site_url) return;
      const site = link("Сайт работодателя ↗", company.site_url);
      if (!site.hasAttribute("href")) return;
      const row = el("div", undefined, "meta");
      row.append(site);
      parent.append(row);
    }
    function descriptionContent(text, markup) {
      const content = el("div", undefined, "description");
      if (markup) {
        content.classList.add("rich");
        // Разметка очищается общим механизмом на сервере до сохранения.
        content.innerHTML = markup;
        for (const a of content.querySelectorAll("a[href]")) a.target = "_blank";
      } else {
        content.textContent = text;
      }
      return content;
    }
    function error(message) {
      $("error").textContent = message || "";
      $("error").hidden = !message;
    }
    async function api(path, method = "GET", body, signal) {
      let r = await fetch(path, {
        signal,
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      let data = await r.json();
      if (!r.ok)
        throw Error(
          typeof data.detail === "string"
            ? data.detail
            : JSON.stringify(data.detail),
        );
      return data;
    }
    const queryTokens = new TokenEditor($("queriesEditor"), $("queries"),
      {label: "Поисковые запросы", maxItems: 10, maxLength: 500, onChange: updateFormFeedback});
    const companyTokens = new TokenEditor($("excludedCompaniesEditor"), $("excludedCompanies"),
      {label: "Исключённые компании", maxItems: 50, maxLength: 200, onChange: updateFormFeedback});
    const textTokens = new TokenEditor($("excludedTextEditor"), $("excludedText"),
      {label: "Исключённый текст", maxItems: 50, maxLength: 200, onChange: updateFormFeedback});
    const tokenEditors = [queryTokens, companyTokens, textTokens];
    const locationEditor = new LocationEditor($("locationGroups"), $("locationCount"),
      $("addLocationGroup"), api, () => {updateUnsupportedConditions(); updateStart(); renderConditionsSummary();});
    const chosenSources = () => sources.filter((s) => $("source-" + s.id)?.checked);
    function updateStart() {
      const b=currentBrief(), active=!!b&&route.view==="settings"&&activeRun(b);
      $("start").disabled = submitting || active;
      $("start").textContent=submitting?"Сохраняем…":"Сохранить и запустить поиск";
      $("start").classList.toggle("secondary",active);$("saveBrief").classList.toggle("secondary",!active);
      $("saveBrief").textContent=active?"Сохранить для следующего поиска":"Сохранить без поиска";
      // While a search runs, the save that still works comes first.
      if(active)$("start").before($("saveBrief"));else $("saveBrief").before($("start"));
      $("startHint").hidden=!active;
      if(active)$("start").setAttribute("aria-describedby","startHint");else $("start").removeAttribute("aria-describedby");
      const state=$("settingsSearchState");state.hidden=!active;
      if(active){
        const history=el("a","История поиска");history.href=historyHref(b,b.last_run.id);
        state.replaceChildren(el("b",`Сейчас идёт поиск (с ${new Date(b.last_run.created_at*1000).toLocaleTimeString("ru",{hour:"2-digit",minute:"2-digit"})})`),el("span","Он работает по условиям на момент запуска."),history);
      }
      $("saveBrief").disabled = submitting;
      if (validationAttempted) validateSearchForm();
    }
    function updateSource() {
      const chosen = chosenSources();
      queryTokens.setRequired(chosen.length > 0 && chosen.every(s => s.capabilities.query_parameters.query === "native"));
      $("queriesRequired").hidden = !queryTokens.required;
      $("sourceCount").textContent = `${chosen.length} из ${sources.length} ${plural(sources.length, "источника", "источников", "источников")}`;
      $("profiles").disabled = !chosen.some((s) => s.capabilities.actions.includes("company_profile"));
      if ($("profiles").disabled) $("profiles").checked = false;
      updateUnsupportedConditions();
      updateStart();
    }
    function updateUnsupportedConditions() {
      const enabled = {
        query: queryTokens.values.length > 0,
        locations: locationEditor.count > 0,
        minimum_work_format: locationEditor.groups.some(group => !!group.minimum),
        relocation_support: locationEditor.groups.some(group => group.relocation),
        employment: selected("employment").length > 0,
        experience_years: $("experience").value !== "",
        salary: $("salaryMin").value !== "",
        published_after: !!$("publishedAfter").value,
        excluded_companies: companyTokens.values.length > 0,
        excluded_text: textTokens.values.length > 0,
      };
      const host = $("unsupportedConditions"); host.replaceChildren();
      for (const source of chosenSources()) {
        const unsupported = Object.keys(enabled).filter(field => enabled[field] && source.capabilities.query_parameters[field] === "none");
        if (unsupported.length) host.append(el("p",`${source.name} — в этом источнике не проверяются: ${unsupported.map(field => conditionNames[field]).join(", ")}.`));
      }
      host.hidden = !host.childElementCount;
    }
    async function loadGeography() {
      const countries = await api("/api/v1/geography/countries");
      locationEditor.setCountries(countries);
      catalogReady = true;
      $("countryRetryBox").hidden = true;
      updateSource();
    }
    const employmentLabels = {full_time:"Полная", part_time:"Частичная", contract:"Проектная / контракт", internship:"Стажировка", volunteer:"Волонтёрство"};
    for (const [value, text] of Object.entries(employmentLabels)) {
        const label = el("label"); const input = document.createElement("input");
        input.type = "checkbox"; input.name = "employment"; input.value = value;
        label.append(input, document.createTextNode(" " + text)); $("employment").append(label);
    }
    const selected = name => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(e => e.value);
    const setSelected = (name, values) => document.querySelectorAll(`input[name="${name}"]`).forEach(e => { e.checked = values.includes(e.value); });
    function readFilters() {
      return {
        locations: locationEditor.read(),
        employment: selected("employment"),
        experience_years: $("experience").value === "" ? null : Number($("experience").value),
        salary: $("salaryMin").value === "" ? null : {minimum:Number($("salaryMin").value), currency:$("currency").value,
          period:$("salaryPeriod").value},
        published_after: $("publishedAfter").value || null,
        excluded_companies: companyTokens.values, excluded_text: textTokens.values,
      };
    }
    function fillFilters(f = {}) {
      locationEditor.load(f);
      setSelected("employment", f.employment || []);
      $("experience").value = f.experience_years ?? "";
      $("salaryMin").value = f.salary?.minimum ?? ""; $("currency").value = f.salary?.currency || "RUB";
      $("salaryPeriod").value = f.salary?.period || "month";
      $("publishedAfter").value = f.published_after || "";
      companyTokens.setValues(f.excluded_companies || []);
      textTokens.setValues(f.excluded_text || []);
    }
    $("countryRetry").onclick = () => { error(""); loadGeography().catch(e => error(e.message)); };
    async function loadCatalog() {
      sources = await api("/api/v1/sources");
      for (const source of sources) {
        const label = el("label", undefined, "type-card"), box = el("input");
        box.type = "checkbox"; box.id = "source-" + source.id; box.checked = true; box.onchange = updateSource;
        const blocked = source.state?.status === "blocked"
          ? "заблокирован до " + new Date(source.state.until * 1000).toLocaleTimeString("ru", {hour:"2-digit", minute:"2-digit"}) : "";
        label.append(box, " " + source.name, el("span", blocked || typeNames[source.type] || source.type, "n"));
        $("sources").append(label);
      }
      $("sourceOptions").replaceChildren(...sources.map(source => Object.assign(el("option"), {value:source.name, label:typeNames[source.type] || source.type})));
      $("sourceFilter").placeholder = `Все источники · ${sources.length}`;
      updateSource();
      await loadGeography();
    }
function notify(message, undo, actionLabel = "Отменить") {
  clearTimeout(toastTimer); $("toastText").textContent = message; $("toast").hidden = false;
  $("toastUndo").hidden = !undo; $("toastUndo").textContent = actionLabel;
  $("toastUndo").onclick = undo ? () => { $("toast").hidden = true; undo(); } : null;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, undo ? 8000 : 3500);
}
const currentBrief = () => briefs.find(b => b.id === route.briefId);
const briefPath = (id, folder = "open") => `#/brief/${encodeURIComponent(id)}/folder/${folder}`;
function parseRoute() {
  const [path, query = ""] = location.hash.slice(1).split("?");
  const parts = path.split("/").filter(Boolean), params = new URLSearchParams(query);
  if (path === "/briefs/new") return {view:"settings", briefId:null};
  if (parts[0] !== "brief" || !parts[1]) return {view:"home"};
  const briefId = decodeURIComponent(parts[1]);
  if (parts[2] === "settings") return {view:"settings", briefId};
  if (parts[2] === "history") return {view:"history", briefId, run:params.get("run")};
  return {view:"workspace", briefId, folder:folderNames[parts[3]] ? parts[3] : "open",
    page:Math.max(1, Number.parseInt(params.get("page"),10) || 1), q:(params.get("q") || "").slice(0,200),
    selection:["all","candidates","accepted","maybe","rejected"].includes(params.get("selection")) ? params.get("selection") : "candidates",
    sort:["first_seen","newest","updated"].includes(params.get("sort")) ? params.get("sort") : "newest",
    source:(params.get("source") || "").slice(0,100)};
}
function pageUrl(updates = {}) {
  const state = {...route, ...updates};
  const params = new URLSearchParams({page:state.page, q:state.q, selection:state.selection, sort:state.sort});
  if (state.source) params.set("source", state.source);
  return briefPath(state.briefId, state.folder) + "?" + params;
}
async function loadOverviews(signal) {
  const data = await api("/api/v1/briefs", "GET", undefined, signal);
  briefs = data;
}
function countLink(b, folder, count) {
  const a = el("a", count || 0, "count-link" + (count ? "" : " zero")); a.href = briefPath(b.id,folder);
  a.setAttribute("aria-label", `${folderNames[folder]}: ${count || 0}, ${b.name}`); return a;
}
const runStatusPills = {
  queued:["queued","○","В очереди"], running:["running","","Идёт поиск"], complete:["ok","✓","Завершён"],
  partial:["warn","!","Частичный результат"], failed:["error","✕","Ошибка"], interrupted:["error","✕","Прерван"],
  cancelled:["stopped","■","Остановлен"], blocked:["stopped","■","Заблокирован"], skipped:["skipped","–","Пропущен"], never:["skipped","–","Поиск не запускали"],
};
function runPill(status) {
  const [cls, icon, text] = runStatusPills[status] || ["stopped","",statusNames[status] || status];
  const pill = el("span", undefined, "run-pill " + cls);
  pill.append(icon ? Object.assign(el("span", icon), {ariaHidden:"true"}) : Object.assign(el("span", undefined, "pulse"), {ariaHidden:"true"}), text);
  return pill;
}
function whenText(seconds) {
  const date = new Date(seconds * 1000), now = new Date();
  const options = {day:"numeric", month:"long", hour:"2-digit", minute:"2-digit"};
  if (date.getFullYear() !== now.getFullYear()) options.year = "numeric";
  return date.toLocaleString("ru", options);
}
async function launchSearch(b, button) {
  button.disabled = true;
  try {
    await api("/api/v1/runs", "POST", {brief_id:b.id, ...searchOptionsForBrief(b)});
    await loadOverviews(); renderedHome = null; renderHome(); notify(`Поиск «${b.name}» запущен`);
  } catch (e) { error(e.message); button.disabled = false; }
}
function lastRunCell(b) {
  const cell = el("td"), box = el("div", undefined, "last-run"), r = b.last_run;
  if (!r) {
    box.append(runPill("never"));
  } else {
    box.append(runPill(r.status), el("small", ["queued","running"].includes(r.status) ? "с " + whenText(r.created_at) : whenText(r.created_at)));
  }
  if (!r || ["partial","failed","interrupted"].includes(r.status)) {
    const button = el("button", r ? "Запустить поиск снова" : "Запустить поиск", "text-button");
    button.type = "button"; button.onclick = () => launchSearch(b, button);
    if (!b.queries.length) { button.disabled = true; button.title = "Сначала добавьте поисковые запросы"; }
    box.append(button);
  }
  cell.append(box); return cell;
}
$("briefSearch").addEventListener("input", () => renderHome());
function renderHome() {
  const query = ($("briefSearch").value || "").trim().toLowerCase();
  const signature = JSON.stringify([briefs, query]);
  if (signature === renderedHome) return;
  renderedHome = signature;
  const host = $("briefTable"); host.replaceChildren(); host.removeAttribute("aria-busy");
  $("briefTools").hidden = briefs.length < 2; $("briefLegend").hidden = !briefs.length;
  host.classList.toggle("table-wrap", briefs.length > 0);
  if (!briefs.length) {
    const first = el("section", undefined, "first-brief"); first.setAttribute("aria-labelledby", "firstTitle");
    const h = el("h2", "Начните с первого брифа"); h.id = "firstTitle";
    const steps = el("ol", undefined, "first-steps");
    for (const [n, title, text] of [["1","Условия","Запросы, страна и город, формат, зарплата, исключения"],["2","Поиск","Все подходящие источники сразу; повторный поиск добавит только новые вакансии"],["3","Разбор","Отклик, собеседование, оффер или «неинтересна» — в один клик"]]) {
      const li = el("li"); li.append(Object.assign(el("b", n), {ariaHidden:"true"}), el("strong", title), el("span", text)); steps.append(li);
    }
    const a = el("a", "+ Новый бриф", "button"); a.href = "#/briefs/new";
    first.append(h, el("p", "Задайте условия поиска, затем разбирайте найденные вакансии по этапам. Для каждого направления — свой бриф: например, отдельно «QA в Москве» и «QA удалённо».", "muted"), steps, a);
    host.append(first); return;
  }
  const shown = briefs.filter(b => !query || b.name.toLowerCase().includes(query) || b.queries.some(q => q.toLowerCase().includes(query)));
  if (!shown.length) {
    const empty = el("div", undefined, "empty"); empty.append(el("strong", "Ничего не найдено"), el("p", "Измените запрос или очистите поле поиска."));
    host.append(empty); return;
  }
  const table = el("table"), caption = el("caption", "Брифы и вакансии по этапам", "sr"), head = el("thead"), row = el("tr");
  const columns = [["Бриф"],["Неразобранные","num"],["Отклик","num"],["Собеседование","num"],["Оффер","num"],["Закрыто","num","Отказ и неинтересные"],["Всего","num"],["Последний поиск"]];
  for (const [name, cls, title] of columns) { const th = el("th", name, cls); th.scope = "col"; if (title) th.title = title; row.append(th); }
  head.append(row); table.append(caption, head); const body = el("tbody");
  for (const b of shown) {
    const f = b.funnel, tr = el("tr"), title = el("td"), a = el("a", b.name); a.href = briefPath(b.id);
    title.append(a, el("small", b.queries.join(" · ") || "Запросов пока нет")); tr.append(title);
    const cells = [["open", f.open], ["applied", f.applied], ["interview", f.interview], ["offer", f.offer]];
    for (const [folder, count] of cells) {
      const td = el("td", undefined, "num"), link = countLink(b, folder, count);
      if (folder === "open" && f.new) link.classList.add("hot");
      td.append(link); tr.append(td);
    }
    const closed = (f.rejected || 0) + (f.dismissed || 0);
    const closedCell = el("td", undefined, "num"); closedCell.append(el("span", closed, "count-link" + (closed ? "" : " zero"))); tr.append(closedCell);
    const all = el("td", undefined, "num"); all.append(countLink(b, "all", f.all)); tr.append(all);
    tr.append(lastRunCell(b)); body.append(tr);
  }
  table.append(body); host.append(table);
}
function renderFolders(b) {
  const signature = JSON.stringify([b.id,b.funnel,route.folder]);
  if (renderedFolders === signature) return;
  renderedFolders = signature; $("folders").replaceChildren();
  for (const [key,name] of Object.entries(folderNames)) {
    const a = el("a",undefined,route.folder === key ? "active" : "");
    a.href = briefPath(b.id,key); a.dataset.folder = key;
    if (route.folder === key) a.setAttribute("aria-current","page");
    const count = el("span", (b.funnel[key] || 0).toLocaleString("ru"));
    if (key === "open" && b.funnel.new) {
      const fresh = el("b", "+" + b.funnel.new.toLocaleString("ru"), "fresh-count");
      count.append(" · ", fresh, el("span", " " + plural(b.funnel.new, "новая", "новые", "новых"), "sr"));
    }
    a.append(el("span",name),count); $("folders").append(a);
  }
  $("folderTitle").textContent = folderNames[route.folder];
}
const activeRun = b => !!b.last_run && ["queued","running"].includes(b.last_run.status);
function historyHref(b, run) { return `#/brief/${encodeURIComponent(b.id)}/history` + (run ? `?run=${encodeURIComponent(run)}` : ""); }
function showHidden(event) { event.preventDefault(); location.hash = pageUrl({page:1, selection:"rejected"}); }
function showSearchState(b) {
  const r = b.last_run, active = activeRun(b), banner = $("searchState");
  $("runSearch").disabled = active || !b.queries.length;
  $("runSearch").textContent = active ? "Поиск идёт…" : "Запустить поиск";
  $("runSearch").title = b.queries.length ? "" : "Сначала добавьте поисковые запросы в условиях";
  $("runBanner").hidden = !active;
  banner.hidden = active;
  if (active) { $("runHistoryLink").href = historyHref(b, r.id); showRunProgress(r); return; }
  closeStopSearch();
  banner.replaceChildren(); banner.className = "banner";
  if (!r) {
    banner.classList.add("idle");
    banner.append(el("span", "Поиск ещё не запускали. Нажмите «Запустить поиск» — найденные вакансии появятся в «Неразобранных»."));
  } else {
    const tone = {partial:"warn", failed:"error", interrupted:"error", cancelled:"idle"}[r.status];
    if (tone) banner.classList.add(tone);
    const title = {complete:"Поиск завершён", partial:"Поиск завершён частично", failed:"Поиск завершился ошибкой",
      interrupted:"Поиск прерван", cancelled:"Поиск остановлен"}[r.status] || statusNames[r.status] || r.status;
    banner.append(el("b", `${title} ${fullDate(r.created_at)}`));
    if (["partial","failed","interrupted"].includes(r.status)) {
      const again = el("button", "Запустить поиск снова", "text-button"); again.type = "button";
      again.onclick = () => startSearch(b, again); banner.append(again);
    }
  }
  if (b.hidden_count) {
    banner.append(el("span", `Скрыто по обязательным условиям: ${b.hidden_count.toLocaleString("ru")}.`));
    if (route.selection !== "rejected") {
      const show = el("a", "Показать скрытые"); show.href = pageUrl({page:1, selection:"rejected"}); show.onclick = showHidden; banner.append(show);
    }
  }
  if (r) { const history = el("a", "История поиска", "push"); history.href = historyHref(b, r.id); banner.append(history); }
}
let runProgressRequest = 0;
async function showRunProgress(summary) {
  const request = ++runProgressRequest;
  try {
    const run = await api(`/api/v1/runs/${encodeURIComponent(summary.id)}`);
    if (request !== runProgressRequest) return;
    const states = Object.values(run.sources || {}), total = states.length;
    const count = (...statuses) => states.filter(state => statuses.includes(state.status)).length;
    const done = count("complete","partial","skipped","blocked","cancelled"), failed = count("failed","interrupted");
    const parts = [[`${done} из ${total}`, ` ${plural(total, "источника", "источников", "источников")} готовы`]];
    if (count("running")) parts.push([count("running"), " идут"]);
    if (count("queued")) parts.push([count("queued"), " в очереди"]);
    if (failed) parts.push([failed, " с ошибкой", "err"]);
    const host = $("runProgress"); host.replaceChildren();
    if (!total) { host.append(el("span", "Готовим источники…")); }
    for (const [number, text, cls] of parts) { const span = el("span", undefined, cls); span.append(el("b", String(number)), text); host.append(span); }
    host.append(el("span", `Запущен ${fullDate(run.created_at)}`));
  } catch { /* the banner keeps the last known progress */ }
}
function closeStopSearch() { $("stopSearch").setAttribute("aria-expanded","false"); $("stopSearchPopover").hidden = true; }
async function startSearch(b, button) {
  button.disabled = true;
  try {
    await api("/api/v1/runs", "POST", {brief_id:b.id, ...searchOptionsForBrief(b)});
    notify(`Поиск «${b.name}» запущен`);
    await refresh().catch(handleRefreshError);
  } catch (e) { error(e.message); button.disabled = false; }
}
let shownHash = location.hash;
function navigate() {
  if (formDirty() && !submitting && parseRoute().view !== "settings"
      && !confirm("Уйти без сохранения? Изменения в условиях брифа пропадут.")) {
    history.replaceState(null, "", shownHash);
    return;
  }
  shownHash = location.hash;
  const next = parseRoute(), previous = route;
  route = next; viewVersion++; requestVersion++; refreshController?.abort(); clearTimeout(searchTimer);
  error(""); closeVacancy(); closePopups();
  for (const id of ["home","workspace","settings","history"]) $(id+"View").hidden = id !== route.view;
  if (route.briefId && !currentBrief()) {
    error("Бриф не найден. Вернитесь к списку брифов.");
    for (const id of ["workspace","settings","history"]) $(id+"View").hidden = true;
    return;
  }
  const b = currentBrief();
  document.title = b ? `${b.name} · Поиск работы` : route.view === "settings" ? "Новый бриф · Поиск работы" : "Мои брифы · Поиск работы";
  if (route.view === "home") renderHome();
  if (route.view === "settings") fillSettings(b);
  if (route.view === "workspace") {
    $("briefTitle").textContent = b.name;
    renderBriefSummary(b);
    $("vacancySearch").value = route.q; $("selectionView").value = route.selection; $("sortView").value = route.sort;
    $("sourceFilter").value = sources.find(s => s.id === route.source)?.name || route.source;
    renderFolders(b); showSearchState(b);
    // Keep row nodes for page-only navigation until the replacement has arrived.
    if (previous.briefId !== route.briefId || previous.folder !== route.folder || previous.view !== route.view) {
      $("results").replaceChildren(el("div","Загружаем вакансии…","empty"));
      renderedCards = null; renderedPagination = null;
      $("pagination").hidden = true;
    }
  }
  if (route.view === "history") $("historyTitle").textContent = "История · " + b.name;
  if (previous.view !== route.view || previous.briefId !== route.briefId) window.scrollTo(0,0);
  refresh().catch(handleRefreshError);
}
function handleRefreshError(e) { if (!servicePaused && e.name !== "AbortError") error("Не удалось обновить данные: " + e.message); }
function salaryText(v) {
  const s = v.salary || {};
  if (!s.from && !s.to) return "";
  return [s.from ? "от " + Number(s.from).toLocaleString("ru") : "", s.to ? "до " + Number(s.to).toLocaleString("ru") : "", s.currencyCode || "", s.gross === false ? "на руки" : s.gross === true ? "до налогов" : ""].filter(Boolean).join(" ");
}
function renderBriefSummary(b) {
  const summary = $("briefSummary"); summary.replaceChildren();
  if (b.queries.length) summary.append(`Запросы: ${b.queries.join(" · ")}`);
  const chosen = b.search?.sources || [];
  const sourceText = !chosen.length || chosen.length === sources.length ? `все, ${sources.length}` : chosen.map(id => sourceName(id)).join(", ");
  summary.append(`${b.queries.length ? " · " : ""}Источники: ${sourceText}`);
}
const sourceName = id => sources.find(s => s.id === id)?.name || id;
function placeText(v) {
  const place = v.locations?.find(l => !l.excluded && (l.city || l.country));
  const city = place?.city || place?.country || v.location || "";
  const format = v.locations?.find(l => l.format)?.format || v.work_formats?.[0];
  return {city, format:workFormatNames[format] || ""};
}
const openStatuses = ["new","viewed"];
function stagePill(status) { return el("span", userStatusNames[status] || status, "pill stage-" + status); }
async function quickStage(v, status, buttons) {
  for (const button of buttons) button.disabled = true;
  const briefId = route.briefId, previous = v.user_status;
  const save = next => api(`/api/v1/briefs/${briefId}/vacancies/${encodeURIComponent(v.key)}`, "PATCH", {status:next, note:v.note || ""});
  try {
    await save(status);
    notify(`«${v.title}» → ${userStatusNames[status]}`, async () => {
      try { await save(previous); notify(`Этап «${v.title}» возвращён`); }
      catch (e) { error("Не удалось отменить: " + e.message); }
      refresh().catch(handleRefreshError);
    });
    await refresh().catch(handleRefreshError);
  } catch (e) { error("Не удалось сохранить этап: " + e.message); for (const button of buttons) button.disabled = false; }
}
function vacancyRow(v) {
  const row = el("div", undefined, "r" + (v.user_status === "new" ? " fresh" : "")); row.setAttribute("role","row"); row.dataset.id = v.key;
  const cell = (cls = "") => { const c = el("div", undefined, "cell " + cls); c.setAttribute("role","cell"); row.append(c); return c; };
  const company = v.company?.name || "Компания не указана";
  const main = cell(), title = el("button", v.title, "vac-btn"); title.type = "button"; title.onclick = () => openVacancy(v.key);
  const meta = el("div", undefined, "r-meta"); meta.append(el("span", company));
  const match = selectionLabel(v.selection); if (match) meta.append(el("span", match.text, "pill " + match.cls));
  if (v.user_status === "new") meta.append(el("span", "Новая", "pill new"));
  main.append(title, meta);
  const place = placeText(v), where = cell();
  where.append(el("div", place.city || "Не указано", place.city ? "" : "muted")); if (place.format) where.append(el("div", place.format, "t2"));
  const salary = salaryLine(v.salary);
  cell().append(el("span", salary || "Не указана", salary ? "has-salary" : "no-salary"));
  const origin = cell(); origin.append(el("div", sourceName(v.source)), el("div", shortDate(v.first_seen), "t2"));
  cell().append(stagePill(v.user_status));
  const quick = cell("end");
  if (openStatuses.includes(v.user_status)) {
    const box = el("span", undefined, "qb"), yes = el("button", "Откликнулся"), no = el("button", "Неинтересна");
    yes.type = no.type = "button";
    yes.setAttribute("aria-label", `Откликнулся: ${v.title}, ${company}`); no.setAttribute("aria-label", `Неинтересна: ${v.title}, ${company}`);
    yes.onclick = () => quickStage(v, "applied", [yes, no]); no.onclick = () => quickStage(v, "dismissed", [yes, no]);
    box.append(yes, no); quick.append(box);
  } else {
    quick.append(el("span", "—", "muted"), el("span", "этап уже выбран", "sr"));
  }
  return row;
}
function showCards(items) {
  const signature = JSON.stringify([route.briefId,route.folder,route.page,route.q,route.selection,route.sort,route.source,items]);
  if (renderedCards === signature) return;
  const rows = [...$("results").querySelectorAll(".r[data-id]")];
  const anchor = rows.find(c => c.getBoundingClientRect().bottom>0 && c.getBoundingClientRect().top<innerHeight);
  const anchorTop = anchor?.getBoundingClientRect().top;
  const focusedId = document.activeElement?.closest?.(".r[data-id]")?.dataset.id;
  renderedCards = signature; pageItems = items; $("results").replaceChildren();
  if (!items.length) {
    const filtered = route.q || route.source || route.selection !== "candidates";
    const empty = el("div",undefined,"empty");
    empty.append(el("strong", filtered ? "Ничего не найдено" : "В этой папке пока нет вакансий"));
    empty.append(el("p", filtered ? "Измените поиск, источник или фильтр соответствия." : route.folder === "open" ? "Новые находки появятся здесь после поиска." : "Откройте вакансию и выберите этап."));
    $("results").append(empty); return;
  }
  const head = el("div", undefined, "r h"); head.setAttribute("role","row");
  for (const [text, cls] of [["Вакансия"],["Место"],["Зарплата"],["Источник, дата"],["Этап"],["Быстрый этап","end"]]) {
    const h = el("span", text, cls); h.setAttribute("role","columnheader"); head.append(h);
  }
  $("results").append(head, ...items.map(vacancyRow));
  if (focusedId) $("results").querySelector(`.r[data-id="${CSS.escape(focusedId)}"] .vac-btn`)?.focus({preventScroll:true});
  if(anchor){const updated=[...$("results").querySelectorAll(".r[data-id]")].find(c=>c.dataset.id===anchor.dataset.id);if(updated)window.scrollBy(0,updated.getBoundingClientRect().top-anchorTop);}
}
function showPagination(data) {
  pageInfo={page:data.page,pages:data.pages,total:data.total};
  if($("vacancyDialog").open && data.total>readerTotal){
    readerTotal=data.total;readerEnds.clear();
    if(!readerSaving)updateReaderNavigation();
  }
  const signature=JSON.stringify([route.briefId,route.folder,route.q,route.selection,route.sort,route.source,data.total,data.page,data.pages]);
  $("folderCount").textContent = `Вакансий: ${data.total.toLocaleString("ru")}`;
  for(const id of ["pagination"]){
    const nav=$(id); nav.hidden=data.pages<=1&&!data.total;if(renderedPagination===signature)continue;nav.replaceChildren();
    const prev=el("button","← Назад","secondary"), next=el("button","Далее →","secondary");
    prev.disabled=data.page<=1;next.disabled=data.page>=data.pages;
    prev.onclick=()=>{location.hash=pageUrl({page:data.page-1});};next.onclick=()=>{location.hash=pageUrl({page:data.page+1});};
    const label=el("label","Страница "),select=el("select");select.setAttribute("aria-label","Номер страницы");
    for(let n=1;n<=data.pages;n++){const option=el("option",`${n} из ${data.pages}`);option.value=n;select.append(option);}select.value=data.page;
    select.onchange=()=>{location.hash=pageUrl({page:Number(select.value)});};label.append(select);
    const first=data.total?(data.page-1)*data.page_size+1:0,last=Math.min(data.page*data.page_size,data.total);
    nav.append(prev,label,next,el("small",`${first}–${last} из ${data.total}`));
  }
  renderedPagination=signature;
}
function closeVacancy() {
  detailVersion++;
  readerController?.abort();readerController=null;readerTransition=false;
  $("vacancyFooter").inert=false;
  const selectedKey=selectedVacancy?.key;
  if($("vacancyDialog").open)$("vacancyDialog").close();
  if(readerChangedPage && route.view==="workspace"){
    [...$("results").querySelectorAll(".r[data-id]")].find(c=>c.dataset.id===selectedKey)?.scrollIntoView({block:"center"});
  }
  selectedVacancy=null;readerItems=[];readerIndex=-1;readerDrafts.clear();readerChangedPage=false;readerEnds.clear();
}
$("closeVacancy").onclick=closeVacancy;
$("closeVacancyIcon").onclick=closeVacancy;
$("vacancyDialog").addEventListener("cancel",e=>{e.preventDefault();if(!closeTips())closeVacancy();});
// Close only when the click starts and ends outside the reader, not when selecting text.
let readerBackdropPressed=false;
function outsideReader(event) {
  const dialog=$("vacancyDialog"), bounds=dialog.getBoundingClientRect();
  return event.target===dialog && (event.clientX<bounds.left || event.clientX>bounds.right || event.clientY<bounds.top || event.clientY>bounds.bottom);
}
$("vacancyDialog").addEventListener("pointerdown",e=>{readerBackdropPressed=e.button===0 && outsideReader(e);});
$("vacancyDialog").addEventListener("pointercancel",()=>{readerBackdropPressed=false;});
$("vacancyDialog").addEventListener("click",e=>{
  const close=readerBackdropPressed && outsideReader(e);
  readerBackdropPressed=false;
  if(close)closeVacancy();
});
function readerEntry(v) {
  return {key:v.key,time:route.sort==="updated"?(v.status_updated_at ?? v.first_seen):v.first_seen};
}
function updateReaderNavigation() {
  $("previousVacancy").disabled=readerTransition || readerIndex<0 || (readerIndex===0 && (readerPage<=1 || readerEnds.has(-1)));
  $("nextVacancy").disabled=readerTransition || readerIndex<0 || (readerIndex===readerItems.length-1 && ((readerPage-1)*pageSize+readerItems.length>=readerTotal || readerEnds.has(1)));
  $("readerPosition").textContent=readerTransition?"Загружаем…":readerIndex<0?"":`${(readerPage-1)*pageSize+readerIndex+1} из ${readerTotal}`;
  $("readerPosition").title="Вакансии в выбранной папке с учётом фильтров";
}
async function adjacentVacancy(direction) {
  if(readerTransition || readerIndex<0)return;
  const item=readerItems[readerIndex+direction];
  if(item){await openVacancy(item.key,true);return;}
  const version=detailVersion, context=viewVersion;
  const valid=()=>version===detailVersion && context===viewVersion && $("vacancyDialog").open;
  readerTransition=true;readerController=new AbortController();
  ++requestVersion;refreshController?.abort();
  $("vacancyFooter").inert=true;updateReaderNavigation();readerMessage("");
  try{
    const params=new URLSearchParams({direction:direction>0?"next":"previous",page_size:pageSize,status:route.folder,selection:route.selection,q:route.q,sort:route.sort,source:route.source});
    params.set("anchor_time",readerItems[readerIndex].time);
    const result=await api(`/api/v1/briefs/${route.briefId}/vacancies/${encodeURIComponent(readerItems[readerIndex].key)}/adjacent?${params}`,"GET",undefined,readerController.signal);
    if(!valid())return;
    if(!result.key){
      readerEnds.add(direction);
      readerMessage(direction>0?"Дальше вакансий в этой папке нет.":"Это первая вакансия в этой папке.");
      return;
    }
    const data=result.page;
    readerItems=data.items.map(readerEntry);readerPage=data.page;readerTotal=data.total;readerEnds.clear();
    route.page=data.page;history.replaceState(null,"",pageUrl());
    showPagination(data);showCards(data.items);$("pagination").dataset.page=data.page;
    $("results").removeAttribute("aria-busy");readerChangedPage=true;
    readerTransition=false;readerController=null;$("vacancyFooter").inert=false;
    await openVacancy(result.key,true);
  }catch(e){
    if(valid() && e.name!=="AbortError")readerMessage(direction>0?"Не удалось загрузить следующую вакансию.":"Не удалось загрузить предыдущую вакансию.",()=>adjacentVacancy(direction));
  }finally{
    if(valid()){
      readerTransition=false;readerController=null;$("vacancyFooter").inert=false;updateReaderNavigation();
    }
  }
}
$("previousVacancy").onclick=()=>adjacentVacancy(-1);
$("nextVacancy").onclick=()=>adjacentVacancy(1);
let readerSaving=false, noteTimer=null, noteSaving=Promise.resolve();
/** One line in the reader footer: info, or an error with «Повторить». */
function readerMessage(text, retry) {
  const host=$("readerMessage");host.replaceChildren();host.hidden=!text;
  host.className=retry?"inline-err":"reader-note";host.setAttribute("role",retry?"alert":"status");
  if(!text)return;
  host.append(el("span",text));
  if(retry){const again=el("button","Повторить","secondary");again.type="button";again.onclick=()=>{readerMessage("");retry();};host.append(again);}
}
/* Tips on the match pill and condition chips: one open at a time, Esc closes it first. */
function closeTips(except){
  let closed=false;
  for(const button of $("vacancyBody").querySelectorAll('[aria-expanded="true"][data-tip]')){
    if(button===except)continue;
    button.setAttribute("aria-expanded","false");button.classList.remove("open");closed=true;
  }
  return closed;
}
function tipButton(cls, content, head, text, id){
  const button=el("button",undefined,cls);button.type="button";button.dataset.tip="";
  button.setAttribute("aria-expanded","false");button.setAttribute("aria-controls",id);
  const tip=el("span",undefined,"tip");tip.id=id;tip.setAttribute("role","note");
  tip.append(el("b",head),el("br"),text,el("span","Esc — закрыть","tip-x"));
  button.append(...content,tip);
  button.onclick=()=>{const open=button.getAttribute("aria-expanded")!=="true";closeTips(button);
    button.setAttribute("aria-expanded",String(open));button.classList.toggle("open",open);};
  return button;
}
$("vacancyDialog").addEventListener("click",e=>{if(!e.target.closest("[data-tip]"))closeTips();});
async function flushNote(){
  clearTimeout(noteTimer);noteTimer=null;
  const note=$("vacancyNote");
  if(note&&selectedVacancy&&note.value!==(selectedVacancy.note||""))await saveVacancy({note:note.value});
  await noteSaving;
}
async function openVacancy(key,adjacent=false) {
  if(adjacent){
    const note=$("vacancyNote");
    if(selectedVacancy&&note)readerDrafts.set(selectedVacancy.key,note.value);
    flushNote().catch(()=>{});
  }else{
    readerItems=pageItems.map(readerEntry);readerDrafts.clear();readerEnds.clear();
    readerPage=pageInfo.page;readerTotal=pageInfo.total;readerChangedPage=false;
  }
  readerIndex=readerItems.findIndex(v=>v.key===key);
  updateReaderNavigation();
  const version=++detailVersion, briefId=route.briefId;
  selectedVacancy=null;readerMessage("");$("stageLine").hidden=true;
  $("vacancySource").replaceChildren();$("vacancyStageRow").hidden=true;
  const loading=el("h2","Загружаем…");loading.id="vacancyTitle";$("vacancyBody").replaceChildren(loading);
  if(!$("vacancyDialog").open)$("vacancyDialog").showModal();
  try{
    const v=await api(`/api/v1/briefs/${briefId}/vacancies/${encodeURIComponent(key)}`);
    if(version!==detailVersion)return;selectedVacancy=v;renderVacancy(v,briefId);
  }catch(e){
    if(version!==detailVersion)return;
    const box=el("div",undefined,"open-error");box.setAttribute("role","alert");
    const title=el("h2","Не удалось открыть вакансию");title.id="vacancyTitle";
    const actions=el("div",undefined,"actions"),retry=el("button","Повторить"),close=el("button","Закрыть","secondary");
    retry.type=close.type="button";retry.onclick=()=>openVacancy(key,true);close.onclick=closeVacancy;actions.append(retry,close);
    box.append(title,el("p",e instanceof TypeError?"Сервис не ответил. Проверьте, что он работает, и попробуйте ещё раз.":e.message),actions);
    $("vacancyBody").replaceChildren(box);
  }
}
function conditionLabel(check,v){
  const place=placeText(v);
  switch(check.field){
    case "locations": return place.city || "Место не указано";
    case "minimum_work_format": return place.format || "Формат не указан";
    case "salary": return salaryLine(v.salary) || "Зарплата не указана";
    case "experience_years":
      if(v.experience)return v.experience;
      return v.min_experience!=null?`Опыт от ${v.min_experience.toLocaleString("ru")} ${plural(Math.ceil(v.min_experience),"года","лет","лет")}`:"Опыт не указан";
    case "published_after": return v.published_at?`Опубликована ${dayMonth(v.published_at)}`:"Дата публикации не указана";
    case "employment": return v.employment?.length?"Занятость: "+v.employment.map(e=>(employmentLabels[e]||e).toLowerCase()).join(", "):"Занятость не указана";
    case "query": case "profile": {
      const title=(v.title||"").toLowerCase(),query=(currentBrief()?.queries||[]).find(q=>title.includes(q.toLowerCase()));
      return query?`Запрос: ${query}`:conditionNames[check.field];
    }
    default: return conditionNames[check.field] || check.field;
  }
}
function selectionSummary(v){
  const checks=v.selection?.checks||[];
  if(v.selection?.status==="rejected")return "Вакансия не подходит по обязательному условию брифа и скрыта из списка.";
  if(v.selection?.status==="maybe")return "Есть несовпадения с пожеланиями брифа.";
  if(checks.some(c=>c.status==="unknown"||c.supported===false))return "Противоречий с брифом нет, но часть условий не удалось проверить.";
  return "Все условия брифа совпали.";
}
function renderVacancy(v,briefId) {
  const body=$("vacancyBody");body.replaceChildren();body.scrollTop=0;closeTips();
  const checks=v.selection?.checks||[], head=el("div",undefined,"vac-head");
  if(v.selection&&checks.length){
    const match=selectionLabel(v.selection);
    head.append(tipButton("pill "+match.cls,[match.text],"Соответствие брифу",selectionSummary(v),"selectionTip"));
  }else head.append(el("span","Без оценки","pill plain"));
  if(v.user_status==="new")head.append(el("span","Новая","pill new"));
  const title=el("h2",v.title);title.id="vacancyTitle";
  const place=placeText(v), line=el("p",[v.company?.name,[place.city,place.format].filter(Boolean).join(", ")].filter(Boolean).join(" · ")||"Компания и регион не указаны","muted vac-line");
  const site=v.company?.site_url?link("Сайт работодателя ↗",v.company.site_url):null;
  if(site?.hasAttribute("href")){site.append(el("span"," (откроется в новой вкладке)","sr"));line.append(" · ",site);}
  body.append(head,title,line);
  const salary=salaryLine(v.salary);
  if(salary)body.append(el("p",[salary,v.salary?.gross===false?"на руки":v.salary?.gross===true?"до вычета налогов":""].filter(Boolean).join(" "),"vac-salary"));
  if(checks.length){
    const chips=el("div",undefined,"chips");
    checks.forEach((check,n)=>{
      const tone=checkTone(check),icon=el("i",{ok:"✓",no:"✕",unk:"?"}[tone]);icon.setAttribute("aria-hidden","true");
      chips.append(tipButton("chip "+tone,[icon,conditionLabel(check,v),el("span",": "+checkStatusNames[tone==="unk"?"unknown":tone==="no"?"rejected":"accepted"].toLowerCase(),"sr")],checkHead(check),check.reason,"checkTip"+n));
    });
    body.append(chips);
    const status=v.selection.status;
    const problems=status==="rejected"?checks.filter(c=>c.status==="rejected"&&c.kind==="hard")
      :status==="maybe"?checks.filter(c=>c.status==="rejected")
      :checks.filter(c=>c.status==="unknown"||c.supported===false);
    if(problems.length){
      const why=el("div",undefined,"why"),list=el("ul");
      why.append(el("h3",status==="rejected"?"Почему вакансия скрыта":status==="maybe"?"Почему «Может быть подходит»":"Что не удалось проверить"));
      for(const check of problems){const item=el("li");item.append(el("b",checkHead(check)+".")," "+check.reason);list.append(item);}
      why.append(list);body.append(why);
    }
  }else{
    const why=el("div",undefined,"why");
    why.append(el("p","В брифе нет условий отбора, поэтому соответствие не оценивается."));
    const edit=el("a","Задать условия брифа");edit.href=`#/brief/${encodeURIComponent(briefId)}/settings`;why.append(edit);body.append(why);
  }
  body.append(noteBox(v));
  const sourceName=sources.find(s=>s.id===v.source)?.name || "сайте источника";
  const original=()=>{const a=link(`Открыть на ${sourceName} ↗`,v.url);a.className="button secondary ext";a.append(el("span"," (откроется в новой вкладке)","sr"));return a;};
  $("vacancySource").replaceChildren(original());
  if(v.description){
    body.append(el("h3","Описание вакансии"));
    if(v.summary&&v.summary!==v.description)body.append(descriptionContent(v.summary,v.summary_html));
    body.append(descriptionContent(v.description,v.description_html));
  }else{
    if(v.summary)body.append(el("h3","Краткое описание"),descriptionContent(v.summary,v.summary_html));
    const missing=el("div",undefined,"ph");
    missing.append(el("p","Полное описание пока не получено. Его можно прочитать на сайте источника."),original());
    body.append(missing);
  }
  if(v.skills?.length)body.append(el("p","Навыки: "+v.skills.join(" · "),"muted vac-skills"));
  renderVacancyActions(v,briefId);
}
function noteBox(v){
  const box=el("div",undefined,"note-box"),label=el("label","Ваша заметка"),status=el("span");
  label.htmlFor="vacancyNote";status.id="noteStatus";status.setAttribute("aria-live","polite");label.append(status);
  const note=el("textarea");note.id="vacancyNote";note.rows=2;note.maxLength=2000;
  note.placeholder="Дата собеседования, условия или ваши пометки";
  note.value=readerDrafts.get(v.key) ?? v.note ?? "";
  status.textContent=v.note?"Сохранено":"";
  note.oninput=()=>{status.textContent="Изменено";clearTimeout(noteTimer);noteTimer=setTimeout(()=>flushNote().catch(()=>{}),1200);};
  note.onblur=()=>{if(noteTimer)flushNote().catch(()=>{});};
  box.append(label,note);return box;
}
/* All writes of the open vacancy go through one queue, so a note never overwrites a newer stage. */
function saveVacancy({status,note}){
  const v=selectedVacancy,briefId=route.briefId,version=detailVersion;
  if(!v)return Promise.resolve();
  const job=noteSaving.catch(()=>{}).then(async()=>{
    const current=version===detailVersion?selectedVacancy:v;
    const body={status:status??current.user_status,note:note??(version===detailVersion?$("vacancyNote")?.value:undefined)??current.note??""};
    const noteStatus=()=>version===detailVersion?$("noteStatus"):null;
    if(note!==undefined&&noteStatus())noteStatus().textContent="Сохраняем…";
    try{
      const updated=await api(`/api/v1/briefs/${briefId}/vacancies/${encodeURIComponent(v.key)}`,"PATCH",body);
      if(version===detailVersion){
        selectedVacancy=updated;readerDrafts.delete(v.key);
        if(note!==undefined&&noteStatus())noteStatus().textContent="Сохранено в "+new Date().toLocaleTimeString("ru",{hour:"2-digit",minute:"2-digit"});
      }
      refresh().catch(handleRefreshError);
      return updated;
    }catch(e){
      if(version===detailVersion){
        if(note!==undefined&&noteStatus())noteStatus().textContent="Не сохранено";
        readerMessage("Не удалось сохранить: "+e.message,()=>saveVacancy({status,note}));
      }
      throw e;
    }
  });
  noteSaving=job;return job;
}
const readerStages=["new","applied","interview","offer","rejected","dismissed"];
function renderVacancyActions(v,briefId) {
  const host=$("vacancyStages");host.replaceChildren();$("vacancyStageRow").hidden=false;
  const pressed=status=>status==="viewed"?"new":status;
  const buttons=readerStages.map(status=>{
    const button=el("button",userStatusNames[status]);button.type="button";button.dataset.status=status;
    button.onclick=()=>chooseStage(status);return button;
  });
  host.append(...buttons);
  function update(){for(const b of buttons){b.setAttribute("aria-pressed",String(b.dataset.status===pressed(selectedVacancy?.user_status||v.user_status)));b.disabled=readerSaving;}}
  async function chooseStage(status,undo=false){
    const previous=selectedVacancy.user_status;
    if(pressed(previous)===status&&!undo)return;
    readerSaving=true;update();updateReaderNavigation();
    try{
      await saveVacancy({status});
      update();
      if(undo){$("stageLine").hidden=true;return;}
      showStageLine(status,previous,s=>chooseStage(s,true));
    }catch{/* readerMessage shows the error */}
    finally{readerSaving=false;update();updateReaderNavigation();}
  }
  update();
}
function showStageLine(status,previous,undo){
  const line=$("stageLine");line.replaceChildren();line.hidden=false;
  const text=el("span","Этап: ");text.append(el("b",userStatusNames[status]));
  const back=el("button","Отменить","undo");back.type="button";back.onclick=()=>undo(previous);
  line.append(text,back);
  if(route.folder==="open"&&!openStatuses.includes(status)){
    if($("nextVacancy").disabled)line.append(el("span","Неразобранных дальше нет","push"));
    else{const next=el("button","Следующая неразобранная →","push");next.type="button";next.onclick=()=>adjacentVacancy(1);line.append(next);}
  }
}
function searchOptionsForBrief(b) {
  // Saved run diagnostics contain per-source outcomes, unlike editable search options.
  const saved=b?.search || b?.last_run?.request || {};
  const profiles=saved.company_profiles;
  return {
    sources:[...(saved.sources || [])],
    limits:structuredClone(saved.limits || {}),
    company_profiles:profiles && typeof profiles==="object"
      ? Object.values(profiles).some(value=>value===true)
      : profiles ?? null,
    second_chance:saved.second_chance ?? null,
  };
}
function fillSettings(b) {
  resetFormValidation();
  $("settingsTitle").textContent=b?b.name:"Новый бриф";
  $("settingsBack").textContent=b?"← К вакансиям брифа":"← Все брифы";
  $("settingsBack").href=$("settingsCancel").href=b?briefPath(b.id):"#/briefs";
  $("settingsIntro").textContent=b?"Условия брифа. Изменения применятся к следующему поиску; найденные вакансии, этапы и заметки сохранятся."
    :"Что искать, где и какие вакансии вам подходят.";
  $("name").value=b?.name || "";queryTokens.setValues(b?.queries || []);$("briefText").value=b?.text || "";
  $("briefNotice").textContent=b?.migration_note || b?.filters_migration_notice || "";fillFilters(b?.filters || {});
  settingsSearch=searchOptionsForBrief(b);
  const selected=settingsSearch.sources?.length?settingsSearch.sources:sources.map(s=>s.id);
  for(const s of sources)$("source-"+s.id).checked=selected.includes(s.id);
  const limits=selected.map(id=>settingsSearch.limits?.[id] || {});
  for(const [id,key] of [["limit","max_items_per_query"],["pages","max_pages_per_query"]]){
    const values=[...new Set(limits.map(l=>l[key]??""))];$(id).value=values.length===1?values[0]:"";
    $(id).placeholder=values.length>1?"Разные лимиты источников":"По настройкам";
  }
  initialLimit=$("limit").value;initialPages=$("pages").value;
  $("searchForm").querySelector(".search-volume").open = false;
  $("secondChance").checked=settingsSearch.second_chance===true;
  $("profiles").checked=settingsSearch.company_profiles!==false;updateSource();
  formSnapshot=formState();renderConditionsSummary();
}
/* Everything the user can change, without validation; compared to detect unsaved edits. */
let formSnapshot=null;
function formState(){
  return JSON.stringify([$("name").value.trim(),queryTokens.values,$("briefText").value,locationEditor.locations,selected("employment"),
    $("experience").value,$("salaryMin").value,$("currency").value,$("salaryPeriod").value,$("publishedAfter").value,
    companyTokens.values,textTokens.values,chosenSources().map(s=>s.id),$("secondChance").checked,$("profiles").checked,$("limit").value,$("pages").value]);
}
const formDirty=()=>route.view==="settings"&&formSnapshot!==null&&formState()!==formSnapshot;
function summaryList(rows){
  const dl=el("dl");
  for(const [term,value] of rows)if(value){dl.append(el("dt",term),el("dd",value));}
  return dl.childElementCount?dl:el("p","Не заданы","muted");
}
const formatPhrases={office:"любой: офис, гибрид или удалённо",hybrid:"гибрид или удалённо",remote_country:"удалённо по стране или по миру",remote_worldwide:"удалённо по миру"};
function renderConditionsSummary(){
  const groups=locationEditor.groups.filter(g=>g.country);
  const countryName=code=>locationEditor.countries.find(c=>c.code===code)?.name||code;
  const places=groups.map(g=>[countryName(g.country),...(g.wholeCountry?[]:g.cities.map(c=>c.city_display_name||c.city))].join(", ")).join("; ");
  const formats=[...new Set(groups.map(g=>formatPhrases[g.minimum]).filter(Boolean))].join("; ");
  const relocation=groups.some(g=>g.relocation)?"нужна помощь с переездом":"";
  const periods={month:"в месяц",year:"в год",hour:"в час"};
  const salary=$("salaryMin").value?`от ${money($("salaryMin").value)} ${currencySigns[$("currency").value]||$("currency").value} ${periods[$("salaryPeriod").value]}`:"";
  const years=$("experience").value;
  const exclusions=[...companyTokens.values.map(c=>`компания «${c}»`),...textTokens.values.map(t=>`слово «${t}»`)].join(", ");
  const host=$("conditionsSummary");host.replaceChildren();
  const heading=(cls,text,note)=>{const h=el("h3");h.append(el("span",text,"kind "+cls));host.append(h,el("p",note,"kind-note"));};
  heading("must","Обязательные","Несовпадение скрывает вакансию");
  host.append(summaryList([["Запросы",queryTokens.values.join(" · ")],["Места",places],["Исключения",exclusions]]));
  heading("wish","Пожелания","Несовпадение — «Может быть подходит»");
  host.append(summaryList([["Формат",formats],["Релокация",relocation],["Зарплата",salary],
    ["Опыт",years===""?"":`ваш опыт ${years.replace(".",",")} ${plural(Math.ceil(Number(years)),"год","года","лет")}`],
    ["Публикация",$("publishedAfter").value?"не раньше "+dayMonth($("publishedAfter").value):""],
    ["Занятость",selected("employment").map(v=>employmentLabels[v].toLowerCase()).join(", ")]]));
  const chosen=chosenSources();
  host.append(el("h3","Источники"),el("p",chosen.length===sources.length?`Все · ${sources.length}: ${sources.map(s=>s.name).join(", ")}`:chosen.length?chosen.map(s=>s.name).join(", "):"Не выбраны","src-sum"));
  const dirty=formDirty();$("dirtyNote").hidden=!dirty||!route.briefId;
  $("dirtyNote").textContent="Есть несохранённые изменения. Если уйти со страницы, спросим, можно ли их потерять.";
}
window.addEventListener("beforeunload",e=>{if(formDirty()&&!submitting){e.preventDefault();e.returnValue="";}});
function readSearchOptions() {
  const limits={};
  for(const source of chosenSources()){
    const own={...settingsSearch.limits?.[source.id]};
    for(const [id,key,initial] of [["limit","max_items_per_query",initialLimit],["pages","max_pages_per_query",initialPages]]){
      if($(id).value!==initial){if($(id).value)own[key]=Number($(id).value);else delete own[key];}
    }
    limits[source.id]=own;
  }
  return {sources:chosenSources().map(s=>s.id),limits,company_profiles:$("profiles").checked,second_chance:$("secondChance").checked};
}
function clearFormErrors() {
  for (const {target, message} of validationFields) {
    target.removeAttribute("aria-invalid");
    const descriptions = (target.getAttribute("aria-describedby") || "").split(" ").filter(id => id && id !== message.id);
    if (descriptions.length) target.setAttribute("aria-describedby", descriptions.join(" "));
    else target.removeAttribute("aria-describedby");
    message.remove();
  }
  validationFields = [];
}
function resetFormValidation() {
  validationAttempted = false;
  clearFormErrors();
  $("formSummary").hidden = true;
  if ($("error").textContent === validationSummary) error("");
}
function validateSearchForm(focus = false) {
  clearFormErrors();
  const errors = new Map();
  if (!$("name").value.trim()) errors.set($("name"), "Назовите бриф, чтобы отличать его от других.");
  for (const editor of tokenEditors) if (editor.error) errors.set(editor.input, editor.error);
  if (queryTokens.required && !queryTokens.values.length)
    errors.set($("queries"), "Добавьте хотя бы один поисковый запрос для выбранного источника.");
  if (!chosenSources().length) errors.set($("sourcesBox"), "Выберите хотя бы один источник поиска.");
  for (const {target, message} of locationEditor.validationErrors()) errors.set(target, message);
  for (const field of $("searchForm").querySelectorAll("input, textarea, select")) {
    if (field.disabled || field.validity.valid || errors.has(field)) continue;
    const validity = field.validity;
    errors.set(field, validity.valueMissing ? "Заполните это поле."
      : validity.badInput ? "Введите число."
      : validity.rangeUnderflow ? `Значение должно быть не меньше ${field.min}.`
      : validity.rangeOverflow ? `Значение должно быть не больше ${field.max}.`
      : validity.stepMismatch ? `Допустимый шаг значения: ${field.step}.`
      : validity.tooLong ? `Не больше ${field.maxLength} символов.` : "Укажите корректное значение.");
  }
  for (const [target, text] of errors) {
    const message = el("small", text, "field-error"); message.id = `brief-field-error-${++validationId}`;
    target.setAttribute("aria-invalid", "true");
    target.setAttribute("aria-describedby", [target.getAttribute("aria-describedby"), message.id].filter(Boolean).join(" "));
    (target.closest(".token-editor, .location-picker") || target).after(message); validationFields.push({target, message});
  }
  const summary=$("formSummary");
  if (errors.size && (focus || !summary.hidden)) {
    $("formSummaryTitle").textContent=`Проверьте ${errors.size} ${plural(errors.size,"поле","поля","полей")}, чтобы сохранить бриф`;
    $("formSummaryList").replaceChildren(...[...errors].map(([target,text])=>{
      const item=el("li"),a=el("a",`${fieldName(target)}: ${text.charAt(0).toLowerCase()+text.slice(1)}`);a.href="#";
      a.onclick=event=>{event.preventDefault();focusField(target);};item.append(a);return item;
    }));
    summary.hidden=false;
  } else summary.hidden=true;
  if (focus && errors.size) {
    summary.focus();
    return false;
    const target = [...errors.keys()].find(field => !field.disabled);
    if (target) {
      if (!target.matches("input, textarea, select")) target.tabIndex = -1;
      for (let parent = target.parentElement; parent; parent = parent.parentElement) {
        if (parent.tagName === "DETAILS") parent.open = true;
      }
      target.focus({preventScroll: true}); target.scrollIntoView({block: "center", behavior: "smooth"});
    }
  } else if (!errors.size && $("error").textContent === validationSummary) error("");
  return errors.size === 0;
}
function fieldName(target){
  const group=target.closest(".location-group")?.querySelector("legend")?.textContent;
  const label=target.id?document.querySelector(`label[for="${CSS.escape(target.id)}"]`)?.textContent:"";
  const section=target.closest(".form-section")?.querySelector("h2")?.textContent;
  return [group,(label||section||"Поле").replace("*","").trim()].filter(Boolean).join(", ");
}
function focusField(target){
  if (!target.matches("input, textarea, select")) target.tabIndex = -1;
  for (let parent = target.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true;
  target.focus({preventScroll: true}); target.scrollIntoView({block: "center", behavior: "smooth"});
}
function updateFormFeedback() {
  updateUnsupportedConditions();
  if (validationAttempted) validateSearchForm();
  renderConditionsSummary();
}
$("searchForm").addEventListener("input", updateFormFeedback);
$("searchForm").addEventListener("change", updateFormFeedback);
$("searchForm").onsubmit=async e=>{
  e.preventDefault();if(submitting)return;
  validationAttempted = true;
  if (!validateSearchForm(true)) return;
  for (const editor of tokenEditors) editor.commit();
  const launch=e.submitter?.id==="start",id=route.briefId;submitting=true;updateStart();error("");
  try{
    if(launch && ["queued","running"].includes(currentBrief()?.last_run?.status))throw Error("Поиск уже идёт. Дождитесь его завершения или остановите его в ленте.");
    if(!catalogReady)throw Error("Дождитесь загрузки географии");
    const search=readSearchOptions();if(!search.sources.length)throw Error("Выберите хотя бы один источник");
    const payload={name:$("name").value,queries:queryTokens.values,text:$("briefText").value,filters:readFilters(),search};
    const b=await api(id?`/api/v1/briefs/${id}`:"/api/v1/briefs",id?"PUT":"POST",payload);
    await loadOverviews();
    formSnapshot=formState();
    if(launch){
      try{await api("/api/v1/runs","POST",{brief_id:b.id,...search});await loadOverviews();notify("Бриф сохранён, поиск запущен. Вакансии появятся в «Неразобранных».");}
      catch(e){
        const saved=briefs.find(x=>x.id===b.id)||b;
        notify("Бриф сохранён, но поиск не запустился: "+e.message,()=>startSearch(saved,$("runSearch")),"Запустить поиск");
      }
    }else notify(id?"Изменения сохранены.":"Бриф сохранён без поиска.");
    location.hash=briefPath(b.id);
  }catch(e){error(e.message);}finally{submitting=false;updateStart();}
};
function backToBrief(){location.hash=route.briefId?briefPath(route.briefId):"#/briefs";}
$("historyBack").onclick=backToBrief;
$("openSearch").onclick=()=>{location.hash=`#/brief/${route.briefId}/settings`;};
$("openHistory").onclick=()=>{location.hash=`#/brief/${route.briefId}/history`;};
$("runSearch").onclick=()=>{const b=currentBrief();if(b)startSearch(b,$("runSearch"));};
$("stopSearchCancel").onclick=()=>{closeStopSearch();$("stopSearch").focus();};
$("stopSearchConfirm").onclick=async()=>{
  const b=currentBrief(),button=$("stopSearchConfirm");button.disabled=true;
  try{await api(`/api/v1/runs/${b.last_run.id}/cancel`,"POST");closeStopSearch();notify("Поиск остановлен. Найденные вакансии остались в брифе.");await refresh().catch(handleRefreshError);}
  catch(e){error(e.message);}finally{button.disabled=false;}
};
function applySourceFilter(){
  const text=$("sourceFilter").value.trim(),match=sources.find(s=>s.name.toLowerCase()===text.toLowerCase()||s.id===text);
  const source=text?match?.id:"";
  if(source===undefined)return;
  if(source!==route.source)location.hash=pageUrl({page:1,source});
}
$("sourceFilter").addEventListener("change",applySourceFilter);
$("sourceFilter").addEventListener("input",()=>{if(!$("sourceFilter").value)applySourceFilter();else if(sources.some(s=>s.name===$("sourceFilter").value))applySourceFilter();});
$("selectionView").onchange=()=>{location.hash=pageUrl({page:1,selection:$("selectionView").value});};
$("sortView").onchange=()=>{location.hash=pageUrl({page:1,sort:$("sortView").value});};
$("vacancySearch").oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{location.hash=pageUrl({page:1,q:$("vacancySearch").value.trim()});},300);};
let historySignature=null;
function renderHistory(runs, r) {
  const nav=$("history"),signature=JSON.stringify(runs.map(r=>[r.id,r.status]));
  if(nav.dataset.signature!==signature+historyRun){
    nav.replaceChildren();nav.dataset.signature=signature+historyRun;
    for(const run of runs){const a=el("a",new Date(run.created_at*1000).toLocaleString("ru"),run.id===historyRun?"active":"");a.href=`#/brief/${route.briefId}/history?run=${run.id}`;a.append(el("small",statusNames[run.status]));nav.append(a);}
  }
  if(!r){$("runDetails").replaceChildren(el("div","Поисков ещё не было. Запустите поиск из вакансий брифа.","empty"));return;}
  if(historySignature===JSON.stringify(r))return;historySignature=JSON.stringify(r);
  const details=$("runDetails"),wasOpen=details.querySelector('#eventDetails')?.open || false;details.replaceChildren();
  details.append(el("h2",statusNames[r.status] || r.status));
  if(typeof r.request?.second_chance==="boolean")details.append(el("p",`Второй шанс: ${r.request.second_chance?"включён":"выключен"}`,"muted"));
  if(r.error)details.append(el("p",r.error,"notice"));
  if(r.limit_reached)details.append(el("p","Достигнут заданный лимит поиска.","muted"));
  const stats=el("div",undefined,"stats"),m=r.metrics;
  for(const [name,value] of [["Найдено вакансий",m.unique_seen||0],["Подходит",m.accepted||0],["Может быть подходит",m.maybe||0],["Скрыто",m.rejected||0],["Полных описаний",m.descriptions_ready||0],["Страниц источников",m.pages||0],["Карточек из базы",m.details_reused||0],["Время поиска, с",((m.elapsed_ms||0)/1000).toFixed(1)],["Ошибок",(m.detail_errors||0)+(m.company_errors||0)+(m.listing_errors||0)]]){const d=el("div",undefined,"stat");d.append(el("strong",value),el("small",name));stats.append(d);}details.append(stats);
  for(const [id,state] of Object.entries(r.sources || {}))details.append(el("p",[sources.find(s=>s.id===id)?.name || id,statusNames[state.status] || state.status,state.hint || state.error].filter(Boolean).join(" · "),"muted"));
  const snapshot=el("details",undefined,"run-snapshot");snapshot.append(el("summary","Условия этого запуска"),el("pre",JSON.stringify(r.request,null,2)));details.append(snapshot);
  const log=el("details");log.id="eventDetails";log.open=wasOpen;const pre=el("pre");pre.id="log";log.append(el("summary","Технические события"),pre);details.append(log);
  log.querySelector("summary").onclick=()=>{if(!log.open)setTimeout(()=>refresh().catch(handleRefreshError),0);};
}
async function refresh() {
  if(servicePaused || readerTransition)return;
  const version=viewVersion, request=++requestVersion;
  refreshController?.abort();const controller=new AbortController();refreshController=controller;
  const signal=controller.signal,valid=()=>version===viewVersion&&request===requestVersion;
  const updated=await api("/api/v1/briefs","GET",undefined,signal);if(!valid())return;briefs=updated;
  if(route.view==="home"){renderHome();return;}
  const b=currentBrief();if(route.view==="settings"){updateStart();return;}if(!b)return;
  if(route.view==="workspace"){
    renderFolders(b);showSearchState(b);
    const params=new URLSearchParams({page:route.page,page_size:pageSize,status:route.folder,selection:route.selection,q:route.q,sort:route.sort,source:route.source});
    $("results").setAttribute("aria-busy","true");
    const data=await api(`/api/v1/briefs/${b.id}/vacancies?${params}`,"GET",undefined,signal);if(!valid())return;
    const previousPage=$("pagination").dataset.page;
    route.page=data.page;history.replaceState(null,"",pageUrl());showPagination(data);showCards(data.items);
    $("results").removeAttribute("aria-busy");$("pagination").dataset.page=data.page;
    if(previousPage&&previousPage!==String(data.page)&&!$("vacancyDialog").open)$("pagination").scrollIntoView({block:"start"});
    return;
  }
  if(route.view==="history"){
    const runs=await api(`/api/v1/runs?brief_id=${b.id}`,"GET",undefined,signal);if(!valid())return;
    const r=route.run?runs.find(r=>r.id===route.run):runs[0];
    if(route.run&&!r){$("runDetails").replaceChildren(el("p","Запуск не найден в истории этого брифа.","notice"));return;}
    if(historyRun!==r?.id){historyRun=r?.id;lastEvent=0;events=[];historySignature=null;}
    renderHistory(runs,r);
    if(r&&$("eventDetails")?.open){
      const batch=await api(`/api/v1/runs/${r.id}/events?after=${lastEvent}`,"GET",undefined,signal);if(!valid())return;
      events.push(...batch);if(batch.length)lastEvent=batch.at(-1).id;
    }
    if($("log"))$("log").textContent=events.map(e=>`${new Date(e.time*1000).toLocaleTimeString("ru")} ${e.event} ${JSON.stringify(e.data)}`).join("\n");
  }
}
function showServiceStopped(title="Сервис остановлен") {
  servicePaused=true;clearTimeout(pollTimer);refreshController?.abort();readerController?.abort();
  locationEditor.abort();clearTimeout(searchTimer);closePopups();
  if($("vacancyDialog").open)$("vacancyDialog").close();
  if($("stopServiceDialog").open)$("stopServiceDialog").close();
  $("mainContent").inert=true;$("serviceStopped").hidden=false;
  $("serviceStoppedTitle").textContent=title;$("checkService").disabled=title!=="Сервис остановлен" && title!=="Нет связи с сервисом";
  $("serviceIndicatorText").textContent=title==="Сервис останавливается…"?"Сервис останавливается":"Сервис не работает";
  $("serviceDot").className="svc-dot stopping";error("");
}
function activeRunBriefs(state){
  return briefs.filter(b=>b.last_run && state.active_runs.includes(b.last_run.id)).map(b=>b.name);
}
async function checkServiceStatus() {
  const status=await api("/api/v1/service");
  $("stopService").hidden=!status.managed;
  const n=status.active_runs.length;
  $("serviceActiveRuns").textContent=n?`Идут поиски: ${n}.`:"Активных поисков нет.";
  if(status.status==="stopping")showServiceStopped("Сервис останавливается…");
  return status;
}
/* Header popovers: one open at a time, Esc and outside click close it. */
function closePopups(except){
  for(const button of document.querySelectorAll(".pop-anchor > [aria-controls]")){
    if(button===except)continue;
    button.setAttribute("aria-expanded","false");$(button.getAttribute("aria-controls")).hidden=true;
  }
}
for(const button of document.querySelectorAll(".pop-anchor > [aria-controls]")){
  button.addEventListener("click",()=>{
    const open=button.getAttribute("aria-expanded")!=="true";closePopups(button);
    button.setAttribute("aria-expanded",String(open));$(button.getAttribute("aria-controls")).hidden=!open;
  });
}
document.addEventListener("click",event=>{if(!event.target.closest(".pop-anchor"))closePopups();});
document.addEventListener("keydown",event=>{
  if(event.key!=="Escape")return;
  const open=document.querySelector('.pop-anchor > [aria-expanded="true"]');
  if(open){closePopups();open.focus();}
});
function confirmStop(state){
  const dialog=$("stopServiceDialog"),names=activeRunBriefs(state),n=state.active_runs.length;
  $("stopServiceRuns").textContent=n?`Сейчас идут поиски: ${n}. Они будут отменены.`:"Активных поисков нет.";
  $("stopServiceBriefs").replaceChildren(...names.map(name=>el("li",name)));
  $("stopServiceBriefs").hidden=!names.length;
  $("stopServiceConfirm").textContent=n?"Отменить поиски и остановить":"Остановить";
  return new Promise(resolve=>{
    const done=value=>{dialog.close();resolve(value);};
    $("stopServiceConfirm").onclick=()=>done(true);
    $("stopServiceCancel").onclick=()=>done(false);
    dialog.oncancel=()=>resolve(false);
    dialog.showModal();$("stopServiceCancel").focus();
  });
}
$("stopService").onclick=async()=>{
  const button=$("stopService");button.disabled=true;closePopups();
  try {
    let state=await checkServiceStatus();
    if(!await confirmStop(state))return;
    let cancelActive=state.active_runs.length>0;
    while(true){
      const response=await fetch("/api/v1/service/stop",{
        method:"POST",headers:{"Content-Type":"application/json","X-Job-Search-Instance":state.instance_id},
        body:JSON.stringify({cancel_active:cancelActive}),
      });
      const result=await response.json();
      if(response.status===409 && !cancelActive){
        state=await checkServiceStatus();
        if(!await confirmStop(state))return;
        cancelActive=true;continue;
      }
      if(!response.ok)throw Error(result.detail || "Не удалось остановить сервис");
      showServiceStopped("Сервис останавливается…");
      for(let i=0;i<50;i++){
        await new Promise(resolve=>setTimeout(resolve,300));
        try {await api("/api/v1/service");}
        catch {showServiceStopped("Сервис остановлен");return;}
      }
      $("serviceCheckResult").textContent="Остановка ещё не завершена. Проверьте состояние через ассистента.";
      return;
    }
  } catch(e){error(e.message);}finally{button.disabled=false;}
};
$("checkService").onclick=async()=>{
  try {
    const state=await api("/api/v1/service");
    if(state.status==="running"){location.reload();return;}
    $("serviceCheckResult").textContent="Сервис ещё останавливается.";
  } catch {$("serviceCheckResult").textContent="Сервис пока не запущен. Попросите ассистента запустить его.";}
};
async function poll(){
  if(servicePaused)return;
  try{await checkServiceStatus();await refresh();}
  catch(e){if(e instanceof TypeError)showServiceStopped("Нет связи с сервисом");else handleRefreshError(e);}
  finally{if(!servicePaused)pollTimer=setTimeout(poll,3000);}
}
async function boot(){
  const results=await Promise.allSettled([loadCatalog(),loadOverviews(),checkServiceStatus()]);
  window.addEventListener("hashchange",navigate);navigate();
  for(const result of results)if(result.status==="rejected"){error(result.reason.message);$("countryRetryBox").hidden=true;}
  if(!catalogReady)$("countryRetryBox").hidden=false;
  if(!servicePaused)pollTimer=setTimeout(poll,3000);
}
boot().catch(e=>error(e.message));
