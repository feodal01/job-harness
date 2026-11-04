/* A list of phrases, kept as the same string arrays used by the service. */
class TokenEditor {
  constructor(root, input, {label, maxItems, maxLength, onChange}) {
    this.root = root; this.input = input; this.label = label;
    this.maxItems = maxItems; this.maxLength = maxLength; this.onChange = onChange;
    this.items = []; this.required = false;
    input.maxLength = maxLength; input.autocomplete = "off"; input.required = false;
    this.list = document.createElement("div"); this.list.className = "token-list";
    this.list.setAttribute("role", "list"); this.list.setAttribute("aria-label", label);
    const compose = document.createElement("div"); compose.className = "token-compose";
    const add = document.createElement("button"); add.type = "button"; add.className = "secondary";
    add.textContent = "Добавить"; add.setAttribute("aria-label", `Добавить: ${label}`);
    add.onclick = () => {if (this.commit()) input.focus();};
    this.status = document.createElement("small"); this.status.className = "token-error";
    this.status.id = `${input.id}-status`; this.status.setAttribute("role", "status"); this.status.hidden = true;
    input.setAttribute("aria-describedby", [input.getAttribute("aria-describedby"), this.status.id].filter(Boolean).join(" "));
    compose.append(input, add); root.append(this.list, compose, this.status);
    input.oninput = () => {this.showError(""); this.onChange();};
    input.onkeydown = event => {
      if (event.isComposing) return;
      if (event.key === "Enter") {event.preventDefault(); this.commit();}
      if (event.key === "Backspace" && !input.value && this.items.length) {
        event.preventDefault(); this.items.pop(); this.render(); this.onChange();
      }
    };
    input.onpaste = event => {
      const text = event.clipboardData.getData("text");
      if (!/[\r\n]/.test(text)) return;
      event.preventDefault();
      const values = [...new Set([...this.values, ...text.split(/\r?\n/).map(v => v.trim()).filter(Boolean)])];
      const error = this.errorFor(values);
      if (error) {this.showError(error); return;}
      this.items = values; input.value = ""; this.render(); this.showError(""); this.onChange();
    };
  }
  get values() {
    return [...new Set([...this.items, this.input.value.trim()].filter(Boolean))];
  }
  errorFor(values) {
    if (values.length > this.maxItems) return `Можно добавить до ${this.maxItems} значений.`;
    if (values.some(value => value.length > this.maxLength))
      return `В одном значении должно быть не больше ${this.maxLength} символов.`;
    return "";
  }
  get error() {return this.errorFor(this.values);}
  setRequired(required) {
    this.required = required; this.input.setAttribute("aria-required", String(required));
  }
  showError(message) {this.status.textContent = message; this.status.hidden = !message;}
  commit() {
    const error = this.error;
    if (error) {this.showError(error); this.onChange(); return false;}
    this.items = this.values; this.input.value = "";
    this.render(); this.showError(""); this.onChange(); return true;
  }
  setValues(values) {
    this.items = [...new Set(values)]; this.input.value = ""; this.showError(""); this.render();
  }
  render() {
    this.list.replaceChildren(); this.list.hidden = !this.items.length;
    for (const value of this.items) {
      const item = document.createElement("span"); item.className = "token"; item.setAttribute("role", "listitem");
      const text = document.createElement("span"); text.textContent = value;
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×";
      remove.setAttribute("aria-label", `Убрать: ${value}`);
      remove.onclick = () => {
        this.items = this.items.filter(item => item !== value); this.render(); this.showError("");
        this.input.focus(); this.onChange();
      };
      item.append(text, remove); this.list.append(item);
    }
  }
}
