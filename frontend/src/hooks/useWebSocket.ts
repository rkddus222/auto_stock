import { useEffect, useRef, useState } from 'react';

const WS_BASE = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';
const RECONNECT_MS = 3000;
const HEARTBEAT_MS = 30000;

export type WsMessage = 
  | { type: 'status_update'; payload: unknown }
  | { type: 'trade_event'; symbol: string; side: string; price: number; quantity: number }
  | { type: 'price_update'; payload: unknown };

export interface UseWebSocketOptions {
  onStatusUpdate?: (payload: unknown) => void;
  onTradeEvent?: (msg: { symbol: string; side: string; price: number; quantity: number }) => void;
  onPriceUpdate?: (payload: unknown) => void;
}

export function useWebSocket(options: UseWebSocketOptions = {}) {
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    const url = `${WS_BASE.replace(/^http/, 'ws')}/ws`;
    let closed = false;

    function clearTimers() {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (heartbeatTimerRef.current) {
        clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
    }

    function connect() {
      if (closed) return;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        clearTimers();
        heartbeatTimerRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, HEARTBEAT_MS);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WsMessage;
          if (data.type === 'status_update' && 'payload' in data) {
            optionsRef.current.onStatusUpdate?.(data.payload);
          } else if (data.type === 'trade_event' && 'symbol' in data) {
            optionsRef.current.onTradeEvent?.({
              symbol: data.symbol,
              side: data.side,
              price: data.price,
              quantity: data.quantity,
            });
          } else if (data.type === 'price_update' && 'payload' in data) {
            optionsRef.current.onPriceUpdate?.(data.payload);
          }
        } catch {
          if (event.data === 'pong') return;
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;
        clearTimers();
        if (!closed) {
          reconnectTimerRef.current = setTimeout(connect, RECONNECT_MS);
        }
      };

      ws.onerror = () => {};
    }

    connect();
    return () => {
      closed = true;
      clearTimers();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setIsConnected(false);
    };
  }, []);

  return { isConnected };
}
