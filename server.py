#!/usr/bin/env python3
"""Global MCP Server for searching Claude Code session history (v1.0).

Works across all projects. Auto-detects current project from CWD,
supports explicit project selection and cross-project search.
"""

import os
import sys
import json
import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Configuration
SESSIONS_DIR = Path(os.path.expanduser(os.getenv("CLAUDE_SESSIONS_DIR", "~/.claude/projects")))

# Logging
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.log")
logger = logging.getLogger("sessions-mcp")
logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler.setFormatter(formatter)
stderr_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(stderr_handler)

server = Server("sessions")

# ── Helpers ──────────────────────────────────────────────────────────────────

_SYSTEM_TAGS_RE = re.compile(
    r'<(?:local-command-caveat|system-reminder|user-prompt-submit-hook|available-deferred-tools|command-name|command-message|command-args|local-command-stdout)[^>]*>.*?'
    r'</(?:local-command-caveat|system-reminder|user-prompt-submit-hook|available-deferred-tools|command-name|command-message|command-args|local-command-stdout)>',
    re.DOTALL,
)


def _strip_system_tags(text: str) -> str:
    return _SYSTEM_TAGS_RE.sub('', text).strip()


def _path_to_project_name(path: str) -> str:
    """Convert filesystem path to Claude project directory name.

    /home/user/projects/myapp -> -home-user-projects-myapp
    """
    return path.replace("/", "-")


def _project_name_to_path(name: str) -> str:
    """Convert Claude project directory name back to filesystem path.

    -home-user-projects-myapp -> /home/user/projects/myapp
    """
    return "/" + name.lstrip("-").replace("-", "/") if name.startswith("-") else name


def _project_name_to_short(name: str) -> str:
    """Extract short project name for display.

    -home-user-projects-myapp -> myapp
    """
    parts = name.lstrip("-").split("-")
    # Find index after "project" or "projects" keyword
    for keyword in ("project", "projects"):
        try:
            idx = parts.index(keyword)
            return "-".join(parts[idx + 1:]) if idx + 1 < len(parts) else name
        except ValueError:
            continue
    return name


def _resolve_project(project: str | None) -> str | None:
    """Resolve project argument to a project directory name.

    Accepts:
    - None -> None (caller decides default)
    - "myapp" -> finds matching project dir by short name
    - "-home-user-projects-myapp" -> used as-is
    - "/home/user/projects/myapp" -> converted to dir name
    """
    if not project:
        return None

    # Full directory name
    if (SESSIONS_DIR / project).is_dir():
        return project

    # Full filesystem path
    if project.startswith("/"):
        name = _path_to_project_name(project)
        if (SESSIONS_DIR / name).is_dir():
            return name
        return None

    # Short name — search for matching project (exact suffix)
    for d in SESSIONS_DIR.iterdir():
        if d.is_dir() and d.name.endswith(f"-{project}"):
            return d.name

    # Partial match
    for d in SESSIONS_DIR.iterdir():
        if d.is_dir() and project in d.name:
            return d.name

    return None


def _all_projects() -> list[str]:
    """List all project directory names, sorted by most recent session."""
    if not SESSIONS_DIR.exists():
        return []
    return sorted(
        [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir() and list(d.glob("*.jsonl"))],
        key=lambda n: max(
            (f.stat().st_mtime for f in (SESSIONS_DIR / n).glob("*.jsonl")),
            default=0,
        ),
        reverse=True,
    )


def _project_dir(project: str | None) -> Path:
    resolved = _resolve_project(project)
    if resolved:
        return SESSIONS_DIR / resolved
    return SESSIONS_DIR / "__none__"


def _session_files(project: str | None = None) -> list[Path]:
    d = _project_dir(project)
    if not d.exists():
        return []
    return sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)


def _git_dir_for_project(project_name: str) -> str | None:
    """Derive git directory from project name."""
    path = _project_name_to_path(project_name)
    if os.path.isdir(os.path.join(path, ".git")):
        return path
    return None


def _parse_messages(path: Path) -> list[dict]:
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") not in ("user", "assistant"):
                continue
            msg = entry.get("message", {})
            role = msg.get("role", entry.get("type"))
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, str):
                        text_parts.append(block)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                content = "\n".join(text_parts)
            if not content.strip():
                continue
            ts = entry.get("timestamp")
            messages.append({"role": role, "content": content, "timestamp": ts})
    return messages


def _session_meta(path: Path) -> dict | None:
    first_user_msg = None
    msg_count = 0
    first_ts = None
    last_ts = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") not in ("user", "assistant"):
                continue
            msg_count += 1
            ts = entry.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            if first_user_msg is None and entry.get("type") == "user":
                msg = entry.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, str):
                            cleaned = _strip_system_tags(block)
                            if cleaned:
                                first_user_msg = cleaned[:200]
                                break
                        elif isinstance(block, dict) and block.get("type") == "text":
                            cleaned = _strip_system_tags(block.get("text", ""))
                            if cleaned:
                                first_user_msg = cleaned[:200]
                                break
                elif isinstance(content, str):
                    cleaned = _strip_system_tags(content)
                    if cleaned:
                        first_user_msg = cleaned[:200]
    if msg_count == 0:
        return None
    return {
        "session_id": path.stem,
        "date": first_ts or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "first_message": first_user_msg or "(no text)",
        "message_count": msg_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def _extract_content(entry: dict) -> str:
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        )
    return content


def _line_matches_words(line_lower: str, words: list[str]) -> bool:
    return any(w in line_lower for w in words)


def _session_matches_words(path: Path, words: list[str]) -> bool:
    found = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            ll = line.lower()
            for w in words:
                if w in ll:
                    found.add(w)
            if len(found) == len(words):
                return True
    return False


def _search_in_project(project_name: str, words: list[str], limit: int) -> list[dict]:
    """Search sessions in a single project."""
    results = []
    for path in _session_files(project_name):
        if not _session_matches_words(path, words):
            continue

        matches = []
        first_user_msg = None
        first_ts = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line_lower = line.lower()
                if first_user_msg is None:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("type") == "user":
                            c = _strip_system_tags(_extract_content(entry))
                            if c:
                                first_user_msg = c[:150]
                                first_ts = entry.get("timestamp")
                    except json.JSONDecodeError:
                        pass

                if _line_matches_words(line_lower, words):
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("type") in ("user", "assistant"):
                            content = _extract_content(entry)
                            content_lower = content.lower()
                            for w in words:
                                idx = content_lower.find(w)
                                if idx >= 0:
                                    start = max(0, idx - 80)
                                    end = min(len(content), idx + len(w) + 80)
                                    matches.append(f"  ...{content[start:end]}...")
                                    break
                    except json.JSONDecodeError:
                        pass

        if matches:
            results.append({
                "project": _project_name_to_short(project_name),
                "project_full": project_name,
                "session_id": path.stem,
                "date": first_ts or "?",
                "first_message": first_user_msg or "?",
                "match_count": len(matches),
                "snippets": matches[:3],
            })
        if len(results) >= limit:
            break
    return results


# ── Tools ────────────────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="sessions_projects",
        description="GLOBAL: List ALL projects that have Claude Code session history. This server has access to sessions from ALL projects, not just the current one.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="sessions_list",
        description="GLOBAL: List recent Claude Code sessions from ANY project.",
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project short name, full dir name, or filesystem path. Omit for current project."},
                "days": {"type": "integer", "description": "How many days back to look (default: 30)", "default": 30},
                "limit": {"type": "integer", "description": "Max sessions to return (default: 20)", "default": 20},
            },
        },
    ),
    Tool(
        name="sessions_search",
        description="GLOBAL: Search Claude Code sessions by keyword across ANY or ALL projects. Has access to conversation history from all projects.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (words are AND-matched across session)"},
                "project": {"type": "string", "description": "Project short name, or '*'/'all' to search ALL projects. Omit for current project."},
                "limit": {"type": "integer", "description": "Max results (default: 10)", "default": 10},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="sessions_by_file",
        description="GLOBAL: Find sessions that mention a specific file path, across ANY or ALL projects.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File path to search for"},
                "project": {"type": "string", "description": "Project short name, or '*'/'all' to search ALL projects. Omit for current project."},
                "limit": {"type": "integer", "description": "Max results (default: 10)", "default": 10},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="session_summary",
        description="GLOBAL: Get session overview from ANY project: dates, message count, first/last user message, files mentioned.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session UUID"},
                "project": {"type": "string", "description": "Project short name. Omit to auto-detect."},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="session_diff",
        description="GLOBAL: Get git commits made during a session's time window, from ANY project.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session UUID"},
                "project": {"type": "string", "description": "Project short name. Omit to auto-detect."},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="session_messages",
        description="GLOBAL: Get raw messages from a session in ANY project, optionally filtered by query.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session UUID"},
                "query": {"type": "string", "description": "Optional filter query"},
                "limit": {"type": "integer", "description": "Max messages (default: 50)", "default": 50},
                "project": {"type": "string", "description": "Project short name. Omit to auto-detect."},
            },
            "required": ["session_id"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    logger.info(f"Tool call: {name}({json.dumps(arguments, ensure_ascii=False)[:200]})")
    try:
        result = await _dispatch(name, arguments)
        logger.info(f"Tool {name} OK, {len(result)} chars")
        return [TextContent(type="text", text=result)]
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict) -> str:
    if name == "sessions_projects":
        return _handle_projects(args)
    elif name == "sessions_list":
        return _handle_sessions_list(args)
    elif name == "sessions_search":
        return _handle_sessions_search(args)
    elif name == "sessions_by_file":
        return _handle_sessions_by_file(args)
    elif name == "session_summary":
        return _handle_session_summary(args)
    elif name == "session_diff":
        return _handle_session_diff(args)
    elif name == "session_messages":
        return _handle_session_messages(args)
    else:
        return f"Unknown tool: {name}"


# ── Tool implementations ─────────────────────────────────────────────────────

def _handle_projects(args: dict) -> str:
    projects = _all_projects()
    if not projects:
        return "No projects found."
    lines = []
    for p in projects:
        short = _project_name_to_short(p)
        session_count = len(list((SESSIONS_DIR / p).glob("*.jsonl")))
        lines.append(f"- **{short}** ({session_count} sessions) `{p}`")
    return "\n".join(lines)


def _handle_sessions_list(args: dict) -> str:
    project = args.get("project")
    days = args.get("days", 30)
    limit = args.get("limit", 20)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    resolved = _resolve_project(project)
    if project and not resolved:
        return f"Project '{project}' not found. Use sessions_projects to list available projects."

    results = []
    for path in _session_files(resolved):
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            break
        meta = _session_meta(path)
        if meta:
            results.append(meta)
        if len(results) >= limit:
            break

    if not results:
        return "No sessions found."
    lines = []
    for s in results:
        lines.append(f"**{s['session_id']}**\n  Date: {s['date']}\n  Messages: {s['message_count']}\n  First: {s['first_message']}")
    return "\n\n".join(lines)


def _is_all_projects(project: str | None) -> bool:
    return project in ("*", "all")


def _handle_sessions_search(args: dict) -> str:
    query = args["query"]
    project = args.get("project")
    limit = args.get("limit", 10)
    words = [w.lower() for w in query.split() if w.strip()]
    if not words:
        return "Empty query."

    if _is_all_projects(project):
        # Search across all projects — collect from each, then sort by date
        all_results = []
        per_project_limit = max(limit, 5)
        for proj_name in _all_projects():
            results = _search_in_project(proj_name, words, per_project_limit)
            all_results.extend(results)
        all_results.sort(key=lambda r: r.get("date", ""), reverse=True)
        all_results = all_results[:limit]
        if not all_results:
            return f"No sessions found matching '{query}' in any project."
        lines = []
        for r in all_results:
            snippets = "\n".join(r["snippets"])
            lines.append(f"**[{r['project']}] {r['session_id']}**\n  Date: {r['date']}\n  First: {r['first_message']}\n  Matches: {r['match_count']}\n{snippets}")
        return "\n\n".join(lines)
    else:
        resolved = _resolve_project(project)
        if project and not resolved:
            return f"Project '{project}' not found. Use sessions_projects to list available projects."
        results = _search_in_project(resolved, words, limit)
        if not results:
            return f"No sessions found matching '{query}'."
        lines = []
        for r in results:
            snippets = "\n".join(r["snippets"])
            lines.append(f"**{r['session_id']}**\n  Date: {r['date']}\n  First: {r['first_message']}\n  Matches: {r['match_count']}\n{snippets}")
        return "\n\n".join(lines)


def _handle_sessions_by_file(args: dict) -> str:
    file_path = args["file_path"]
    basename = os.path.basename(file_path)
    return _handle_sessions_search({
        "query": basename,
        "project": args.get("project"),
        "limit": args.get("limit", 10),
    })


def _find_session(session_id: str, project: str | None) -> Path | None:
    """Find session file, searching in specified project or all projects."""
    resolved = _resolve_project(project)
    if resolved:
        path = SESSIONS_DIR / resolved / f"{session_id}.jsonl"
        if path.exists():
            return path
        return None

    # No project specified — search all projects
    for proj_name in _all_projects():
        path = SESSIONS_DIR / proj_name / f"{session_id}.jsonl"
        if path.exists():
            return path
    return None


def _handle_session_summary(args: dict) -> str:
    session_id = args["session_id"]
    project = args.get("project")
    path = _find_session(session_id, project)
    if not path:
        return f"Session {session_id} not found."

    messages = _parse_messages(path)
    if not messages:
        return "No messages in session."

    file_pattern = re.compile(r'(?:app/|mcp-|mcp/|templates/|tests/|docs/|src/)[\w/._-]+\.(?:py|html|js|ts|tsx|css|json|md|yaml|yml|sh|txt)')
    files_mentioned = set()
    for m in messages:
        files_mentioned.update(file_pattern.findall(m["content"]))

    user_msgs = [m for m in messages if m["role"] == "user"]
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]

    first_msg = user_msgs[0]["content"][:300] if user_msgs else "(no user messages)"
    last_msg = user_msgs[-1]["content"][:300] if user_msgs else ""

    project_name = path.parent.name
    parts = [
        f"**Session:** {session_id}",
        f"**Project:** {_project_name_to_short(project_name)}",
        f"**Date:** {messages[0].get('timestamp', '?')} → {messages[-1].get('timestamp', '?')}",
        f"**Messages:** {len(messages)} total ({len(user_msgs)} user, {len(assistant_msgs)} assistant)",
        f"\n**First user message:**\n{first_msg}",
    ]
    if last_msg and len(user_msgs) > 1:
        parts.append(f"\n**Last user message:**\n{last_msg}")
    if files_mentioned:
        parts.append(f"\n**Files mentioned ({len(files_mentioned)}):**\n" + "\n".join(f"- {f}" for f in sorted(files_mentioned)[:30]))

    return "\n".join(parts)


def _handle_session_diff(args: dict) -> str:
    session_id = args["session_id"]
    project = args.get("project")
    path = _find_session(session_id, project)
    if not path:
        return f"Session {session_id} not found."

    project_name = path.parent.name
    git_dir = _git_dir_for_project(project_name)
    if not git_dir:
        return f"No git repository found for project '{_project_name_to_short(project_name)}' (tried: {_project_name_to_path(project_name)})"

    first_ts = None
    last_ts = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                ts = entry.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
            except json.JSONDecodeError:
                continue

    if not first_ts or not last_ts:
        return "No timestamps found in session."

    try:
        result = subprocess.run(
            ["git", "-C", git_dir, "log",
             f"--after={first_ts}", f"--before={last_ts}",
             "--format=%h %ai %s", "--stat"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if not output:
            return f"No commits found between {first_ts} and {last_ts}."
        return output
    except Exception as e:
        return f"Git log failed: {e}"


def _handle_session_messages(args: dict) -> str:
    session_id = args["session_id"]
    project = args.get("project")
    query = args.get("query")
    limit = args.get("limit", 50)
    path = _find_session(session_id, project)
    if not path:
        return f"Session {session_id} not found."

    messages = _parse_messages(path)
    if query:
        words = [w.lower() for w in query.split() if w.strip()]
        messages = [m for m in messages if any(w in m["content"].lower() for w in words)]

    messages = messages[:limit]
    if not messages:
        return "No messages found."

    lines = []
    for m in messages:
        content = m["content"]
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"[{m['role']}] ({m.get('timestamp', '?')}):\n{content}")
    return "\n\n---\n\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    logger.info("Sessions MCP server v1.0 starting (global)")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
