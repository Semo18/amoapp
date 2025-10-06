import { useState, useEffect } from "react";
import UserMessages from "./UserMessages"; // просмотр истории сообщений выбранного чата

const API = import.meta.env.VITE_API_BASE;

export default function App() {
  const [tab, setTab] = useState("chat");
  const [chats, setChats] = useState([]);       // всегда массив
  const [summary, setSummary] = useState({});   // объект сводки
  const [loading, setLoading] = useState(false);
  const [selectedChat, setSelectedChat] = useState(null); // текущий выбранный чат
  const [period, setPeriod] = useState("day");            // day/week/month

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        if (tab === "chat") {
          const r = await fetch(`${API}/chats`);
          const data = await r.json(); // ожидаем { total, items: [...] }
          if (!cancelled) {
            setChats(Array.isArray(data?.items) ? data.items : []);
          }
        } else {
          const r = await fetch(`${API}/analytics/summary?period=${period}`);
          const data = await r.json();
          if (!cancelled) setSummary(data ?? {});
        }
      } catch (e) {
        console.error(e);
        if (!cancelled) {
          setChats([]);
          setSummary({});
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [tab, period]);

  return (
    <div
      style={{
        fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        padding: "32px 24px",
        maxWidth: 900,
        margin: "0 auto",
      }}
    >
      <h1 style={{ margin: "8px 0 16px" }}>🩺 MedBot — Админ Панель</h1>

      <nav style={{ marginBottom: 20, display: "flex", gap: 8 }}>
        <button onClick={() => { setTab("chat"); setSelectedChat(null); }} disabled={tab === "chat"}>
          💬 Чаты
        </button>
        <button onClick={() => setTab("analytics")} disabled={tab === "analytics"}>
          📊 Аналитика
        </button>
      </nav>

      {loading && <p>⏳ Загрузка...</p>}

      {/* Вкладка Чаты */}
      {tab === "chat" && !loading && (
        <div>
          {!selectedChat && (
            <>
              <h2 style={{ marginBottom: 12 }}>Список чатов</h2>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {chats.map((c) => (
                  <li
                    key={c.chat_id}
                    style={{
                      padding: "10px 12px",
                      border: "1px solid #e5e7eb",
                      borderRadius: 8,
                      marginBottom: 8,
                      cursor: "pointer",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                    onClick={() => setSelectedChat(c.chat_id)}
                    title="Открыть переписку"
                  >
                    <span>
                      <b>@{c.username || "без_ника"}</b>
                    </span>
                    <span style={{ opacity: 0.7 }}>
                      {c.messages_total ?? 0} всего / {c.messages_in_period ?? 0} за период
                    </span>
                  </li>
                ))}
                {chats.length === 0 && <li style={{ opacity: 0.7 }}>Данных пока нет.</li>}
              </ul>
            </>
          )}

          {selectedChat && (
            <UserMessages chatId={selectedChat} onBack={() => setSelectedChat(null)} />
          )}
        </div>
      )}

      {/* Вкладка Аналитика */}
      {tab === "analytics" && !loading && (
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>Аналитика</h2>
            <div style={{ marginLeft: "auto" }}>
              <label style={{ marginRight: 8 }}>Период:</label>
              <select value={period} onChange={(e) => setPeriod(e.target.value)}>
                <option value="day">День</option>
                <option value="week">Неделя</option>
                <option value="month">Месяц</option>
              </select>
            </div>
          </div>

          <pre style={{ background: "#f8fafc", padding: 12, borderRadius: 8 }}>
            {JSON.stringify(summary, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
