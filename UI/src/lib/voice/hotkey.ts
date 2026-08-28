/**
 * Whether this keystroke should open voice mode.
 *
 * Pure, and takes plain descriptors rather than a `KeyboardEvent`, because the
 * test runner is `node --experimental-strip-types` with no DOM at all. Reading
 * `document.activeElement` and probing for an open dialog is the caller's job;
 * deciding what those readings mean is this file's, and that is the half that
 * gets a decision wrong.
 *
 * V is deliberately used instead of Space: it does not scroll the page or
 * activate a focused control, while text-entry fields are still excluded below.
 */

import type { ChatStatus } from "../chat/types";

/**
 * Where V is a character, not a command.
 *
 * `data-no-quote` is already on the composer shell for `SelectionReply`, and it
 * means the same thing here, so it is reused rather than duplicated.
 * `data-voice-block` is the new escape hatch for anything that needs to opt out
 * without pretending to be a text field.
 */
export const VOICE_TEXT_ENTRY_SELECTOR = [
  "input",
  "textarea",
  "select",
  "[contenteditable='true']",
  "[role='textbox']",
  "[data-no-quote]",
  "[data-voice-block]",
].join(",");

/**
 * A surface that owns the screen while it is open.
 *
 * Queried against the whole document, not the focused element: MUI's `Dialog`
 * and `Menu` portal to `<body>`, so they are nowhere near whatever has focus.
 * `AdvancedMenu` and `ContextRing` already declare `role="dialog"` and
 * `MentionMenu` declares `role="listbox"`, so the app's own non-portalled
 * popovers are covered by the same probe without touching them.
 */
export const VOICE_BLOCKING_SELECTOR = [
  "[role='dialog']",
  "[aria-modal='true']",
  "[role='menu']",
  "[role='listbox']",
  ".MuiModal-root",
  "[data-voice-block]",
].join(",");

/**
 * Voice mode's own dock, which the probe above must never count.
 *
 * The dock is on screen for the whole back half of a turn, and pressing V
 * while the answer is being read is how the next question gets asked. Matching
 * it as a blocking surface would mean the feature quietly refused to be
 * interrupted by anyone -- so anything inside this subtree is skipped, and the
 * dock is left free to carry menus or dialogs of its own later without
 * silently switching barge-in off.
 */
export const VOICE_DOCK_SELECTOR = "[data-voice-dock]";

export type VoiceHotkeyEvent = {
  code: string;
  repeat: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
};

export type VoiceHotkeyContext = {
  /** Path with the locale prefix already stripped, as `usePathname` reports it. */
  pathname: string;
  /** Focus is somewhere V types a character. */
  inTextEntry: boolean;
  /** A dialog, menu or listbox is open somewhere on the page. */
  blockingSurface: boolean;
  /** The assistant panel is open. */
  popupOpen: boolean;
  /** The conversation's status, from `useChat()`. */
  status: ChatStatus;
  signedIn: boolean;
  /**
   * Voice mode is open in a phase a new press must not interrupt.
   *
   * False while it is reading an answer out and false while the dock lingers
   * afterwards: pressing V to cut the assistant off and ask the next thing
   * is how people actually talk, and the dock stays up so that next thing has
   * somewhere to land.
   */
  voiceBusy: boolean;
};

/** The assistant is mid-turn. `idle` is declared but nothing ever produces it. */
export function isChatBusy(status: ChatStatus): boolean {
  return status === "submitted" || status === "streaming";
}

export function shouldOpenVoiceMode(
  event: VoiceHotkeyEvent,
  context: VoiceHotkeyContext,
): boolean {
  if (event.code !== "KeyV") return false;
  // A held key repeats about sixty times a second. Only the first press opens.
  if (event.repeat) return false;
  // Modified V belongs to the browser or the OS, not to us.
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;

  if (!context.signedIn) return false;
  if (context.voiceBusy) return false;
  if (context.popupOpen) return false;
  if (context.blockingSurface) return false;
  if (context.inTextEntry) return false;
  // The composer autofocuses on /chat, so V there is nearly always a
  // character. Asking by voice on the page built for typing is also the one
  // place the user has an obvious alternative.
  if (context.pathname === "/chat") return false;
  // A second question while the first is still being answered would abort the
  // first turn and orphan the half-written bubble it had already rendered.
  if (isChatBusy(context.status)) return false;

  return true;
}
