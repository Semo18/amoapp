-- users: справочник пользователей телеграма
CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT UNIQUE NOT NULL,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  language_code VARCHAR(8),
  is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  messages_total INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_users_last_seen_at ON users (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);

-- messages: лента сообщений (вход/выход)
CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
  direction SMALLINT NOT NULL,      -- 0=in (от пользователя), 1=out (наш ответ)
  message_id BIGINT,
  text TEXT,
  content_type VARCHAR(32) NOT NULL DEFAULT 'text',  -- text|photo|voice|audio|document|system
  attachment_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_created ON messages (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_in ON messages (chat_id) WHERE direction = 0;
CREATE INDEX IF NOT EXISTS idx_messages_out ON messages (chat_id) WHERE direction = 1;

-- user_daily_stats: агрегаты по дням
CREATE TABLE IF NOT EXISTS user_daily_stats (
  day DATE NOT NULL,
  chat_id BIGINT NOT NULL,
  messages_in INTEGER NOT NULL DEFAULT 0,
  messages_out INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_user_daily_stats_day ON user_daily_stats (day);
