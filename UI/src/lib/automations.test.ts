import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  REPORT_PARAM,
  WEEKDAYS_ONLY,
  WEEKEND,
  describeSchedule,
  formatTime,
  fromJsDay,
  reportHref,
  reportSearch,
  sameDays,
  type ScheduleLabels,
} from "./automations.ts";

/** Stands in for the `automations` message namespace. */
const labels: ScheduleLabels = {
  daily: (time) => `Her gün ${time}`,
  weekdays: (time) => `Hafta içi ${time}`,
  weekend: (time) => `Hafta sonu ${time}`,
  someDays: (days, time) => `${days} ${time}`,
  dayNames: ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"],
};

describe("reportSearch", () => {
  it("sets the parameter", () => {
    assert.equal(reportSearch("", "abc"), `${REPORT_PARAM}=abc`);
  });

  it("removes it when nothing is open", () => {
    assert.equal(reportSearch("rapor=abc", null), "");
  });

  it("keeps other parameters", () => {
    const search = reportSearch("tab=2", "abc");
    assert.ok(search.includes("tab=2"));
    assert.ok(search.includes("rapor=abc"));
  });

  it("percent-encodes the id", () => {
    // Report ids are uuids today, but the encoder is what makes that an
    // implementation detail rather than a constraint.
    assert.equal(reportSearch("", "a b"), "rapor=a+b");
  });
});

describe("reportHref", () => {
  it("is locale-prefixed and in-app", () => {
    assert.equal(reportHref("tr", "abc"), "/tr/profile/reports?rapor=abc");
  });

  it("drops the question mark when nothing is selected", () => {
    assert.equal(reportHref("tr", null), "/tr/profile/reports");
  });
});

describe("fromJsDay", () => {
  it("maps Sunday-first to Monday-first", () => {
    // Date.getDay(): 0 = Sunday. Python weekday(): 6 = Sunday.
    assert.equal(fromJsDay(0), 6);
    assert.equal(fromJsDay(1), 0);
    assert.equal(fromJsDay(6), 5);
  });

  it("round-trips every day exactly once", () => {
    const mapped = [0, 1, 2, 3, 4, 5, 6].map(fromJsDay).sort();
    assert.deepEqual(mapped, [0, 1, 2, 3, 4, 5, 6]);
  });
});

describe("formatTime", () => {
  it("pads both halves", () => {
    assert.equal(formatTime(9, 0), "09:00");
    assert.equal(formatTime(7, 5), "07:05");
    assert.equal(formatTime(21, 30), "21:30");
    assert.equal(formatTime(0, 0), "00:00");
  });
});

describe("sameDays", () => {
  it("compares in order, which both sides guarantee", () => {
    assert.equal(sameDays([0, 4], [0, 4]), true);
    assert.equal(sameDays([0, 4], [0, 5]), false);
    assert.equal(sameDays([0], [0, 4]), false);
    assert.equal(sameDays([], []), true);
  });
});

describe("describeSchedule", () => {
  it("reads an empty set as every day", () => {
    assert.equal(describeSchedule(9, 0, [], labels), "Her gün 09:00");
  });

  it("names the weekday set rather than listing it", () => {
    // Someone who picked Monday-Friday reads "Hafta içi" as their own words.
    assert.equal(
      describeSchedule(7, 30, WEEKDAYS_ONLY, labels),
      "Hafta içi 07:30",
    );
  });

  it("names the weekend set", () => {
    assert.equal(describeSchedule(10, 0, WEEKEND, labels), "Hafta sonu 10:00");
  });

  it("lists anything else", () => {
    assert.equal(describeSchedule(21, 30, [0, 4], labels), "Pzt, Cum 21:30");
  });

  it("uses Monday-first day names", () => {
    assert.equal(describeSchedule(9, 0, [0], labels), "Pzt 09:00");
    assert.equal(describeSchedule(9, 0, [6], labels), "Paz 09:00");
  });

  it("ignores an out-of-range day rather than printing undefined", () => {
    assert.equal(describeSchedule(9, 0, [0, 9], labels), "Pzt 09:00");
  });
});
