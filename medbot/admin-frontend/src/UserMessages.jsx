// src/UserMessages.jsx
import { useEffect, useMemo, useRef, useState } from "react";

const API = import.meta.env.VITE_API_BASE;

/**
 * Просмотр сообщений одного чата.
 * - Бесконечный скролл вниз (порции по 20)
 * - Поддержка ДВУХ режимов API:
 *   1) Новый:  /admin-api/messages?chat_id=&limit=&order=&before_id=&after_id=  // ⚙️
 *   2) Старый:  /admin-api/chats/{chat_id}/messages?limit=&offset=               // ⚙️
 *   Режим выбирается автоматически по ответу сервера.
 * - 🔴 Если есть фильтр периода (preset/custom), используем СТАРЫЙ эндпойнт,
 *   потому что именно он поддерживает date_from/date_to/period.
 */
export default function UserMessages({
  chatId,
  onBack,
  period,       // 🔴 пресет периода: day|week|month|this_month|prev_month|custom
  dateFrom,     // 🔴 YYYY-MM-DD (для custom)
  dateTo,       // 🔴 YYYY-MM-DD (для custom)
}) {
  const PAGE = 20;                             // размер страницы
  const [items, setItems] = useState([]);      // аккумулированные сообщения
  const [loading, setLoading] = useState(false);     // загрузка текущей порции
  const [hasMore, setHasMore] = useState(true);      // есть ли ещё сообщения ниже
  const [error, setError] = useState(null);          // текст ошибки (если возникла)

  // фильтры и поиск
  const [direction, setDirection] = useState("");     // "", "0", "1"
  const [contentType, setContentType] = useState(""); // "", "text", ...
  const [query, setQuery] = useState("");             // строка ввода
  const [debouncedQ, setDebouncedQ] = useState("");   // фактически применённая строка

  // наблюдатель для бесконечного скролла
  const sentinelRef = useRef(null);   // нижний "якорь"
  const observerRef = useRef(null);   // сам IntersectionObserver

  // курсоры + offset для совместимости
  const [offset, setOffset] = useState(0);            // ⚙️ offset для старого API
  const apiModeRef = useRef("cursor");                // ⚙️ "cursor" | "offset"
  const triedModeRef = useRef(false);                 // ⚙️ чтобы не зациклиться

  const minId = useMemo(
    () => (items.length ? Math.min(...items.map((m) => m.id)) : null),
    [items]
  );
  const maxId = useMemo(
    () => (items.length ? Math.max(...items.map((m) => m.id)) : null),
    [items]
  );

  // 🔴 если задан период (preset или корректный custom) — работаем через offset-API
  useEffect(() => {
    const isCustom = period === "custom"; // 🔴 определяем режим
    const hasDates = dateFrom && dateTo;  // 🔴 обе границы заданы
    if (period && (!isCustom || (isCustom && hasDates))) {
      apiModeRef.current = "offset"; // 🔴 принудительно старый эндпойнт
    } else {
      apiModeRef.current = "cursor"; // 🔴 иначе можно пробовать новый
    }
    // сбрасываем накопленное при смене периода
    setItems([]);           // 🔴 очищаем ленту
    setHasMore(true);       // 🔴 снова есть что грузить
    setError(null);         // 🔴 сбрасываем ошибки
    setOffset(0);           // 🔴 обнуляем offset
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, dateFrom, dateTo]);

  // дебаунс поиска (300мс)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  // при смене чата/фильтров/поиска начинаем сначала
  useEffect(() => {
    setItems([]);
    setHasMore(true);
    setError(null);
    setOffset(0);                        // ⚙️ сбрасываем offset на 0
  }, [chatId, direction, contentType, debouncedQ]);

  // универсальная загрузка порции: сначала пробуем новый API, при 404/405 — старый
  async function loadMore() {
    if (loading || !hasMore) return;
    setLoading(true);
    setError(null);

    try {
      // 🔴 Если период активен — ВСЕГДА используем старый эндпойнт.
      const forceOffset =
        apiModeRef.current === "offset"; // 🔴 флаг вынужденного режима

      if (apiModeRef.current === "cursor" && !forceOffset) {
        const params = new URLSearchParams();
        params.set("chat_id", String(chatId));
        params.set("limit", String(PAGE));
        params.set("order", "desc");
        if (minId) params.set("before_id", String(minId));
        if (debouncedQ) params.set("q", debouncedQ);
        if (direction !== "") params.set("direction", direction);
        if (contentType !== "") params.set("content_type", contentType);

        const r = await fetch(`${API}/messages?${params.toString()}`);
        if (r.status === 404 || r.status === 405) {        // ⚙️ переключаемся на offset-режим
          apiModeRef.current = "offset";
          if (!triedModeRef.current) {
            triedModeRef.current = true;
            setLoading(false);
            return loadMore();                              // ⚙️ повторяем запрос в новом режиме
          }
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const batch = Array.isArray(data.items) ? data.items : [];
        setItems((prev) => [...prev, ...batch]);
        setHasMore(Boolean(data.has_more ?? batch.length === PAGE));
      } else {
        // ⚙️ старый API (теперь с поддержкой фильтров, поиска и контента)
        const url = new URL(`${API}/chats/${chatId}/messages`);
        url.searchParams.set("limit", String(PAGE));
        url.searchParams.set("offset", String(offset));

        // 🔴 Добавляем активные фильтры (бекенд теперь их поддерживает)
        if (debouncedQ) url.searchParams.set("q", debouncedQ);
        if (direction !== "") url.searchParams.set("direction", direction);
        if (contentType !== "") url.searchParams.set("content_type", contentType);

        // 🔴 добавим период/даты (бэкенд поддерживает оба стиля)
        if (period && period !== "custom") {
          url.searchParams.set("period", period); // 🔴 пресет
        } else if (period === "custom" && dateFrom && dateTo) {
          url.searchParams.set("date_from", dateFrom); // 🔴 явные
          url.searchParams.set("date_to", dateTo);     // 🔴 границы
        }
        // старый эндпойнт не поддерживает текстовый поиск и фильтры
        // направления/типа — поэтому их здесь не отправляем.

        const r = await fetch(url.toString());
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const batch = Array.isArray(data.items) ? data.items : data;
        setItems((prev) => [...prev, ...batch]);
        setOffset((prev) => prev + batch.length);          // ⚙️ двигаем offset
        setHasMore(batch.length === PAGE);
      }
    } catch (e) {
      setError(e.message || "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }

  // первая загрузка + реакция на фильтры (после сброса состояния)
  useEffect(() => {
    if (items.length === 0 && hasMore && !loading) {
      loadMore();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length, hasMore, loading, chatId, direction, contentType, debouncedQ]);

  // IntersectionObserver для нижнего якоря
  useEffect(() => {
    if (!sentinelRef.current) return;
    observerRef.current?.disconnect();
    observerRef.current = new IntersectionObserver((entries) => {
      const [entry] = entries;
      if (entry.isIntersecting) loadMore();
    });
    observerRef.current.observe(sentinelRef.current);
    return () => observerRef.current?.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sentinelRef.current, minId, hasMore, direction, contentType, debouncedQ]);

  // ручная подгрузка «сверху» — появилось ли что-то новее
  async function refreshNewer() {
    try {
      if (apiModeRef.current === "cursor") {               // ⚙️ только новый API умеет after_id/asc
        const params = new URLSearchParams();
        params.set("chat_id", String(chatId));
        params.set("limit", String(PAGE));
        params.set("order", "asc");
        if (maxId) params.set("after_id", String(maxId));
        if (debouncedQ) params.set("q", debouncedQ);
        if (direction !== "") params.set("direction", direction);
        if (contentType !== "") params.set("content_type", contentType);

        const r = await fetch(`${API}/messages?${params.toString()}`);
        if (!r.ok) return;                                  // в offset-режиме просто молча выходим
        const data = await r.json();
        const batch = Array.isArray(data.items) ? data.items : [];
        setItems((prev) => [...batch, ...prev]);
      }
    } catch (_) {
      // молча игнорируем
    }
  }

  return (
    <div>
      <button
        onClick={onBack}
        style={{
          margin: "12px 0 16px",
          padding: "8px 12px",
          border: "1px solid #e5e7eb",
          borderRadius: 8,
          background: "#fff",
          cursor: "pointer",
        }}
      >
        ← Назад к списку
      </button>

      {/* Панель фильтров / поиска */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 160px 200px 160px",
          gap: 8,
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по тексту…"
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid #e5e7eb",
          }}
          disabled={false}  // 🔴 backend теперь поддерживает фильтры
        />
        <select
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid #e5e7eb",
          }}
          disabled={false}  // 🔴 backend теперь поддерживает фильтры
        >
          <option value="">Направление: все</option>
          <option value="0">Входящие от пользователя</option>
          <option value="1">Исходящие ассистента</option>
        </select>
        <select
          value={contentType}
          onChange={(e) => setContentType(e.target.value)}
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid #e5e7eb",
          }}
          disabled={false}  // 🔴 backend теперь поддерживает фильтры
        >
          <option value="">Тип контента: все</option>
          <option value="text">text</option>
          <option value="photo">photo</option>
          <option value="voice">voice</option>
          <option value="audio">audio</option>
          <option value="document">document</option>
        </select>

        <button
          onClick={refreshNewer}
          title="Загрузить новые (если появились)"
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid #e5e7eb",
            background: "#fff",
            cursor: "pointer",
          }}
          disabled={false}  // 🔴 backend теперь поддерживает фильтры
        >
          ⤴️ Загрузить новые
        </button>
      </div>

      {/* Лента сообщений */}
      <div
        style={{
          border: "1px solid #e5e7eb",
          borderRadius: 10,
          padding: 12,
          minHeight: 320,
          maxHeight: "65vh",
          overflowY: "auto",
          background: "#fafafa",
        }}
      >
        {items.length === 0 && !loading && !error && (
          <p style={{ margin: 8, opacity: 0.7 }}>
            Нет сообщений за выбранные условия.
          </p>
        )}

        {items.map((m) => (
          <Bubble key={`${m.id}-${m.created_at}`} msg={m} />  // 🔴 уникальный ключ
        ))}

        {error && (
          <p style={{ color: "#b91c1c", marginTop: 8 }}>
            Ошибка: {String(error)}
          </p>
        )}
        {loading && <p style={{ marginTop: 8, opacity: 0.7 }}>Загружаем…</p>}

        <div ref={sentinelRef} style={{ height: 1 }} />
      </div>
    </div>
  );
}

/** Отрисовка одного сообщения в стиле чата */
function Bubble({ msg }) {
  const isOut = Number(msg.direction) === 1;
  const time = new Date(msg.created_at).toLocaleString();

  return (
    <div
      style={{
        display: "flex",
        justifyContent: isOut ? "flex-end" : "flex-start",
        margin: "8px 0",
      }}
      title={`id=${msg.id} • ${time}`}
    >
      <div
        style={{
          maxWidth: "70%",
          padding: "10px 12px",
          borderRadius: 12,
          border: "1px solid #e5e7eb",
          background: isOut ? "#e6f4ff" : "#fff",
          boxShadow: "0 1px 2px rgba(0,0,0,0.03)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        <div
          style={{
            fontSize: 12,
            opacity: 0.6,
            marginBottom: 6,
            textAlign: isOut ? "right" : "left",
          }}
        >
          {isOut ? "Ассистент" : "Пользователь"} • {time}
          {msg.content_type && ` • ${msg.content_type}`}
        </div>

        {msg.text ? (
          <span>{msg.text}</span>
        ) : (
          <i style={{ opacity: 0.7 }}>
            (пустой текст
            {msg.attachment_name ? `, вложение: ${msg.attachment_name}` : ""})
          </i>
        )}
      </div>
    </div>
  );
}
