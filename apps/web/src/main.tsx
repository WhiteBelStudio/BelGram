import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
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
type Message = {
  id: number; conversation_id: number; sender_id: number; body: string;
  reply_to_id: number | null; forwarded_from_id: number | null; mentions: string[];
  link_url: string | null; link_preview_title: string | null; created_at: string;
  edited_at: string | null; deleted_at: string | null; delivered_at: string | null;
  read_at: string | null; pinned_at: string | null; reactions: Record<string, number>;
};
type Dialog = { id: number; peer: Profile; last_message: Message | null; unread_count: number; created_at: string };
type EventMessage = { type: string; conversation_id: number; message?: Message; message_id?: number; read_at?: string; delivered_at?: string };

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function api<T>(token: string, path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(API + path, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(options.headers ?? {}) },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail ?? "Ошибка запроса");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

function avatar(profile: Profile) {
  return profile.avatar_url ? `${API}${profile.avatar_url}` : "";
}

function App() {
  const [token, setToken] = useState(localStorage.getItem("belgram_access_token") ?? "");
  const [me, setMe] = useState<Profile | null>(null);
  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [editing, setEditing] = useState<Message | null>(null);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<Message[]>([]);
  const [newUserId, setNewUserId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [profileOpen, setProfileOpen] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [privacy, setPrivacy] = useState({ profile_visibility: "public", show_email: false, show_last_seen: true, show_status: true });
  const [blockedUsers, setBlockedUsers] = useState<Array<{ id: number; username: string; display_name: string; avatar_url: string | null }>>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const draftTimer = useRef<number | null>(null);
  const active = dialogs.find((dialog) => dialog.id === activeId) ?? null;

  const sortedDialogs = useMemo(() => [...dialogs].sort((a, b) => {
    const at = a.last_message?.created_at ?? a.created_at;
    const bt = b.last_message?.created_at ?? b.created_at;
    return bt.localeCompare(at);
  }), [dialogs]);

  async function load() {
    if (!token) return;
    setLoading(true); setError("");
    try {
      const [profile, list] = await Promise.all([
        api<Profile>(token, "/profiles/me"),
        api<Dialog[]>(token, "/messages/dialogs"),
      ]);
      setMe(profile); setProfile(profile); setDialogs(list);
    } catch (e) { setError(e instanceof Error ? e.message : "Ошибка подключения"); }
    finally { setLoading(false); }
  }

  async function openDialog(id: number) {
    setActiveId(id); setReplyTo(null); setEditing(null);
    try {
      const [items, savedDraft] = await Promise.all([
        api<Message[]>(token, `/messages/dialogs/${id}/messages?limit=50`),
        api<{ body: string }>(token, `/messages/dialogs/${id}/draft`),
      ]);
      setMessages(items); setDraft(savedDraft.body);
      await api(token, `/messages/dialogs/${id}/read`, { method: "POST", body: "{}" });
      setDialogs((current) => current.map((d) => d.id === id ? { ...d, unread_count: 0 } : d));
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось открыть диалог"); }
  }

  useEffect(() => { void load(); }, [token]);

  useEffect(() => {
    if (!token) return;
    const url = API.replace(/^http/, "ws") + `/ws?token=${encodeURIComponent(token)}`;
    const socket = new WebSocket(url); wsRef.current = socket;
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data) as EventMessage;
      if (!data.conversation_id) return;
      if (data.type === "message.new" && data.message) {
        setDialogs((current) => current.map((d) => d.id === data.conversation_id ? { ...d, last_message: data.message!, unread_count: d.id === activeId ? 0 : d.unread_count + 1 } : d));
        if (data.conversation_id === activeId) {
          setMessages((current) => current.some((m) => m.id === data.message!.id) ? current : [...current, data.message!]);
          void api(token, `/messages/dialogs/${data.conversation_id}/read`, { method: "POST", body: "{}" });
        }
      }
      if ((data.type === "message.updated" || data.type === "message.reaction" || data.type === "message.pinned") && data.message) {
        setMessages((current) => current.map((m) => m.id === data.message!.id ? data.message! : m));
      }
      if (data.type === "message.deleted" && data.message_id) {
        setMessages((current) => current.map((m) => m.id === data.message_id ? { ...m, body: "", deleted_at: new Date().toISOString() } : m));
      }
      if (data.type === "message.read" && data.read_at) {
        setMessages((current) => current.map((m) => m.sender_id === me?.id ? { ...m, read_at: data.read_at ?? null } : m));
      }
      if (data.type === "message.delivered" && data.message_id) {
        setMessages((current) => current.map((m) => m.id === data.message_id ? { ...m, delivered_at: data.delivered_at ?? new Date().toISOString() } : m));
      }
    };
    socket.onclose = () => { if (wsRef.current === socket) wsRef.current = null; };
    return () => { socket.close(); };
  }, [token, activeId, me?.id]);

  useEffect(() => {
    if (!activeId) return;
    if (draftTimer.current) window.clearTimeout(draftTimer.current);
    draftTimer.current = window.setTimeout(() => {
      void api(token, `/messages/dialogs/${activeId}/draft`, { method: "PUT", body: JSON.stringify({ body: draft }) });
    }, 500);
    return () => { if (draftTimer.current) window.clearTimeout(draftTimer.current); };
  }, [draft, activeId, token]);

  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (!activeId || !draft.trim()) return;
    const body = draft.trim();
    setDraft("");
    try {
      if (editing) {
        const updated = await api<Message>(token, `/messages/dialogs/${activeId}/messages/${editing.id}`, { method: "PATCH", body: JSON.stringify({ body }) });
        setMessages((current) => current.map((m) => m.id === updated.id ? updated : m)); setEditing(null); return;
      }
      const sent = await api<Message>(token, `/messages/dialogs/${activeId}/messages`, { method: "POST", body: JSON.stringify({ body, reply_to_id: replyTo?.id ?? null }) });
      setMessages((current) => current.some((m) => m.id === sent.id) ? current : [...current, sent]);
      setReplyTo(null);
      setDialogs((current) => current.map((d) => d.id === activeId ? { ...d, last_message: sent } : d));
    } catch (e) { setDraft(body); setError(e instanceof Error ? e.message : "Не удалось отправить"); }
  }

  async function react(message: Message, emoji: string) {
    try {
      const updated = await api<Message>(token, `/messages/dialogs/${message.conversation_id}/messages/${message.id}/reaction`, { method: "POST", body: JSON.stringify({ emoji }) });
      setMessages((current) => current.map((m) => m.id === updated.id ? updated : m));
    } catch (e) { setError(e instanceof Error ? e.message : "Ошибка реакции"); }
  }

  async function togglePin(message: Message) {
    try {
      const updated = await api<Message>(token, `/messages/dialogs/${message.conversation_id}/messages/${message.id}/pin`, { method: "POST", body: "{}" });
      setMessages((current) => current.map((m) => m.id === updated.id ? updated : m));
    } catch (e) { setError(e instanceof Error ? e.message : "Ошибка закрепления"); }
  }

  async function remove(message: Message) {
    if (!confirm("Удалить сообщение?")) return;
    try {
      await api(token, `/messages/dialogs/${message.conversation_id}/messages/${message.id}`, { method: "DELETE" });
      setMessages((current) => current.map((m) => m.id === message.id ? { ...m, body: "", deleted_at: new Date().toISOString() } : m));
    } catch (e) { setError(e instanceof Error ? e.message : "Ошибка удаления"); }
  }

  async function forward(message: Message) {
    if (!activeId) return;
    try {
      const sent = await api<Message>(token, `/messages/dialogs/${activeId}/messages`, { method: "POST", body: JSON.stringify({ body: message.body, forwarded_from_id: message.id }) });
      setMessages((current) => [...current, sent]);
    } catch (e) { setError(e instanceof Error ? e.message : "Ошибка пересылки"); }
  }

  async function createDialog(event: FormEvent) {
    event.preventDefault();
    const id = Number(newUserId);
    if (!Number.isInteger(id) || id <= 0) return;
    try {
      const dialog = await api<Dialog>(token, `/messages/dialogs/${id}`, { method: "POST", body: "{}" });
      setDialogs((current) => current.some((d) => d.id === dialog.id) ? current : [dialog, ...current]);
      setNewUserId(""); await openDialog(dialog.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось создать диалог"); }
  }

  async function older() {
    if (!activeId || !messages.length) return;
    try {
      const items = await api<Message[]>(token, `/messages/dialogs/${activeId}/messages?before_id=${messages[0].id}&limit=50`);
      setMessages((current) => [...items, ...current.filter((m) => !items.some((x) => x.id === m.id))]);
    } catch (e) { setError(e instanceof Error ? e.message : "Ошибка загрузки истории"); }
  }

  async function clearHistory() {
    if (!activeId || !confirm("Очистить всю историю этого диалога?")) return;
    await api(token, `/messages/dialogs/${activeId}/history`, { method: "DELETE" });
    setMessages([]); setDialogs((current) => current.map((d) => d.id === activeId ? { ...d, last_message: null } : d));
  }

  async function runSearch(value: string) {
    setSearch(value);
    if (!value.trim()) { setSearchResults([]); return; }
    try {
      const results = await api<{ message: Message; peer: Profile }[]>(token, `/messages/search?q=${encodeURIComponent(value)}`);
      setSearchResults(results.map((item) => item.message));
    } catch { setSearchResults([]); }
  }

  async function openProfile() {
    try {
      const [mine, settings, blocked] = await Promise.all([
        api<Profile>(token, "/profiles/me"),
        api<typeof privacy>(token, "/profiles/me/privacy"),
        api<Array<{ id: number; username: string; display_name: string; avatar_url: string | null; created_at: string }>>(token, "/profiles/me/blocked"),
      ]);
      setProfile(mine); setPrivacy(settings); setBlockedUsers(blocked); setProfileOpen(true);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось открыть профиль"); }
  }

  async function savePrivacy(next: typeof privacy) {
    try { const saved = await api<typeof privacy>(token, "/profiles/me/privacy", { method: "PATCH", body: JSON.stringify(next) }); setPrivacy(saved); setProfile((p) => p ? { ...p, is_private: saved.profile_visibility === "private" } : p); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось сохранить приватность"); }
  }

  async function toggleBlock(userId: number, blocked: boolean) {
    try {
      await api(token, `/profiles/${userId}/block`, { method: blocked ? "DELETE" : "POST", body: blocked ? undefined : "{}" });
      setBlockedUsers((items) => blocked ? items.filter((item) => item.id !== userId) : items);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось изменить блокировку"); }
  }

  function logout() { localStorage.removeItem("belgram_access_token"); setToken(""); setMe(null); setProfile(null); wsRef.current?.close(); }

  if (!token) return <main className="login"><div className="brand">BelGram</div><div className="login-card"><h1>Мессенджер</h1><p>Открой аккаунт и продолжи общение.</p><input value={token} onChange={(e) => setToken(e.target.value)} placeholder="Access token" onKeyDown={(e) => { if (e.key === "Enter") { localStorage.setItem("belgram_access_token", token); } }} /><button onClick={() => { localStorage.setItem("belgram_access_token", token); void load(); }}>Войти</button></div></main>;

  return <main className="messenger">
    <aside className={`sidebar ${activeId ? "mobile-hidden" : ""}`}>
      <div className="side-head"><button className="profile-short" onClick={() => void openProfile()}><div className="mini-avatar">{me && (avatar(me) ? <img src={avatar(me)} alt="" /> : me.display_name.slice(0,1))}</div><div><strong>{me?.display_name ?? "BelGram"}</strong><span>{me?.is_online ? "● в сети" : "offline"}</span></div></button><button className="icon-btn" onClick={logout}>↪</button></div>
      <div className="search"><input value={search} onChange={(e) => void runSearch(e.target.value)} placeholder="Поиск сообщений" /></div>
      <form className="new-dialog" onSubmit={createDialog}><input value={newUserId} onChange={(e) => setNewUserId(e.target.value)} placeholder="ID пользователя" /><button>+</button></form>
      {searchResults.length > 0 && <div className="search-results">{searchResults.map((m) => <button key={m.id} onClick={() => { setSearch(""); setSearchResults([]); void openDialog(m.conversation_id); }}>{m.body || "Удалённое сообщение"}</button>)}</div>}
      <div className="dialogs">{loading ? <div className="muted">Загрузка…</div> : sortedDialogs.map((dialog) => <button className={`dialog ${dialog.id === activeId ? "active" : ""}`} key={dialog.id} onClick={() => void openDialog(dialog.id)}>
        <div className="mini-avatar">{avatar(dialog.peer) ? <img src={avatar(dialog.peer)} alt="" /> : dialog.peer.display_name.slice(0,1)}</div>
        <div className="dialog-copy"><div className="dialog-name">{dialog.peer.display_name}{dialog.peer.is_online && <i />}</div><div className="dialog-last">{dialog.last_message?.body || "Нет сообщений"}</div></div>{dialog.unread_count > 0 && <b className="badge">{dialog.unread_count}</b>}
      </button>)}</div>
    </aside>
    <section className={`chat ${activeId ? "" : "mobile-hidden"}`}>
      {!active ? <div className="welcome"><div className="welcome-logo">B</div><h2>BelGram</h2><p>Выбери диалог или создай новый.</p></div> : <>
        <header className="chat-head"><button className="back" onClick={() => setActiveId(null)}>‹</button><div className="mini-avatar">{avatar(active.peer) ? <img src={avatar(active.peer)} alt="" /> : active.peer.display_name.slice(0,1)}</div><div><strong>{active.peer.display_name}</strong><span>{active.peer.is_online ? "в сети" : "не в сети"}</span></div><button className="profile-open" onClick={() => { setProfile(active.peer); setProfileOpen(true); }}>Профиль</button><button className="clear" onClick={() => void clearHistory()}>Очистить</button></header>
        <div className="messages"><button className="older" onClick={() => void older()}>Загрузить старше</button>{messages.map((message) => <div className={`message-row ${message.sender_id === me?.id ? "mine" : ""}`} key={message.id}>
          <div className={`bubble ${message.deleted_at ? "deleted" : ""}`}>
            {message.reply_to_id && <div className="reply">Ответ на #{message.reply_to_id}</div>}
            {message.forwarded_from_id && <div className="forwarded">↗ Переслано</div>}
            {message.body || "Сообщение удалено"}
            {message.link_preview_title && <a className="link-card" href={message.link_url ?? "#"} target="_blank" rel="noreferrer">{message.link_preview_title}</a>}
            <div className="meta">{new Date(message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}{message.edited_at && " · изм."}{message.sender_id === me?.id && ` · ${message.read_at ? "✓✓" : message.delivered_at ? "✓✓" : "✓"}`}</div>
          </div>
          <div className="message-actions"><button onClick={() => setReplyTo(message)}>↩</button><button onClick={() => void react(message, "❤️")}>♥</button><button onClick={() => void togglePin(message)}>{message.pinned_at ? "★" : "☆"}</button><button onClick={() => void forward(message)}>↗</button>{message.sender_id === me?.id && <><button onClick={() => { setEditing(message); setDraft(message.body); }}>✎</button><button onClick={() => void remove(message)}>×</button></>}</div>
          {Object.entries(message.reactions).length > 0 && <div className="reactions">{Object.entries(message.reactions).map(([emoji, count]) => <button key={emoji} onClick={() => void react(message, emoji)}>{emoji} {count}</button>)}</div>}
        </div>)}</div>
        <form className="composer" onSubmit={(e) => void send(e)}>{(replyTo || editing) && <div className="composer-mode"><span>{editing ? "Редактирование" : `Ответ на #${replyTo?.id}`}</span><button type="button" onClick={() => { setReplyTo(null); setEditing(null); if (editing) setDraft(""); }}>×</button></div>}<div className="compose-row"><textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Написать сообщение…" rows={1} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} /><button type="submit">➤</button></div></form>
      </>}
    </section>
    {profileOpen && profile && <div className="profile-overlay" onClick={() => setProfileOpen(false)}><section className="profile-card" onClick={(e) => e.stopPropagation()}><button className="profile-close" onClick={() => setProfileOpen(false)}>×</button><div className="profile-avatar">{avatar(profile) ? <img src={avatar(profile)} alt="" /> : profile.display_name.slice(0,1)}</div><h2>{profile.display_name}{profile.is_verified && " ✓"}</h2><p className="profile-username">@{profile.username}</p><p>{profile.bio || "Нет описания"}</p>{profile.status && <p className="profile-status">{profile.status}</p>}<div className="profile-stats"><span>{profile.mutual_groups} общих групп</span><span>{profile.mutual_communities} общих сообществ</span></div>{profile.id === me?.id ? <><h3>Приватность</h3><label><input type="checkbox" checked={privacy.profile_visibility === "private"} onChange={(e) => void savePrivacy({ ...privacy, profile_visibility: e.target.checked ? "private" : "public" })} /> Закрытый профиль</label><label><input type="checkbox" checked={privacy.show_last_seen} onChange={(e) => void savePrivacy({ ...privacy, show_last_seen: e.target.checked })} /> Показывать время последнего посещения</label><label><input type="checkbox" checked={privacy.show_status} onChange={(e) => void savePrivacy({ ...privacy, show_status: e.target.checked })} /> Показывать статус</label><h3>Заблокированные</h3>{blockedUsers.length ? blockedUsers.map((item) => <div className="blocked-row" key={item.id}><span>{item.display_name} (@{item.username})</span><button onClick={() => void toggleBlock(item.id, true)}>Разблокировать</button></div>) : <p className="muted">Нет заблокированных пользователей</p></>}<button className="profile-block" onClick={() => void toggleBlock(profile.id, profile.is_blocked)}>{profile.is_blocked ? "Разблокировать" : "Заблокировать"}</button></section></div>}
    {error && <button className="toast" onClick={() => setError("")}>{error} ×</button>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
