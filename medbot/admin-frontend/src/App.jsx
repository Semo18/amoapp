// src/App.jsx
import { useState, useEffect, useMemo } from "react"; // хук-состояния/эффект
// 🔴 добавил useMemo для вычисляемого флага isCustomReady
import UserMessages from "./UserMessages"; // вложенный компонент переписки

// Базовый URL API берём из Vite env; падение назад — пустая строка.
// Это позволяет безопасно формировать URL даже без .env.
const API = import.meta.env.VITE_API_BASE || ""; // 🔴 fallback добавлен

export default function App() {
  // Текущая вкладка интерфейса: «chat» или «analytics».
  const [tab, setTab] = useState("chat");

  // Кэш чатов за выбранный период для списка.
  const [chats, setChats] = useState([]);

  // Сводка аналитики за период (агрегированные числа).
  const [summary, setSummary] = useState({});

  // Флаг фоновой загрузки (общий для вкладок).
  const [loading, setLoading] = useState(false);

  // Выбранный чат (id) для просмотра переписки; null — список.
  const [selectedChat, setSelectedChat] = useState(null);

  // Выбранный период фильтрации данных.
  // Поддерживаем предустановки и кастомный интервал.
  const [period, setPeriod] = useState("day");

  // Поля дат для кастомного интервала (date inputs).
  const [dateFrom, setDateFrom] = useState(""); // формат YYYY-MM-DD
  const [dateTo, setDateTo] = useState(""); // формат YYYY-MM-DD

  // Вычисляем, «готов» ли кастомный период: обе даты заданы
  // и правая граница позже левой. Это отфильтровывает
  // полузаданные и некорректные интервалы до обращения к API.
  const isCustomReady = useMemo(() => { // 🔴 useMemo вместо инлайн-выраж.
    // Нестандартные пресеты всегда «готовы».
    if (period !== "custom") return true;
    // Для custom обе даты должны быть заданы и валидны.
    if (!dateFrom || !dateTo) return false;
    // Сравниваем как даты; date_to строго больше date_from.
    return new Date(dateTo) > new Date(dateFrom);
  }, [period, dateFrom, dateTo]); // зависим от входов вычисления

  // Утилита сборки query-параметров под оба режима.
  // Возвращаем URLSearchParams, чтобы одинаково использовать
  // его с /chats и /analytics/summary.
  function buildPeriodParams() {
    const params = new URLSearchParams(); // пустой набор параметров
    if (period === "custom") {
      // Для custom передаём явные границы (бек ожидает именно их).
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
    } else {
      // Для пресетов — один параметр «period».
      params.set("period", period);
    }
    return params; // унифицированный результат
  }

  useEffect(() => {
    // Аборт-контроллер позволяет отменять fetch при размонтаже
    // и смене зависимостей, чтобы избежать гонок и setState
    // на размонтированном компоненте.
    const ac = new AbortController(); // 🔴 вместо флага cancelled

    // Единый загрузчик: в зависимости от вкладки тянем разные
    // ресурсы, но одинаково применяем период и обработку ошибок.
    async function load() {
      // Если кастомный период «сырой», сбрасываем данные
      // и не делаем сетевые запросы (ранний выход).
      if (!isCustomReady) {
        setChats([]); // пустые данные, чтобы UI не путал
        setSummary({}); // сброс агрегатов
        setLoading(false); // снимаем индикатор загрузки
        return; // выходим до fetch
      }

      setLoading(true); // включаем индикатор перед запросом
      try {
        const p = buildPeriodParams(); // собираем параметры периода
        if (tab === "chat") {
          // Для списка чатов получаем элементы с учётом периода.
          const url = `${API}/chats?${p.toString()}`; // итоговый URL
          const r = await fetch(url, { signal: ac.signal }); // 🔴 abortable
          if (!r.ok) {
            // Явно бросаем на non-2xx, чтобы попасть в catch.
            throw new Error(`HTTP ${r.status} ${r.statusText}`); // 🔴
          }
          const data = await r.json(); // JSON-ответ API
          // Обновляем список, нормализуя неверные структуры.
          setChats(Array.isArray(data?.items) ? data.items : []); // safe
        } else {
          // Для аналитики запрашиваем агрегированную сводку.
          const url = `${API}/analytics/summary?${p.toString()}`;
          const r = await fetch(url, { signal: ac.signal }); // 🔴 abortable
          if (!r.ok) {
            throw new Error(`HTTP ${r.status} ${r.statusText}`); // 🔴
          }
          const data = await r.json(); // JSON-ответ со сводкой
          setSummary(data ?? {}); // безопасный дефолт
        }
      } catch (e) {
        // Игнорируем ошибки отмены; остальные — логируем и сбрасываем.
        if (e.name !== "AbortError") { // 🔴 фильтруем cancel
          console.error(e); // диагностика проблем сети/сервера
          setChats([]); // сброс списка
          setSummary({}); // сброс сводки
        }
      } finally {
        // Завершаем индикатор, если запрос не был отменён.
        if (!ac.signal.aborted) setLoading(false); // 🔴 guard
      }
    }

    // Точка входа: загружаем при каждой смене вкладки/периода/дат.
    load(); // инициируем загрузку

    // Отмена запроса при размонтаже/переключении зависимостей.
    return () => ac.abort(); // 🔴 корректная отмена fetch
  }, [tab, period, dateFrom, dateTo, isCustomReady]); // зависимости эффекта

  // Рендерим простой макет: заголовок, навигация, контент текущей вкладки.
  return (
    <div
      // Базовые отступы и ограничение ширины для читаемости.
      style={{
        fontFamily:
          "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        padding: "32px 24px",
        maxWidth: 900,
        margin: "0 auto",
      }}
    >
      {/* Заголовок панели администратора медбота. */}
      <h1 style={{ margin: "8px 0 16px" }}>🩺 MedBot — Админ Панель</h1>

      {/* Простая навигация: две кнопки-вкладки. */}
      <nav style={{ marginBottom: 20, display: "flex", gap: 8 }}>
        <button
          // Переключение на список чатов и сброс выбранного чата.
          onClick={() => {
            setTab("chat");
            setSelectedChat(null);
          }}
          disabled={tab === "chat"} // блокируем активную кнопку
        >
          💬 Чаты
        </button>
        <button
          // Переключение на вкладку аналитики.
          onClick={() => setTab("analytics")}
          disabled={tab === "analytics"} // блокируем активную кнопку
        >
          📊 Аналитика
        </button>
      </nav>

      {/* Универсальный индикатор фоновой загрузки. */}
      {loading && <p>⏳ Загрузка...</p>}

      {/* Вкладка «Чаты»: список или конкретный диалог. */}
      {tab === "chat" && !loading && (
        <div>
          {/* Пока чат не выбран — показываем список. */}
          {!selectedChat && (
            <>
              <h2 style={{ marginBottom: 12 }}>Список чатов</h2>

              {/* Панель выбора периода для списка чатов. */}
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "center",
                  margin: "0 0 12px",
                }}
              >
                <label style={{ opacity: 0.8 }}>Период:</label>
                <select
                  // Контролируемый select со значением периода.
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                >
                  <option value="day">День</option>
                  <option value="week">Неделя</option>
                  <option value="month">30 дней</option>
                  <option value="this_month">Текущий месяц</option>
                  <option value="prev_month">Прошлый месяц</option>
                  <option value="custom">Произвольный период</option>
                </select>

                {/* При custom — показываем поля дат. */}
                {period === "custom" && (
                  <>
                    <input
                      type="date"
                      value={dateFrom}
                      onChange={(e) => setDateFrom(e.target.value)}
                      title="Дата начала (включительно)"
                    />
                    <input
                      type="date"
                      value={dateTo}
                      onChange={(e) => setDateTo(e.target.value)}
                      title="Дата конца (исключительно)"
                    />
                  </>
                )}
              </div>

              {/* Подсказка, если custom выбран, но даты ещё не заданы. */}
              {period === "custom" && !isCustomReady && (
                <p style={{ marginTop: -4, marginBottom: 8, opacity: 0.7 }}>
                  Укажите обе даты, чтобы загрузить данные.
                </p>
              )}

              {/* Простой список чатов; кликом открываем переписку. */}
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {chats.map((c) => (
                  <li
                    key={c.chat_id} // стабильный ключ для списка
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
                      {c.messages_total ?? 0} всего /{" "}
                      {c.messages_in_period ?? 0} за период
                    </span>
                  </li>
                ))}
                {/* Пустое состояние для готового периода без данных. */}
                {chats.length === 0 && isCustomReady && (
                  <li style={{ opacity: 0.7 }}>Данных пока нет.</li>
                )}
              </ul>
            </>
          )}

          {/* Когда чат выбран — рендерим компонент переписки. */}
          {selectedChat && (
            <UserMessages
              chatId={selectedChat} // id чата для бэкенда
              onBack={() => setSelectedChat(null)} // возврат к списку
              period={period} // 🔴 передаём выбранный пресет периода
              dateFrom={dateFrom} // 🔴 и явные границы для custom
              dateTo={dateTo} // 🔴
            />
          )}
        </div>
      )}

      {/* Вкладка «Аналитика»: фильтры + «человеческая» сводка. */}
      {tab === "analytics" && !loading && (
        <div>
          {/* Заголовок и панель фильтра справа. */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 12,
            }}
          >
            <h2 style={{ margin: 0 }}>Аналитика</h2>

            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <label style={{ marginRight: 4 }}>Период:</label>
              <select
                // Контролируемый select периода для сводки.
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
              >
                <option value="day">День</option>
                <option value="week">Неделя</option>
                <option value="month">30 дней</option>
                <option value="this_month">Текущий месяц</option>
                <option value="prev_month">Прошлый месяц</option>
                <option value="custom">Произвольный период</option>
              </select>

              {/* При custom — показываем поля дат. */}
              {period === "custom" && (
                <>
                  <input
                    type="date"
                    value={dateFrom}
                    onChange={(e) => setDateFrom(e.target.value)}
                    title="Дата начала (включительно)"
                  />
                  <input
                    type="date"
                    value={dateTo}
                    onChange={(e) => setDateTo(e.target.value)}
                    title="Дата конца (исключительно)"
                  />
                </>
              )}
            </div>
          </div>

          {/* Хинт, если custom выбран, но границы не заданы. */}
          {period === "custom" && !isCustomReady && (
            <p style={{ marginTop: -4, marginBottom: 12, opacity: 0.7 }}>
              Укажите обе даты, чтобы увидеть сводку.
            </p>
          )}

          {/* «Человеческая» сводка агрегатов по периоду. */}
          {isCustomReady && (
            <div
              style={{
                background: "#f8fafc",
                padding: 16,
                borderRadius: 8,
                border: "1px solid #e5e7eb",
                lineHeight: 1.6,
                maxWidth: 520,
              }}
            >
              <p style={{ margin: 0 }}>
                <b>Всего пользователей:</b> {summary.users_total ?? 0}
              </p>
              <p style={{ margin: 0 }}>
                <b>Всего сообщений:</b> {summary.messages_total ?? 0}
              </p>
              <p style={{ margin: 0 }}>
                <b>Исходящих (бот → пользователю):</b>{" "}
                {summary.messages_out ?? 0}
              </p>
              <p style={{ margin: 0 }}>
                <b>Входящих (пользователь → боту):</b>{" "}
                {summary.messages_in ?? 0}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
