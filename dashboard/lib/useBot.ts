"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { fetchState, type BotState } from "./api";
import { getDashboardToken } from "./auth";
import { PanelSession } from "./panelSession";

const WS_URL = "ws://127.0.0.1:8090";
const HTTP_FALLBACK_INTERVAL = 500;

export function useBot() {
  const [state, setState] = useState<BotState | null>(null);
  const [mcpConnected, setMcpConnected] = useState(false);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const cancelledRef = useRef(false);
  const session = useRef<PanelSession | null>(null);
  const lastReceived = useRef(-Infinity);
  const accept = useCallback((data:BotState) => {
    const now = performance.now()/1000;
    lastReceived.current = now;
    session.current ??= new PanelSession();
    setState(session.current.observe(data, now));
    setMcpConnected(true);
  }, []);

  const refresh = useCallback(async () => {
    const requested = performance.now()/1000;
    try {
      const data = await fetchState();
      if (cancelledRef.current || lastReceived.current > requested) return;
      accept(data);
    } catch {
      if (cancelledRef.current || lastReceived.current > requested) return;
      setState(null);
      setMcpConnected(false);
    }
  }, [accept]);

  useEffect(() => {
    cancelledRef.current = false;
    let fallbackTimer: ReturnType<typeof setTimeout> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;
    const staleTimer = setInterval(() => {
      if (performance.now()/1000 - lastReceived.current <= 2) return;
      setState(previous => {
        const stats = previous?.session_statistics;
        return previous && stats?.source === "panel" && stats.status === "active"
          ? {...previous,session_statistics:{...stats,status:"stale",xp_per_hour:null}} : previous;
      });
    }, 1000);

    function connectWs() {
      if (disposed) return;

      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = async () => {
        try {
          const token = await getDashboardToken();
          if (!disposed && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ token }));
          }
        } catch { ws.close(); }
      };

      ws.onmessage = (event) => {
        try {
          const data: BotState = JSON.parse(event.data);
          if (disposed) return;
          accept(data);
          if (fallbackTimer) {
            clearTimeout(fallbackTimer);
            fallbackTimer = null;
          }
        } catch {}
      };

      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null;
        if (!disposed) {
          setMcpConnected(false);
          // Fall back to HTTP polling, retry WS after 2s
          startHttpFallback();
          retryTimer = setTimeout(connectWs, 2000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    function startHttpFallback() {
      if (fallbackTimer || disposed) return;
      async function poll() {
        fallbackTimer = null;
        if (disposed || wsRef.current?.readyState === WebSocket.OPEN) return;
        await refresh();
        if (!disposed && wsRef.current?.readyState !== WebSocket.OPEN) {
          fallbackTimer = setTimeout(poll, HTTP_FALLBACK_INTERVAL);
        }
      }
      poll();
    }

    if (window.dbvAuth) connectWs();
    else setDashboardError("Open the Electron dashboard through start_bot. Browser access is disabled.");

    return () => {
      disposed = true;
      clearInterval(staleTimer);
      cancelledRef.current = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (fallbackTimer) clearTimeout(fallbackTimer);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [refresh, accept]);

  return { state, mcpConnected, refresh, dashboardError };
}
