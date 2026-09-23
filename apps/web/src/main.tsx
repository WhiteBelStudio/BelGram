import { FormEvent, useEffect, useState } from "react";
import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Profile = {
  id: number;
  username: string;
  display_name: string;
  avatar_url: string | null;
  bio: string;
  status: string | null;
  is_verified: boolean;
  is_online: boolean;
  last_seen_at: string | null;
  mutual_groups: number;
  mutual_communities: number;
  is_blocked: boolean;
  is_private: boolean;
};

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

function App() {
  const [token, setToken] = useState(localStorage.getItem("belgram_access_token") ?? "");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function loadProfile() {
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API}/profiles/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Не удалось загрузить профиль");
      setProfile(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка подключения");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadProfile(); }, [token]);

  function saveToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    localStorage.setItem("belgram_access_token", token);
    void loadProfile();
  }

  return (
    <main className="shell">
      <section className="workspace">
        <header className="topbar"><strong>BelGram</strong><span>Profiles</span></header>
        {!token ? (
          <form className="token-card" onSubmit={saveToken}>
            <h1>Профиль BelGram</h1>
            <p>Вставь access token текущей сессии, чтобы открыть профиль.</p>
            <input value={token} onChange={(event) => setToken(event.target.value)} placeholder="Access token" />
            <button type="submit">Открыть профиль</button>
          </form>
        ) : loading ? <div className="empty">Загрузка профиля…</div> : error ? <div className="empty error">{error}</div> : profile ? (
          <article className="profile-card">
            <div className="avatar">{profile.avatar_url ? <img src={`${API}${profile.avatar_url}`} alt="" /> : profile.display_name.slice(0, 1).toUpperCase()}</div>
            <div className="profile-main">
              <div className="name-row"><h1>{profile.display_name}</h1>{profile.is_verified && <span className="verified">✓</span>}</div>
              <div className="username">@{profile.username}</div>
              <div className="presence">{profile.is_online ? "● В сети" : profile.last_seen_at ? `Был(а) в сети ${new Date(profile.last_seen_at).toLocaleString()}` : "Не в сети"}</div>
              {profile.status && <div className="status-text">{profile.status}</div>}
              <p className="bio">{profile.bio || "Описание пока не добавлено."}</p>
              <div className="mutuals"><span>Общие группы: {profile.mutual_groups}</span><span>Общие сообщества: {profile.mutual_communities}</span></div>
              <button type="button" onClick={() => { localStorage.removeItem("belgram_access_token"); setToken(""); setProfile(null); }}>Выйти из профиля</button>
            </div>
          </article>
        ) : <div className="empty">Авторизуйся, чтобы открыть профиль.</div>}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
