/* Where a brief searches (mockups 8о, 8о-2): the choice model, the form section and the dialog.
 * The brief keeps rules, not a list: `null` is «everywhere» (every source covering the brief's
 * places), otherwise `{mode:"auto", groups, include, exclude}`. The person sees one list instead:
 * each built-in site and «company sites» with a box, company sites either all or only chosen ones.
 * Counts and names come from POST /api/v1/sources/preview, which resolves rules like a run. */

const BUILTIN_GROUPS = ["aggregator", "it_board"];
const COMPANY = "company:";
const MAX_RULE_ITEMS = 1000;
const ruleWord = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};
const countText = (n, one, few, many) => `${n.toLocaleString("ru")} ${ruleWord(n, one, few, many)}`;
const sourcesText = n => countText(n, "источник", "источника", "источников");

/* The person's choice. `builtins` — checked built-in sites; `sites` — company sites at all;
 * `companies` — "all" or "selected"; `picked` — chosen companies (or boards); `skipped` — company
 * sites not to search while all are searched. `list` is the built-in sources: [{id, group, name}]. */
const SourceRulesModel = {
  fromRules(rules, list) {
    const ids = list.map(source => source.id);
    if (!rules) return {custom: false, builtins: ids, sites: true, companies: "all", picked: [], skipped: []};
    const others = items => items.filter(item => !ids.includes(item));
    if (rules.mode === "selected") {
      const picked = others(rules.include);
      return {custom: true, builtins: ids.filter(id => rules.include.includes(id)), sites: picked.length > 0,
        companies: "selected", picked, skipped: others(rules.exclude)};
    }
    const groups = rules.groups ?? null, all = groups === null || groups.includes("company_site");
    const picked = others(rules.include);
    return {
      custom: groups !== null || rules.include.length > 0 || rules.exclude.length > 0,
      builtins: list.filter(source => !rules.exclude.includes(source.id)
        && (groups === null || groups.includes(source.group) || rules.include.includes(source.id))).map(source => source.id),
      sites: all || picked.length > 0, companies: all ? "all" : "selected", picked, skipped: others(rules.exclude),
    };
  },
  toRules(state, list) {
    if (!state.custom) return null;
    const groups = [], exclude = [];
    // A built-in type joins when any of its sites is checked, so new sites of that type join too;
    // its unchecked sites are left out by name.
    for (const group of BUILTIN_GROUPS) {
      const members = list.filter(source => source.group === group).map(source => source.id);
      if (!members.some(id => state.builtins.includes(id))) continue;
      groups.push(group);
      exclude.push(...members.filter(id => !state.builtins.includes(id)));
    }
    const all = state.sites && state.companies === "all";
    if (all) groups.push("company_site");
    const include = state.sites && state.companies === "selected" ? [...state.picked] : [];
    if (all) exclude.push(...state.skipped);
    return {mode: "auto", groups, include, exclude};
  },
  /* Why these rules cannot be searched; null when they can. */
  problem(state, list) {
    const rules = SourceRulesModel.toRules(state, list);
    if (!rules) return null;
    if (state.sites && state.companies === "selected" && !state.picked.length)
      return "Выберите компанию или снимите «Сайты компаний».";
    if (!rules.groups.length && !rules.include.length) return "Отметьте, где искать.";
    if (rules.include.length > MAX_RULE_ITEMS || rules.exclude.length > MAX_RULE_ITEMS)
      return `Можно выбрать до ${MAX_RULE_ITEMS} компаний.`;
    return null;
  },
  /* Every rule item that names a found company site: its board id and each of its companies. */
  itemsOf(match) {
    return new Set([match.rule_item, match.id, ...(match.company_ids || []).map(id => COMPANY + id)].filter(Boolean));
  },
  isPicked: (state, match) => state.picked.some(item => SourceRulesModel.itemsOf(match).has(item)),
  isSkipped: (state, match) => state.skipped.some(item => SourceRulesModel.itemsOf(match).has(item)),
  /* `known` — items the rules had before, so picking a site again restores its own item. */
  pick(state, match, known = []) {
    const next = SourceRulesModel.copy(state), own = SourceRulesModel.itemsOf(match);
    next.skipped = next.skipped.filter(item => !own.has(item));
    if (!next.picked.some(item => own.has(item))) next.picked.push(known.find(item => own.has(item)) || match.rule_item);
    return next;
  },
  unpick(state, match) {
    const next = SourceRulesModel.copy(state), own = SourceRulesModel.itemsOf(match);
    next.picked = next.picked.filter(item => !own.has(item));
    return next;
  },
  skip(state, match, known = []) {
    const next = SourceRulesModel.copy(state), own = SourceRulesModel.itemsOf(match);
    next.picked = next.picked.filter(item => !own.has(item));
    if (!next.skipped.some(item => own.has(item))) next.skipped.push(known.find(item => own.has(item)) || match.rule_item);
    return next;
  },
  unskip(state, match) {
    const next = SourceRulesModel.copy(state), own = SourceRulesModel.itemsOf(match);
    next.skipped = next.skipped.filter(item => !own.has(item));
    return next;
  },
  without(state, key, item) {
    const next = SourceRulesModel.copy(state);
    next[key] = next[key].filter(other => other !== item);
    return next;
  },
  /* The same choice whatever the order of its lists. */
  key(state) {
    const sorted = list => [...list].sort();
    return JSON.stringify({...state, builtins: sorted(state.builtins), picked: sorted(state.picked), skipped: sorted(state.skipped)});
  },
  copy(state) {
    return {...state, builtins: [...state.builtins], picked: [...state.picked], skipped: [...state.skipped]};
  },
  /* Short text where no count is at hand, e.g. the brief card. */
  describe(rules, list) {
    if (!rules) return "везде";
    const state = SourceRulesModel.fromRules(rules, list);
    const parts = list.filter(source => state.builtins.includes(source.id)).map(source => source.name);
    if (state.sites && state.companies === "all") parts.push("сайты компаний");
    else if (state.sites && state.picked.length) parts.push(countText(state.picked.length, "компания", "компании", "компаний"));
    let text = parts.length ? parts.join(", ") : "не выбраны";
    if (state.sites && state.companies === "all" && state.skipped.length)
      text += `; не искать: ${countText(state.skipped.length, "сайт", "сайта", "сайтов")}`;
    return text;
  },
};

const GROUP_NAMES = {aggregator: "Агрегаторы", it_board: "IT-площадки"};
const listText = (names, limit = 3) => names.length <= limit ? names.join(", ")
  : `${names.slice(0, limit - 1).join(", ")} и ещё ${names.length - limit + 1}`;

class SourceRulesEditor {
  /* api(path, method, body, signal); builtins() — built-in sources [{id, group, name}];
   * countries() — ISO codes of the form's places, places() — their text; onChange() runs when the
   * rules or their count change. */
  constructor({api, builtins, countries, places, countryNames = () => [], onChange}) {
    Object.assign(this, {api, builtins, countries, places, countryNames, onChange});
    this.rules = null; this.preview = null; this.everywhere = null; this.error = ""; this.loading = false;
    this.timer = null; this.controller = null; this.version = 0; this.pending = false;
    this.draft = null; this.draftPreview = null; this.draftError = ""; this.draftVersion = 0; this.draftStale = false;
    this.names = new Map(); this.rows = null; this.rowsKey = ""; this.rowsError = ""; this.rowsController = null;
    this.category = null; this.onlyChecked = false; this.showUnreadable = false; this.searchTimer = null;
    const $ = id => document.getElementById(id);
    this.ui = Object.fromEntries(["sourceSummary", "sourceCatalogNote", "openSourceRules", "sourceCount",
      "sourcesDialog", "sourcesDialogClose", "sourcesCancel", "sourcesApply", "sdPlaces", "sdModeAuto",
      "sdModeCustom", "sdAutoCount", "sdCatalogNote", "sdList", "sdBuiltins", "sdSites", "sdSitesNum",
      "sdSitesBody", "sdCats", "sdSearch", "sdShowAll", "sdShowChecked", "sdAll", "sdAllNote", "sdRows",
      "sdRowsFoot", "sdTotal", "sdTotalNote", "sdProblem"].map(id => [id, $(id)]));
    const ui = this.ui, M = SourceRulesModel;
    ui.openSourceRules.onclick = () => this.openDialog();
    ui.sourcesDialogClose.onclick = ui.sourcesCancel.onclick = () => this.closeDialog(false);
    ui.sourcesApply.onclick = () => this.closeDialog(true);
    ui.sourcesDialog.oncancel = event => { event.preventDefault(); this.closeDialog(false); };
    ui.sdModeAuto.onchange = () => { if (ui.sdModeAuto.checked) this.edit({...M.copy(this.draft), custom: false}); };
    ui.sdModeCustom.onchange = () => { if (ui.sdModeCustom.checked) this.edit({...M.copy(this.draft), custom: true}); };
    // Ticking company sites with none chosen yet means all of them.
    ui.sdSites.onchange = () => this.edit({...M.copy(this.draft), sites: ui.sdSites.checked,
      companies: ui.sdSites.checked && !this.draft.picked.length ? "all" : this.draft.companies});
    // «Отметить все»: every company site, or none when all are already checked.
    ui.sdAll.onchange = () => {
      // Nothing checked is simply «no company sites», so the box above unchecks too.
      const all = ui.sdAll.checked;
      this.edit({...M.copy(this.draft), sites: all, companies: all ? "all" : "selected", picked: [], skipped: []});
    };
    ui.sdShowAll.onclick = () => { this.onlyChecked = false; this.drawRows(); };
    ui.sdShowChecked.onclick = () => { this.onlyChecked = true; this.drawRows(); };
    ui.sdSearch.oninput = () => { clearTimeout(this.searchTimer); this.searchTimer = setTimeout(() => this.drawRows(), 150); };
    ui.sdSearch.onkeydown = event => { if (event.key === "Enter") event.preventDefault(); };
  }

  /* --- The form (mockup 8о-2) ------------------------------------------------------------ */

  get list() { return this.builtins(); }
  load(rules) {
    // Rules that mean «everywhere» (a run's snapshot, an API client) load as the default.
    this.rules = rules && SourceRulesModel.fromRules(rules, this.list).custom ? structuredClone(rules) : null;
    this.preview = null; this.error = "";
    this.render(); this.refresh(0);
  }
  set(rules) { this.rules = rules; this.render(); this.onChange(); this.refresh(0); }
  /* What the service refuses on save: no source chosen at all. */
  get problem() {
    const r = this.rules;
    if (!r) return null;
    const none = r.mode === "selected" ? !r.include.length : r.groups != null && !r.groups.length && !r.include.length;
    return none ? "Отметьте, где искать." : null;
  }
  get requiresQuery() { return this.preview?.requires_query === true; }
  get builtin() { return this.preview?.builtin ?? null; }
  describe() {
    if (!this.preview || this.loading) return SourceRulesModel.describe(this.rules, this.list);
    return this.rules ? `${sourcesText(this.preview.total)} по выбору` : `везде · ${this.preview.total.toLocaleString("ru")}`;
  }

  /* Count again after the rules or the brief's places change; only the latest answer is shown. */
  refresh(delay = 300) {
    clearTimeout(this.timer); this.pending = true;
    this.timer = setTimeout(() => this.recount(), delay);
  }
  async recount() {
    const version = ++this.version;
    this.pending = false;
    this.controller?.abort();
    const controller = this.controller = new AbortController();
    this.loading = true; this.render();
    try {
      // «Везде» says which built-ins cover the places; the brief's own rules say what is searched.
      const everywhere = await this.request(null, controller.signal);
      const answer = this.problem ? null : this.rules ? await this.request(this.rules, controller.signal) : everywhere;
      if (version !== this.version) return;
      this.everywhere = everywhere; this.preview = answer; this.error = "";
    } catch (error) {
      if (version !== this.version) return;
      this.preview = null; this.error = `Не удалось посчитать источники: ${error.message}`;
    }
    this.loading = false; this.render(); this.onChange();
  }
  request(rules, signal) {
    return this.api("/api/v1/sources/preview", "POST", {source_rules: rules ?? {}, countries: this.countryCodes()}, signal);
  }
  countryCodes() { return [...new Set(this.countries().filter(Boolean).map(code => code.toUpperCase()))]; }
  learnNames(preview) { for (const entry of preview?.items || []) if (entry.name) this.names.set(entry.item, entry.name); }
  covered(preview = this.everywhere) { return new Set(preview?.builtin || []); }
  groupCount(preview, group) { return preview?.groups.find(g => g.group === group) || {available: 0, selected: 0}; }
  catalogNote(preview) {
    const catalog = preview?.catalog;
    if (!catalog || ["ok", "not_requested"].includes(catalog.status)) return "";
    return `Сайты компаний сейчас недоступны: ${catalog.message || "каталог не отвечает"}`;
  }

  render() {
    const ui = this.ui, preview = this.preview;
    ui.sourceCount.textContent = this.loading ? "Считаем…"
      : preview ? `будем искать в ${countText(preview.total, "источнике", "источниках", "источниках")}` : "";
    const note = this.error || this.catalogNote(preview);
    ui.sourceCatalogNote.textContent = note; ui.sourceCatalogNote.hidden = !note;
    if (!this.rules) {
      ui.sourceSummary.replaceChildren(el("p", "Везде, где есть вакансии для мест брифа"));
      return;
    }
    if (!preview) { ui.sourceSummary.replaceChildren(el("p", this.loading ? "Считаем…" : this.problem || "")); return; }
    this.learnNames(preview);
    const state = SourceRulesModel.fromRules(this.rules, this.list), dl = el("dl", undefined, "src-rows");
    const row = (term, text, muted) => {
      const line = el("div", undefined, "src-row");
      line.append(el("dt", term), el("dd", text, muted ? "muted" : undefined));
      dl.append(line);
    };
    const covered = this.covered();
    for (const group of BUILTIN_GROUPS) {
      const members = this.list.filter(source => source.group === group && covered.has(source.id));
      if (!members.length) continue;
      const on = members.filter(source => preview.builtin.includes(source.id));
      const off = members.filter(source => !on.includes(source));
      row(GROUP_NAMES[group], !on.length ? "не ищем" : !off.length
        ? (members.length === 1 ? members[0].name : `все ${members.length}`)
        : `${on.length} из ${members.length} · без ${listText(off.map(s => s.name))}`, !on.length);
    }
    const sites = this.groupCount(preview, "company_site"), available = this.groupCount(this.everywhere, "company_site").available;
    let text;
    if (this.catalogNote(preview)) text = "недоступны без каталога";
    else if (!state.sites || !sites.selected) text = "не ищем";
    else if (state.companies === "all") text = state.skipped.length
      ? `все, ${sites.selected} из ${available} · без ${listText(state.skipped.map(item => this.names.get(item) || "компания"))}`
      : `все ${available}`;
    else text = `${sites.selected} из ${available}: ${listText(state.picked.map(item => this.names.get(item) || "компания"))}`;
    row("Сайты компаний", text, text === "не ищем" || text.startsWith("недоступны"));
    row("Telegram-каналы", "скоро", true);
    ui.sourceSummary.replaceChildren(dl);
  }

  /* --- The dialog (mockup 8о) ------------------------------------------------------------ */

  openDialog() {
    if (!this.list.length) return;  // the built-in sources are not loaded yet
    this.draft = SourceRulesModel.fromRules(this.rules, this.list);
    // «Готово» without a change keeps rules made elsewhere exactly as they are.
    this.initial = SourceRulesModel.key(this.draft); this.initialState = SourceRulesModel.copy(this.draft);
    this.draftPreview = this.preview; this.draftStale = this.loading || this.pending; this.draftError = "";
    this.category = null; this.onlyChecked = false; this.showUnreadable = false; this.ui.sdSearch.value = "";
    const places = this.places();
    this.ui.sdPlaces.textContent = places ? `Места брифа: ${places}` : "Места брифа не выбраны: учитываются все страны";
    this.ui.sourcesDialog.showModal();
    (this.draft.custom ? this.ui.sdModeCustom : this.ui.sdModeAuto).focus();
    this.drawDialog(); this.recountDraft(); this.loadRows();
  }
  closeDialog(apply) {
    if (apply && this.ui.sourcesApply.disabled) return;
    this.draftController?.abort(); this.rowsController?.abort(); clearTimeout(this.searchTimer);
    this.ui.sourcesDialog.close();
    if (apply && SourceRulesModel.key(this.draft) !== this.initial) this.set(SourceRulesModel.toRules(this.draft, this.list));
    this.ui.openSourceRules.focus();
  }
  edit(state) { this.draft = state; this.draftStale = true; this.drawDialog(); this.recountDraft(); }
  async recountDraft() {
    const version = ++this.draftVersion;
    this.draftController?.abort();
    if (SourceRulesModel.problem(this.draft, this.list)) {
      this.draftPreview = null; this.draftStale = false; this.drawDialog(); return;
    }
    const controller = this.draftController = new AbortController();
    try {
      const answer = await this.request(SourceRulesModel.toRules(this.draft, this.list), controller.signal);
      if (version !== this.draftVersion) return;
      this.draftPreview = answer; this.draftError = ""; this.draftStale = false;
    } catch (error) {
      if (version !== this.draftVersion) return;
      this.draftStale = false; this.draftPreview = null; this.draftError = `Не удалось посчитать источники: ${error.message}`;
    }
    this.drawDialog();
  }
  /* Every company site for the places, read once per set of countries: names, industries, and
   * whether the site can be read. Kept in memory only while the page is open. */
  async loadRows() {
    const key = this.countryCodes().join(",");
    if (this.rows && this.rowsKey === key) { this.drawRows(); return; }
    this.rowsController?.abort();
    const controller = this.rowsController = new AbortController();
    this.rows = null; this.rowsError = ""; this.drawRows();
    try {
      const rows = [];
      for (let page = 1; ; page++) {
        const answer = await this.api("/api/v1/sources/search", "POST", {countries: this.countryCodes(),
          groups: ["company_site"], page, page_size: 200}, controller.signal);
        rows.push(...answer.items);
        if (answer.catalog.status !== "ok") { this.rowsError = this.catalogNote({catalog: answer.catalog}); break; }
        if (rows.length >= answer.total || !answer.items.length) break;
      }
      if (controller.signal.aborted) return;
      for (const row of rows) {
        row.categories = row.categories || [];
        row.items = [...SourceRulesModel.itemsOf(row)];  // computed once: checks run per row on every change
        if (row.rule_item) for (const item of row.items) this.names.set(item, row.name);
      }
      rows.sort((a, b) => a.name.localeCompare(b.name, "ru"));
      // A failed catalog is asked again next time; only a full answer is kept for these countries.
      this.rows = this.rowsError ? null : rows; this.rowsKey = this.rowsError ? "" : key;
    } catch (error) {
      if (controller.signal.aborted) return;
      this.rowsError = `Не удалось загрузить сайты компаний: ${error.message}`;
    }
    this.drawDialog();
  }
  /* Controls rebuilt on a change get focus back by their data-key. */
  keepFocus(draw) {
    const key = document.activeElement?.dataset?.key;
    draw();
    if (!key || document.activeElement?.dataset?.key === key) return;
    const again = this.ui.sourcesDialog.querySelector(`[data-key="${CSS.escape(key)}"]`);
    (again && again.offsetParent !== null ? again : this.ui.sdSearch.offsetParent !== null ? this.ui.sdSearch : this.ui.sdSites).focus();
  }
  drawDialog() { this.keepFocus(() => this.draw()); }
  draw() {
    const ui = this.ui, state = this.draft, preview = this.draftStale ? null : this.draftPreview;
    this.learnNames(this.draftPreview);
    ui.sdModeAuto.checked = !state.custom; ui.sdModeCustom.checked = state.custom;
    ui.sdAutoCount.textContent = this.everywhere ? ` · ${sourcesText(this.everywhere.total)}` : "";
    ui.sdList.hidden = !state.custom;
    ui.sdBuiltins.replaceChildren(...BUILTIN_GROUPS.map(group => this.groupCard(group)).filter(Boolean));
    const counts = this.siteCounts();
    const some = counts.known ? counts.checked + counts.elsewhere > 0 : state.sites;
    ui.sdSites.checked = state.sites && some;
    ui.sdSites.indeterminate = state.sites && counts.known && counts.checked > 0 && counts.checked < counts.total;
    ui.sdSites.closest(".group").classList.toggle("on", ui.sdSites.checked);
    ui.sdSitesNum.replaceChildren(...(counts.total ? [String(counts.checked), el("small", ` из ${counts.total}`
      + (counts.elsewhere ? ` · ещё ${counts.elsewhere} вне мест брифа` : ""))] : []));
    this.drawRows();
    const problem = SourceRulesModel.problem(state, this.list);
    const catalogOk = !preview || !state.sites || ["ok", "not_requested"].includes(preview.catalog.status);
    ui.sourcesApply.disabled = !!problem || this.draftStale || (!!preview && !preview.total && catalogOk);
    ui.sdProblem.textContent = problem || this.draftError || ""; ui.sdProblem.hidden = !(problem || this.draftError);
    ui.sdTotal.textContent = problem ? "Негде искать" : !preview ? "Считаем…" : preview.total
      ? `Будем искать в ${countText(preview.total, "источнике", "источниках", "источниках")}` : "По этому выбору искать негде";
    ui.sdTotalNote.textContent = preview && preview.total ? this.composition(preview) : "";
    const catalog = this.catalogNote(preview);
    ui.sdCatalogNote.textContent = catalog; ui.sdCatalogNote.hidden = !catalog;
  }
  /* «HH, SuperJob, Getmatch и 4 компании» */
  composition(preview) {
    const parts = this.list.filter(source => preview.builtin.includes(source.id)).map(source => source.name);
    const sites = this.groupCount(preview, "company_site").selected;
    if (sites) parts.push(countText(sites, "сайт компании", "сайта компаний", "сайтов компаний"));
    const last = parts.pop();
    return parts.length ? `${parts.join(", ")} и ${last}` : last || "";
  }
  groupCard(group) {
    const members = this.list.filter(source => source.group === group);
    if (!members.length) return null;
    const covered = this.covered(), usable = members.filter(source => covered.has(source.id));
    const on = usable.filter(source => this.draft.builtins.includes(source.id));
    const card = el("section", undefined, "group" + (on.length ? " on" : ""));
    const head = el("div", undefined, "g-head"), box = el("input"), title = el("label", GROUP_NAMES[group]);
    box.type = "checkbox"; box.id = "sdGroup-" + group; box.dataset.key = "group:" + group; title.htmlFor = box.id;
    box.checked = usable.length > 0 && on.length === usable.length;
    box.indeterminate = on.length > 0 && on.length < usable.length;
    box.disabled = !usable.length;
    box.onchange = () => {
      const next = SourceRulesModel.copy(this.draft), ids = members.map(source => source.id);
      next.builtins = this.list.map(source => source.id)
        .filter(id => ids.includes(id) ? box.checked : next.builtins.includes(id));
      this.edit(next);
    };
    // Members that do not cover the places follow the group, so an unchecked group stays out.
    const follow = next => {
      const anyOn = usable.some(source => next.builtins.includes(source.id));
      const rest = members.filter(source => !covered.has(source.id)).map(source => source.id);
      next.builtins = this.list.map(source => source.id)
        .filter(id => rest.includes(id) ? anyOn : next.builtins.includes(id));
      return next;
    };
    const num = el("span", undefined, "num");
    num.append(String(on.length), el("small", ` из ${usable.length}`));
    head.append(box, title, num);
    const body = el("div", undefined, "g-body members");
    for (const source of members) {
      if (!covered.has(source.id)) {
        const item = el("span", undefined, "member na"), off = el("input"), names = this.countryNames();
        const note = el("span", names.length ? `не ищет: ${names.join(", ")}` : "не ищет в местах брифа", "cov");
        note.id = "sdCov-" + source.id; off.type = "checkbox"; off.disabled = true; off.setAttribute("aria-describedby", note.id);
        item.append(off, el("span", source.name), note);
        body.append(item);
        continue;
      }
      const item = el("label", undefined, "member"), check = el("input");
      check.type = "checkbox"; check.dataset.key = "builtin:" + source.id; check.checked = this.draft.builtins.includes(source.id);
      item.classList.toggle("on", check.checked);
      check.onchange = () => {
        const next = SourceRulesModel.copy(this.draft);
        next.builtins = this.list.map(s => s.id).filter(id => id === source.id ? check.checked : next.builtins.includes(id));
        this.edit(follow(next));
      };
      item.append(check, el("span", source.name));
      body.append(item);
    }
    card.append(head, body);
    return card;
  }
  checker(state = this.draft) {
    const all = state.companies === "all", marks = new Set(all ? state.skipped : state.picked);
    return row => state.sites && !!row.rule_item && (all ? !row.items.some(i => marks.has(i)) : row.items.some(i => marks.has(i)));
  }
  isChecked(row) { return this.checker()(row); }
  siteCounts(state = this.draft) {
    if (!this.rows) {  // still loading or the catalog failed: the preview's count stands in
      const counts = this.groupCount(this.draftStale ? null : this.draftPreview, "company_site");
      return {checked: state.sites ? counts.selected : 0, total: this.groupCount(this.everywhere, "company_site").available,
        known: false};
    }
    const usable = this.rows.filter(row => row.rule_item), checked = this.checker(state);
    // Chosen companies with no site in these countries still count: the rules keep them.
    const listed = new Set(this.rows.flatMap(row => row.items));
    const elsewhere = state.sites && state.companies === "selected" ? state.picked.filter(item => !listed.has(item)).length : 0;
    return {checked: usable.filter(checked).length, total: usable.length, known: true, elsewhere};
  }
  drawRows() {
    const ui = this.ui;
    if (!this.draft) return;
    ui.sdShowAll.setAttribute("aria-pressed", String(!this.onlyChecked));
    ui.sdShowChecked.setAttribute("aria-pressed", String(this.onlyChecked));
    const counts = this.siteCounts();
    ui.sdShowChecked.textContent = `Отмеченные · ${counts.checked}`;
    ui.sdAll.checked = counts.total > 0 && counts.checked === counts.total;
    ui.sdAll.indeterminate = counts.checked > 0 && counts.checked < counts.total;
    const filtered = !!this.category || !!ui.sdSearch.value.trim() || this.onlyChecked;
    ui.sdAllNote.textContent = filtered ? `все ${counts.total}, не только найденные · отмечено ${counts.checked}`
      : `отмечено ${counts.checked} из ${counts.total}`;
    if (this.rowsError || !this.rows) {
      ui.sdCats.replaceChildren();
      ui.sdRows.replaceChildren(el("p", this.rowsError || "Загружаем сайты компаний…", this.rowsError ? "sd-error" : "muted"));
      ui.sdRowsFoot.textContent = "";
      return;
    }
    this.keepFocus(() => {
      this.drawCategories();
      const query = ui.sdSearch.value.trim().toLowerCase();
      const base = this.rows.filter(row => this.showUnreadable || row.rule_item);
      const checked = this.checker();
      const shown = base.filter(row => (!this.category || row.categories.includes(this.category))
        && (!query || row.name.toLowerCase().includes(query) || row.categories.some(c => c.toLowerCase().includes(query)))
        && (!this.onlyChecked || checked(row)));
      shown.sort((a, b) => Number(!a.rule_item) - Number(!b.rule_item));
      const known = [...this.initialState.picked, ...this.initialState.skipped], M = SourceRulesModel;
      const nodes = shown.map(row => {
        const categories = el("small", row.categories.join(" · "));
        if (!row.rule_item) {
          const line = el("div", undefined, "row na"), box = el("input"), text = el("span");
          box.type = "checkbox"; box.disabled = true;
          text.append(el("b", row.name), categories);
          line.append(box, text, el("span", "Сайт пока не читаем", "pill"));
          return line;
        }
        const line = el("label", undefined, "row"), box = el("input"), text = el("span");
        box.type = "checkbox"; box.checked = checked(row); box.dataset.key = "row:" + row.id;
        box.onchange = () => {
          let state = M.copy(this.draft);
          // Company sites were off: start a selection, keeping companies chosen before.
          if (!state.sites) state = {...state, sites: true, companies: "selected"};
          let next = state.companies === "all" ? (box.checked ? M.unskip(state, row) : M.skip(state, row, known))
            : (box.checked ? M.pick(state, row, known) : M.unpick(state, row));
          const left = this.siteCounts(next);
          if (!left.checked && !left.elsewhere) next = {...next, sites: false, companies: "selected", picked: [], skipped: []};
          this.edit(next);
        };
        text.append(el("b", row.name), categories);
        line.append(box, text, el("span"));
        return line;
      });
      ui.sdRows.replaceChildren(...(nodes.length ? nodes : [el("p", this.onlyChecked ? "Ничего не отмечено." : "Ничего не нашлось.", "muted")]));
      const usable = this.rows.filter(row => row.rule_item).length, unreadable = this.rows.length - usable;
      const total = shown.length === base.length ? countText(base.length, "компания", "компании", "компаний")
        : `Показано ${shown.length} из ${base.length}`;
      const foot = [el("span", this.showUnreadable && unreadable ? `${total}, из них ${unreadable} пока не читаем` : total)];
      if (unreadable) {
        const toggle = el("button", this.showUnreadable ? "Скрыть сайты, которые пока не читаем"
          : `Показать ещё ${countText(unreadable, "сайт", "сайта", "сайтов")}, которые пока не читаем`, "text-button");
        toggle.type = "button"; toggle.dataset.key = "unreadable";
        toggle.onclick = () => { this.showUnreadable = !this.showUnreadable; this.drawRows(); };
        foot.push(toggle);
      }
      ui.sdRowsFoot.replaceChildren(...foot);
    });
  }
  drawCategories() {
    const counts = new Map(), base = this.rows.filter(row => this.showUnreadable || row.rule_item);
    for (const row of base) for (const category of row.categories) counts.set(category, (counts.get(category) || 0) + 1);
    const sorted = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ru"));
    const button = (label, value, count) => {
      const b = el("button", undefined, "cat" + (this.category === value ? " on" : ""));
      b.type = "button"; b.dataset.key = "cat:" + (value ?? ""); b.setAttribute("aria-pressed", String(this.category === value));
      b.append(el("span", label), el("span", String(count), "n"));
      b.onclick = () => { this.category = value; this.drawRows(); };
      return b;
    };
    this.ui.sdCats.replaceChildren(el("h4", "Отрасль"), button("Все", null, base.length),
      ...sorted.map(([category, count]) => button(category, category, count)));
  }
}

if (typeof module !== "undefined") module.exports = {SourceRulesModel, MAX_RULE_ITEMS};
