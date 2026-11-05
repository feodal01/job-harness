// One vocabulary for every screen (mockup set 8): funnel stages, brief match, formats.
// Order follows the funnel; «Новая» is a label for status `new`, not a stage.

const userStatusNames = {
  new:"Неразобранная", applied:"Отклик", interview:"Собеседование", offer:"Оффер",
  rejected:"Отказ", dismissed:"Неинтересна", viewed:"Неразобранная",
};
const folderNames = {
  open:"Неразобранные", applied:"Отклик", interview:"Собеседование", offer:"Оффер",
  rejected:"Отказ", dismissed:"Неинтересные", all:"Все",
};
const selectionNames = {accepted:"Подходит", maybe:"Может быть подходит", rejected:"Скрыта", unknown:"Не проверено"};
const workFormatNames = {
  office:"Офис", hybrid:"Гибрид", remote_country:"Удалённо по стране", remote_worldwide:"Удалённо по миру", remote:"Удалённо",
};
const currencySigns = {RUB:"₽", RUR:"₽", USD:"$", EUR:"€", KZT:"₸"};

/** Text and pill class of the brief match; «not everything checked» when an accepted vacancy has unknown checks. */
function selectionLabel(selection) {
  const status = selection?.status;
  if (!status) return null;
  if (status === "accepted" && selection.checks?.some(check => check.status === "unknown"))
    return {text:"Подходит, не всё проверено", cls:"ok"};
  return {text:selectionNames[status] || status, cls:{accepted:"ok", maybe:"maybe", rejected:"hidden"}[status] || "unknown"};
}

function money(value) {
  return Math.round(Number(value)).toLocaleString("ru").replace(/ /g, " ");
}

/** «180 000 – 240 000 ₽», «от 200 000 ₽», «до 150 000 $»; empty string when the salary is not given. */
function salaryLine(salary = {}) {
  const from = salary.from, to = salary.to;
  if (!from && !to) return "";
  const code = salary.currencyCode || salary.currency || "";
  const sign = currencySigns[code] || code;
  const amount = from && to ? (from === to ? money(from) : `${money(from)} – ${money(to)}`) : from ? `от ${money(from)}` : `до ${money(to)}`;
  return [amount, sign].filter(Boolean).join(" ");
}

/** «28 окт» for tables. */
function shortDate(seconds) {
  if (!seconds) return "";
  const date = new Date(seconds * 1000), now = new Date();
  const options = {day:"numeric", month:"short"};
  if (date.getFullYear() !== now.getFullYear()) options.year = "numeric";
  return date.toLocaleDateString("ru", options).replace(/\./g, "").replace(/\s*г$/, "");
}

/** «3 ноября 2026, 14:42». */
function fullDate(seconds) {
  return new Date(seconds * 1000).toLocaleString("ru", {day:"numeric", month:"long", year:"numeric", hour:"2-digit", minute:"2-digit"})
    .replace(" г.", "").replace(" в ", ", ");
}

/** Russian plural: plural(2, "источник", "источника", "источников"). */
function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
