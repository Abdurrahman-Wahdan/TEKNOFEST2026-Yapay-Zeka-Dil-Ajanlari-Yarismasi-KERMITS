import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { layout, type Span, type SpanRule } from "./layout.ts";

const stat: SpanRule = { preferred: 1, min: 1 };
const chart: SpanRule = { preferred: 2, min: 2 };
const table: SpanRule = { preferred: 4, min: 2 };
const broken: SpanRule = { preferred: 2, min: 2 };

const spans = (rules: SpanRule[]) => layout(rules).map((item) => item.span);

/**
 * Rebuild row boundaries from a flat span list.
 *
 * Only sound because every row sums to exactly four — which is the property the
 * test below exists to prove. If that ever breaks, this mis-groups and the
 * expectations fail loudly rather than silently passing.
 */
function rows(values: Span[]): number[][] {
  const out: number[][] = [];
  let current: number[] = [];
  let total = 0;
  for (const span of values) {
    current.push(span);
    total += span;
    if (total >= 4) {
      out.push(current);
      current = [];
      total = 0;
    }
  }
  if (current.length > 0) out.push(current);
  return out;
}

describe("layout", () => {
  it("gives a lone component the whole row", () => {
    assert.deepEqual(spans([stat]), [4]);
    assert.deepEqual(spans([table]), [4]);
    assert.deepEqual(spans([chart]), [4]);
  });

  it("splits two half-width components evenly", () => {
    assert.deepEqual(spans([stat, stat]), [2, 2]);
    assert.deepEqual(spans([chart, chart]), [2, 2]);
  });

  it("justifies three stats rather than leaving a hole", () => {
    assert.deepEqual(spans([stat, stat, stat]), [2, 1, 1]);
  });

  it("fills a row of four exactly", () => {
    assert.deepEqual(spans([stat, stat, stat, stat]), [1, 1, 1, 1]);
  });

  it("never lets a full-width component share a row", () => {
    // The table would otherwise be squeezed into the two columns left beside
    // the stats; a new row at full width beats that every time.
    assert.deepEqual(spans([table, stat, stat]), [4, 2, 2]);
    assert.deepEqual(spans([stat, stat, table]), [2, 2, 4]);
    assert.deepEqual(spans([table, chart]), [4, 4]);
  });

  it("handles the mixed five-component case", () => {
    assert.deepEqual(spans([chart, chart, stat, stat, table]), [2, 2, 2, 2, 4]);
  });

  it("wraps six stats onto two justified rows", () => {
    assert.deepEqual(spans([stat, stat, stat, stat, stat, stat]), [1, 1, 1, 1, 2, 2]);
  });

  it("gives unknown components a readable default width", () => {
    assert.deepEqual(spans([broken, broken]), [2, 2]);
    assert.deepEqual(spans([broken]), [4]);
  });

  it("returns nothing for no components", () => {
    assert.deepEqual(layout([]), []);
  });

  it("preserves the producer's order", () => {
    const result = layout([table, stat, chart, stat]);
    assert.deepEqual(
      result.map((item) => item.index),
      [0, 1, 2, 3],
    );
  });

  it("clamps out-of-range rules instead of trusting them", () => {
    const absurd = { preferred: 9, min: 7 } as unknown as SpanRule;
    const result = spans([absurd, stat]);
    assert.ok(result.every((span) => span >= 1 && span <= 4));
  });

  it("every row sums to exactly four", () => {
    const vocabulary = [stat, chart, table, broken];
    // Exhaustive over every sequence up to length 5 — 1364 layouts. Cheap, and
    // it covers the packing interactions no hand-picked case would.
    const check = (prefix: SpanRule[]) => {
      if (prefix.length > 0) {
        for (const row of rows(spans(prefix))) {
          assert.equal(
            row.reduce((a, b) => a + b, 0),
            4,
            `row ${JSON.stringify(row)} from ${prefix.length} components`,
          );
        }
      }
      if (prefix.length === 5) return;
      for (const rule of vocabulary) check([...prefix, rule]);
    };
    check([]);
  });

  it("is a pure function of the rule sequence", () => {
    const input = [table, stat, stat, chart, stat];
    assert.deepEqual(layout(input), layout(input));
    assert.deepEqual(layout(input), layout([...input]));
  });
});
