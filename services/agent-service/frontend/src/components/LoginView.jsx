import { useEffect, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { api } from "../api.js";
import { errorMessage } from "../utils.js";

export function LoginView({ onLogin, sessionError }) {
  const [accounts, setAccounts] = useState([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("123456");
  const [error, setError] = useState(sessionError || "");
  const [loading, setLoading] = useState(true);
  const devLoginAvailable = accounts.length > 0;

  useEffect(() => {
    let mounted = true;
    api.devAccounts()
      .then((payload) => {
        if (!mounted) return;
        const rows = Array.isArray(payload?.accounts) ? payload.accounts : [];
        setAccounts(rows);
        setUsername(String(rows[0]?.username || rows[0]?.user_id || ""));
      })
      .catch(() => {
        if (mounted) setAccounts([]);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await onLogin(username, password);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-panel" onSubmit={submit}>
        <div className="brand-row">
          <ShieldCheck size={22} />
          <div>
            <h1>Agent 客户自助</h1>
            <p>{devLoginAvailable ? "本地开发登录" : "等待用户中心登录"}</p>
          </div>
        </div>
        {devLoginAvailable ? (
          <>
            <label>
              账号
              <select value={username} onChange={(event) => setUsername(event.target.value)} disabled={loading}>
                {accounts.map((account) => (
                  <option key={`${account.tenant_id}:${account.user_id}`} value={account.username || account.user_id}>
                    {account.label || account.display_name || account.username || account.user_id}
                  </option>
                ))}
              </select>
            </label>
            <label>
              密码
              <input value={password} type="password" onChange={(event) => setPassword(event.target.value)} />
            </label>
          </>
        ) : (
          <div className="notice">生产模式请先在统一用户中心完成登录，或由网关注入同域会话后刷新本页。</div>
        )}
        {error ? <div className="inline-error">{error}</div> : null}
        {devLoginAvailable ? (
          <button className="primary-button" type="submit" disabled={loading || !username}>
            {loading ? <Loader2 className="spin" size={16} /> : <ShieldCheck size={16} />}
            登录
          </button>
        ) : null}
      </form>
    </main>
  );
}
