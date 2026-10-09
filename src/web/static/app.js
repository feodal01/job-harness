const $ = id => document.getElementById(id);
let servicePaused = false, pollTimer = null;
let briefs = [], sources = [], countries = [], selectedLocations = [], chosenCity = null,
  cityVersion = 0, cityTimer = null, cityController = null, catalogReady = false, submitting = false;
let route = {}, viewVersion = 0, requestVersion = 0, refreshController = null,
  renderedCards = null, renderedPagination = null, renderedHome = null, renderedFolders = null,
  pageItems = [], searchTimer = null, toastTimer = null, detailVersion = 0,
  selectedVacancy = null, settingsSearch = {}, initialLimit = "", initialPages = "",
  historyRun = null, lastEvent = 0, events = [];
let readerItems = [], readerIndex = -1, readerDrafts = new Map(), pageInfo = null,
  readerPage = 1, readerTotal = 0, readerTransition = false,
  readerController = null, readerChangedPage = false, readerEnds = new Set();
const pageSize = 50;
const userStatusNames = {new:"Неразобранная", viewed:"Неразобранная · просмотрена", dismissed:"Неинтересна", applied:"Отправлен отклик", interview:"Собеседование", rejected:"Отказ", offer:"Оффер"};
const folderNames = {all:"Все вакансии", open:"Неразобранные", dismissed:"Неинтересна", applied:"Отправлен отклик", interview:"Собеседование", rejected:"Отказ", offer:"Оффер"};
const typeNames = {aggregator:"агрегатор",career_site:"карьерный сайт",ats:"ATS",telegram:"Telegram"};
const statusNames = {blocked:"Заблокирован",skipped:"Пропущен",queued:"В очереди",running:"Идёт поиск",complete:"Завершён",partial:"Частичный результат",failed:"Ошибка",cancelled:"Остановлен",interrupted:"Прерван"};
const selectionNames = {accepted:"Подходит",maybe:"Может быть подходит",rejected:"Скрыто"};
const conditionNames = {query:"Профиль вакансии",profile:"Профиль вакансии",locations:"Места работы",minimum_work_format:"Формат работы",employment:"Занятость",experience_years:"Опыт",salary:"Зарплата",published_after:"Дата публикации",relocation_support:"Помощь с релокацией",excluded_companies:"Исключённые компании",excluded_text:"Исключённые фразы"};
const checkStatusNames = {accepted:"Подходит",rejected:"Не подходит",unknown:"Не проверено"};
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
    const chosenSources = () => sources.filter((s) => $("source-" + s.id)?.checked);
    function updateStart() {
      const active=["queued","running"].includes(currentBrief()?.last_run?.status);
      $("start").disabled = submitting || active || !catalogReady || !chosenSources().length;
      $("start").textContent=submitting?"Сохраняем…":active?"Поиск уже идёт":"Сохранить и искать";
      $("settingsSearchState").textContent=active?"Поиск уже идёт по условиям на момент запуска. Изменения можно сохранить для следующего поиска.":"";
      $("settingsSearchState").hidden=!active;
      $("saveBrief").disabled = submitting || !catalogReady;
    }
    function clearCity() {
      cityVersion++;
      clearTimeout(cityTimer);
      cityController?.abort();
      chosenCity = null;
      $("city").value = "";
      $("city").disabled = !$("country").value;
      $("cityOptions").replaceChildren();
      $("cityOptions").hidden = true;
      $("city").setAttribute("aria-expanded", "false");
      $("cityRetry").hidden = true;
      $("cityStatus").textContent = $("country").value
        ? "Без города — вся страна. Подсказки уточняют одноимённые города." : "Выберите страну для поиска города.";
      updateStart();
    }
    function updateSource() {
      const chosen = chosenSources();
      $("sourceStatus").textContent = chosen.map((s) => s.name + ": " + s.capabilities.query_notes).join(" ");
      $("queries").required = chosen.length > 0 && chosen.every((s) => s.capabilities.query_parameters.query === "native");
      $("profiles").disabled = !chosen.some((s) => s.capabilities.actions.includes("company_profile"));
      if ($("profiles").disabled) $("profiles").checked = false;
      updateUnsupportedConditions();
      updateStart();
    }
    function updateUnsupportedConditions() {
      const enabled = {
        query: lines("queries").length > 0,
        locations: selectedLocations.length > 0 || !!$("country").value,
        minimum_work_format: !!$("minimumWorkFormat").value,
        employment: selected("employment").length > 0,
        experience_years: $("experience").value !== "",
        salary: $("salaryMin").value !== "",
        published_after: !!$("publishedAfter").value,
        relocation_support: $("relocation").checked,
        excluded_companies: lines("excludedCompanies").length > 0,
        excluded_text: lines("excludedText").length > 0,
      };
      const host = $("unsupportedConditions"); host.replaceChildren();
      for (const source of chosenSources()) {
        const unsupported = Object.keys(enabled).filter(field => enabled[field] && source.capabilities.query_parameters[field] === "none");
        if (unsupported.length) host.append(el("p",`${source.name} — в этом источнике не проверяются: ${unsupported.map(field => conditionNames[field]).join(", ")}.`));
      }
      host.hidden = !host.childElementCount;
    }
    async function loadGeography() {
      countries = await api("/api/v1/geography/countries");
      $("country").replaceChildren(el("option", "Без ограничения"));
      $("country").firstChild.value = "";
      for (const p of countries) {
        const option = el("option", p.name); option.value = p.code; $("country").append(option);
      }
      $("country").disabled = false;
      catalogReady = true;
      clearCity();
      updateSource();
    }
    function drawLocations() {
      $("selectedLocations").replaceChildren();
      selectedLocations.forEach((p, i) => {
        const row = el("div");
        row.append(el("span", [countries.find(c => c.code === p.country)?.name, p.city, p.region].filter(Boolean).join(" · ")));
        const remove = el("button", "Убрать", "secondary"); remove.type = "button";
        remove.onclick = () => {selectedLocations.splice(i, 1); drawLocations();}; row.append(remove);
        $("selectedLocations").append(row);
      });
      updateUnsupportedConditions();
    }
    function addLocation() {
      if (!$("country").value) return;
      const city = $("city").value.trim() || null;
      const place = {country: $("country").value, city, region: chosenCity?.region || null};
      if (!selectedLocations.some(p => JSON.stringify(p) === JSON.stringify(place))) selectedLocations.push(place);
      $("country").value = ""; clearCity(); drawLocations();
    }
    $("addLocation").onclick = addLocation;
    const employmentLabels = {full_time:"Полная", part_time:"Частичная", contract:"Проектная / контракт", internship:"Стажировка", volunteer:"Волонтёрство"};
    for (const [value, text] of Object.entries(employmentLabels)) {
        const label = el("label"); const input = document.createElement("input");
        input.type = "checkbox"; input.name = "employment"; input.value = value;
        label.append(input, document.createTextNode(" " + text)); $("employment").append(label);
    }
    const selected = name => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(e => e.value);
    const setSelected = (name, values) => document.querySelectorAll(`input[name="${name}"]`).forEach(e => { e.checked = values.includes(e.value); });
    const lines = id => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
    function readFilters() {
      addLocation();
      return {
        locations: selectedLocations,
        minimum_work_format: $("minimumWorkFormat").value || null, employment: selected("employment"),
        experience_years: $("experience").value === "" ? null : Number($("experience").value),
        salary: $("salaryMin").value === "" ? null : {minimum:Number($("salaryMin").value), currency:$("currency").value,
          period:$("salaryPeriod").value},
        published_after: $("publishedAfter").value || null,
        relocation_support: $("relocation").checked,
        excluded_companies: lines("excludedCompanies"), excluded_text: lines("excludedText"),
      };
    }
    function fillFilters(f = {}) {
      selectedLocations = structuredClone(f.locations || []); drawLocations();
      $("country").value = ""; clearCity();
      $("minimumWorkFormat").value = f.minimum_work_format || "";
      setSelected("employment", f.employment || []);
      $("experience").value = f.experience_years ?? "";
      $("salaryMin").value = f.salary?.minimum ?? ""; $("currency").value = f.salary?.currency || "RUB";
      $("salaryPeriod").value = f.salary?.period || "month";
      $("publishedAfter").value = f.published_after || ""; $("relocation").checked = !!f.relocation_support;
      $("excludedCompanies").value = (f.excluded_companies || []).join("\n");
      $("excludedText").value = (f.excluded_text || []).join("\n");
    }
    function chooseCity(city) {
      chosenCity = city;
      $("city").value = city.name;
      $("cityStatus").textContent = [city.name, ...city.path].join(" · ");
      $("cityOptions").hidden = true;
      $("city").setAttribute("aria-expanded", "false");
      updateStart();
    }
    async function suggestCities(version) {
      const text = $("city").value.trim();
      const params = new URLSearchParams({country: $("country").value, text});
      $("cityStatus").textContent = "Ищем варианты…";
      cityController = new AbortController();
      try {
        const items = await api(`/api/v1/geography/cities?${params}`,
          "GET", undefined, cityController.signal);
        if (version !== cityVersion) return;
        $("cityOptions").replaceChildren();
        for (const city of items) {
          const button = el("button", [city.name, ...city.path].join(" · "), "secondary");
          button.type = "button";
          button.style.cssText = "display:block;width:100%;text-align:left;margin-bottom:4px";
          button.onclick = () => chooseCity(city);
          $("cityOptions").append(button);
        }
        $("cityOptions").hidden = !items.length;
        $("city").setAttribute("aria-expanded", String(!!items.length));
        $("cityStatus").textContent = items.length
          ? "Выберите город из подсказок." : "Ничего не найдено. Уточните название или очистите поле.";
      } catch (e) {
        if (version !== cityVersion || e.name === "AbortError") return;
        $("cityStatus").textContent = "Не удалось получить подсказки: " + e.message;
        $("cityRetry").hidden = false;
      }
    }
    $("city").oninput = () => {
      const version = ++cityVersion;
      clearTimeout(cityTimer);
      cityController?.abort();
      chosenCity = null;
      $("cityOptions").replaceChildren();
      $("cityOptions").hidden = true;
      $("city").setAttribute("aria-expanded", "false");
      $("cityRetry").hidden = true;
      const text = $("city").value.trim();
      $("cityStatus").textContent = text ? "Введите хотя бы два символа и выберите подсказку."
        : "Без города — поиск по всей выбранной стране.";
      updateStart();
      if (text.length >= 2 && $("country").value)
        cityTimer = setTimeout(() => suggestCities(version), 400);
    };
    $("city").onkeydown = (e) => {
      if (e.key === "ArrowDown" && !$("cityOptions").hidden) {
        e.preventDefault();
        $("cityOptions").firstElementChild?.focus();
      }
      if (e.key === "Escape") {
        $("cityOptions").hidden = true;
        $("city").setAttribute("aria-expanded", "false");
      }
    };
    $("country").onchange = clearCity;
    $("countryRetry").onclick = () => { error(""); loadGeography().catch(e => error(e.message)); };
    $("cityRetry").onclick = () => {
      $("cityRetry").hidden = true;
      suggestCities(++cityVersion);
    };
    async function loadCatalog() {
      sources = await api("/api/v1/sources");
      for (const source of sources) {
        const label = el("label"), box = el("input");
        box.type = "checkbox"; box.id = "source-" + source.id; box.checked = true; box.onchange = updateSource;
        const blocked = source.state?.status === "blocked"
          ? " · заблокирован до " + new Date(source.state.until * 1000).toLocaleTimeString("ru") : "";
        label.append(box, document.createTextNode(" " + source.name + " · " + (typeNames[source.type] || source.type) + blocked));
        $("sources").append(label);
      }
      updateSource();
      await loadGeography();
    }
function notify(message) {
  clearTimeout(toastTimer); $("toast").textContent = message; $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
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
    selection:["all","candidates","accepted","maybe"].includes(params.get("selection")) ? params.get("selection") : "candidates",
    sort:["first_seen","newest","updated"].includes(params.get("sort")) ? params.get("sort") : "first_seen"};
}
function pageUrl(updates = {}) {
  const state = {...route, ...updates};
  const params = new URLSearchParams({page:state.page, q:state.q, selection:state.selection, sort:state.sort});
  return briefPath(state.briefId, state.folder) + "?" + params;
}
async function loadOverviews(signal) {
  const data = await api("/api/v1/briefs", "GET", undefined, signal);
  briefs = data;
}
function countLink(b, folder, count) {
  const a = el("a", count || 0, "count-link"); a.href = briefPath(b.id,folder);
  a.setAttribute("aria-label", `${folderNames[folder]}: ${count || 0}, ${b.name}`); return a;
}
function renderHome() {
  const signature = JSON.stringify(briefs);
  if (signature === renderedHome) return;
  renderedHome = signature;
  const host = $("briefTable"); host.replaceChildren();
  if (!briefs.length) {
    const empty = el("div", undefined, "empty"); empty.append(el("strong", "Начните с первого брифа"),el("p", "Задайте условия поиска, затем разбирайте найденные вакансии по этапам."));
    const a = el("a", "Создать бриф", "button"); a.href = "#/briefs/new"; empty.append(a); host.append(empty); return;
  }
  const table = el("table"), head = el("thead"), row = el("tr");
  for (const name of ["Бриф","Всего","Неразобранные","Отклики","Собеседования","Офферы","Последний поиск"]) row.append(el("th",name));
  head.append(row); table.append(head); const body = el("tbody");
  for (const b of briefs) {
    const tr = el("tr"), title = el("td"), a = el("a",b.name); a.href = briefPath(b.id);
    title.append(a,el("small", b.queries.join(" · ") || "Поиск по условиям брифа")); tr.append(title);
    for (const folder of ["all","open","applied","interview","offer"]) {const td = el("td"); td.append(countLink(b,folder,b.funnel[folder]));tr.append(td);}
    const last = el("td");
    if (b.last_run) last.append(el("span",new Date(b.last_run.created_at*1000).toLocaleDateString("ru")),el("small",statusNames[b.last_run.status] || b.last_run.status));
    else last.append(el("small","Ещё не запускали"));
    tr.append(last); body.append(tr);
  }
  table.append(body); host.append(table);
}
function renderFolders(b) {
  const hidden = $("hiddenVacancies"); hidden.replaceChildren(); hidden.hidden = !b.hidden_count;
  if (b.hidden_count) {
    const a = el("a", "Все, включая скрытые"); a.href = pageUrl({folder:"all",page:1,q:"",selection:"all"});
    hidden.append(document.createTextNode(`Скрыто по жёстким условиям: ${b.hidden_count.toLocaleString("ru")}. `), a);
  }
  const signature = JSON.stringify([b.id,b.funnel,b.hidden_count,route.folder]);
  if (renderedFolders === signature) return;
  renderedFolders = signature; $("folders").replaceChildren();
  for (const [key,name] of Object.entries(folderNames)) {
    const a = el("a",undefined,route.folder === key ? "active" : "");
    a.href = briefPath(b.id,key); a.dataset.folder = key;
    if (route.folder === key) a.setAttribute("aria-current","page");
    a.append(el("span",name),el("span",b.funnel[key] || 0)); $("folders").append(a);
  }
  $("folderTitle").textContent = folderNames[route.folder];
}
function showSearchState(b) {
  const r = b.last_run, active = r && ["queued","running"].includes(r.status);
  $("searchState").textContent = !r ? "Поиск ещё не запускали. Откройте «Поиск и условия», задайте запросы и запустите поиск."
    : active ? "Поиск идёт · новые вакансии появляются в «Неразобранных»"
    : `Последний поиск: ${statusNames[r.status]?.toLowerCase() || r.status} · ${new Date(r.created_at*1000).toLocaleString("ru")} `;
  if (r && ["partial","failed","interrupted"].includes(r.status)) {
    const a = el("a","Подробнее");a.href=`#/brief/${b.id}/history?run=${r.id}`;$("searchState").append(a);
  }
  $("stopSearch").hidden = !active;

}
function navigate() {
  const next = parseRoute(), previous = route;
  route = next; viewVersion++; requestVersion++; refreshController?.abort(); clearTimeout(searchTimer);
  error(""); closeVacancy();
  for (const id of ["home","workspace","settings","history"]) $(id+"View").hidden = id !== route.view;
  if (route.briefId && !currentBrief()) {
    error("Бриф не найден. Вернитесь к списку брифов.");
    for (const id of ["workspace","settings","history"]) $(id+"View").hidden = true;
    return;
  }
  const b = currentBrief();
  document.title = b ? `${b.name} · Поиск работы` : "Мои брифы · Поиск работы";
  if (route.view === "home") renderHome();
  if (route.view === "settings") fillSettings(b);
  if (route.view === "workspace") {
    $("briefTitle").textContent = b.name;
    $("briefSummary").textContent = b.queries.join(" · ") || "Поиск по условиям брифа";
    $("vacancySearch").value = route.q; $("selectionView").value = route.selection; $("sortView").value = route.sort;
    renderFolders(b); showSearchState(b);
    // Keep card nodes for page-only navigation until the replacement has arrived.
    if (previous.briefId !== route.briefId || previous.folder !== route.folder || previous.view !== route.view) {
      $("results").replaceChildren(el("div","Загружаем вакансии…","empty"));
      renderedCards = null; renderedPagination = null;
      $("pagination").hidden = true; $("paginationBottom").hidden = true;
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
function showCards(items) {
  const signature = JSON.stringify([route.briefId,route.folder,route.page,route.q,route.selection,route.sort,items]);
  if (renderedCards === signature) return;
  const cards = [...$("results").querySelectorAll("article")];
  const anchor = cards.find(c => c.getBoundingClientRect().bottom>0 && c.getBoundingClientRect().top<innerHeight);
  const anchorTop = anchor?.getBoundingClientRect().top;
  renderedCards = signature; pageItems = items; $("results").replaceChildren();
  if (!items.length) {
    const empty = el("div",undefined,"empty");
    empty.append(el("strong",route.q || route.selection !== "candidates" ? "Ничего не найдено" : "В этой папке пока нет вакансий"));
    empty.append(el("p",route.q || route.selection !== "candidates" ? "Измените поисковый запрос или фильтр соответствия." : route.folder === "open" ? "Новые находки появятся здесь после поиска." : "Откройте вакансию и выберите нужный этап."));
    $("results").append(empty); return;
  }
  for (const v of items) {
    const card = el("article",undefined,"card");card.dataset.id=v.key;
    card.onclick=e=>{if(!e.target.closest("a,button,input,select,textarea")&&!window.getSelection()?.toString())openVacancy(v.key);};
    const top = el("div",undefined,"card-top"), title = el("button",v.title,"vacancy-link");title.onclick=()=>openVacancy(v.key);
    top.append(title,el("span",userStatusNames[v.user_status],"badge "+v.user_status));card.append(top);
    card.append(el("div",[v.company?.name,v.location].filter(Boolean).join(" · ") || "Компания и регион не указаны","meta"));
    appendCompanySite(card, v.company);
    if (salaryText(v)) card.append(el("div",salaryText(v),"salary"));
    if (v.summary || v.description) card.append(el("p",(v.summary || v.description).replace(/\s+/g," ").slice(0,280),"snippet"));
    const footer=el("div",undefined,"card-footer");
    footer.append(el("span",[selectionNames[v.selection?.status],sources.find(s=>s.id===v.source)?.name || v.source].filter(Boolean).join(" · ")));
    const open=el("button","Открыть вакансию","secondary");open.onclick=()=>openVacancy(v.key);footer.prepend(open);card.append(footer);$("results").append(card);
  }
  if(anchor){const updated=[...$("results").querySelectorAll("article")].find(c=>c.dataset.id===anchor.dataset.id);if(updated)window.scrollBy(0,updated.getBoundingClientRect().top-anchorTop);}
}
function showPagination(data) {
  pageInfo={page:data.page,pages:data.pages,total:data.total};
  if($("vacancyDialog").open && data.total>readerTotal){
    readerTotal=data.total;readerEnds.clear();
    if(!$("vacancyStage")?.disabled)updateReaderNavigation();
  }
  const signature=JSON.stringify([route.briefId,route.folder,route.q,route.selection,route.sort,data.total,data.page,data.pages]);
  $("folderCount").textContent = `Вакансий: ${data.total.toLocaleString("ru")}`;
  for(const id of ["pagination","paginationBottom"]){
    const nav=$(id); nav.hidden=false;if(renderedPagination===signature)continue;nav.replaceChildren();
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
    [...$("results").querySelectorAll("article")].find(c=>c.dataset.id===selectedKey)?.scrollIntoView({block:"center"});
  }
  selectedVacancy=null;readerItems=[];readerIndex=-1;readerDrafts.clear();readerChangedPage=false;readerEnds.clear();
}
$("closeVacancy").onclick=closeVacancy;
$("closeVacancyIcon").onclick=closeVacancy;
$("vacancyDialog").addEventListener("cancel",e=>{e.preventDefault();closeVacancy();});
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
function positionReader() {
  const left=innerWidth<=650?0:Math.max(0,document.querySelector(".vacancies").getBoundingClientRect().left);
  $("vacancyDialog").style.setProperty("--reader-left",left+"px");
}
window.addEventListener("resize",()=>{if($("vacancyDialog").open)positionReader();});
function readerEntry(v) {
  return {key:v.key,time:route.sort==="updated"?(v.status_updated_at ?? v.first_seen):v.first_seen};
}
function updateReaderNavigation() {
  $("previousVacancy").disabled=readerTransition || readerIndex<0 || (readerIndex===0 && (readerPage<=1 || readerEnds.has(-1)));
  $("nextVacancy").disabled=readerTransition || readerIndex<0 || (readerIndex===readerItems.length-1 && ((readerPage-1)*pageSize+readerItems.length>=readerTotal || readerEnds.has(1)));
  $("readerPosition").textContent=readerTransition?"Загружаем…":readerIndex<0?"":`${(readerPage-1)*pageSize+readerIndex+1} / ${readerTotal}`;
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
  $("vacancyFooter").inert=true;updateReaderNavigation();
  const feedback=$("vacancyFooter").querySelector(".action-feedback");
  if(feedback)feedback.textContent="";
  try{
    const params=new URLSearchParams({direction:direction>0?"next":"previous",page_size:pageSize,status:route.folder,selection:route.selection,q:route.q,sort:route.sort});
    params.set("anchor_time",readerItems[readerIndex].time);
    const result=await api(`/api/v1/briefs/${route.briefId}/vacancies/${encodeURIComponent(readerItems[readerIndex].key)}/adjacent?${params}`,"GET",undefined,readerController.signal);
    if(!valid())return;
    if(!result.key){
      readerEnds.add(direction);
      if(feedback)feedback.textContent=direction>0?"Вы просмотрели все вакансии в этой папке.":"Это первая вакансия в этой папке.";
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
    if(valid() && e.name!=="AbortError" && feedback)feedback.textContent="Не удалось загрузить следующую вакансию. Попробуйте ещё раз. "+e.message;
  }finally{
    if(valid()){
      readerTransition=false;readerController=null;$("vacancyFooter").inert=false;updateReaderNavigation();
    }
  }
}
$("previousVacancy").onclick=()=>adjacentVacancy(-1);
$("nextVacancy").onclick=()=>adjacentVacancy(1);
async function openVacancy(key,adjacent=false) {
  if(adjacent){
    const note=$("vacancyNote");
    if(selectedVacancy&&note)readerDrafts.set(selectedVacancy.key,{text:note.value,open:document.querySelector(".vacancy-notes").open});
  }else{
    readerItems=pageItems.map(readerEntry);readerDrafts.clear();readerEnds.clear();
    readerPage=pageInfo.page;readerTotal=pageInfo.total;readerChangedPage=false;
  }
  readerIndex=readerItems.findIndex(v=>v.key===key);
  updateReaderNavigation();positionReader();
  const version=++detailVersion, briefId=route.briefId;
  selectedVacancy=null;
  $("vacancySource").replaceChildren();$("vacancyFooter").hidden=true;
  $("vacancyBody").replaceChildren(el("h2","Загружаем…"));$("vacancyBody").firstChild.id="vacancyTitle";
  if(!$("vacancyDialog").open)$("vacancyDialog").showModal();
  try{
    const v=await api(`/api/v1/briefs/${briefId}/vacancies/${encodeURIComponent(key)}`);
    if(version!==detailVersion)return;selectedVacancy=v;renderVacancy(v,briefId);
  }catch(e){
    if(version===detailVersion){const title=el("h2","Не удалось открыть вакансию");title.id="vacancyTitle";$("vacancyBody").replaceChildren(title,el("p",e.message));}
  }
}
function renderVacancy(v,briefId) {
  const body=$("vacancyBody");body.replaceChildren();body.scrollTop=0;
  const title=el("h2",v.title);title.id="vacancyTitle";body.append(title);
  body.append(el("p",[v.company?.name,v.location].filter(Boolean).join(" · "),"muted"));
  appendCompanySite(body, v.company);
  if(salaryText(v))body.append(el("p",salaryText(v),"salary"));
  const sourceName=sources.find(s=>s.id===v.source)?.name || "сайте источника";
  const original=link(`Открыть на ${sourceName} ↗`,v.url);
  original.id="vacancyOriginal";original.className="button";
  $("vacancySource").replaceChildren(original);
  renderVacancyActions(v,briefId);
  if(v.description||v.summary)body.append(el("h3","Описание вакансии"));
  if(v.summary){
    const section=el("section");
    section.append(el("h4","Краткое описание"),descriptionContent(v.summary,v.summary_html));
    body.append(section);
  }
  if(v.description)body.append(descriptionContent(v.description,v.description_html));
  if(!v.description&&!v.summary)body.append(el("p","Описание пока не получено. Вакансию можно посмотреть в оригинале.","muted"));
  if(v.skills?.length)body.append(el("p",v.skills.join(" · "),"muted"));
  if(v.selection){
    const section=el("section",undefined,"selection-checks");
    section.append(el("h3","Проверка условий брифа"));
    const checks=v.selection.checks;
    let explanation;
    if(!checks.length)explanation="Условия отбора не заданы. Вакансия показана без оценки соответствия.";
    else if(v.selection.status==="rejected")explanation="Вакансия скрыта из-за несовпадения с обязательными условиями. Причины ниже.";
    else if(v.selection.status==="maybe")explanation="Есть несовпадения с пожеланиями из брифа. Подробности ниже.";
    else if(checks.some(check=>check.status==="unknown"||check.supported===false))explanation="Противоречий с условиями брифа не найдено, но часть условий не удалось проверить. Причины ниже.";
    else explanation="Вакансия соответствует проверенным условиям брифа.";
    section.append(el("p",explanation));
    for(const check of checks){
      const item=el("div",undefined,"selection-check");
      item.append(el("strong",conditionNames[check.field] || check.field));
      const kind=check.kind==="hard"?"Жёсткое условие":"Мягкое условие";
      const status=check.supported===false?"Не проверено · В этом источнике не проверяется":checkStatusNames[check.status] || "Не проверено";
      item.append(el("small",`${kind} · ${status}`,"check-status "+check.status));
      item.append(el("p",check.reason));section.append(item);
    }
    body.append(section);
  }
}
function renderVacancyActions(v,briefId) {
  const footer=$("vacancyFooter"),version=detailVersion;
  footer.replaceChildren();footer.hidden=false;
  let saved=v, busy=false;
  const row=el("div",undefined,"vacancy-action-row");
  const select=el("select");select.id="vacancyStage";select.setAttribute("aria-label","Этап воронки");
  for(const [key,name] of Object.entries(userStatusNames)){
    if(key==="viewed")continue;
    const option=el("option",name);option.value=key;select.append(option);
  }
  const applied=el("button","Отклик отправлен");applied.id="markApplied";
  applied.title="Отметить уже отправленный отклик";
  const dismissed=el("button","Неинтересна","secondary");dismissed.id="markDismissed";
  row.append(applied,dismissed,select);
  const notes=el("details",undefined,"vacancy-notes"),summary=el("summary");
  const note=el("textarea");note.id="vacancyNote";note.setAttribute("aria-label","Заметка к вакансии");
  note.maxLength=2000;note.rows=2;note.value=readerDrafts.get(v.key)?.text ?? v.note ?? "";notes.open=readerDrafts.get(v.key)?.open || false;note.placeholder="Дата собеседования, условия или ваши пометки";
  const saveNote=el("button","Сохранить заметку","secondary");saveNote.id="saveVacancyNote";
  const feedback=el("div",undefined,"action-feedback");feedback.setAttribute("role","status");
  notes.append(summary,note,saveNote);footer.append(row,notes,feedback);
  function updateControls(){
    select.value=saved.user_status==="viewed"?"new":saved.user_status;
    applied.hidden=!["new","viewed"].includes(saved.user_status);
    dismissed.hidden=saved.user_status==="dismissed";
    summary.textContent=saved.note?"Заметка · добавлена":"Добавить заметку";
    for(const control of [select,applied,dismissed,saveNote])control.disabled=busy;
  }
  async function save(status,noteValue,isNote=false){
    if(busy)return;
    busy=true;updateControls();$("previousVacancy").disabled=true;$("nextVacancy").disabled=true;feedback.textContent="Сохраняем…";
    try{
      const updated=await api(`/api/v1/briefs/${briefId}/vacancies/${encodeURIComponent(v.key)}`,"PATCH",{status,note:noteValue});
      saved=updated;
      if(version===detailVersion){
        selectedVacancy=updated;
        feedback.textContent=isNote?"Заметка сохранена":userStatusNames[updated.user_status]+" · сохранено";
        if(isNote&&note.value===noteValue)notes.open=false;
      }
      await refresh().catch(handleRefreshError);
    }catch(e){if(version===detailVersion)feedback.textContent="Не удалось сохранить: "+e.message;}
    finally{busy=false;updateControls();if(version===detailVersion)updateReaderNavigation();}
  }
  // Stage changes keep the saved note; an unfinished note stays in the editor.
  select.onchange=()=>save(select.value,saved.note || "");
  applied.onclick=()=>save("applied",saved.note || "");
  dismissed.onclick=()=>save("dismissed",saved.note || "");
  saveNote.onclick=()=>save(saved.user_status,note.value,true);
  updateControls();
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
  $("settingsTitle").textContent=b?"Поиск · "+b.name:"Новый бриф";
  $("settingsBack").textContent=b?"← К вакансиям":"← Все брифы";
  $("name").value=b?.name || "";$("queries").value=(b?.queries || []).join("\n");$("briefText").value=b?.text || "";
  $("queries").rows=Math.max(3,Math.min(10,(b?.queries.length || 0)+1));$("queries").scrollTop=0;
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
  $("secondChance").checked=settingsSearch.second_chance===true;
  $("profiles").checked=settingsSearch.company_profiles!==false;updateSource();
}
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
$("searchForm").addEventListener("input",updateUnsupportedConditions);
$("searchForm").addEventListener("change",updateUnsupportedConditions);
$("searchForm").onsubmit=async e=>{
  e.preventDefault();if(submitting)return;const launch=e.submitter?.id==="start",id=route.briefId;submitting=true;updateStart();error("");
  try{
    if(launch && ["queued","running"].includes(currentBrief()?.last_run?.status))throw Error("Поиск уже идёт. Дождитесь его завершения или остановите его в ленте.");
    if(!catalogReady)throw Error("Дождитесь загрузки географии");
    const search=readSearchOptions();if(!search.sources.length)throw Error("Выберите хотя бы один источник");
    const payload={name:$("name").value,queries:lines("queries"),text:$("briefText").value,filters:readFilters(),search};
    const b=await api(id?`/api/v1/briefs/${id}`:"/api/v1/briefs",id?"PUT":"POST",payload);
    await loadOverviews();
    if(launch){
      try{await api("/api/v1/runs","POST",{brief_id:b.id,...search});await loadOverviews();notify("Бриф сохранён. Поиск запущен");}
      catch(e){location.hash=`#/brief/${b.id}/settings`;setTimeout(()=>error("Бриф сохранён, но поиск не запущен: "+e.message),0);return;}
    }else notify("Бриф сохранён");
    location.hash=briefPath(b.id);
  }catch(e){error(e.message);}finally{submitting=false;updateStart();}
};
function backToBrief(){location.hash=route.briefId?briefPath(route.briefId):"#/briefs";}
$("settingsBack").onclick=backToBrief;$("settingsCancel").onclick=backToBrief;$("historyBack").onclick=backToBrief;
$("openSearch").onclick=()=>{location.hash=`#/brief/${route.briefId}/settings`;};
$("openHistory").onclick=()=>{location.hash=`#/brief/${route.briefId}/history`;};
$("stopSearch").onclick=async()=>{const b=currentBrief();$("stopSearch").disabled=true;try{await api(`/api/v1/runs/${b.last_run.id}/cancel`,"POST");await refresh().catch(handleRefreshError);}catch(e){error(e.message);}finally{$("stopSearch").disabled=false;}};
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
    const params=new URLSearchParams({page:route.page,page_size:pageSize,status:route.folder,selection:route.selection,q:route.q,sort:route.sort});
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
  clearTimeout(cityTimer);clearTimeout(searchTimer);cityController?.abort();
  if($("vacancyDialog").open)$("vacancyDialog").close();
  $("mainContent").inert=true;$("serviceStopped").hidden=false;
  $("serviceStoppedTitle").textContent=title;$("stopService").hidden=true;error("");
}
async function checkServiceStatus() {
  const status=await api("/api/v1/service");
  $("stopService").hidden=!status.managed;
  if(status.status==="stopping")showServiceStopped("Сервис останавливается…");
  return status;
}
$("stopService").onclick=async()=>{
  const button=$("stopService");button.disabled=true;
  try {
    let state=await checkServiceStatus();
    let cancelActive=false;
    while(true){
      if(state.active_runs.length && !cancelActive){
        cancelActive=confirm(`Сейчас идут поиски: ${state.active_runs.length}. Отменить их и остановить сервис? Найденные результаты сохранятся.`);
        if(!cancelActive)return;
      }
      const response=await fetch("/api/v1/service/stop",{
        method:"POST",headers:{"Content-Type":"application/json","X-Job-Search-Instance":state.instance_id},
        body:JSON.stringify({cancel_active:cancelActive}),
      });
      const result=await response.json();
      if(response.status===409 && !cancelActive){state=await checkServiceStatus();if(state.active_runs.length)continue;}
      if(!response.ok)throw Error(result.detail || "Не удалось остановить сервис");
      showServiceStopped("Сервис останавливается…");
      for(let i=0;i<50;i++){
        await new Promise(resolve=>setTimeout(resolve,300));
        try {await api("/api/v1/service");}
        catch {$("serviceStoppedTitle").textContent="Сервис остановлен";return;}
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
  for(const result of results)if(result.status==="rejected"){error(result.reason.message);$("countryRetry").hidden=true;}
  if(!catalogReady)$("countryRetry").hidden=false;
  if(!servicePaused)pollTimer=setTimeout(poll,3000);
}
boot().catch(e=>error(e.message));
