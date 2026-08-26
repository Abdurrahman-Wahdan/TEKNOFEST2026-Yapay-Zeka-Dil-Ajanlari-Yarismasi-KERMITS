"use client";

import Skeleton from "@mui/material/Skeleton";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Pencil, Play, Trash2, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { ActionButton } from "@/components/ui/ActionButton";
import { CenteredState } from "@/components/ui/CenteredState";
import { Pill } from "@/components/ui/Pill";
import { RoundButton } from "@/components/ui/RoundButton";
import { Toggle } from "@/components/ui/Toggle";
import { VuiBox, VuiButton, VuiTypography } from "@/components/vision";
import { api, type Automation } from "@/lib/api";
import {
  AUTOMATIONS_KEY,
  describeSchedule,
  type Weekday,
} from "@/lib/automations";
import { formatDateTime } from "@/lib/format";

import {
  FrequencyField,
  PromptField,
  ScheduleFields,
  TitleField,
  toggleDay,
  type Chosen,
} from "./AutomationFields";

import { REPORTS_KEY } from "./ReportsBrowser";
import { STATS_KEY } from "./ProfileStats";

type Locale = "tr" | "en";

/**
 * The user's standing orders, and the controls for them.
 *
 * Oldest first, matching the API. This is a list someone maintains rather than a
 * feed they read, and a list whose rows move when you add one is hard to keep
 * track of — the new row appears at the bottom, next to the composer that made
 * it.
 *
 * Deleting is here and **not** available to the agent. Deleting the wrong
 * automation is unrecoverable and a model has no way to be sure which of two
 * gold-price automations the user meant; a button on the row it names does.
 */
export function AutomationsBoard() {
  const t = useTranslations("automations");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const queryClient = useQueryClient();
  const [failed, setFailed] = useState<string | null>(null);
  const [started, setStarted] = useState<string | null>(null);
  /** Which row is open for editing. One at a time: two open forms on one list
   *  is two sets of unsaved changes and no way to tell them apart. */
  const [editingId, setEditingId] = useState<string | null>(null);

  const automations = useQuery({
    queryKey: AUTOMATIONS_KEY,
    queryFn: () => api.automations(),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: AUTOMATIONS_KEY });
    queryClient.invalidateQueries({ queryKey: STATS_KEY });
  };

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.updateAutomation(id, { enabled }),
    onError: () => setFailed(t("createFailed")),
    onSettled: refresh,
  });

  const edit = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: AutomationEdit }) =>
      api.updateAutomation(id, patch),
    onSuccess: () => {
      setFailed(null);
      // Closing on success only. A save that failed must leave the form open
      // with the user's text still in it -- reverting to the row and losing a
      // rewritten prompt is the one unrecoverable thing this screen can do.
      setEditingId(null);
    },
    onError: () => setFailed(t("editFailed")),
    onSettled: refresh,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteAutomation(id),
    onError: () => setFailed(t("deleteFailed")),
    // `onSettled`, not `onSuccess`: an id already gone answers 404, and the list
    // still needs refreshing — that is exactly when it is stale.
    onSettled: () => {
      refresh();
      queryClient.invalidateQueries({ queryKey: REPORTS_KEY });
    },
  });

  const runNow = useMutation({
    mutationFn: (id: string) => api.runAutomation(id),
    onSuccess: (_data, id) => {
      setFailed(null);
      setStarted(id);
    },
    onError: () => setFailed(t("runFailed")),
    // Deliberately no report refetch here. A run is minutes of ten bank
    // specialists; polling for its report would be a request every few seconds
    // for most of that time. The notification bell already polls, and it is how
    // a scheduled run announces itself too — one mechanism, not two.
  });

  if (automations.isLoading) {
    return (
      <VuiBox display="flex" flexDirection="column" gap="12px">
        {[0, 1].map((n) => (
          <Skeleton key={n} variant="rounded" height={84} />
        ))}
      </VuiBox>
    );
  }

  if (automations.isError) {
    return (
      <CenteredState
        icon={<TriangleAlert size={22} />}
        label={t("loadFailed")}
        tone="error"
      >
        <VuiButton
          size="small"
          variant="outlined"
          color="white"
          onClick={() => automations.refetch()}
        >
          {tc("retry")}
        </VuiButton>
      </CenteredState>
    );
  }

  const rows = automations.data ?? [];
  if (rows.length === 0) {
    return <CenteredState icon={<CalendarClock size={22} />} label={t("empty")} />;
  }

  return (
    <VuiBox display="flex" flexDirection="column" gap="12px">
      {failed && (
        <VuiTypography variant="caption" sx={{ color: "var(--destructive)" }}>
          {failed}
        </VuiTypography>
      )}
      {rows.map((row) => (
        <Row
          key={row.id}
          row={row}
          locale={locale}
          justStarted={started === row.id}
          editing={editingId === row.id}
          busy={
            (toggle.isPending && toggle.variables?.id === row.id) ||
            (remove.isPending && remove.variables === row.id) ||
            (runNow.isPending && runNow.variables === row.id) ||
            (edit.isPending && edit.variables?.id === row.id)
          }
          onToggle={(enabled) => toggle.mutate({ id: row.id, enabled })}
          onRun={() => runNow.mutate(row.id)}
          onDelete={() => remove.mutate(row.id)}
          onEdit={() => {
            setFailed(null);
            setEditingId(row.id);
          }}
          onCancelEdit={() => setEditingId(null)}
          onSave={(patch) => edit.mutate({ id: row.id, patch })}
        />
      ))}
    </VuiBox>
  );
}

/** The fields the editor can change. Everything else on the row is derived. */
export interface AutomationEdit {
  title: string;
  prompt?: string;
  hour?: number;
  minute?: number;
  weekdays?: Weekday[];
  interval_minutes?: number;
}

function Row({
  row,
  locale,
  busy,
  justStarted,
  editing,
  onToggle,
  onRun,
  onDelete,
  onEdit,
  onCancelEdit,
  onSave,
}: {
  row: Automation;
  locale: Locale;
  busy: boolean;
  justStarted: boolean;
  editing: boolean;
  onToggle: (enabled: boolean) => void;
  onRun: () => void;
  onDelete: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSave: (patch: AutomationEdit) => void;
}) {
  const t = useTranslations("automations");
  const dayNames = [
    t("dayMon"), t("dayTue"), t("dayWed"), t("dayThu"),
    t("dayFri"), t("daySat"), t("daySun"),
  ];
  const minuteOfDay = (value: number) =>
    `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;

  const schedule = row.interval_minutes !== null
    ? [
        t("everyMinutes", { minutes: row.interval_minutes }),
        row.weekdays.length > 0
          ? row.weekdays.map((day) => dayNames[day]).join(", ")
          : null,
        row.window_start_minute !== null && row.window_end_minute !== null
          ? t("timeWindow", {
              start: minuteOfDay(row.window_start_minute),
              end: minuteOfDay(row.window_end_minute),
            })
          : null,
      ].filter(Boolean).join(" · ")
    : describeSchedule(row.hour, row.minute, row.weekdays, {
    daily: (time) => t("daily", { time }),
    weekdays: (time) => t("weekdaysOnly", { time }),
    weekend: (time) => t("weekend", { time }),
    someDays: (days, time) => t("someDays", { days, time }),
    dayNames,
      });

  if (editing) {
    return (
      <RowEditor
        row={row}
        busy={busy}
        onCancel={onCancelEdit}
        onSave={onSave}
      />
    );
  }

  return (
    <VuiBox
      display="flex"
      alignItems="center"
      gap="12px"
      sx={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "16px",
        padding: "14px 16px",
        opacity: row.enabled ? 1 : 0.6,
      }}
    >
      <VuiBox flex={1} display="flex" flexDirection="column" gap="4px">
        <VuiBox display="flex" alignItems="center" gap="8px" flexWrap="wrap">
          <VuiTypography
            variant="button"
            fontWeight="bold"
            sx={{ color: "var(--foreground)" }}
          >
            {row.title}
          </VuiTypography>
          {!row.enabled && <Pill tone="neutral">{t("paused")}</Pill>}
          {row.kind === "condition_alert" && <Pill tone="neutral">{t("alert")}</Pill>}
          {row.last_error && <Pill tone="bad">{t("lastErrorLabel")}</Pill>}
        </VuiBox>
        <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
          {schedule}
        </VuiTypography>
        <VuiTypography variant="caption" sx={{ color: "var(--text-faint)" }}>
          {/* Next run only while it is enabled: a paused automation still has a
              `next_run_at` in the table, and showing it would promise a report
              that will not arrive. */}
          {row.enabled
            ? t("nextRun", { date: formatDateTime(row.next_run_at, locale) })
            : ""}
          {row.last_run_at
            ? `  ·  ${t("lastRun", { date: formatDateTime(row.last_run_at, locale) })}`
            : `  ·  ${t("neverRun")}`}
        </VuiTypography>
        {row.kind === "condition_alert" && row.last_condition_met !== null && (
          <VuiTypography
            variant="caption"
            sx={{
              color: row.last_condition_met
                ? "var(--primary-strong)"
                : "var(--control-ink)",
            }}
          >
            {row.last_condition_met ? t("conditionMet") : t("conditionNotMet")}
          </VuiTypography>
        )}
        {justStarted && (
          <VuiTypography variant="caption" sx={{ color: "var(--primary-strong)" }}>
            {row.kind === "condition_alert" ? t("checkStarted") : t("runStarted")}
          </VuiTypography>
        )}
      </VuiBox>

      {/* One line, one height, centred against the text block beside it.

          The three used to be a 30px MUI `IconButton`, a 36px switch and
          another 30px `IconButton`, in a row whose parent aligned to
          `flex-start` -- so they sat high against three lines of text and on
          three different centres. They are all `RoundButton` now: the same
          36px control the composer uses, which is what makes "centre them"
          mean anything at all. */}
      <VuiBox display="flex" alignItems="center" gap="2px" sx={{ flexShrink: 0 }}>
        <RoundButton label={t("runNow")} onClick={onRun} disabled={busy}>
          <Play size={16} />
        </RoundButton>
        {/* Beside run, before the pause switch: run and edit are the two things
            done to an automation that is working, and the switch and the bin
            are the two that stop it. */}
        <RoundButton label={t("edit")} onClick={onEdit} disabled={busy}>
          <Pencil size={16} />
        </RoundButton>
        {/* The app's switch, not MUI's. MUI's arrives with its own palette --
            a pale capsule and a white knob -- and on this dark card it was the
            one control on the page visibly not ours. `ui/Toggle` is the same
            switch the composer's Advanced menu draws. */}
        <Toggle
          on={row.enabled}
          disabled={busy}
          onChange={onToggle}
          label={row.enabled ? t("disable") : t("enable")}
        />
        {/* The hint rides on the label rather than in a tooltip of its own:
            deleting an automation keeps its reports, and that is the one thing
            a reader wants to know before clicking a bin. */}
        <RoundButton
          label={`${t("delete")} — ${t("deleteKeepsReports")}`}
          onClick={onDelete}
          disabled={busy}
        >
          <Trash2 size={16} />
        </RoundButton>
      </VuiBox>
    </VuiBox>
  );
}

/**
 * One row, open for editing.
 *
 * Its own component so the draft lives and dies with it: mounting seeds the
 * fields from the row and unmounting throws the draft away, which is exactly
 * what Cancel should mean. Holding the draft in `AutomationsBoard` instead
 * would have meant remembering to clear it on every path out, and the one that
 * got forgotten would show the previous automation's prompt in the next row
 * somebody opened.
 *
 * The fields are `AutomationFields`, the same ones the composer above creates
 * with — so a day chip means the same thing, and looks the same, on the way in
 * and on the way back.
 */
function RowEditor({
  row,
  busy,
  onCancel,
  onSave,
}: {
  row: Automation;
  busy: boolean;
  onCancel: () => void;
  onSave: (patch: AutomationEdit) => void;
}) {
  const t = useTranslations("automations");
  const tc = useTranslations("common");

  const [title, setTitle] = useState(row.title);
  const [prompt, setPrompt] = useState(row.prompt);
  const [hour, setHour] = useState<Chosen>(row.hour);
  const [minute, setMinute] = useState<Chosen>(row.minute);
  // Never null here: an existing automation has a set, and an empty one means
  // every day. `null` is the composer's "the user has not touched this yet".
  const [days, setDays] = useState<Weekday[]>(row.weekdays as Weekday[]);
  const [interval, setInterval] = useState<Chosen>(row.interval_minutes ?? 60);

  const isAlert = row.kind === "condition_alert";
  const isInterval = row.interval_minutes !== null;

  const canSave =
    title.trim().length > 0 &&
    (isAlert || prompt.trim().length > 0) &&
    (!isInterval || interval !== null) &&
    !busy;

  return (
    <VuiBox
      display="flex"
      flexDirection="column"
      gap="14px"
      sx={{
        background: "var(--card)",
        // The ring, not the border: an open editor is the thing being worked
        // on, and it sits in a column of otherwise identical rows.
        border: "1px solid var(--ring)",
        borderRadius: "16px",
        padding: "14px 16px",
      }}
    >
      <VuiBox display="flex" flexDirection="column" gap="6px">
        <VuiTypography variant="caption" color="text">
          {t("titleLabel")}
        </VuiTypography>
        <TitleField
          value={title}
          disabled={busy}
          maxLength={160}
          aria-label={t("titleLabel")}
          onChange={(event) => setTitle(event.target.value)}
        />
      </VuiBox>

      {!isAlert && <VuiBox display="flex" flexDirection="column" gap="6px">
        <VuiTypography variant="caption" color="text">
          {t("promptLabel")}
        </VuiTypography>
        <VuiBox
          sx={{
            border: "1px solid var(--border)",
            borderRadius: "15px",
            padding: "12px 14px",
          }}
        >
          {/* No Enter-to-submit here, unlike the composer. This is a prompt
              being *revised* rather than typed once, so a stray Enter mid-edit
              must break the line, not save the row. */}
          <PromptField
            value={prompt}
            disabled={busy}
            aria-label={t("promptLabel")}
            onChange={(event) => setPrompt(event.target.value)}
          />
        </VuiBox>
      </VuiBox>}

      {!isInterval && <ScheduleFields
        hour={hour}
        minute={minute}
        days={days}
        disabled={busy}
        onHour={setHour}
        onMinute={setMinute}
        onToggleDay={(day) => setDays((current) => toggleDay(current, day))}
      />}

      {isInterval && (
        <>
          <FrequencyField
            value={interval}
            onChange={setInterval}
            disabled={busy}
          />
          <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
            {isAlert ? row.prompt : t("frequencyHint")}
          </VuiTypography>
        </>
      )}

      <VuiBox display="flex" alignItems="center" justifyContent="flex-end" gap="8px">
        <ActionButton variant="outlined" color="white" onClick={onCancel} disabled={busy}>
          {tc("cancel")}
        </ActionButton>
        <ActionButton
          disabled={!canSave}
          onClick={() =>
            onSave(
              isInterval
                ? {
                    title: title.trim(),
                    ...(!isAlert ? { prompt: prompt.trim() } : {}),
                    interval_minutes: interval ?? row.interval_minutes ?? 60,
                  }
                : {
                    title: title.trim(),
                    prompt: prompt.trim(),
                    // Both are real numbers on this form: `allowAuto` is off.
                    hour: hour ?? row.hour,
                    minute: minute ?? row.minute,
                    weekdays: days,
                  },
            )
          }
        >
          {busy ? t("saving") : t("save")}
        </ActionButton>
      </VuiBox>
    </VuiBox>
  );
}
