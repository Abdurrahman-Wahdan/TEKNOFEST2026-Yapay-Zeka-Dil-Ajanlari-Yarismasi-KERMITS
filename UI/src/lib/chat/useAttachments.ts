"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  AttachedCapture,
  AttachedContext,
  AttachedFile,
  AttachedImage,
  MentionTarget,
} from "./types";

/**
 * Files the user has staged for the next message.
 *
 * Local only. There is no upload endpoint yet, so the bytes stay in the browser:
 * images get an object URL for their thumbnail and everything else is carried as
 * name and size. When the backend lands, this is where the upload call goes --
 * the shape it hands the composer does not have to change.
 *
 * Images are split from other files because they are *shown* rather than listed,
 * which is the one place the two kinds genuinely differ.
 */

/** What counts as an image, and so gets a thumbnail instead of a file chip. */
const IMAGE_TYPES = /^image\//;

let attachmentSeq = 0;

export function useAttachments() {
  const [images, setImages] = useState<AttachedImage[]>([]);
  const [files, setFiles] = useState<AttachedFile[]>([]);
  /**
   * Pieces of the app, not files: a quote, a table row, a whole table.
   *
   * Kept beside the files rather than in their own hook because everything about
   * the lifecycle is the same -- staged for one turn, listed in the tray,
   * offered to the `@` menu, cleared on send -- and a second hook would mean two
   * places to remember to clear.
   */
  const [contexts, setContexts] = useState<AttachedContext[]>([]);
  /**
   * Screenshots of the page.
   *
   * Separate from `images` because these are data URLs rather than blobs -- there
   * is no object URL to revoke -- and because they leave on their own field of the
   * request, never through the transcript.
   */
  const [captures, setCaptures] = useState<AttachedCapture[]>([]);

  // Every object URL handed out, so they can be released. A thumbnail URL pins
  // its blob in memory until revoked, and a chat session that stages a dozen
  // screenshots and clears them would otherwise hold all twelve for the life of
  // the page.
  const urls = useRef<Map<string, string>>(new Map());

  const release = useCallback((id: string) => {
    const url = urls.current.get(id);
    if (url) {
      URL.revokeObjectURL(url);
      urls.current.delete(id);
    }
  }, []);

  useEffect(() => {
    const map = urls.current;
    return () => {
      for (const url of map.values()) URL.revokeObjectURL(url);
      map.clear();
    };
  }, []);

  const add = useCallback((incoming: FileList | File[]) => {
    for (const file of Array.from(incoming)) {
      const id = `att-${++attachmentSeq}`;
      if (IMAGE_TYPES.test(file.type)) {
        const url = URL.createObjectURL(file);
        urls.current.set(id, url);
        setImages((prev) => [...prev, { id, filename: file.name, url, size: file.size }]);
      } else {
        setFiles((prev) => [...prev, { id, filename: file.name, size: file.size }]);
      }
    }
  }, []);

  const removeImage = useCallback(
    (id: string) => {
      release(id);
      setImages((prev) => prev.filter((image) => image.id !== id));
    },
    [release],
  );

  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((file) => file.id !== id));
  }, []);

  /**
   * Stage a piece of the UI. Returns its id, so a caller that needs to undo can.
   *
   * The body is serialised by the caller, at the moment of the click -- see
   * `AttachedContext`. Ids come off the same counter the files use: they end up
   * as React keys in a list rendered on both server and client, and anything
   * random there is a hydration mismatch.
   */
  const addContext = useCallback((incoming: Omit<AttachedContext, "id">) => {
    const id = `att-${++attachmentSeq}`;
    setContexts((prev) => [...prev, { ...incoming, id }]);
    return id;
  }, []);

  const removeContext = useCallback((id: string) => {
    setContexts((prev) => prev.filter((context) => context.id !== id));
  }, []);

  const addCapture = useCallback((incoming: Omit<AttachedCapture, "id">) => {
    const id = `att-${++attachmentSeq}`;
    setCaptures((prev) => [...prev, { ...incoming, id }]);
    return id;
  }, []);

  const removeCapture = useCallback((id: string) => {
    setCaptures((prev) => prev.filter((capture) => capture.id !== id));
  }, []);

  const clear = useCallback(() => {
    for (const id of urls.current.keys()) release(id);
    setImages([]);
    setFiles([]);
    setContexts([]);
    setCaptures([]);
  }, [release]);

  /** Everything staged, in one list, for the `@` menu and the request payload. */
  const targets: MentionTarget[] = [
    ...images.map((image) => ({ id: image.id, filename: image.filename, kind: "image" as const })),
    ...files.map((file) => ({ id: file.id, filename: file.filename, kind: "file" as const })),
    // An attached table becomes `@`-mentionable for free by being here, which is
    // the point of one flattened list: "what does @[Kâr oranları] say about
    // Kuveyt Türk" needs no new plumbing.
    ...contexts.map((context) => ({
      id: context.id,
      filename: context.label,
      kind: "context" as const,
      contextKind: context.kind,
    })),
  ];

  return {
    images,
    files,
    contexts,
    captures,
    targets,
    add,
    addContext,
    addCapture,
    removeImage,
    removeFile,
    removeContext,
    removeCapture,
    clear,
  };
}
