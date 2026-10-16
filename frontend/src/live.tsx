import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "./auth";
export function LiveStatus() {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const [status, setStatus] = useState("Live updates not connected");
  useEffect(() => {
    if (!identity) return;
    let ended = false;
    let socket: WebSocket | null = null;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    let after = 0;
    async function connect() {
      try {
        const result = await api<{ ticket: string }>({
          url: "/api/v1/events/ticket",
          method: "POST",
        });
        if (ended) return;
        socket = new WebSocket(
          `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/api/v1/events/live`,
        );
        socket.onopen = () => {
          socket?.send(JSON.stringify({ ticket: result.ticket, after }));
          setStatus("Live updates connected");
        };
        socket.onmessage = (message) => {
          try {
            const event = JSON.parse(message.data);
            if (typeof event.sequence === "number") after = event.sequence;
            if (event.type === "invalidate") {
              clearTimeout(refresh);
              refresh = setTimeout(() => {
                void cache.invalidateQueries();
              }, 150);
            }
          } catch {
            setStatus("Invalid live update; refresh manually");
          }
        };
        socket.onclose = () => {
          if (!ended) {
            setStatus("Live disconnected; reconnecting");
            reconnect = setTimeout(() => void connect(), 10000);
          }
        };
        socket.onerror = () => socket?.close();
      } catch {
        if (!ended) {
          setStatus(
            "Live disabled/unavailable; manual refresh remains available",
          );
          reconnect = setTimeout(() => void connect(), 60000);
        }
      }
    }
    void connect();
    return () => {
      ended = true;
      clearTimeout(reconnect);
      clearTimeout(refresh);
      socket?.close();
    };
  }, [identity?.user.id, api, cache]);
  return identity ? <span className="processing-note">{status}</span> : null;
}
