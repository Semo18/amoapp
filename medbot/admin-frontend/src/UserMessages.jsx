import { useState, useEffect } from "react";

const API = import.meta.env.VITE_API_BASE;

export default function UserMessages({ chatId, onBack }) {
  const [messages, setMessages] = useState([]);

  useEffect(() => {
    async function load() {
      try {
        const r = await fetch(`${API}/analytics/messages/${chatId}`);
        const data = await r.json();
        setMessages(data.items || []);
      } catch (e) {
        console.error(e);
      }
    }
    load();
  }, [chatId]);

  return (
    <div>
      <button onClick={onBack} style={{ marginBottom: 12 }}>← Назад к списку</button>
      <h2 style={{ marginTop: 0 }}>Сообщения пользователя {chatId}</h2>

      <div style={{ display: "grid", gap: 8 }}>
        {messages.map((m) => (
          <div
            key={m.id}
            style={{
              background: m.direction === 0 ? "#eef5ff" : "#f7f7f7",
              borderRadius: 8,
              padding: "10px 12px",
              lineHeight: 1.4,
            }}
          >
            <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>
              {m.direction === 0 ? "👤 Пользователь" : "🤖 Бот"}
            </div>
            <div>{m.text}</div>
          </div>
        ))}
        {messages.length === 0 && <div>Нет сообщений за выбранный период.</div>}
      </div>
    </div>
  );
}
