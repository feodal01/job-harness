/* Country groups are a view of source-independent per-city search conditions. */
class LocationEditor {
  constructor(root, counter, addButton, request, onChange) {
    this.root = root;
    this.counter = counter;
    this.addButton = addButton;
    this.request = request;
    this.onChange = onChange;
    this.countries = [];
    this.groups = [];
    this.views = new Map();
    this.nextId = 0;
    document.addEventListener("pointerdown", event => {
      for (const view of this.views.values()) {
        if (!view.picker.contains(event.target)) this.closePicker(view);
      }
    });
    this.addButton.onclick = () => {
      if (this.count >= 5 || this.groups.length >= 5) return;
      this.groups.push(this.newGroup());
      this.render();
      this.onChange();
    };
    this.groups.push(this.newGroup());
    this.render();
  }

  newGroup() {
    return {id: ++this.nextId, country: "", cities: [], minimum: "", relocation: false, wholeCountry: false, draft: ""};
  }

  get count() { return this.groups.reduce((n, group) => n + (group.wholeCountry ? 1 : group.cities.length), 0); }
  get valid() {
    return this.validationErrors().length === 0;
  }
  validationErrors() {
    if (!this.countries.length)
      return [{target: this.root, message: "Дождитесь загрузки справочника стран. Если загрузка не удалась, повторите её."}];
    if (this.count > 5)
      return [{target: this.root, message: "Можно выбрать до 5 мест поиска. Удалите лишние места."}];
    const errors = [];
    for (const group of this.groups) {
      const view = this.views.get(group.id);
      if (!group.country) {
        errors.push({target: view.country, message: "Выберите страну для этого места."});
      } else if (group.draft.trim()) {
        errors.push({target: view.input, message: "Выберите введённый город из подсказок или очистите поле."});
      } else if (group.wholeCountry) {
        continue;
      } else if (!group.cities.length) {
        errors.push({target: view.input, message: "Выберите хотя бы один город из подсказок или удалите пустой блок."});
      } else if (group.cities.some(city => !city.geoname_id || !city.city_display_name)) {
        errors.push({target: view.input, message: "Удалите неподтверждённый город и выберите его заново из подсказок."});
      }
    }
    return errors;
  }
  get locations() {
    return this.groups.flatMap(group => {
      const conditions = {country: group.country, minimum_work_format: group.minimum || null,
        relocation_support: group.relocation};
      return group.wholeCountry ? [{...conditions, scope: "country"}]
        : group.cities.map(city => ({...city, ...conditions, scope: "city"}));
    });
  }
  read() {
    if (!this.valid) throw Error("Выберите от 1 до 5 мест. Завершите или удалите пустые блоки мест.");
    return this.locations;
  }
  setCountries(countries) {
    this.countries = [...countries].sort((a, b) => a.name.localeCompare(b.name, "en"));
    this.render();
    this.onChange();
  }
  load(filters = {}) {
    const groups = new Map();
    for (const city of filters.locations || []) {
      const minimum = city.minimum_work_format ?? filters.minimum_work_format ?? "";
      const relocation = city.relocation_support ?? filters.relocation_support ?? false;
      const wholeCountry = city.scope === "country";
      const key = JSON.stringify([city.country, minimum, relocation, wholeCountry]);
      if (!groups.has(key)) groups.set(key, {...this.newGroup(), country: city.country, minimum, relocation, wholeCountry});
      if (!wholeCountry) groups.get(key).cities.push(city);
    }
    this.groups = groups.size ? [...groups.values()] : [this.newGroup()];
    this.render();
    this.onChange();
  }
  abort() {
    for (const view of this.views.values()) {
      clearTimeout(view.timer);
      view.controller?.abort();
      view.version++;
    }
  }
  node(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  button(text, action, className = "secondary") {
    const button = this.node("button", text, className);
    button.type = "button";
    button.onclick = action;
    return button;
  }
  help(id, label, text) {
    const wrapper = this.node("span", undefined, "field-help");
    const tooltip = this.node("span", text, "field-tooltip");
    tooltip.id = id; tooltip.hidden = true; tooltip.setAttribute("role", "tooltip");
    let hideTimer;
    const show = () => {clearTimeout(hideTimer); tooltip.hidden = false;};
    const button = this.button("i", show, "info-button");
    button.setAttribute("aria-label", label); button.setAttribute("aria-describedby", id);
    wrapper.onmouseenter = show;
    wrapper.onmouseleave = () => {
      if (!wrapper.contains(document.activeElement)) hideTimer = setTimeout(() => {tooltip.hidden = true;}, 150);
    };
    button.onfocus = show;
    button.onblur = () => {tooltip.hidden = true;};
    button.onkeydown = event => {
      if (event.key === "Escape") {event.preventDefault(); tooltip.hidden = true;}
    };
    wrapper.append(button, tooltip);
    return wrapper;
  }
  kind(type) {
    return this.node("span", type === "must" ? "Обязательное" : "Пожелание", "kind " + type);
  }
  setStatus(view, text) {
    view.status.textContent = text; view.status.hidden = !text;
  }
  closePicker(view) {
    view.dropdown.hidden = true;
    view.input.setAttribute("aria-expanded", "false");
    view.toggle.setAttribute("aria-expanded", "false");
  }
  openPicker(group, view) {
    if (!group.country || !view.dropdown.hidden) return;
    for (const other of this.views.values()) if (other !== view) this.closePicker(other);
    this.countryOption(group, view);
    this.setStatus(view, "");
    view.dropdown.hidden = false;
    view.input.setAttribute("aria-expanded", "true");
    view.toggle.setAttribute("aria-expanded", "true");
    if (group.draft.trim() && this.count - Number(group.wholeCountry) < 5)
      view.timer = setTimeout(() => this.suggest(group, view), 400);
  }
  updateSummary() {
    this.counter.textContent = `${this.count} из 5 мест`;
    this.addButton.disabled = !this.countries.length || this.count >= 5 || this.groups.length >= 5;
    for (const group of this.groups) {
      const view = this.views.get(group.id);
      view.input.disabled = !group.country || (this.count - Number(group.wholeCountry) >= 5 && !group.draft);
      view.toggle.disabled = !group.country;
    }
  }
  render() {
    this.abort();
    this.views.clear();
    this.root.replaceChildren();
    this.groups.forEach((group, index) => {
      const fieldset = this.node("fieldset", undefined, "location-group");
      fieldset.append(this.node("legend", `Место ${index + 1}`));
      const remove = this.button("Удалить место", () => {
        this.groups = this.groups.filter(item => item !== group);
        if (!this.groups.length) this.groups.push(this.newGroup());
        this.render();
        this.onChange();
      }, "text-button");
      remove.setAttribute("aria-label", `Удалить место ${index + 1}`);
      const heading = this.node("div", undefined, "location-group-heading");
      heading.append(remove);
      fieldset.append(heading);
      const countryId = `location-country-${group.id}`, cityId = `location-city-${group.id}`;
      const countryLabel = this.node("label", "Страна"); countryLabel.htmlFor = countryId;
      const country = this.node("select"); country.id = countryId;
      const placeholder = this.node("option", "Выберите страну"); placeholder.value = "";
      country.append(placeholder);
      for (const item of this.countries) {
        const option = this.node("option", item.name); option.value = item.code; country.append(option);
      }
      country.value = group.country; country.disabled = !this.countries.length;
      country.onchange = () => {
        // The change applies at once; cities that belonged to the old country can be brought back.
        const previous = {country: group.country, cities: group.cities, wholeCountry: group.wholeCountry};
        group.country = country.value; group.cities = []; group.wholeCountry = false; group.draft = "";
        this.render(); this.onChange();
        const removed = previous.wholeCountry ? ["вся страна"] : previous.cities.map(city => city.city_display_name || city.city);
        if (removed.length && this.onCountryReset) this.onCountryReset(removed, () => {
          if (!this.groups.includes(group)) return;
          Object.assign(group, previous, {draft: ""});
          this.render(); this.onChange();
          this.views.get(group.id)?.country.focus();
        });
      };
      const fields = this.node("div", undefined, "location-fields");
      const countryField = this.node("div", undefined, "location-field");
      countryField.append(countryLabel, country); fields.append(countryField);
      const chips = this.node("div", undefined, "location-cities");
      if (group.wholeCountry) {
        const chip = this.node("div", undefined, "location-city");
        chip.append(this.node("span", "Вся страна"));
        const removeCountry = this.button("×", () => {
          group.wholeCountry = false; this.render(); this.onChange();
        });
        removeCountry.setAttribute("aria-label", "Убрать выбор всей страны");
        chip.append(removeCountry); chips.append(chip);
      }
      for (const city of group.cities) {
        const chip = this.node("div", undefined, "location-city");
        chip.append(this.node("span", [city.city_display_name || city.city, city.region,
          city.geoname_id ? null : "Выберите город заново"].filter(Boolean).join(" · ")));
        const name = city.city_display_name || city.city || "город";
        const removeCity = this.button("×", () => {
          group.cities = group.cities.filter(item => item !== city);
          this.render(); this.onChange();
        });
        removeCity.setAttribute("aria-label", `Убрать город ${name}`);
        chip.append(removeCity); chips.append(chip);
      }
      const cityLabel = this.node("label", "Города");
      cityLabel.htmlFor = cityId;
      const input = this.node("input"); input.id = cityId; input.autocomplete = "off"; input.maxLength = 100;
      input.placeholder = this.count >= 5 && !group.wholeCountry ? "Выбрано 5 мест" : "Найти город";
      input.value = group.draft;
      const status = this.node("small"); status.hidden = true;
      status.id = `${cityId}-status`; status.setAttribute("role", "status");
      const options = this.node("div", undefined, "location-suggestions");
      options.id = `${cityId}-options`; options.setAttribute("role", "listbox"); options.setAttribute("aria-label", "Города и вся страна");
      const picker = this.node("div", undefined, "location-picker");
      const dropdown = this.node("div", undefined, "location-dropdown"); dropdown.hidden = true;
      // Сохраняем фокус до клика по подсказке или повторному поиску: Safari не фокусирует кнопки мышью.
      dropdown.onmousedown = event => {
        if (event.button === 0 && event.target.closest("button")) event.preventDefault();
      };
      input.setAttribute("role", "combobox"); input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-describedby", status.id); input.setAttribute("aria-controls", options.id);
      input.setAttribute("aria-expanded", "false");
      const toggle = this.button("▾", () => {
        if (dropdown.hidden) {this.openPicker(group, view); if (!input.disabled) input.focus();}
        else this.closePicker(view);
      }, "location-toggle");
      toggle.setAttribute("aria-label", "Открыть список городов"); toggle.setAttribute("aria-haspopup", "listbox");
      toggle.setAttribute("aria-controls", options.id); toggle.setAttribute("aria-expanded", "false");
      const retry = this.button("Повторить поиск города", () => {
        input.focus();
        this.suggest(group, view);
      });
      retry.hidden = true;
      const view = {country, input, status, options, retry, picker, dropdown, toggle, version: 0, controller: null, timer: null};
      this.views.set(group.id, view);
      input.onfocus = input.onclick = () => this.openPicker(group, view);
      picker.onfocusout = event => {
        if (!picker.contains(event.relatedTarget)) this.closePicker(view);
      };
      input.oninput = () => {
        clearTimeout(view.timer); view.controller?.abort(); view.version++;
        group.draft = input.value; retry.hidden = true;
        this.countryOption(group, view); this.setStatus(view, "");
        dropdown.hidden = false; input.setAttribute("aria-expanded", "true"); toggle.setAttribute("aria-expanded", "true");
        this.updateSummary(); this.onChange();
        if (group.draft.trim() && this.count - Number(group.wholeCountry) < 5)
          view.timer = setTimeout(() => this.suggest(group, view), 400);
      };
      picker.onkeydown = event => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault(); this.openPicker(group, view);
          const choices = [...options.querySelectorAll("button:not(:disabled)")];
          const current = choices.indexOf(document.activeElement);
          const next = current < 0 ? (event.key === "ArrowDown" ? 0 : choices.length - 1)
            : (current + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length;
          choices[next]?.focus();
        } else if (event.key === "Escape") {
          event.preventDefault(); (input.disabled ? toggle : input).focus(); this.closePicker(view);
        } else if (event.key === "Enter" && event.target === input) {
          event.preventDefault();
        }
      };
      const cityField = this.node("div", undefined, "location-field location-city-field");
      const cityControl = this.node("div", undefined, "location-city-control");
      cityControl.append(chips, input, toggle);
      dropdown.append(options, status, retry); picker.append(cityControl, dropdown);
      cityField.append(cityLabel, picker); fields.append(cityField);
      const formatId = `location-format-${group.id}`;
      const formatLabel = this.node("label", "Минимальный формат работы"); formatLabel.htmlFor = formatId;
      const format = this.node("select"); format.id = formatId;
      for (const [value, label] of [["", "Любой"], ["office", "Офис"], ["hybrid", "Гибрид"],
        ["remote_country", "Удалённо по стране"], ["remote_worldwide", "Удалённо по миру"]]) {
        const option = this.node("option", label); option.value = value; format.append(option);
      }
      format.value = group.minimum;
      format.onchange = () => {group.minimum = format.value; this.onChange();};
      const relocationLabel = this.node("label", undefined, "location-relocation");
      const relocation = this.node("input"); relocation.type = "checkbox"; relocation.checked = group.relocation;
      relocation.onchange = () => {group.relocation = relocation.checked; this.onChange();};
      relocationLabel.append(relocation, document.createTextNode(" Нужна помощь с релокацией"));
      const formatField = this.node("div", undefined, "location-field location-format-field");
      const formatHeading = this.node("div", undefined, "field-label");
      formatHeading.append(formatLabel, this.help(`${formatId}-help`, "О формате работы",
        "Подходит выбранный формат и все более свободные: офис → гибрид → удалённо по стране → удалённо по миру. " +
        "Например, при «Гибрид» подходят гибрид и удалённая работа, а вакансии в офисе получат «Может быть подходит»."));
      format.setAttribute("aria-describedby", `${formatId}-note`);
      const formatNote = this.node("small", "Подходят и более удалённые форматы.");
      formatNote.id = `${formatId}-note`;
      formatField.append(formatHeading, format, formatNote, this.kind("wish"));
      fields.append(formatField);
      const relocationRow = this.node("div", undefined, "location-relocation-row");
      relocationRow.append(relocationLabel, this.kind("wish"), this.help(`${formatId}-relocation-help`, "О релокации",
        "Релокационные пакеты различаются в разных компаниях: они могут включать помощь с оформлением виз, " +
        "компенсацию переезда, жильё и транспортировку вещей. Конкретный состав нужно уточнять у работодателя."));
      fieldset.append(fields, relocationRow);
      this.root.append(fieldset);
      if (group.country && group.draft.trim() && this.count - Number(group.wholeCountry) < 5)
        this.openPicker(group, view);
    });
    this.updateSummary();
  }
  countryOption(group, view) {
    // Keep the pinned option mounted so a pending response cannot steal keyboard focus.
    if (view.options.firstElementChild?.classList.contains("country-choice")) {
      while (view.options.childElementCount > 1) view.options.lastElementChild.remove();
      return;
    }
    const button = this.button("Вся страна", () => {
      if (this.groups.some(other => other !== group && other.wholeCountry && other.country === group.country)) {
        this.setStatus(view, "Вся эта страна уже выбрана в другой локации."); return;
      }
      if (this.count - group.cities.length - Number(group.wholeCountry) >= 5) {
        this.setStatus(view, "Можно выбрать не больше 5 мест поиска."); return;
      }
      group.cities = []; group.wholeCountry = true; group.draft = "";
      this.render(); this.onChange();
      this.views.get(group.id).toggle.focus();
    }, "country-choice");
    button.setAttribute("role", "option"); button.setAttribute("aria-selected", String(group.wholeCountry));
    view.options.append(button);
  }
  async suggest(group, view) {
    clearTimeout(view.timer); view.controller?.abort();
    const version = ++view.version, country = group.country, text = group.draft.trim();
    if (!country || !text) return;
    view.controller = new AbortController(); view.retry.hidden = true; this.setStatus(view, "Ищем города…");
    try {
      const params = new URLSearchParams({country, text});
      const cities = await this.request(`/api/v1/geography/cities?${params}`, "GET", undefined, view.controller.signal);
      if (this.views.get(group.id) !== view || version !== view.version || country !== group.country) return;
      this.countryOption(group, view);
      for (const city of cities) {
        const choice = this.button([city.name, ...city.path].join(" · "), () => {
          if (this.locations.some(place => place.geoname_id === city.geoname_id)) {
            this.setStatus(view, "Этот город уже выбран. Измените его условия в существующей локации."); return;
          }
          if (this.count - Number(group.wholeCountry) >= 5) {
            this.setStatus(view, "Можно выбрать не больше 5 мест поиска."); return;
          }
          group.wholeCountry = false;
          group.cities.push({country: city.country, geoname_id: city.geoname_id,
            city: city.ascii_name, city_display_name: city.name, country_name_en: city.country_name_en, region: city.region});
          group.draft = "";
          this.render(); this.onChange();
          this.views.get(group.id).input.focus();
        });
        choice.setAttribute("role", "option"); choice.setAttribute("aria-selected", "false");
        view.options.append(choice);
      }
      this.setStatus(view, cities.length ? "" : "Города не найдены. Уточните название.");
    } catch (error) {
      if (this.views.get(group.id) !== view || version !== view.version || error.name === "AbortError") return;
      this.setStatus(view, `Не удалось получить подсказки: ${error.message}`); view.retry.hidden = false;
    }
  }
}
