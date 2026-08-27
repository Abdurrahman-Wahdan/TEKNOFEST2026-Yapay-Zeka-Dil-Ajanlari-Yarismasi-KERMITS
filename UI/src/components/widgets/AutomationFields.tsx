"use client";

import { styled } from "@mui/material/styles";
import { useTranslations } from "next-intl";

import { CONTROL_PADDING_X, controlShape } from "@/components/ui/control";
import { Dropdown } from "@/components/ui/Dropdown";
import { ToggleChip } from "@/components/ui/ToggleChip";
import { Toggle } from "@/components/ui/Toggle";
import { VuiBox, VuiTypography } from "@/components/vision";
import { WEEKDAYS, type Weekday } from "@/lib/automations";

/**
 * The fields an automation is made of, for the two places that edit one.
 *
 * The composer creates them and the board's rows edit them, and those are the
 * same five values: a sentence, an hour, a minute and a set of days. They were
 * about to be two implementations — the row would have grown its own dropdowns
 * and its own day chips — and the failure mode is the one this codebase keeps
 * writing down: the create form gets a fix, the edit form does not, and the two
 * quietly disagree about what a Tuesday is.
 *
 * **`allowAuto` is the one real difference between them.** Creating, an unset
 * hour means "read it out of my sentence" and the drafting agent decides.
 * Editing, there is nothing to decide: the automation already has an hour, and
 * offering "Asistan seçsin" against a stored 16:00 would be offering to un-choose
 * something without saying what would happen. So the empty option exists on the
 * way in and not on the way back.
 */

/**
 * The field, as a real element rather than `VuiBox component="textarea"`.
 *
 * The same reason `ChatComposer` does it: `VuiBox` is `styled(Box)` with the
 * template's decorative ownerState bolted on, and pushing a *controlled* field
 * through that indirection made typing unreliable — the value reached the DOM
 * but did not always survive the next render, which for a controlled input
 * means the text silently disappears.
 */
export const PromptField = styled("textarea")({
  width: "100%",
  minHeight: 72,
  maxHeight: 220,
  resize: "none",
  border: "none",
  outline: "none",
  background: "transparent",
  color: "var(--foreground)",
  fontFamily: "inherit",
  fontSize: "0.9375rem",
  lineHeight: "22px",
  display: "block",
  padding: 0,
  margin: 0,
  "&::placeholder": { color: "var(--control-ink)" },
  "&:disabled": { opacity: 0.5, cursor: "not-allowed" },
});

/**
 * The title, single line.
 *
 * Only the editor renders this: creating, the title is the drafting agent's
 * one-line summary of the sentence, and asking for it up front would be asking
 * the same thing twice. Editing, it has to be here — rewrite the prompt and the
 * generated title is describing the old one, and the title is what the row shows.
 *
 * Geometry from `controlShape`, so it is the same 44px box with the same radius
 * as the dropdowns beneath it rather than a third height on the same card.
 */
export const TitleField = styled("input")(({ theme }) => ({
  ...controlShape(theme),
  width: "100%",
  boxSizing: "border-box",
  padding: `0 ${CONTROL_PADDING_X}`,
  border: "1px solid var(--border)",
  outline: "none",
  background: "var(--input-bg, transparent)",
  color: "var(--foreground)",
  fontFamily: "inherit",
  "&::placeholder": { color: "var(--control-ink)" },
  "&:hover:not(:disabled)": { borderColor: "var(--ring)" },
  "&:focus-visible": { borderColor: "var(--ring)" },
  "&:disabled": { opacity: 0.5, cursor: "not-allowed" },
}));

const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

/**
 * The quarter hours, and only those, for a *new* automation: a schedule nobody
 * is watching does not need 09:37, and sixty options is a scroll.
 *
 * An existing one can hold any minute — the drafting agent reads "09:50" out of
 * a sentence quite happily — so `minuteOptions` keeps whatever is already
 * stored. Without that the select would silently fall to its first option and
 * an edit to the *days* would quietly move the time.
 */
const MINUTES = [0, 15, 30, 45];

/** `null` means "the agent decides"; a number means the user chose. */
export type Chosen = number | null;

/** The empty option's value. `""` is what a native select reports for it. */
export const AUTO = "";

/** Common presets. The agent/API may preserve any value from five minutes up. */
export const CHECK_INTERVALS = [5, 15, 30, 60, 180, 360, 720, 1440];

export function EmailDeliveryFields({
  enabled,
  format,
  onEnabled,
  onFormat,
  disabled = false,
}: {
  enabled: boolean;
  format: "pdf" | "docx";
  onEnabled: (enabled: boolean) => void;
  onFormat: (format: "pdf" | "docx") => void;
  disabled?: boolean;
}) {
  const t = useTranslations("automations");
  return (
    <VuiBox display="flex" alignItems="center" flexWrap="nowrap" gap="12px">
      <Toggle on={enabled} onChange={onEnabled} disabled={disabled} label={t("emailToggle")} />
      <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
        {t("emailToggle")}
      </VuiTypography>
      {enabled && (
        <Dropdown
          label={t("reportFormat")}
          value={format}
          options={[{ value: "pdf", label: "PDF" }, { value: "docx", label: "Word" }]}
          minWidth="10rem"
          fullWidth={false}
          disabled={disabled}
          onChange={(value) => onFormat(value as "pdf" | "docx")}
        />
      )}
    </VuiBox>
  );
}

export function FrequencyField({
  value,
  onChange,
  disabled = false,
  allowAuto = false,
}: {
  value: Chosen;
  onChange: (minutes: Chosen) => void;
  disabled?: boolean;
  allowAuto?: boolean;
}) {
  const t = useTranslations("automations");
  const options = [
    ...(allowAuto ? [{ value: AUTO, label: t("automatic") }] : []),
    ...Array.from(new Set([
      ...(value !== null ? [value] : []),
      ...CHECK_INTERVALS,
    ])).sort((a, b) => a - b).map((minutes) => ({
      value: String(minutes),
      label: t("everyMinutes", { minutes }),
    })),
  ];

  return (
    <Dropdown
      label={t("checkFrequency")}
      value={value === null ? AUTO : String(value)}
      options={options}
      minWidth="13rem"
      fullWidth={false}
      disabled={disabled}
      onChange={(next) => onChange(next === AUTO ? null : Number(next))}
    />
  );
}

/** Every field of an automation that either surface edits. */
export interface AutomationDraft {
  text: string;
  hour: Chosen;
  minute: Chosen;
  /** `null` while untouched, so a create can tell "unset" from "every day". */
  days: Weekday[] | null;
}

/** Add or remove one day, sorted — which is what the API and `sameDays` expect. */
export function toggleDay(days: Weekday[] | null, day: Weekday): Weekday[] {
  const base = days ?? [];
  const next = base.includes(day)
    ? base.filter((d) => d !== day)
    : [...base, day];
  return next.sort((a, b) => a - b);
}

export function ScheduleFields({
  hour,
  minute,
  days,
  onHour,
  onMinute,
  onToggleDay,
  disabled = false,
  allowAuto = false,
}: {
  hour: Chosen;
  minute: Chosen;
  days: Weekday[] | null;
  onHour: (hour: Chosen) => void;
  onMinute: (minute: Chosen) => void;
  /**
   * Which day was hit, not the resulting set.
   *
   * The caller applies it against its own latest state, so two chips toggled in
   * one React batch each see the previous one. Handing the computed array up
   * instead meant both were derived from the same rendered `days` prop and the
   * second silently discarded the first.
   */
  onToggleDay: (day: Weekday) => void;
  disabled?: boolean;
  /** Offer "let the assistant choose" — creating only. See the header. */
  allowAuto?: boolean;
}) {
  const t = useTranslations("automations");

  const dayNames = [
    t("dayMon"), t("dayTue"), t("dayWed"), t("dayThu"),
    t("dayFri"), t("daySat"), t("daySun"),
  ];

  const pad = (value: number) => String(value).padStart(2, "0");
  const auto = allowAuto ? [{ value: AUTO, label: t("automatic") }] : [];

  const hourOptions = [
    ...auto,
    ...HOURS.map((h) => ({ value: String(h), label: pad(h) })),
  ];
  // Whatever is stored, even when it is not a quarter hour. See MINUTES.
  const minuteValues =
    minute !== null && !MINUTES.includes(minute)
      ? [...MINUTES, minute].sort((a, b) => a - b)
      : MINUTES;
  const minuteOptions = [
    ...auto,
    ...minuteValues.map((m) => ({ value: String(m), label: pad(m) })),
  ];

  return (
    <>
      {/* Labelled controls stacked in groups, the way the comparator's card
          does it. The first version put the label, both selects, the word
          "Günler", seven days and the submit button on one wrapped line, and
          at that density the labels stopped reading as labels. */}
      <VuiBox display="flex" flexWrap="wrap" gap="12px">
        <Dropdown
          label={t("timeLabel")}
          value={hour === null ? AUTO : String(hour)}
          options={hourOptions}
          minWidth="10rem"
          fullWidth={false}
          disabled={disabled}
          onChange={(value) => onHour(value === AUTO ? null : Number(value))}
        />
        <Dropdown
          label={t("minuteLabel")}
          value={minute === null ? AUTO : String(minute)}
          options={minuteOptions}
          minWidth="10rem"
          fullWidth={false}
          // Minutes past an unset hour mean nothing. Only reachable while
          // creating: editing, the hour is always a real number.
          disabled={disabled || hour === null}
          onChange={(value) => onMinute(value === AUTO ? null : Number(value))}
        />
      </VuiBox>

      <VuiBox display="flex" flexDirection="column" gap="6px">
        <VuiTypography variant="caption" color="text">
          {t("daysLabel")}
        </VuiTypography>
        <VuiBox display="flex" alignItems="center" flexWrap="wrap" gap="6px">
          {WEEKDAYS.map((day) => (
            <ToggleChip
              key={day}
              label={dayNames[day]}
              on={days?.includes(day) ?? false}
              disabled={disabled}
              onClick={() => onToggleDay(day)}
            />
          ))}
          {/* Only when it is true. The empty string this used to render when
              days were chosen left a stray flex child in the row. */}
          {(days === null || days.length === 0) && (
            <VuiTypography
              variant="caption"
              sx={{ color: "var(--text-faint)", marginInlineStart: "6px" }}
            >
              {t("everyDay")}
            </VuiTypography>
          )}
        </VuiBox>
      </VuiBox>
    </>
  );
}
