"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { AttachedFile, AttachedImage, MentionTarget } from "./types";

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

  const clear = useCallback(() => {
    for (const id of urls.current.keys()) release(id);
    setImages([]);
    setFiles([]);
  }, [release]);

  /** Everything staged, in one list, for the `@` menu and the request payload. */
  const targets: MentionTarget[] = [
    ...images.map((image) => ({ id: image.id, filename: image.filename, kind: "image" as const })),
    ...files.map((file) => ({ id: file.id, filename: file.filename, kind: "file" as const })),
  ];

  return { images, files, targets, add, removeImage, removeFile, clear };
}
