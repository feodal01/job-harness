/* Where a brief searches (mockup 8о): the choice model, the form line and the dialog.
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
    if (!rules.groups.length && !rules.include.length) {
      return state.sites && state.companies === "selected" ? "Выберите компанию или отметьте другой источник."
        : "Отметьте, где искать.";
    }
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
  pick(state, match) {
    const next = SourceRulesModel.copy(state), own = SourceRulesModel.itemsOf(match);
    next.skipped = next.skipped.filter(item => !own.has(item));
    if (!next.picked.some(item => own.has(item))) next.picked.push(match.rule_item);
    return next;
  },
  unpick(state, match) {
    const next = SourceRulesModel.copy(state), own = SourceRulesModel.itemsOf(match);
    next.picked = next.picked.filter(item => !own.has(item));
    return next;
  },
  skip(state, match) {
    const next = SourceRulesModel.copy(state), own = SourceRulesModel.itemsOf(match);
    next.picked = next.picked.filter(item => !own.has(item));
    if (!next.skipped.some(item => own.has(item))) next.skipped.push(match.rule_item);
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

class SourceRulesEditor {
  /* api(path, method, body, signal); builtins() — built-in sources [{id, group, name}];
   * countries() — ISO codes of the form's places, places() — their text; onChange() runs when the
   * rules or their count change. */
  constructor({api, builtins, countries, places, onChange}) {
    Object.assign(this, {api, builtins, countries, places, onChange});
    this.rules = null; this.preview = null; this.error = ""; this.loading = false;
    this.timer = null; this.controller = null; this.version = 0;
    this.draft = null; this.draftPreview = null; this.draftError = ""; this.draftVersion = 0; this.draftController = null;
    this.everywhere = null; this.names = new Map();
    this.query = ""; this.searchTimer = null; this.searchController = null; this.matches = null; this.skipOpen = false;
    const $ = id => document.getElementById(id);
    this.ui = Object.fromEntries(["sourceSummary", "sourceCatalogNote", "openSourceRules", "sourceCount", "sourcesDialog",
      "sourcesDialogClose", "sourcesCancel", "sourcesApply", "sdModeAuto", "sdModeCustom", "sdAutoText", "sdCatalogNote",
      "sdList", "sdBuiltins", "sdSites", "sdSitesCount", "sdSitesBox", "sdSitesAll", "sdSitesPicked",
      "sdSitesPickedText", "sdSitesNote", "sdChips", "sdSkipOpen", "sdSearchBox", "sdSearch", "sdResults", "sdTotal"]
      .map(id => [id, $(id)]));
    const ui = this.ui, M = SourceRulesModel;
    ui.openSourceRules.onclick = () => this.openDialog();
    ui.sourcesDialogClose.onclick = ui.sourcesCancel.onclick = () => this.closeDialog(false);
    ui.sourcesApply.onclick = () => this.closeDialog(true);
    ui.sourcesDialog.oncancel = event => { event.preventDefault(); this.closeDialog(false); };
    ui.sdModeAuto.onchange = () => { if (ui.sdModeAuto.checked) this.edit({...M.copy(this.draft), custom: false}); };
    ui.sdModeCustom.onchange = () => { if (ui.sdModeCustom.checked) this.edit({...M.copy(this.draft), custom: true}); };
    // Ticking company sites with no company chosen yet means all of them.
    ui.sdSites.onchange = () => this.edit({...M.copy(this.draft), sites: ui.sdSites.checked,
      companies: ui.sdSites.checked && !this.draft.picked.length ? "all" : this.draft.companies});
    ui.sdSitesAll.onchange = () => { if (ui.sdSitesAll.checked) this.edit({...M.copy(this.draft), companies: "all"}); };
    ui.sdSitesPicked.onchange = () => { if (ui.sdSitesPicked.checked) this.edit({...M.copy(this.draft), companies: "selected"}); };
    ui.sdSkipOpen.onclick = () => { this.skipOpen = true; this.drawDialog(); ui.sdSearch.focus(); };
    ui.sdSearch.oninput = () => { clearTimeout(this.searchTimer); this.searchTimer = setTimeout(() => this.search(), 300); };
    ui.sdSearch.onkeydown = event => { if (event.key === "Enter") event.preventDefault(); };
  }

  /* --- The form ------------------------------------------------------------------------- */

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
    return this.preview && !this.loading ? this.summary(this.rules, this.preview)
      : SourceRulesModel.describe(this.rules, this.list);
  }

  /* Count again after the rules or the brief's places change; only the latest answer is shown. */
  refresh(delay = 300) { clearTimeout(this.timer); this.timer = setTimeout(() => this.recount(), delay); }
  async recount() {
    const version = ++this.version;
    this.controller?.abort();
    if (this.problem) { this.preview = null; this.loading = false; this.render(); this.onChange(); return; }
    const controller = this.controller = new AbortController();
    this.loading = true; this.render();
    try {
      const answer = await this.request(this.rules, controller.signal);
      if (version !== this.version) return;
      this.preview = answer; this.error = "";
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

  render() {
    const ui = this.ui, preview = this.preview;
    ui.sourceCount.textContent = this.loading ? "Считаем…" : preview ? sourcesText(preview.total) : "";
    ui.sourceSummary.textContent = this.loading && !preview ? "Считаем…" : this.describe();
    const note = this.error || this.catalogNote(preview);
    ui.sourceCatalogNote.textContent = note; ui.sourceCatalogNote.hidden = !note;
  }
  /* «HH, Getmatch и 218 сайтов компаний»; chosen companies by name. */
  summary(rules, preview) {
    if (!preview.total) return rules ? "Негде искать: отметьте хотя бы один источник."
      : "Для мест брифа нет источников: измените места.";
    this.learnNames(preview);
    const state = SourceRulesModel.fromRules(rules, this.list);
    const parts = this.list.filter(source => preview.builtin.includes(source.id)).map(source => source.name);
    const sites = preview.groups.find(group => group.group === "company_site")?.selected || 0;
    if (state.sites && state.companies === "selected" && state.picked.length) {
      const names = state.picked.map(item => this.names.get(item) || "компания");
      parts.push(names.length <= 3 ? names.join(", ") : `${names.slice(0, 2).join(", ")} и ещё ${names.length - 2}`);
    } else if (sites) parts.push(countText(sites, "сайт компании", "сайта компаний", "сайтов компаний"));
    if (!parts.length) parts.push(sourcesText(preview.total));
    const last = parts.pop();
    let text = parts.length ? `${parts.join(", ")} и ${last}` : last;
    if (state.sites && state.companies === "all" && state.skipped.length)
      text += `; не искать: ${state.skipped.map(item => this.names.get(item) || "сайт").join(", ")}`;
    return (rules ? "" : "Везде: ") + text;
  }
  catalogNote(preview) {
    const catalog = preview?.catalog;
    if (!catalog || ["ok", "not_requested"].includes(catalog.status)) return "";
    return `Сайты компаний сейчас не учитываются: ${catalog.message || "каталог недоступен"}`;
  }

  /* --- The dialog ------------------------------------------------------------------------ */

  openDialog() {
    if (!this.list.length) return;  // the built-in sources are not loaded yet
    this.draft = SourceRulesModel.fromRules(this.rules, this.list);
    // «Готово» without a change keeps rules made elsewhere exactly as they are.
    this.initial = JSON.stringify(this.draft);
    this.draftPreview = this.preview; this.draftStale = this.loading; this.draftError = ""; this.everywhere = null;
    this.skipOpen = false; this.query = ""; this.matches = null; this.ui.sdSearch.value = ""; this.ui.sdResults.replaceChildren();
    this.ui.sourcesDialog.showModal();
    (this.draft.custom ? this.ui.sdModeCustom : this.ui.sdModeAuto).focus();
    this.drawDialog(); this.recountDraft();
    this.request(null).then(answer => { this.everywhere = answer; this.drawDialog(); }).catch(() => {});
  }
  closeDialog(apply) {
    if (apply && SourceRulesModel.problem(this.draft, this.list)) return;
    this.draftController?.abort(); this.searchController?.abort(); clearTimeout(this.searchTimer);
    this.ui.sourcesDialog.close();
    if (apply && JSON.stringify(this.draft) !== this.initial) this.set(SourceRulesModel.toRules(this.draft, this.list));
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
  /* Controls rebuilt on a change get focus back by their data-key. */
  keepFocus(draw) {
    const key = document.activeElement?.dataset?.key;
    draw();
    if (!key || document.activeElement?.dataset?.key === key) return;
    const visible = node => node && node.offsetParent !== null;
    const again = this.ui.sourcesDialog.querySelector(`[data-key="${CSS.escape(key)}"]`);
    [again, this.ui.sdSearch, this.ui.sdSkipOpen, this.ui.sdSites].find(visible)?.focus();
  }
  drawDialog() { this.keepFocus(() => this.draw()); }
  draw() {
    const ui = this.ui, state = this.draft, preview = this.draftStale ? null : this.draftPreview;
    this.learnNames(this.draftPreview);
    ui.sdModeAuto.checked = !state.custom; ui.sdModeCustom.checked = state.custom;
    const places = this.places(), total = this.everywhere?.total;
    ui.sdAutoText.textContent = [total != null ? sourcesText(total) : "Все источники", places ? `для мест брифа: ${places}` : "",
      ].filter(Boolean).join(" ") + ". Новые подключатся сами.";
    ui.sdList.hidden = !state.custom;
    ui.sdBuiltins.replaceChildren(...this.list.map(source => this.builtinRow(source, preview)));
    const sites = preview?.groups.find(group => group.group === "company_site");
    ui.sdSites.checked = state.sites;
    ui.sdSitesCount.textContent = sites?.available ? `${countText(sites.available, "сайт", "сайта", "сайтов")} для мест брифа` : "";
    ui.sdSitesBox.hidden = !state.sites;
    const all = state.companies === "all";
    ui.sdSitesAll.checked = all; ui.sdSitesPicked.checked = !all;
    ui.sdSitesPickedText.textContent = state.picked.length ? `Только выбранные · ${state.picked.length}` : "Только выбранные";
    const note = all && state.picked.length
      ? `Выбранные компании (${state.picked.length}) не сохранятся: ищем на всех сайтах.`
      : !all && state.skipped.length ? `Сайты без поиска (${state.skipped.length}) не сохранятся: ищем только на выбранных.` : "";
    ui.sdSitesNote.textContent = note; ui.sdSitesNote.hidden = !note;
    const chips = all ? state.skipped : state.picked;
    ui.sdChips.replaceChildren(...(chips.length ? [el("span", all ? "Без этих сайтов:" : "Выбраны:", "sd-chips-label"),
      ...chips.map(item => this.chip(item, all ? "skipped" : "picked"))] : []));
    const searching = !all || this.skipOpen || state.skipped.length > 0;
    ui.sdSkipOpen.hidden = searching; ui.sdSearchBox.hidden = !searching;
    ui.sdSearch.placeholder = all ? "Название компании, где не искать" : "Название компании";
    const problem = SourceRulesModel.problem(state, this.list);
    ui.sdTotal.classList.toggle("sd-problem", !!problem || !!this.draftError || (preview && !preview.total));
    ui.sdTotal.textContent = problem || this.draftError || (!preview ? "Считаем…" : preview.total
      ? `Будем искать в ${countText(preview.total, "источнике", "источниках", "источниках")}` : "По этому выбору искать негде.");
    ui.sourcesApply.disabled = !!problem || (!!preview && !preview.total);
    const catalog = this.catalogNote(preview);
    ui.sdCatalogNote.textContent = catalog; ui.sdCatalogNote.hidden = !catalog;
    if (this.matches) this.drawMatches();
  }
  builtinRow(source, preview) {
    const row = el("label", undefined, "sd-type"), box = el("input"), text = el("span", undefined, "sd-type-text");
    box.type = "checkbox"; box.dataset.key = "builtin:" + source.id; box.checked = this.draft.builtins.includes(source.id);
    box.onchange = () => {
      const next = SourceRulesModel.copy(this.draft);
      next.builtins = this.list.map(s => s.id).filter(id => id === source.id ? box.checked : next.builtins.includes(id));
      this.edit(next);
    };
    text.append(el("b", source.name));
    if (box.checked && preview && !preview.builtin.includes(source.id)) text.append(el("small", "Не ищет в местах брифа"));
    row.append(box, text);
    return row;
  }
  chip(item, key) {
    const name = this.names.get(item) || "Компания", chip = el("span", undefined, "token");
    const button = el("button", "×"); button.type = "button"; button.dataset.key = "chip:" + item;
    button.setAttribute("aria-label", key === "skipped" ? `Снова искать на сайте: ${name}` : `Убрать: ${name}`);
    button.onclick = () => this.edit(SourceRulesModel.without(this.draft, key, item));
    chip.append(el("span", name), button);
    return chip;
  }
  /* Company sites by name; whether each is searched follows the draft, so it is drawn locally. */
  async search() {
    clearTimeout(this.searchTimer);
    this.searchController?.abort();
    this.query = this.ui.sdSearch.value.trim();
    if (!this.query) { this.matches = null; this.ui.sdResults.replaceChildren(); return; }
    const controller = this.searchController = new AbortController();
    try {
      const page = await this.api("/api/v1/sources/search", "POST", {countries: this.countryCodes(), name: this.query,
        groups: ["company_site"], page: 1, page_size: 50}, controller.signal);
      if (controller.signal.aborted) return;
      for (const match of page.items) if (match.rule_item) this.names.set(match.rule_item, match.name);
      this.matches = page; this.drawMatches();
    } catch (error) {
      if (controller.signal.aborted) return;
      this.matches = null; this.ui.sdResults.replaceChildren(el("p", `Поиск не удался: ${error.message}`, "sd-error"));
    }
  }
  drawMatches() {
    const page = this.matches, host = this.ui.sdResults;
    const usable = page.items.filter(match => match.rule_item), unusable = page.items.filter(match => !match.rule_item);
    const nodes = [];
    if (!page.items.length) nodes.push(el("p", `Не нашли компанию «${this.query}».`, "muted"));
    if (usable.length) {
      const list = el("ul", undefined, "sd-matches");
      for (const match of usable) list.append(this.matchRow(match));
      nodes.push(list);
    }
    if (unusable.length) {
      const more = el("details", undefined, "sd-unusable");
      more.append(el("summary", unusable.length === 1 ? "Ещё 1 компания: на её сайте пока не умеем искать"
        : `Ещё ${countText(unusable.length, "компания", "компании", "компаний")}: на их сайтах пока не умеем искать`),
        el("p", unusable.map(match => match.name).join(", "), "muted"));
      nodes.push(more);
    }
    if (page.total > page.items.length)
      nodes.push(el("p", `Показаны первые ${page.items.length} из ${page.total}: уточните название.`, "muted"));
    host.replaceChildren(...nodes);
  }
  matchRow(match) {
    const state = this.draft, all = state.companies === "all", M = SourceRulesModel;
    const on = all ? !M.isSkipped(state, match) : M.isPicked(state, match);
    const row = el("li", undefined, "sd-match"), label = el("label"), box = el("input");
    box.type = "checkbox"; box.checked = on; box.dataset.key = "match:" + match.id;
    box.onchange = () => this.edit(all ? (box.checked ? M.unskip : M.skip)(this.draft, match)
      : (box.checked ? M.pick : M.unpick)(this.draft, match));
    label.append(box, el("span", match.name));
    row.append(label);
    return row;
  }
}

if (typeof module !== "undefined") module.exports = {SourceRulesModel, MAX_RULE_ITEMS};
