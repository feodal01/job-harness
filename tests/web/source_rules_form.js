const {ruleSources:r,sourceRules:s,rulesEmpty:e,rulesReachBeyondBuiltIns:x}=require(process.argv[2]);const a=require("assert");const C="company:x";
const A=(o)=>({mode:"auto",groups:null,include:[],exclude:[],...o});
// default
a.deepStrictEqual(s(["hh_ru","getmatch"],null),null);a.deepStrictEqual(s(["hh_ru"],null),{mode:"selected",include:["hh_ru"]});a.ok(e(s([],null)));
// auto + exclude company
const ex=A({exclude:[C]});a.deepStrictEqual(r(ex),["hh_ru","getmatch"]);a.deepStrictEqual(s(r(ex),ex),ex);a.deepStrictEqual(s(["hh_ru"],ex),A({exclude:[C,"getmatch"]}));
a.deepStrictEqual(s(["hh_ru","getmatch"],A({exclude:[C,"getmatch"]})),ex);
// review 1: groups [] include hh, uncheck hh -> empty, caught
const g0=A({groups:[],include:["hh_ru"]});a.deepStrictEqual(r(g0),["hh_ru"]);a.ok(!e(s(["hh_ru"],g0)));a.deepStrictEqual(s(["hh_ru"],g0),g0);a.ok(e(s([],g0)));
// review 2: groups company_site -> boxes unchecked; roundtrip unchanged; checking hh adds include
const gc=A({groups:["company_site"]});a.deepStrictEqual(r(gc),[]);a.deepStrictEqual(s([],gc),gc);a.deepStrictEqual(s(["hh_ru"],gc),A({groups:["company_site"],include:["hh_ru"]}));
const ga=A({groups:["aggregator"]});a.deepStrictEqual(r(ga),["hh_ru"]);a.deepStrictEqual(s(["hh_ru"],ga),ga);a.deepStrictEqual(s([],ga),A({groups:["aggregator"],exclude:["hh_ru"]}));
const gcomp=A({groups:[],include:[C]});a.deepStrictEqual(r(gcomp),[]);a.deepStrictEqual(s([],gcomp),gcomp);a.ok(!e(gcomp));
// selected
const sel={mode:"selected",groups:null,include:[C,"hh_ru"],exclude:["ats:lever:a"]};a.deepStrictEqual(r(sel),["hh_ru"]);a.deepStrictEqual(s([],sel),{...sel,include:[C]});a.ok(!e(s([],sel)));
// review 3: query requirement
a.ok(x(s(["hh_ru"],sel)));a.ok(!x(s(["hh_ru"],null)));a.ok(!x(s(["hh_ru"],ga)));a.ok(x(s(["hh_ru"],gc)));a.ok(x(null));
// roundtrip: every case, saving unchanged boxes keeps rules equivalent
for(const R of [ex,g0,gc,ga,gcomp,sel])a.deepStrictEqual(r(s(r(R),R)),r(R));
// explicitly included built-ins survive a save
const inc=A({include:["hh_ru",C]});a.deepStrictEqual(s(r(inc),inc),inc);a.deepStrictEqual(s(["getmatch"],inc),A({include:[C],exclude:["hh_ru"]}));
const gai=A({groups:["aggregator"],include:["hh_ru"]});a.deepStrictEqual(s(r(gai),gai),gai);
// auto rules whose chosen types end up empty
a.ok(e(s([],ga)));a.ok(e(s([],A({groups:["aggregator","it_board"]}))));a.ok(!e(s([],gc)));a.ok(!e(s([],ex)));a.ok(!e(null));
console.log("ok");
