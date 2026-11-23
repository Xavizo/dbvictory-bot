"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchState, type BotState } from "./api";
import { getDashboardToken } from "./auth";
import { PanelSession } from "./panelSession";

const WS_URL = "ws://127.0.0.1:8090";
const POLL_MS = 500;
const STALE_MS = 3000;
const RETRY_MS = 2000;
const REQUEST_TIMEOUT_MS = 5000;

function isSnapshot(data: unknown): data is BotState {
  if (!data || typeof data !== "object") return false;
  const snapshot = data as Partial<BotState>;
  return typeof snapshot.connected === "boolean" && Array.isArray(snapshot.actions)
    && (!snapshot.connected || (typeof snapshot.player?.hp === "number" && typeof snapshot.player.max_hp === "number"));
}

export function useBot() {
  const [state, setState] = useState<BotState | null>(null);
  const [mcpConnected, setMcpConnected] = useState(false);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const session = useRef<PanelSession | null>(null);
  const received = useRef(-Infinity);
  const request = useRef<{controller:AbortController; timeout:ReturnType<typeof setTimeout>} | null>(null);
  const active = useRef(false);

  const accept = useCallback((data: BotState) => {
    const now = performance.now();
    session.current ??= new PanelSession();
    setState(session.current.observe(data, now / 1000));
    received.current = now;
    setMcpConnected(true);
  }, []);

  const readStatus = useCallback(async (afterEdit: boolean) => {
    if (!active.current) return;
    if (request.current) {
      if (!afterEdit) return;
      clearTimeout(request.current.timeout);
      request.current.controller.abort();
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => {
      controller.abort();
      if (request.current?.controller === controller) request.current = null;
    }, REQUEST_TIMEOUT_MS);
    request.current = {controller, timeout};
    try {
      const data = await fetchState(controller.signal);
      if (active.current && !controller.signal.aborted && isSnapshot(data)) accept(data);
    } catch {
      // The watchdog marks the last good state stale regardless of transport.
    } finally {
      clearTimeout(timeout);
      if (request.current?.controller === controller) request.current = null;
    }
  }, [accept]);

  const refresh = useCallback(() => readStatus(true), [readStatus]);

  useEffect(() => {
    if (!window.dbvAuth) {
      setDashboardError("Otwórz panel Electron ze skrótu DBVictory.");
      return;
    }
    active.current = true;
    received.current = -Infinity;
    let disposed = false;
    let socket: WebSocket | null = null;
    let lastFrame = -Infinity;
    let openedAt = performance.now();
    let nextConnect = openedAt;
    let fallback = false;

    function abortRead() {
      if (request.current) {
        clearTimeout(request.current.timeout);
        request.current.controller.abort();
        request.current = null;
      }
    }

    function connect() {
      openedAt = performance.now();
      lastFrame = -Infinity;
      const ws = new WebSocket(WS_URL);
      socket = ws;

      ws.onopen = async () => {
        try {
          const token = await getDashboardToken();
          if (socket === ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({token}));
        } catch { ws.close(); }
      };
      ws.onmessage = event => {
        if (disposed || socket !== ws) return;
        try {
          const data: unknown = JSON.parse(event.data);
          if (!isSnapshot(data)) return;
          abortRead();
          accept(data);
          lastFrame = performance.now();
          fallback = false;
        } catch { /* Keep the clock of the last valid snapshot. */ }
      };
      ws.onclose = () => {
        if (disposed || socket !== ws) return;
        socket = null;
        nextConnect = performance.now() + RETRY_MS;
        fallback = true;
        void readStatus(false);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    const timer = setInterval(() => {
      const now = performance.now();
      if (now - received.current >= STALE_MS) {
        setMcpConnected(false);
        setState(previous => {
          if (!previous || (!previous.connected && previous.session_statistics?.status !== "active")) return previous;
          const stats = previous.session_statistics;
          return {...previous, connected:false, session_statistics: stats?.status === "active"
            ? {...stats, status:"stale", xp_per_hour:null} : stats};
        });
      }

      // A socket can remain OPEN without delivering any data, even before its first frame.
      const quietMs = now - Math.max(openedAt, lastFrame);
      if (quietMs >= STALE_MS) fallback = true;
      if (socket && quietMs >= REQUEST_TIMEOUT_MS) {
        const expired = socket;
        socket = null;
        nextConnect = now + RETRY_MS;
        expired.close();
      }
      if (!socket && now >= nextConnect) connect();
      if (fallback) void readStatus(false);
    }, POLL_MS);

    return () => {
      disposed = true;
      active.current = false;
      clearInterval(timer);
      abortRead();
      socket?.close();
      socket = null;
    };
  }, [accept, readStatus]);

  return { state, mcpConnected, refresh, dashboardError };
}
