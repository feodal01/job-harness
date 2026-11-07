/* Source rules of a brief (mockup 8о): the rules model, the form section and the dialog.
 * The brief keeps rules, not a list: `null` is the default (every source covering the brief's
 * places), otherwise `{mode:"auto", groups, include, exclude}`. Counts and names come from
 * POST /api/v1/sources/preview, which resolves the rules with the same code as a run. */

const RULE_TYPES = ["aggregator", "it_board"];
const COMPANY = "company:";
const MAX_RULE_ITEMS = 1000;
const GROUP_TITLES = {
  aggregator: ["Агрегаторы", "Крупные сайты вакансий: HH и другие"],
  it_board: ["IT-площадки", "Getmatch и другие площадки для IT"],
  company_site: ["Сайты компаний", "Карьерные страницы работодателей: все или только выбранные"],
  telegram: ["Telegram-каналы", "Пока не поддерживаются"],
};
const isCompanyItem = item => item.startsWith(COMPANY);
const ruleWord = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};
const sourcesText = n => `${n.toLocaleString("ru")} ${ruleWord(n, "источник", "источника", "источников")}`;

/* The dialog's state: custom rules or the default, chosen built-in types, all or selected company
 * sites, and the include/exclude lists. Pure, so tests/web runs it in node. */
const SourceRulesModel = {
  fromRules(rules) {
    if (!rules) return {custom: false, types: [...RULE_TYPES], companies: "all", include: [], exclude: []};
    const include = [...rules.include], exclude = [...rules.exclude];
    if (rules.mode === "selected") return {custom: true, types: [], companies: "selected", include, exclude};
    const groups = rules.groups ?? null;
    return {
      custom: groups !== null || include.length > 0 || exclude.length > 0,
      types: groups === null ? [...RULE_TYPES] : RULE_TYPES.filter(type => groups.includes(type)),
      companies: groups === null || groups.includes("company_site") ? "all" : "selected",
      include, exclude,
    };
  },
  toRules(state) {
    if (!state.custom) return null;
    const groups = [...RULE_TYPES.filter(type => state.types.includes(type)),
      ...(state.companies === "all" ? ["company_site"] : [])];
    // With every company site chosen, a chosen company adds nothing; it stays in the draft only.
    const include = state.companies === "all" ? state.include.filter(item => !isCompanyItem(item)) : [...state.include];
    return {mode: "auto", groups, include, exclude: [...state.exclude]};
  },
  /* Why the service would refuse these rules; null when it accepts them. */
  problem(state) {
    const rules = SourceRulesModel.toRules(state);
    if (!rules) return null;
    if (!rules.groups.length && !rules.include.length) return "Выберите тип источника или добавьте компанию.";
    if (rules.include.length > MAX_RULE_ITEMS || rules.exclude.length > MAX_RULE_ITEMS)
      return `В списках правил может быть до ${MAX_RULE_ITEMS} элементов.`;
    return null;
  },
  /* The button a found source gets, from its participation under the draft rules. */
  action(state, match) {
    const item = match.rule_item;
    if (!item) return null;
    switch (match.participation) {
      case "excluded": return {label: "Вернуть", apply: s => SourceRulesModel.restore(s, match)};
      // «Убрать» only when the source is in the search through include alone; when a chosen type
      // brings it in as well, only an exclusion takes it out.
      case "included": {
        const rules = SourceRulesModel.toRules(state), own = SourceRulesModel.itemsOf(match);
        const including = rules ? rules.include.filter(other => own.has(other)) : [];
        return including.length && !rules.groups.includes(match.group)
          ? {label: "Убрать", apply: s => SourceRulesModel.dropAll(s, including)}
          : {label: "Исключить", apply: s => SourceRulesModel.exclude(s, item)};
      }
      case "not_selected": case "not_covered":
        return {label: "Добавить", apply: s => SourceRulesModel.add(s, item)};
      default: return null;
    }
  },
  add(state, item) {
    const next = SourceRulesModel.custom(state);
    next.exclude = next.exclude.filter(other => other !== item);
    if (!next.include.includes(item)) next.include.push(item);
    return next;
  },
  drop(state, item) { return SourceRulesModel.dropAll(state, [item]); },
  dropAll(state, items) {
    const next = SourceRulesModel.custom(state);
    next.include = next.include.filter(other => !items.includes(other));
    return next;
  },
  /* Every rule item that names this source: its id and each catalog company of a shared board. */
  itemsOf(match) {
    return new Set([match.rule_item, match.id, ...(match.company_ids || []).map(id => COMPANY + id)].filter(Boolean));
  },
  exclude(state, item) {
    const next = SourceRulesModel.custom(state);
    next.include = next.include.filter(other => other !== item);
    if (!next.exclude.includes(item)) next.exclude.push(item);
    return next;
  },
  /* An excluded source comes back whether its id or any of its companies was excluded
   * (several catalog companies can share one board). */
  restore(state, match) {
    const next = SourceRulesModel.custom(state), items = SourceRulesModel.itemsOf(match);
    next.exclude = next.exclude.filter(other => !items.has(other));
    return next;
  },
  custom(state) {
    return {...state, custom: true, types: [...state.types], include: [...state.include], exclude: [...state.exclude]};
  },
  /* Short text where no preview is at hand: brief summary, before the count arrives. */
  describe(rules) {
    if (!rules) return "все по местам брифа";
    const state = SourceRulesModel.fromRules(rules), parts = [];
    const types = state.types.map(type => GROUP_TITLES[type][0].toLowerCase());
    if (types.length) parts.push(types.join(", "));
    const companies = state.include.filter(isCompanyItem).length;
    if (state.companies === "all") parts.push("сайты компаний");
    else if (companies) parts.push(`${companies} ${ruleWord(companies, "компания", "компании", "компаний")}`);
    const added = state.include.filter(item => !isCompanyItem(item)).length;
    if (added) parts.push(`добавлено: ${added}`);
    if (state.exclude.length) parts.push(`исключено: ${state.exclude.length}`);
    return parts.length ? parts.join(" · ") : "не выбраны";
  },
};

class SourceRulesEditor {
  /* api(path, method, body, signal); countries() gives the ISO codes of the form's places and
   * places() their text; onChange() runs when the rules or their count change. */
  constructor({api, countries, places, onChange}) {
    this.api = api; this.countries = countries; this.places = places; this.onChange = onChange;
    this.rules = null; this.preview = null; this.error = ""; this.loading = false;
    this.timer = null; this.controller = null; this.version = 0;
    this.draft = null; this.draftPreview = null; this.draftError = ""; this.draftVersion = 0; this.draftController = null;
    this.query = ""; this.searchTimer = null; this.searchController = null; this.matches = null; this.searchPage = 1;
    const $ = id => document.getElementById(id);
    this.ui = {
      auto: $("sourceModeAuto"), custom: $("sourceModeCustom"), autoCount: $("sourceAutoCount"),
      summary: $("sourceRulesSummary"), note: $("sourceCatalogNote"), open: $("openSourceRules"), count: $("sourceCount"),
      dialog: $("sourcesDialog"), close: $("sourcesDialogClose"), cancel: $("sourcesCancel"), apply: $("sourcesApply"),
      dAuto: $("sdModeAuto"), dCustom: $("sdModeCustom"), dAutoCount: $("sdAutoCount"), rules: $("sdRules"),
      types: $("sdTypes"), places: $("sdPlaces"), companies: $("sdCompanies"), added: $("sdAdded"),
      search: $("sdSearch"), results: $("sdResults"), exclusions: $("sdExclusions"), exclusionsTitle: $("sdExclusionsTitle"),
      total: $("sdTotal"), totalNote: $("sdTotalNote"), problem: $("sdProblem"), dNote: $("sdCatalogNote"),
    };
    const ui = this.ui;
    ui.auto.onchange = () => { if (ui.auto.checked) this.set(null); };
    ui.custom.onchange = () => { if (ui.custom.checked) this.openDialog(true); };
    ui.open.onclick = () => this.openDialog(false);
    ui.close.onclick = ui.cancel.onclick = () => this.closeDialog(false);
    ui.apply.onclick = () => this.closeDialog(true);
    ui.dialog.oncancel = event => { event.preventDefault(); this.closeDialog(false); };
    ui.dAuto.onchange = () => { if (ui.dAuto.checked) this.editDraft({...this.draft, custom: false}); };
    ui.dCustom.onchange = () => { if (ui.dCustom.checked) this.editDraft(SourceRulesModel.custom(this.draft)); };
    ui.search.oninput = () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => { this.query = ui.search.value.trim(); this.searchPage = 1; this.search(); }, 300);
    };
    ui.search.onkeydown = event => { if (event.key === "Enter") event.preventDefault(); };
  }

  /* --- The form ------------------------------------------------------------------------- */

  load(rules) {
    // Default rules written out (a run's snapshot, an API client) are the default: keep them as null.
    this.rules = rules && SourceRulesModel.fromRules(rules).custom ? structuredClone(rules) : null;
    this.preview = null; this.error = "";
    this.render(); this.refresh(0);
  }
  set(rules) {
    this.rules = rules; this.render(); this.onChange(); this.refresh(0);
  }
  get problem() { return SourceRulesModel.problem(SourceRulesModel.fromRules(this.rules)); }
  get requiresQuery() { return this.preview?.requires_query === true; }
  get builtin() { return this.preview?.builtin ?? null; }
  describe() {
    if (!this.preview || this.loading) return SourceRulesModel.describe(this.rules);
    return this.rules ? `${sourcesText(this.preview.total)} по правилам`
      : `все по местам брифа · ${this.preview.total.toLocaleString("ru")}`;
  }

  /* Count again after the rules or the brief's places change; only the latest answer is shown. */
  refresh(delay = 300) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.recount(), delay);
  }
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

  render() {
    const ui = this.ui, custom = !!this.rules, preview = this.preview;
    ui.auto.checked = !custom; ui.custom.checked = custom;
    ui.autoCount.textContent = !custom && preview ? ` · ${preview.total.toLocaleString("ru")}` : "";
    ui.count.textContent = this.loading ? "Считаем…" : preview ? sourcesText(preview.total) : "";
    ui.summary.hidden = !custom;
    ui.summary.replaceChildren(...(custom ? this.summaryRows() : []));
    const note = this.error || this.catalogNote(preview) || (preview && !preview.total
      ? "По этим правилам искать не по чему: добавьте тип источника или компанию." : "");
    ui.note.textContent = note; ui.note.hidden = !note;
  }
  summaryRows() {
    const state = SourceRulesModel.fromRules(this.rules), counts = this.groupCounts(this.preview), rows = [];
    const types = state.types.map(type => GROUP_TITLES[type][0] + (counts[type] ? ` · ${counts[type].selected}` : ""));
    rows.push(this.row("Типы", el("span", types.length ? types.join(", ") : "не выбраны")));
    const chosen = state.include.filter(isCompanyItem);
    rows.push(this.row("Сайты компаний", el("span", state.companies === "all"
      ? `все компании${counts.company_site ? " · " + counts.company_site.selected.toLocaleString("ru") : ""}`
      : chosen.length ? `только выбранные · ${chosen.length}: ${chosen.map(item => this.nameOf(item, this.preview)).join(", ")}`
        : "не искать")));
    const added = state.include.filter(item => !isCompanyItem(item));
    if (added.length) rows.push(this.row("Добавлены", this.chips(added, this.preview,
      item => this.set(SourceRulesModel.toRules(SourceRulesModel.drop(state, item))))));
    if (state.exclude.length) rows.push(this.row("Не искать в источниках", this.chips(state.exclude, this.preview,
      item => this.set(SourceRulesModel.toRules({...state, exclude: state.exclude.filter(other => other !== item)})))));
    return rows;
  }
  row(term, content) {
    const row = el("div", undefined, "rs-row");
    row.append(el("span", term, "rs-term"), content);
    return row;
  }
  nameOf(item, preview) {
    const known = preview?.items.find(entry => entry.item === item);
    if (known?.name) return known.name;
    const builtin = typeof sources === "undefined" ? null : sources.find(source => source.id === item);
    return builtin?.name || (isCompanyItem(item) ? "Компания" : item);
  }
  groupCounts(preview) {
    return Object.fromEntries((preview?.groups || []).map(group => [group.group, group]));
  }
  catalogNote(preview) {
    const catalog = preview?.catalog;
    if (!catalog || ["ok", "not_requested"].includes(catalog.status)) return "";
    return `Сайты компаний сейчас не учитываются: ${catalog.message || "каталог недоступен"}`;
  }

  /* --- The dialog ------------------------------------------------------------------------ */

  openDialog(custom) {
    const state = SourceRulesModel.fromRules(this.rules);
    this.draft = custom ? SourceRulesModel.custom(state) : state;
    this.draftPreview = this.preview; this.draftError = "";
    this.matches = null; this.query = ""; this.ui.search.value = ""; this.ui.results.replaceChildren();
    this.ui.dialog.showModal();
    (this.draft.custom ? this.ui.dCustom : this.ui.dAuto).focus();
    this.renderDialog(); this.recountDraft();
  }
  closeDialog(apply) {
    if (apply && SourceRulesModel.problem(this.draft)) return;
    this.draftController?.abort(); this.searchController?.abort(); clearTimeout(this.searchTimer);
    this.ui.dialog.close();
    if (apply) this.set(SourceRulesModel.toRules(this.draft)); else this.render();
    this.ui.open.focus();
  }
  editDraft(state) {
    this.draft = state; this.renderDialog(); this.recountDraft();
    if (this.query) this.search();  // asks again for every row shown, under the new rules
  }
  async recountDraft() {
    const version = ++this.draftVersion;
    this.draftController?.abort();
    if (SourceRulesModel.problem(this.draft)) { this.draftPreview = null; this.renderDialog(); return; }
    const controller = this.draftController = new AbortController();
    this.ui.total.textContent = "Считаем…";
    try {
      const answer = await this.request(SourceRulesModel.toRules(this.draft), controller.signal);
      if (version !== this.draftVersion) return;
      this.draftPreview = answer; this.draftError = "";
    } catch (error) {
      if (version !== this.draftVersion) return;
      this.draftPreview = null; this.draftError = `Не удалось посчитать источники: ${error.message}`;
    }
    this.renderDialog();
  }
  /* Controls are rebuilt on every change; focus goes back to the control with the same key. */
  keepFocus(render) {
    const key = document.activeElement?.dataset?.key;
    render();
    if (!key || document.activeElement?.dataset?.key === key) return;
    const again = this.ui.dialog.querySelector(`[data-key="${CSS.escape(key)}"]`);
    (again || (key.startsWith("match:") || key.startsWith("chip:") ? this.ui.search : null))?.focus();
  }
  renderDialog() { this.keepFocus(() => this.drawDialog()); }
  drawDialog() {
    const ui = this.ui, state = this.draft, preview = this.draftPreview, counts = this.groupCounts(preview);
    ui.dAuto.checked = !state.custom; ui.dCustom.checked = state.custom;
    ui.dAutoCount.textContent = preview && !state.custom ? ` · ${preview.total.toLocaleString("ru")}` : "";
    ui.rules.hidden = !state.custom;
    const places = this.places();
    ui.places.textContent = places ? `Учитываются только источники, которые покрывают места брифа: ${places}.`
      : "Места в брифе не выбраны: учитываются источники всех стран.";
    ui.types.replaceChildren(...[...RULE_TYPES, "company_site", "telegram"].map(group => this.typeRow(group, counts[group])));
    ui.companies.replaceChildren(...this.companyRows(preview, counts.company_site));
    const added = state.include.filter(item => !isCompanyItem(item));
    ui.added.hidden = !added.length;
    ui.added.replaceChildren(...(added.length ? [el("h3", "Добавлены вручную"),
      this.chips(added, preview, item => this.editDraft(SourceRulesModel.drop(this.draft, item)))] : []));
    ui.exclusionsTitle.textContent = `Исключения · ${state.exclude.length}`;
    ui.exclusions.replaceChildren(state.exclude.length
      ? this.chips(state.exclude, preview, item => this.editDraft({...SourceRulesModel.custom(this.draft),
        exclude: this.draft.exclude.filter(other => other !== item)}))
      : el("p", "Нет. Исключить источник можно через поиск выше.", "muted"));
    const problem = SourceRulesModel.problem(state);
    ui.problem.textContent = problem || ""; ui.problem.hidden = !problem;
    ui.apply.disabled = !!problem;
    ui.total.textContent = problem ? "Нет источников" : preview ? sourcesText(preview.total) : "Считаем…";
    ui.totalNote.textContent = this.draftError || (preview && !preview.total ? "по этим правилам искать не по чему"
      : state.custom ? "по правилам" : "все, что покрывают места брифа");
    const note = this.catalogNote(preview);
    ui.dNote.textContent = note; ui.dNote.hidden = !note;
    if (this.matches) this.drawMatches();
  }
  typeRow(group, count) {
    const [title, text] = GROUP_TITLES[group], row = el("label", undefined, "sd-type");
    const body = el("span", undefined, "sd-type-text"), n = el("span", "", "n");
    body.append(el("b", title), el("small", text));
    if (RULE_TYPES.includes(group)) {
      const box = el("input"); box.type = "checkbox"; box.checked = this.draft.types.includes(group); box.dataset.key = "type:" + group;
      box.onchange = () => {
        const next = SourceRulesModel.custom(this.draft);
        next.types = RULE_TYPES.filter(type => type === group ? box.checked : next.types.includes(type));
        this.editDraft(next);
      };
      if (count) n.textContent = box.checked && count.selected !== count.available
        ? `${count.selected} из ${count.available}` : String(count.available);
      row.append(box);
    } else {
      row.classList.add(group === "telegram" ? "off" : "fixed");
      row.append(el("span", undefined, "sd-type-gap"));
      n.textContent = group === "telegram" ? "—" : !count ? "" : count.selected !== count.available
        ? `${count.selected.toLocaleString("ru")} из ${count.available.toLocaleString("ru")}` : count.available.toLocaleString("ru");
    }
    row.append(body, n);
    return row;
  }
  companyRows(preview, count) {
    const state = this.draft, rows = [], chosen = state.include.filter(isCompanyItem);
    const modes = el("div", undefined, "sd-company-modes");
    for (const [value, text] of [["all", `Все компании${count ? " · " + count.available.toLocaleString("ru") : ""}`],
      ["selected", `Только выбранные · ${chosen.length}`]]) {
      const label = el("label", undefined, "check-label"), radio = el("input");
      radio.type = "radio"; radio.name = "sdCompanyMode"; radio.value = value; radio.checked = state.companies === value;
      radio.dataset.key = "companies:" + value;
      radio.onchange = () => { if (radio.checked) this.editDraft({...SourceRulesModel.custom(this.draft), companies: value}); };
      label.append(radio, " " + text); modes.append(label);
    }
    rows.push(modes);
    if (state.companies === "selected") rows.push(chosen.length
      ? this.chips(chosen, preview, item => this.editDraft(SourceRulesModel.drop(this.draft, item)))
      : el("p", "Компании не выбраны: найдите компанию ниже и нажмите «Добавить».", "muted"));
    const catalog = preview?.catalog;
    if (catalog?.status === "ok" && catalog.rows != null && count)
      rows.push(el("small", `В каталоге ${catalog.rows.toLocaleString("ru")} ${ruleWord(catalog.rows, "запись", "записи", "записей")}; `
        + `для мест брифа подходят ${count.available.toLocaleString("ru")} ${ruleWord(count.available, "сайт", "сайта", "сайтов")}.`));
    const find = el("button", "Добавить или убрать компанию", "text-button"); find.type = "button";
    find.onclick = () => { this.ui.search.focus(); this.ui.search.scrollIntoView({block: "center"}); };
    rows.push(find);
    return rows;
  }
  chips(items, preview, remove) {
    const list = el("div", undefined, "sd-chips");
    for (const item of items) {
      const entry = preview?.items.find(known => known.item === item), name = this.nameOf(item, preview);
      const chip = el("span", undefined, "token" + (entry?.status === "missing" ? " missing" : ""));
      const button = el("button", "×"); button.type = "button"; button.setAttribute("aria-label", `Убрать: ${name}`);
      button.dataset.key = "chip:" + item; button.onclick = () => remove(item);
      chip.append(el("span", name), button); list.append(chip);
      if (entry?.status === "missing" && entry.reason) list.append(el("small", `${name}: ${entry.reason}`, "sd-missing"));
    }
    return list;
  }
  async search(more = false) {
    clearTimeout(this.searchTimer);
    this.searchController?.abort();
    if (!this.query) { this.searching = false; this.matches = null; this.ui.results.replaceChildren(); return; }
    const controller = this.searchController = new AbortController();
    // «Показать ещё» asks for the next page; a refresh asks again for every row already shown.
    const size = more ? 20 : Math.min(200, this.searchPage * 20);
    if (!more) this.searchPage = size / 20;
    this.searching = true;
    if (this.matches) this.renderMatches();
    // Rules the service would refuse (nothing chosen yet) cannot say who takes part; then every
    // supported source is shown as not chosen, which is exactly what those rules mean.
    const empty = !!SourceRulesModel.problem(this.draft);
    const rules = empty ? null : SourceRulesModel.toRules(this.draft);
    try {
      const page = await this.api("/api/v1/sources/search", "POST", {source_rules: rules ?? {},
        countries: this.countryCodes(), name: this.query, page: more ? this.searchPage : 1, page_size: size}, controller.signal);
      if (controller.signal.aborted) return;
      this.searching = false;
      if (empty) for (const match of page.items) if (match.rule_item) {
        match.participation = "not_selected"; match.participation_reason = "Ничего не выбрано.";
      }
      this.matches = more && this.matches ? {...page, items: [...this.matches.items, ...page.items]} : page;
      this.renderMatches();
    } catch (error) {
      if (controller.signal.aborted) return;
      this.searching = false;
      this.matches = null; this.ui.results.replaceChildren(el("p", `Поиск не удался: ${error.message}`, "sd-error"));
    }
  }
  renderMatches() { this.keepFocus(() => this.drawMatches()); }
  drawMatches() {
    const page = this.matches, host = this.ui.results;
    host.setAttribute("aria-busy", String(!!this.searching));
    if (!page.items.length) { host.replaceChildren(el("p", `Ничего не нашлось по «${this.query}».`, "muted")); return; }
    const nodes = [];
    for (const group of page.groups) {
      const items = page.items.filter(item => item.group === group.group);
      if (!items.length) continue;
      nodes.push(el("h4", `${GROUP_TITLES[group.group][0]} · ${group.total} ${ruleWord(group.total, "совпадение", "совпадения", "совпадений")}`));
      const list = el("ul", undefined, "sd-matches");
      for (const match of items) list.append(this.matchRow(match));
      nodes.push(list);
    }
    if (page.items.length < page.total) {
      const more = el("button", `Показать ещё · ${page.total - page.items.length}`, "secondary"); more.type = "button";
      more.dataset.key = "more";
      if (this.searching) more.setAttribute("aria-disabled", "true");
      more.onclick = () => { if (this.searching) return; this.searchPage += 1; this.search(true); };
      nodes.push(more);
    }
    host.replaceChildren(...nodes);
  }
  matchRow(match) {
    const row = el("li", undefined, "sd-match " + match.participation);
    const text = el("span", undefined, "sd-match-text");
    text.append(el("b", match.name), el("small", `${match.kind_name} · ${this.participationText(match)}`));
    row.append(text);
    const action = SourceRulesModel.action(this.draft, match);
    if (action) {
      const button = el("button", action.label, "secondary"); button.type = "button";
      button.setAttribute("aria-label", `${action.label}: ${match.name}`); button.dataset.key = "match:" + match.id;
      // While the list is being asked again its labels may be stale; aria-disabled keeps focus.
      if (this.searching) button.setAttribute("aria-disabled", "true");
      button.onclick = () => { if (!this.searching) this.editDraft(action.apply(this.draft)); };
      row.append(button);
    }
    return row;
  }
  participationText(match) {
    const state = {included: "Участвует", excluded: "Не участвует", not_selected: "Не участвует",
      not_covered: "Не участвует", unsupported: "Не поддерживается"}[match.participation];
    const reason = match.participation_reason.replace(/\.$/, "");
    return `${state}: ${reason.charAt(0).toLowerCase()}${reason.slice(1)}`;
  }
}

if (typeof module !== "undefined") module.exports = {SourceRulesModel, MAX_RULE_ITEMS};
