import { useEffect, useRef, useCallback, useState } from 'react';
import type { SearchResult } from '../services/api';

export function useWebSocket(jobId: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const [data, setData] = useState<SearchResult | null>(null);
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    if (!jobId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/${jobId}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as SearchResult;
        setData(parsed);
      } catch {
        // ignore malformed messages
      }
    };

    wsRef.current = ws;
  }, [jobId]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  return { data, connected };
}
