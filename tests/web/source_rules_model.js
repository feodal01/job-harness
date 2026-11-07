// The rules model of the sources dialog (src/web/static/source-rules.js), run by tests/test_web_source_rules.py.
const assert = require("assert");
const {SourceRulesModel: M, MAX_RULE_ITEMS} = require(process.argv[2]);
const C = "company:00000000-0000-4000-8000-000000000001", D = "company:00000000-0000-4000-8000-000000000002";
const A = rules => ({mode: "auto", groups: null, include: [], exclude: [], ...rules});
const ALL = ["aggregator", "it_board", "company_site"];
const round = rules => M.toRules(M.fromRules(rules));

// The default stays the default; opening it as custom shows every type and all company sites.
assert.strictEqual(M.toRules(M.fromRules(null)), null);
assert.deepStrictEqual(M.fromRules(null), {custom: false, types: ["aggregator", "it_board"], companies: "all", include: [], exclude: []});
assert.deepStrictEqual(M.toRules(M.custom(M.fromRules(null))), A({groups: ALL}));
assert.strictEqual(M.fromRules(A()).custom, false);

// Rules made elsewhere (MCP, API) open and apply unchanged in meaning.
for (const rules of [
  A({exclude: [C, "getmatch"]}),
  A({groups: ["aggregator", "company_site"], exclude: ["hh_ru"]}),
  A({groups: ["it_board"], include: [C, D, "ats:lever:acme"]}),
  A({groups: [], include: [C]}),
]) assert.deepStrictEqual(round(rules), {...rules, groups: rules.groups ?? ALL});
// A selection is the same search as auto rules with no types and the same items.
assert.deepStrictEqual(round({mode: "selected", groups: null, include: ["hh_ru", C], exclude: []}),
  A({groups: [], include: ["hh_ru", C]}));
// A company chosen while every company site is searched adds nothing and is left out.
assert.deepStrictEqual(round(A({include: [C, "hh_ru"]})), A({groups: ALL, include: ["hh_ru"]}));

// Problems are the rules the service refuses.
const none = {custom: true, types: [], companies: "selected", include: [], exclude: []};
assert.ok(M.problem(none));
assert.strictEqual(M.problem({...none, include: [C]}), null);
assert.strictEqual(M.problem({...none, types: ["aggregator"]}), null);
assert.strictEqual(M.problem({...none, companies: "all"}), null);
assert.strictEqual(M.problem(M.fromRules(null)), null);
assert.ok(M.problem({...none, include: Array.from({length: MAX_RULE_ITEMS + 1}, (_, i) => `ats:lever:b${i}`)}));

// Actions from search results keep include and exclude apart and make the rules custom.
const auto = M.fromRules(null);
const match = (participation, rule_item, id = rule_item) => ({participation, rule_item, id});
let s = M.action(auto, match("included", "hh_ru")).apply(auto);
assert.deepStrictEqual(M.toRules(s), A({groups: ALL, exclude: ["hh_ru"]}));
assert.strictEqual(M.action(s, match("excluded", "hh_ru")).label, "Вернуть");
s = M.action(s, match("excluded", "hh_ru")).apply(s);
assert.deepStrictEqual(s.exclude, []);
// A board excluded by its id comes back through its company.
s = M.restore({...auto, custom: true, exclude: ["ats:lever:acme", D]}, match("excluded", C, "ats:lever:acme"));
assert.deepStrictEqual(s.exclude, [D]);
// Adding a company in «only selected» mode, then removing it.
const sel = {...none, types: ["aggregator"]};
s = M.action(sel, match("not_selected", C)).apply(sel);
assert.deepStrictEqual(M.toRules(s), A({groups: ["aggregator"], include: [C]}));
assert.strictEqual(M.action(s, match("included", C)).label, "Убрать");
s = M.action(s, match("included", C)).apply(s);
assert.deepStrictEqual(s.include, []);
// With every company site chosen, a company kept from «only selected» is excluded, not removed.
const kept = {...sel, companies: "all", include: [C]};
assert.strictEqual(M.action(kept, match("included", C)).label, "Исключить");
assert.deepStrictEqual(M.toRules(M.action(kept, match("included", C)).apply(kept)), A({groups: ["aggregator", "company_site"], exclude: [C]}));
// Adding what was excluded moves it, never leaves it in both lists.
s = M.add({...sel, exclude: [C]}, C);
assert.deepStrictEqual([s.include, s.exclude], [[C], []]);
s = M.exclude({...sel, include: [C]}, C);
assert.deepStrictEqual([s.include, s.exclude], [[], [C]]);
// Not covered sources can be added explicitly; unsupported ones get no button.
assert.strictEqual(M.action(auto, match("not_covered", "hh_ru")).label, "Добавить");
assert.strictEqual(M.action(auto, match("unsupported", null, "linkedin")), null);
// Actions never change the state they were given.
const frozen = JSON.stringify(sel); M.add(sel, C); M.exclude(sel, "hh_ru"); assert.strictEqual(JSON.stringify(sel), frozen);

// Short descriptions without a preview.
assert.strictEqual(M.describe(null), "все по местам брифа");
assert.strictEqual(M.describe(A({groups: ["aggregator"], include: [C, D], exclude: ["getmatch"]})), "агрегаторы · 2 компании · исключено: 1");
assert.strictEqual(M.describe(A({exclude: [C]})), "агрегаторы, it-площадки · сайты компаний · исключено: 1");
assert.strictEqual(M.describe(A({groups: [], include: ["hh_ru"]})), "добавлено: 1");
console.log("ok");
