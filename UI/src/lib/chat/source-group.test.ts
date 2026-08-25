import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { safeWebSource, siteSection, sourceGroup, SITE_PAGE } from "./source-group.ts";

describe("sourceGroup", () => {
  it("files an indexed document under the knowledge base", () => {
    assert.equal(sourceGroup("indexed_document", "https://www.albaraka.com.tr/x"), "knowledge-base");
  });

  it("files anything else openable as an online source", () => {
    assert.equal(sourceGroup("live_web_source", "https://www.kuveytturk.com.tr/x"), "online");
    assert.equal(sourceGroup(undefined, "http://example.com"), "online");
  });

  it("files one of our own pages under its own heading", () => {
    assert.equal(sourceGroup(SITE_PAGE, "/tr/kampanyalar?tablo=x"), "site");
  });

  it("drops a source it cannot turn into an openable link", () => {
    assert.equal(sourceGroup("live_web_source", "javascript:alert(1)"), null);
    assert.equal(sourceGroup("indexed_document", "not a url"), null);
    assert.equal(sourceGroup(undefined, "/tr/kampanyalar?tablo=x"), null);
  });

  it("will not accept an absolute url as one of our pages", () => {
    // The backend only ever emits site-relative addresses for these. An absolute
    // one carrying the marker would be something else wearing the label.
    assert.equal(sourceGroup(SITE_PAGE, "https://evil.example.com/tr/urunler?tablo=x"), null);
  });
});

describe("siteSection", () => {
  it("names the section a table page lives in, as a nav key", () => {
    assert.equal(siteSection("/tr/kampanyalar?tablo=ara%C3%A7"), "kampanyalar");
    assert.equal(siteSection("/tr/urunler?tablo=kredi-kart%C4%B1"), "urunler");
  });

  it("is empty rather than wrong when there is no section", () => {
    assert.equal(siteSection("/tr"), "");
    assert.equal(siteSection("/"), "");
  });
});

describe("safeWebSource", () => {
  it("accepts only http and https", () => {
    assert.equal(safeWebSource("https://a.com")?.hostname, "a.com");
    assert.equal(safeWebSource("ftp://a.com"), null);
    assert.equal(safeWebSource("/tr/urunler"), null);
  });
});
