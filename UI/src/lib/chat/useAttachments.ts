"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";

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
 * Local entries provide previews and cancellation. Content is uploaded once to
 * the authenticated preparation endpoint; chat requests carry only opaque ids.
 *
 * Images are split from other files because they are *shown* rather than listed,
 * which is the one place the two kinds genuinely differ.
 */

/** What counts as an image, and so gets a thumbnail instead of a file chip. */
const IMAGE_TYPES = /^image\//;
const IMAGE_EXTENSIONS = /\.(?:jpe?g|png|webp)$/i;

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
  const uploads = useRef<Map<string, AbortController>>(new Map());

  const release = useCallback((id: string) => {
    const url = urls.current.get(id);
    if (url) {
      URL.revokeObjectURL(url);
      urls.current.delete(id);
    }
  }, []);

  useEffect(() => {
    const map = urls.current;
    const pending = uploads.current;
    return () => {
      for (const url of map.values()) URL.revokeObjectURL(url);
      map.clear();
      for (const controller of pending.values()) controller.abort();
      pending.clear();
    };
  }, []);

  const add = useCallback((incoming: FileList | File[]) => {
    for (const file of Array.from(incoming)) {
      const id = `att-${++attachmentSeq}`;
      const image = IMAGE_TYPES.test(file.type) || IMAGE_EXTENSIONS.test(file.name);
      if (image) {
        const url = URL.createObjectURL(file);
        urls.current.set(id, url);
        setImages((prev) => [
          ...prev,
          { id, filename: file.name, url, size: file.size, status: "uploading" },
        ]);
      } else {
        const extension = file.name.split(".").pop()?.toLowerCase();
        const kind = extension === "pdf" || extension === "docx" ? "document" : "text";
        setFiles((prev) => [
          ...prev,
          { id, filename: file.name, size: file.size, kind, status: "uploading" },
        ]);
      }

      const controller = new AbortController();
      uploads.current.set(id, controller);
      void api.prepareChatAttachment(file, controller.signal).then(
        (prepared) => {
          uploads.current.delete(id);
          if (image) {
            setImages((prev) =>
              prev.map((item) =>
                item.id === id
                  ? { ...item, attachmentId: prepared.id, status: "ready", error: undefined }
                  : item,
              ),
            );
          } else {
            setFiles((prev) =>
              prev.map((item) =>
                item.id === id
                  ? {
                      ...item,
                      attachmentId: prepared.id,
                      kind: prepared.kind === "document" ? "document" : "text",
                      pageCount: prepared.pageCount ?? undefined,
                      status: "ready",
                      error: undefined,
                    }
                  : item,
              ),
            );
          }
        },
        (error: unknown) => {
          uploads.current.delete(id);
          if (controller.signal.aborted) return;
          const message = error instanceof Error ? error.message : String(error);
          if (image) {
            setImages((prev) =>
              prev.map((item) =>
                item.id === id ? { ...item, status: "error", error: message } : item,
              ),
            );
          } else {
            setFiles((prev) =>
              prev.map((item) =>
                item.id === id ? { ...item, status: "error", error: message } : item,
              ),
            );
          }
        },
      );
    }
  }, []);

  const removeImage = useCallback(
    (id: string) => {
      uploads.current.get(id)?.abort();
      uploads.current.delete(id);
      release(id);
      setImages((prev) => prev.filter((image) => image.id !== id));
    },
    [release],
  );

  const removeFile = useCallback((id: string) => {
    uploads.current.get(id)?.abort();
    uploads.current.delete(id);
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
    for (const controller of uploads.current.values()) controller.abort();
    uploads.current.clear();
    for (const id of urls.current.keys()) release(id);
    setImages([]);
    setFiles([]);
    setContexts([]);
    setCaptures([]);
  }, [release]);

  /** Everything staged, in one list, for the `@` menu and the request payload. */
  const targets: MentionTarget[] = useMemo(
    () => [
      ...images.map((image) => ({
        id: image.id,
        filename: image.filename,
        kind: "image" as const,
      })),
      ...files.map((file) => ({
        id: file.id,
        filename: file.filename,
        kind: "file" as const,
      })),
      // An attached table becomes `@`-mentionable for free by being here, which
      // is the point of one flattened list: "what does @[Kâr oranları] say about
      // Kuveyt Türk" needs no new plumbing.
      ...contexts.map((context) => ({
        id: context.id,
        filename: context.label,
        kind: "context" as const,
        contextKind: context.kind,
      })),
    ],
    [contexts, files, images],
  );

  const prepared = [...images, ...files].flatMap((item) =>
    item.status === "ready" && item.attachmentId ? [{ id: item.attachmentId }] : [],
  );
  const hasPending = [...images, ...files].some((item) => item.status === "uploading");
  const hasError = [...images, ...files].some((item) => item.status === "error");

  return {
    images,
    files,
    contexts,
    captures,
    targets,
    prepared,
    hasPending,
    hasError,
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
