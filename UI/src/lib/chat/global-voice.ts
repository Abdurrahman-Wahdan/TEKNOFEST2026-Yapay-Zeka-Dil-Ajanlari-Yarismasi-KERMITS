import type { ChatStatus } from "./types";

export const GLOBAL_VOICE_BLOCKING_SELECTOR = [
  "input",
  "textarea",
  "select",
  "button",
  "a[href]",
  "[contenteditable='true']",
  "[role='textbox']",
  "[role='button']",
  "[role='dialog']",
  "[role='menu']",
  "[role='listbox']",
  "[data-global-voice-block]",
].join(",");

export const GLOBAL_VOICE_SURFACE_SELECTOR = [
  "[role='dialog']",
  "[aria-modal='true']",
  ".MuiModal-root",
  "[role='menu']",
  "[role='listbox']",
  "[data-global-voice-block]",
].join(",");

export function isGlobalVoiceKey(event: {
  code: string;
  repeat: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}): boolean {
  return (
    event.code === "Space" &&
    !event.repeat &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.shiftKey
  );
}

export function isGlobalVoiceAvailable({
  pathname,
  popupOpen,
  status,
  signedIn,
}: {
  pathname: string;
  popupOpen: boolean;
  status: ChatStatus;
  signedIn: boolean;
}): boolean {
  return signedIn && pathname !== "/chat" && !popupOpen && status === "ready";
}

export function isBlockedVoiceTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest(GLOBAL_VOICE_BLOCKING_SELECTOR));
}

export function hasBlockingVoiceSurface(document: Document): boolean {
  return Boolean(document.querySelector(GLOBAL_VOICE_SURFACE_SELECTOR));
}
