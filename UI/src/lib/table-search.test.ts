import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { foldSearch, scoreTable, searchTables, withinDistance } from "./table-search.ts";

const konut = {
  topic: "Konut Finansmanı",
  docstring: "Yeni ve ikinci el konut alımı için sunulan kâr oranları.",
  subcategory: "Murabaha finansman",
};
const isyeri = {
  topic: "İşyeri Yangın Sigortası",
  docstring: "İşyeri yangın ve paket sigortalarının teminat içerikleri.",
  subcategory: "Sigorta",
};
const kampanya = {
  topic: "Konut Finansmanı Faiz İndirimi Kampanyası",
  docstring: "Konut finansmanında geçerli kampanya oranları.",
  subcategory: "Kampanya",
};

const matches = (table: typeof konut, query: string) => scoreTable(table, query) > 0;

describe("foldSearch", () => {
  it("folds the dotted and dotless I the Turkish way", () => {
    // "I".toLowerCase() is "i" in every locale by default, and "İ" keeps a
    // combining dot unless the locale is passed.
    assert.equal(foldSearch("İŞYERİ"), "isyeri");
    assert.equal(foldSearch("ISITMA"), "isitma");
  });

  it("strips the diacritics an ASCII keyboard cannot type", () => {
    assert.equal(foldSearch("kâr"), "kar");
    assert.equal(foldSearch("Yeşil Finansmanı"), "yesil finansmani");
    assert.equal(foldSearch("Öğrenci Çeki"), "ogrenci ceki");
  });

  it("gives the same result whichever locale the interface is in", () => {
    // The pool is Turkish whatever language the UI is showing, so an English
    // locale must not change what matches.
    assert.equal(foldSearch("İşyeri", "en"), foldSearch("İşyeri", "tr"));
  });
});

describe("withinDistance", () => {
  it("accepts a substitution, an insertion and a transposition", () => {
    assert.equal(withinDistance("finansman", "finansmen", 1), true);
    assert.equal(withinDistance("finansman", "finansmann", 1), true);
    assert.equal(withinDistance("konut", "kount", 2), true);
  });

  it("rejects anything further away than asked", () => {
    assert.equal(withinDistance("konut", "sigorta", 2), false);
    assert.equal(withinDistance("finansman", "finansmenn", 1), false);
  });

  it("decides on length alone when it can", () => {
    assert.equal(withinDistance("a", "abcdef", 2), false);
  });
});

describe("scoreTable", () => {
  it("matches a word in any of the three fields", () => {
    assert.equal(matches(konut, "konut"), true);
    assert.equal(matches(konut, "ikinci el"), true);
    assert.equal(matches(konut, "murabaha"), true);
    assert.equal(matches(konut, "sigorta"), false);
  });

  it("matches what an ASCII keyboard types", () => {
    // The reason this exists: 70% of ASCII-typed queries missed before.
    assert.equal(matches(isyeri, "isyeri"), true);
    assert.equal(matches(isyeri, "yangin sigortasi"), true);
    assert.equal(matches(konut, "kar orani"), true);
  });

  it("matches across a Turkish suffix, in both directions", () => {
    assert.equal(matches(konut, "finansman"), true);   // user drops the suffix
    assert.equal(matches(isyeri, "sigortasi"), true);  // user adds one
  });

  it("matches another inflection of the same word", () => {
    // "oranları" in the text, "oranı" typed: they agree on the stem and then
    // disagree, so neither is a prefix or a substring of the other.
    assert.equal(matches(konut, "kar oranı"), true);
    assert.equal(matches(isyeri, "teminatı"), true);
    assert.equal(matches(konut, "finansmanında"), true);
    // Agreement has to reach the stem: "kongre" shares three letters with
    // "konut" and is a different word.
    assert.equal(matches(konut, "kongre"), false);
  });

  it("forgives a typo in a long enough word", () => {
    assert.equal(matches(konut, "finansmn"), true);
    assert.equal(matches(konut, "kounut"), true);
    // Too short to tell a typo from a different word.
    assert.equal(matches(konut, "kot"), false);
  });

  it("requires every word, in any order and any field", () => {
    assert.equal(matches(konut, "konut murabaha"), true);
    assert.equal(matches(konut, "murabaha konut"), true);
    assert.equal(matches(konut, "konut sigorta"), false);
  });

  it("scores a title match above a description match", () => {
    assert.ok(scoreTable(konut, "konut") > scoreTable(konut, "alımı"));
  });

  it("treats a blank query as no filter at all", () => {
    assert.ok(scoreTable(konut, "") > 0);
    assert.ok(scoreTable(konut, "   ") > 0);
  });
});

describe("searchTables", () => {
  const pool = [kampanya, isyeri, konut];

  it("keeps only the matches", () => {
    assert.deepEqual(searchTables(pool, "sigorta"), [isyeri]);
  });

  it("puts the table the user most likely meant first", () => {
    // Both mention konut finansmanı; the shorter, exactly-titled one wins.
    assert.equal(searchTables(pool, "konut finansmanı")[0], konut);
    assert.equal(searchTables(pool, "kampanya")[0], kampanya);
  });

  it("ranks a title hit above a description-only hit", () => {
    const ranked = searchTables(pool, "konut");
    assert.equal(ranked[0], konut);
    assert.equal(ranked.length, 2);
  });

  it("returns every table for a blank query, as a copy in its original order", () => {
    const result = searchTables(pool, "  ");
    assert.deepEqual(result, pool);
    assert.notEqual(result, pool);
  });

  it("reuses its index without going stale when the list changes", () => {
    // The index is cached on the array reference; a new array must not read
    // the old one's entries.
    assert.deepEqual(searchTables([konut], "konut"), [konut]);
    assert.deepEqual(searchTables([isyeri], "konut"), []);
    assert.deepEqual(searchTables([konut], "konut"), [konut]);
  });
});
