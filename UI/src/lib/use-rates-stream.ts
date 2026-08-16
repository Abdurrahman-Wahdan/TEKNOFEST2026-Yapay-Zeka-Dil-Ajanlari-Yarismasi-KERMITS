"use client";

import { useEffect, useRef, useState } from "react";

import type { Rate } from "@/lib/api";

export interface StreamedBoard {
  rates: Rate[];
  error: string;
  fetched_at: number;
}

export interface RatesStream {
  banks: Record<string, StreamedBoard>;
  /** True once a message has arrived, so the caller can fall back until then. */
  live: boolean;
}

/**
 * The FX board over a socket.
 *
 * The banks are slow — two of the six boards are page reads — and the table has
 * to feel alive. Polling them from the browser would trade one problem for a
 * worse one: every open tab becomes its own load on the banks, and the refresh
 * rate is capped by the slowest of them.
 *
 * So the server polls once and pushes. The browser holds a socket, paints the
 * moment a message lands, and costs the banks nothing regardless of how many
 * people are watching.
 *
 * `live` stays false until the first message, and goes false again if the
 * socket drops, so the caller can keep its polling query as the fallback rather
 * than showing an empty board when a proxy will not upgrade the connection.
 */
export function useRatesStream(enabled: boolean): RatesStream {
  const [state, setState] = useState<RatesStream>({ banks: {}, live: false });
  const retry = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const open = () => {
      if (closed) return;
      // Same origin, so the socket rides the proxy the REST calls already use
      // and needs no second host to configure or allow.
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/api/banks/rates/stream`);

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message?.type === "rates") {
            retry.current = 0;
            setState({ banks: message.banks ?? {}, live: true });
          }
        } catch {
          // A frame we cannot read is dropped rather than killing the socket:
          // losing one tick beats losing the stream.
        }
      };

      const reopen = () => {
        setState((s) => ({ ...s, live: false }));
        if (closed) return;
        // Backing off, capped: a server that is down should not be hammered,
        // and a brief network blip should not cost the user ten seconds.
        retry.current = Math.min(retry.current + 1, 5);
        timer = setTimeout(open, 500 * 2 ** (retry.current - 1));
      };

      socket.onclose = reopen;
      socket.onerror = () => socket?.close();
    };

    open();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [enabled]);

  // Derived rather than reset inside the effect: clearing state there is a
  // synchronous update during an effect, which cascades a render for something
  // already knowable from the argument.
  return enabled ? state : EMPTY;
}

const EMPTY: RatesStream = { banks: {}, live: false };
