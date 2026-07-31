import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { errorMessage, TOKEN_KEY } from "../utils.js";

function isAnonymousDevActor(actor, token) {
  return !token && actor?.source === "dev_headers" && actor?.user_id === "system";
}

export function useSession() {
  const [token, setToken] = useState(localStorage.getItem(TOKEN_KEY) || "");
  const [actor, setActor] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (authToken = token) => {
    setLoading(true);
    setError("");
    try {
      const session = await api.me(authToken || undefined);
      if (isAnonymousDevActor(session.actor, authToken)) {
        setActor(null);
        return null;
      }
      setActor(session.actor);
      return session.actor;
    } catch (err) {
      setActor(null);
      setError(errorMessage(err));
      return null;
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    let mounted = true;
    refresh(token).finally(() => {
      if (mounted) setLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, [refresh, token]);

  const login = useCallback(async (username, password) => {
    setError("");
    const payload = await api.devLogin({ username, password });
    const nextToken = payload.token || "";
    if (nextToken.startsWith("console.")) {
      localStorage.setItem(TOKEN_KEY, nextToken);
    }
    setToken(nextToken);
    setActor(payload.actor);
    return payload;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken("");
    setActor(null);
  }, []);

  return { token, actor, loading, error, setError, refresh, login, logout };
}
