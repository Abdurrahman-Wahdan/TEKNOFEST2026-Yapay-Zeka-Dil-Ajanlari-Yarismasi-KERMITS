/**
 * How many grid columns each component gets.
 *
 * The producer chooses *what* to show and in *what order*. It does not choose
 * width, because a model that can set spans can produce a page with a 1-column
 * table beside a 3-column empty card, and no amount of prompting reliably stops
 * that. Order is content — it is the producer's argument, and reordering would
 * destroy it — so this preserves order absolutely and computes only width.
 *
 * The grid is `CardGrid`: 1 column below `md`, 2 at `md`, 4 at `xl`. `Card`
 * already clamps `data-span` 3 and 4 down to 2 at `md`, so this only ever has
 * to reason about the 4-column case; the responsive collapse is free.
 *
 * Pure by construction: no React, no viewport read, no clock, no randomness.
 * Same sequence of types in, same spans out, always.
 */

export type Span = 1 | 2 | 3 | 4;

export interface SpanRule {
  /** The width this component looks best at. */
  preferred: Span;
  /** The narrowest width at which it is still readable. */
  min: Span;
}

export interface LayoutItem {
  index: number;
  span: Span;
}

const COLUMNS = 4;

/** What an unknown or unreadable component gets: half width, and honest. */
export const FALLBACK_RULE: SpanRule = { preferred: 2, min: 2 };

function clamp(value: number): Span {
  return Math.min(COLUMNS, Math.max(1, Math.round(value))) as Span;
}

/**
 * Hand a closed row its leftover columns.
 *
 * Without this, three stat tiles render as `[1,1,1]` and leave a ragged quarter
 * of empty grid — which reads as a missing tile rather than a design. Widest-
 * preference first, then round-robin, so the component that wanted the most
 * space gets the first extra column and no single item hoovers up the rest.
 */
function justify(row: { index: number; span: number; rule: SpanRule }[], leftover: number) {
  if (leftover <= 0 || row.length === 0) return;

  // Stable: equal preferences keep their original order, so the leftmost of a
  // row of identical tiles is the one that widens. Deterministic either way,
  // but this is the one that looks deliberate.
  const order = [...row].sort((a, b) => b.rule.preferred - a.rule.preferred);

  let remaining = leftover;
  let i = 0;
  while (remaining > 0) {
    const target = order[i % order.length];
    if (target.span < COLUMNS) {
      target.span += 1;
      remaining -= 1;
    } else if (order.every((item) => item.span >= COLUMNS)) {
      // Everything in the row is already full width; nothing left to give.
      break;
    }
    i += 1;
  }
}

/**
 * Assign a span to every component, in order.
 *
 * Two rules beyond plain greedy packing, both there because the naive version
 * produces layouts that look broken:
 *
 *  - **A full-width component never shares a row.** Something that asked for
 *    all four columns squeezed into the two left over beside a pair of stat
 *    tiles is worse than a new row, every time.
 *  - **Every row is justified to exactly four columns.** A row that sums to
 *    three has a hole in it, and a hole looks like a bug.
 *
 * The result is that every returned row sums to exactly `COLUMNS` — the
 * property worth pinning in a test, because it is the one a future refactor
 * will quietly break.
 */
export function layout(rules: readonly SpanRule[]): LayoutItem[] {
  const out: LayoutItem[] = [];
  let row: { index: number; span: number; rule: SpanRule }[] = [];
  let remaining = COLUMNS;

  const close = () => {
    if (row.length === 0) return;
    justify(row, remaining);
    for (const item of row) out.push({ index: item.index, span: clamp(item.span) });
    row = [];
    remaining = COLUMNS;
  };

  rules.forEach((raw, index) => {
    const rule: SpanRule = {
      preferred: clamp(raw.preferred),
      min: clamp(Math.min(raw.min, raw.preferred)),
    };

    const wantsWholeRow = rule.preferred === COLUMNS && remaining < COLUMNS;
    const cannotFit = remaining < rule.min;
    if (row.length > 0 && (wantsWholeRow || cannotFit)) close();

    const span = Math.min(rule.preferred, remaining);
    row.push({ index, span, rule });
    remaining -= span;
  });

  close();
  return out;
}
