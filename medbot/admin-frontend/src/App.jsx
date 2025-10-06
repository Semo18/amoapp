import { useState, useEffect } from "react";
import UserMessages from "./UserMessages"; // просмотр истории сообщений выбранного чата

const API = import.meta.env.VITE_API_BASE;

export default function App() {
  const [tab, setTab] = useState("chat");
  const [chats, setChats] = useState([]);
  const [summary, setSummary] = useState({});
  const [loading, setLoading] = useState(false);
  const [selectedChat, setSelectedChat] = useState(null); // текущий выбранный чат
  const [period, setPeriod] = useState("day");            // период для аналитики: day/week/month

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        if (tab === "chat") {
          const r = await fetch(`${API}/chats`);
          setChats(await r.json());
        } else {
          const r = await fetch(`${API}/analytics/summary?period=${period}`);
          setSummary(await r.json());
        }
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [tab, period]);

  return (
    <div
      style={{
        fontFamily: "system-ui, sans-serif",
        padding: "40px 60px",
        maxWidth: 900,
        margin: "0 auto",
      }}
    >
      <h1 style={{ marginBottom: 16 }}>🩺 MedBot — Админ Панель</h1>

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
                    <span style={{ opacity: 0.7 }}>{c.message_count} сообщений</span>
                  </li>
                ))}
                {chats.length === 0 && <li>Данных пока нет.</li>}
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

          {/* простая сводка; позже заменим на карточки/графики */}
          <pre style={{ background: "#f8fafc", padding: 12, borderRadius: 8 }}>
            {JSON.stringify(summary, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
