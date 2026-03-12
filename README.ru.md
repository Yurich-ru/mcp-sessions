# mcp-sessions

[English version](README.md)

MCP-сервер для поиска по истории переписки Claude Code во всех проектах.

## Возможности

- **Кросс-проектный поиск** — ищите в сессиях любого проекта, не только текущего
- **Умное определение проекта** — короткие имена (`myapp`), полные пути или `*` для всех проектов
- **Полнотекстовый поиск** — AND-сопоставление слов с контекстом вокруг совпадений
- **Интеграция с Git** — просмотр коммитов, сделанных во время сессии
- **Фильтрация системных тегов** — убирает внутренние теги Claude Code из результатов

## Инструменты

| Инструмент | Описание |
|---|---|
| `sessions_projects` | Список всех проектов с историей сессий |
| `sessions_list` | Последние сессии (по проекту) |
| `sessions_search` | Полнотекстовый поиск со сниппетами |
| `sessions_by_file` | Поиск сессий, в которых упоминается файл |
| `session_summary` | Обзор сессии: даты, сообщения, файлы |
| `session_diff` | Git-коммиты за время сессии |
| `session_messages` | Чтение сообщений из сессии |

## Установка

```bash
git clone https://github.com/yurich-ru/mcp-sessions.git
cd mcp-sessions
chmod +x setup.sh
./setup.sh
```

Скрипт выведет JSON-конфигурацию, которую нужно добавить в `~/.claude.json`.

### Ручная установка

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Добавьте в `~/.claude.json`:

```json
{
  "mcpServers": {
    "sessions": {
      "type": "stdio",
      "command": "/path/to/mcp-sessions/venv/bin/python",
      "args": ["/path/to/mcp-sessions/server.py"]
    }
  }
}
```

Перезапустите Claude Code.

## Примеры использования

Инструменты доступны автоматически из Claude Code:

- **«Покажи последние сессии»** → `sessions_list`
- **«Найди в нашей переписке про авторизацию»** → `sessions_search(query="авторизация")`
- **«Поищи обсуждение деплоя в проекте backend»** → `sessions_search(query="deploy", project="backend")`
- **«Поищи во всех проектах про миграции»** → `sessions_search(query="миграция", project="*")`
- **«Какие проекты есть?»** → `sessions_projects`

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `CLAUDE_SESSIONS_DIR` | `~/.claude/projects` | Директория с файлами сессий Claude Code |

## Как это работает

Claude Code хранит историю переписки в JSONL-файлах в `~/.claude/projects/<имя-проекта>/`. Имя директории проекта формируется из пути (например, `/home/user/myapp` → `-home-user-myapp`).

Сервер читает эти файлы и предоставляет инструменты поиска и просмотра через протокол MCP.

## Требования

- Python 3.10+
- Пакет `mcp` (устанавливается автоматически)
- Claude Code (файлы сессий должны существовать в `~/.claude/projects/`)
