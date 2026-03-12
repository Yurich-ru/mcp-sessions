# mcp-sessions

MCP server for searching Claude Code conversation history across all projects.

## Features

- **Cross-project search** — search sessions in any project, not just the current one
- **Smart project resolution** — use short names (`myapp`), full paths, or `*` for all projects
- **Full-text search** — AND-matching across sessions with snippet context
- **Git integration** — see commits made during a session
- **System tag filtering** — strips Claude Code internal tags from displayed content

## Tools

| Tool | Description |
|---|---|
| `sessions_projects` | List all projects with session history |
| `sessions_list` | List recent sessions (by project) |
| `sessions_search` | Full-text search with snippets |
| `sessions_by_file` | Find sessions mentioning a file |
| `session_summary` | Session overview: dates, messages, files |
| `session_diff` | Git commits during a session |
| `session_messages` | Read raw messages from a session |

## Install

```bash
git clone https://github.com/yurich-ru/mcp-sessions.git
cd mcp-sessions
chmod +x setup.sh
./setup.sh
```

The setup script will print the JSON config to add to your `~/.claude.json`.

### Manual setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Add to `~/.claude.json`:

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

Restart Claude Code.

## Usage examples

From Claude Code, the tools are available automatically:

- **"Show my recent sessions"** → `sessions_list`
- **"Search our conversations about auth"** → `sessions_search(query="auth")`
- **"Find discussions about auth in project backend"** → `sessions_search(query="auth", project="backend")`
- **"Search all projects for deploy issues"** → `sessions_search(query="deploy", project="*")`
- **"What projects have session history?"** → `sessions_projects`

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_SESSIONS_DIR` | `~/.claude/projects` | Directory where Claude Code stores session files |

## How it works

Claude Code stores conversation history as JSONL files in `~/.claude/projects/<project-name>/`. Each project directory name is derived from the filesystem path (e.g., `/home/user/myapp` → `-home-user-myapp`).

This server reads those files and provides search/browse tools via the MCP protocol.

## Requirements

- Python 3.10+
- `mcp` package (installed automatically)
- Claude Code (session files must exist in `~/.claude/projects/`)
