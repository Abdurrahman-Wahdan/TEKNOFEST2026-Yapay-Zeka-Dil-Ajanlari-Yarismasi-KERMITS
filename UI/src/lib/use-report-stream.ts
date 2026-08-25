"use client";

import { useEffect, useRef, useState } from "react";

import { getAccessToken } from "@/lib/api";

/** One new report, as the server announces it. */
export interface ReportEvent {
  id: string;
  automation_id: string;
  title: string;
  status: string;
  created_at: string | null;
}

/**
 * New reports over a socket, so the bell does not wait for its next poll.
 *
 * The badge and the menu are still fetched — this does not replace them, and
 * deliberately: a proxy that will not upgrade the connection has to degrade to
 * the minute-long poll rather than to silence. What this removes is the wait.
 * An automation is minutes of work landing at an unpredictable moment, and the
 * poll's `refetchIntervalInBackground: false` meant a user reading a table in an
 * unfocused tab learned about a report only when they next navigated. That is
 * the bug this exists to fix, and it is why `onReport` invalidates rather than
 * writing the count itself: the server's count stays the source of truth.
 *
 * Structured like `use-rates-stream.ts` — same reconnect, same capped backoff —
 * with one addition: this socket is **per user**, so it authenticates. The token
 * goes in the first frame rather than the URL. A browser cannot set an
 * `Authorization` header on a WebSocket, and `?token=` would put an access token
 * in the server log, every proxy log in front of it, and the history entry.
 */
export function useReportStream(
  enabled: boolean,
  onReport: (report: ReportEvent) => void,
): { live: boolean } {
  const [live, setLive] = useState(false);
  const retry = useRef(0);
  // In a ref so a caller that rebuilds its handler every render — the usual
  // case, since it closes over the query client — does not tear the socket down
  // and rebuild it on each one.
  const handler = useRef(onReport);
  useEffect(() => {
    handler.current = onReport;
  }, [onReport]);

  useEffect(() => {
    if (!enabled) return;

    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const open = () => {
      if (closed) return;
      // Same origin, so the socket rides the proxy the REST calls already use.
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(
        `${scheme}://${window.location.host}/api/me/automations/reports/stream`,
      );

      socket.onopen = () => {
        // The token is read here rather than captured when the effect ran: a
        // reconnect after a long sleep must send the *current* one, and the
        // access token rotates on refresh.
        const token = getAccessToken();
        if (!token) {
          // Signed out between opening and now. Close rather than sending a
          // null the server will reject anyway; the backoff handles the retry,
          // by which time `enabled` has usually gone false.
          socket?.close();
          return;
        }
        socket?.send(JSON.stringify({ type: "auth", token }));
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message?.type === "ready") {
            retry.current = 0;
            setLive(true);
            return;
          }
          // A heartbeat. Nothing to do with it: it exists so an idle socket is
          // not reaped by a proxy, and arriving is the whole point.
          if (message?.type === "ping") return;
          if (message?.type === "report" && message.report) {
            handler.current(message.report as ReportEvent);
          }
        } catch {
          // A frame we cannot read is dropped rather than killing the socket:
          // losing one notification beats losing the stream.
        }
      };

      const reopen = () => {
        setLive(false);
        if (closed) return;
        // Backing off, capped. A rejected token closes the socket immediately,
        // so without this a signed-out tab would reconnect in a tight loop.
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

  return { live: enabled && live };
}
