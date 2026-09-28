// The browser only ever talks to our backend. It never sees Alpaca keys and
// never sends a price or an order source: the server decides both.

const TOKEN_KEY = "baselinetrading.token";

export const getToken = (): string | null => {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};

export const setToken = (token: string | null): void => {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the token lives only in memory for this tab */
  }
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public reasons: string[] = []) {
    super(message);
  }
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${getToken() ?? ""}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : payload.message;
    throw new ApiError(response.status, detail ?? `HTTP ${response.status}`, payload.reasons ?? []);
  }
  return payload as T;
}

export interface OrderResult {
  ok: boolean;
  message: string;
  reasons: string[];
}

export function statusSocket(onStatus: (s: unknown) => void, onClose: () => void): WebSocket {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws?token=${encodeURIComponent(getToken() ?? "")}`);
  socket.onmessage = (event) => onStatus(JSON.parse(event.data));
  socket.onclose = onClose;
  return socket;
}
