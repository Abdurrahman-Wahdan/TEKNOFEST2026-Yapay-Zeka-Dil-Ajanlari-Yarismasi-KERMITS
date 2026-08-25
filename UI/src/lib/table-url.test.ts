import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { internalTableHref, tableSearch, TABLE_PARAM } from "./table-url.ts";

/**
 * These assertions are the contract with `dataprep/stamp_table_urls.py`, which
 * writes the same addresses into Qdrant from Python. Nothing is shared between
 * the two languages, so if a spelling here changes, that file has to change with
 * it — `tests/unit/test_stamp_table_urls.py` pins the other half.
 */
describe("TABLE_PARAM", () => {
  it("is the name dataprep/stamp_table_urls.py writes", () => {
    assert.equal(TABLE_PARAM, "tablo");
  });
});

describe("tableSearch", () => {
  it("percent-encodes a Turkish id the way the stamped url does", () => {
    assert.equal(
      tableSearch("", "araç-kiralama-indirim-kampanyası"),
      "tablo=ara%C3%A7-kiralama-indirim-kampanyas%C4%B1",
    );
  });

  it("is empty when nothing is open, so the url falls back to the bare path", () => {
    assert.equal(tableSearch("", null), "");
  });

  it("drops only its own parameter, and keeps anything else on the url", () => {
    assert.equal(tableSearch("tablo=kredi-kartı&ref=chat", null), "ref=chat");
  });

  it("replaces rather than appends when a table is already open", () => {
    assert.equal(tableSearch("tablo=kredi-kartı", "banka-kartı"), "tablo=banka-kart%C4%B1");
  });
});

describe("internalTableHref", () => {
  it("keeps a relative table link as it is", () => {
    assert.equal(
      internalTableHref("/tr/urunler?tablo=kredi-kart%C4%B1"),
      "/tr/urunler?tablo=kredi-kart%C4%B1",
    );
  });

  it("strips a host the assistant invented", () => {
    // Observed 2026-08-25: handed the relative address, the model wrote this.
    assert.equal(
      internalTableHref(
        "https://www.kermits.com.tr/tr/urunler?tablo=alt%C4%B1n-kat%C4%B1lma-hesab%C4%B1",
      ),
      "/tr/urunler?tablo=alt%C4%B1n-kat%C4%B1lma-hesab%C4%B1",
    );
  });

  it("leaves anything that is not one of our table pages external", () => {
    assert.equal(internalTableHref("https://www.kuveytturk.com.tr/kampanyalar"), null);
    assert.equal(internalTableHref("/tr/profile?tablo=x"), null);
    assert.equal(internalTableHref("/tr/kampanyalar"), null); // no id
    assert.equal(internalTableHref("/en/urunler?tablo=x"), null);
    assert.equal(internalTableHref("/tr/urunler/extra?tablo=x"), null);
    assert.equal(internalTableHref("javascript:alert(1)"), null);
    assert.equal(internalTableHref(undefined), null);
  });

  it("re-encodes the id rather than trusting how it arrived", () => {
    assert.equal(
      internalTableHref("/tr/kampanyalar?tablo=araç-kiralama"),
      "tablo=ara%C3%A7-kiralama".replace(/^/, "/tr/kampanyalar?"),
    );
  });
});
