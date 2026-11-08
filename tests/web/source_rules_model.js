// The choice model of the «Где искать вакансии» dialog (src/web/static/source-rules.js),
// run by tests/test_web_source_rules.py.
const assert = require("assert");
const {SourceRulesModel: M, MAX_RULE_ITEMS} = require(process.argv[2]);
const LIST = [{id: "hh_ru", group: "aggregator", name: "HH"}, {id: "getmatch", group: "it_board", name: "Getmatch"}];
const C = "company:00000000-0000-4000-8000-000000000001", D = "company:00000000-0000-4000-8000-000000000002";
const A = rules => ({mode: "auto", groups: null, include: [], exclude: [], ...rules});
const from = rules => M.fromRules(rules, LIST), to = state => M.toRules(state, LIST);
const custom = over => ({custom: true, builtins: ["hh_ru", "getmatch"], sites: true, companies: "all", picked: [], skipped: [], ...over});

// «Везде» stays «везде»; choosing yourself starts from everything checked.
assert.strictEqual(to(from(null)), null);
assert.deepStrictEqual(from(null), {custom: false, builtins: ["hh_ru", "getmatch"], sites: true, companies: "all", picked: [], skipped: []});
assert.deepStrictEqual(to({...from(null), custom: true}), A({groups: ["aggregator", "it_board", "company_site"]}));
assert.strictEqual(from(A()).custom, false);

// An unchecked built-in is just unchecked: its type leaves the rules, nothing lands in a list.
assert.deepStrictEqual(to(custom({builtins: ["getmatch"]})), A({groups: ["it_board", "company_site"]}));
// Rules from MCP or the old dialog show the built-in as it is searched.
assert.deepStrictEqual(from(A({exclude: ["hh_ru"]})).builtins, ["getmatch"]);
assert.deepStrictEqual(from(A({groups: ["it_board"], include: ["hh_ru"]})).builtins, ["hh_ru", "getmatch"]);
assert.deepStrictEqual(from(A({groups: ["company_site"]})).builtins, []);
assert.deepStrictEqual(from({mode: "selected", groups: null, include: ["getmatch", C], exclude: []}),
  {custom: true, builtins: ["getmatch"], sites: true, companies: "selected", picked: [C], skipped: []});

// Every kind of rules opens and applies to the same search.
for (const [rules, expected] of [
  [A({exclude: [C, "getmatch"]}), A({groups: ["aggregator", "company_site"], exclude: [C]})],
  [A({groups: ["aggregator", "company_site"], exclude: ["hh_ru", D]}), A({groups: ["company_site"], exclude: [D]})],
  [A({groups: ["it_board"], include: [C, "ats:lever:acme"]}), A({groups: ["it_board"], include: [C, "ats:lever:acme"]})],
  [A({groups: [], include: [C]}), A({groups: [], include: [C]})],
  [{mode: "selected", groups: null, include: ["hh_ru", C], exclude: []}, A({groups: ["aggregator"], include: [C]})],
]) assert.deepStrictEqual(to(from(rules)), expected, JSON.stringify(rules));

// Company sites: all, only chosen, or none; chosen companies are kept only in «only chosen».
const picked = custom({companies: "selected", picked: [C], skipped: [D]});
assert.deepStrictEqual(to(picked), A({groups: ["aggregator", "it_board"], include: [C]}));
assert.deepStrictEqual(to({...picked, companies: "all"}), A({groups: ["aggregator", "it_board", "company_site"], exclude: [D]}));
assert.deepStrictEqual(to({...picked, sites: false}), A({groups: ["aggregator", "it_board"]}));

// Nothing to search is a problem the dialog names; an empty company choice says so.
assert.strictEqual(M.problem(custom({builtins: [], sites: false}), LIST), "Отметьте, где искать.");
assert.strictEqual(M.problem(custom({builtins: [], companies: "selected"}), LIST), "Выберите компанию или снимите «Сайты компаний».");
// Company sites ticked with no company chosen is not silently dropped.
assert.ok(M.problem(custom({builtins: ["hh_ru"], companies: "selected"}), LIST));
assert.strictEqual(M.problem(custom({builtins: [], companies: "selected", picked: [C]}), LIST), null);
assert.strictEqual(M.problem(custom({builtins: ["hh_ru"], sites: false}), LIST), null);
assert.strictEqual(M.problem(from(null), LIST), null);
const many = Array.from({length: MAX_RULE_ITEMS + 1}, (_, i) => `ats:lever:b${i}`);
assert.ok(M.problem(custom({companies: "selected", picked: many}), LIST));

// Picking and skipping company sites; a board shared by two companies counts as one site.
const abbyy = {rule_item: C, id: "ats:greenhouse:abbyy", company_ids: [C.slice(8), D.slice(8)]};
let s = M.pick(custom({companies: "selected"}), abbyy);
assert.deepStrictEqual(s.picked, [C]); assert.ok(M.isPicked(s, abbyy));
assert.deepStrictEqual(M.pick({...s}, abbyy).picked, [C]);
assert.deepStrictEqual(M.unpick({...s, picked: [C, D, "ats:greenhouse:abbyy"]}, abbyy).picked, []);
s = M.skip(custom(), abbyy);
assert.deepStrictEqual([s.skipped, s.picked], [[C], []]); assert.ok(M.isSkipped(s, abbyy));
assert.deepStrictEqual(M.unskip({...s, skipped: [D, "hh_ru"]}, abbyy).skipped, ["hh_ru"]);
// A site is never both chosen and skipped.
s = M.pick(custom({skipped: [D]}), abbyy);
assert.deepStrictEqual([s.picked, s.skipped], [[C], []]);
s = M.skip(custom({picked: [D]}), abbyy);
assert.deepStrictEqual([s.picked, s.skipped], [[], [C]]);
// Unchecking and checking a site again restores the item the rules had and is no change.
const board = custom({companies: "selected", picked: ["ats:greenhouse:abbyy"]});
const again = M.pick(M.unpick(board, abbyy), abbyy, board.picked);
assert.deepStrictEqual(again.picked, ["ats:greenhouse:abbyy"]);
assert.strictEqual(M.key(again), M.key(board));
assert.strictEqual(M.key(custom({picked: [C, D]})), M.key(custom({picked: [D, C]})));
// Changes never touch the state they were given.
const frozen = custom({picked: [C]}), before = JSON.stringify(frozen);
M.pick(frozen, abbyy); M.skip(frozen, abbyy); M.without(frozen, "picked", C);
assert.strictEqual(JSON.stringify(frozen), before);

// Short descriptions for the brief card.
assert.strictEqual(M.describe(null, LIST), "везде");
assert.strictEqual(M.describe(A({groups: ["aggregator"], include: [C, D]}), LIST), "HH, 2 компании");
assert.strictEqual(M.describe(A({exclude: [C, "getmatch"]}), LIST), "HH, сайты компаний; не искать: 1 сайт");
assert.strictEqual(M.describe(A({groups: ["company_site"]}), LIST), "сайты компаний");
console.log("ok");
