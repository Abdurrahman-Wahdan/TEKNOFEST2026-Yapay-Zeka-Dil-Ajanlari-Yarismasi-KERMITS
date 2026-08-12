"use client";

import { useEffect, useState } from "react";

/**
 * The viewport width, safe to read while rendering.
 *
 * The template reads `window.innerWidth` directly inside JSX in a few places.
 * Under CRA that was fine — nothing rendered on a server. In Next every client
 * component is also rendered on the server, where `window` does not exist, so
 * those reads throw `ReferenceError: window is not defined` and the request
 * 500s.
 *
 * Returns 0 until mounted. That is deliberate: the server and the first client
 * render agree on 0, so there is no hydration mismatch, and the real width
 * arrives in an effect immediately afterwards.
 */
export default function useViewportWidth() {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const update = () => setWidth(window.innerWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return width;
}
