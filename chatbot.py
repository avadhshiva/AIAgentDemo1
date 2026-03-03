import argparse
import math
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_tokens(text: str) -> int:
    # Lightweight heuristic when usage is unavailable.
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


@dataclass
class Message:
    role: str
    content: str


class ChatMemory:
    def __init__(self, max_turns: int = 10, max_chars: int = 12000) -> None:
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.messages: List[Message] = []

    def add(self, role: str, content: str) -> None:
        self.messages.append(Message(role=role, content=content))
        self._trim()

    def clear(self) -> None:
        self.messages = []

    def load(self, messages: List[Message]) -> None:
        self.messages = list(messages)
        self._trim()

    def as_api_messages(self, system_prompt: str) -> List[Dict[str, str]]:
        msgs: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        msgs.extend({"role": m.role, "content": m.content} for m in self.messages)
        return msgs

    def _trim(self) -> None:
        max_messages = self.max_turns * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

        while sum(len(m.content) for m in self.messages) > self.max_chars and len(self.messages) > 2:
            self.messages.pop(0)


class ObservabilityStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                topic TEXT NOT NULL,
                model TEXT NOT NULL,
                summary TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                latency_ms REAL,
                input_chars INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                guardrail_triggered INTEGER DEFAULT 0,
                api_error INTEGER DEFAULT 0,
                error_message TEXT DEFAULT '',
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            );

            CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_id);
            CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at);
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            """
        )
        self.conn.commit()

    def ensure_session(self, session_id: str, topic: str, model: str) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO sessions(session_id, created_at, last_updated, topic, model)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, now, now, topic, model),
        )
        self.conn.execute(
            """
            UPDATE sessions
            SET last_updated = ?, topic = ?, model = ?
            WHERE session_id = ?
            """,
            (now, topic, model, session_id),
        )
        self.conn.commit()

    def touch_session(self, session_id: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET last_updated = ? WHERE session_id = ?",
            (utc_now_iso(), session_id),
        )
        self.conn.commit()

    def save_message(self, session_id: str, role: str, content: str) -> None:
        self.conn.execute(
            """
            INSERT INTO messages(session_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, content, utc_now_iso()),
        )
        self.touch_session(session_id)

    def load_messages(self, session_id: str) -> List[Message]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [Message(role=row["role"], content=row["content"]) for row in rows]

    def clear_messages(self, session_id: str) -> None:
        self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self.touch_session(session_id)

    def log_request(
        self,
        session_id: str,
        input_chars: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: Optional[float] = None,
        guardrail_triggered: bool = False,
        api_error: bool = False,
        error_message: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO requests(
                request_id,
                session_id,
                created_at,
                latency_ms,
                input_chars,
                input_tokens,
                output_tokens,
                total_tokens,
                guardrail_triggered,
                api_error,
                error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                utc_now_iso(),
                latency_ms,
                input_chars,
                input_tokens,
                output_tokens,
                total_tokens,
                1 if guardrail_triggered else 0,
                1 if api_error else 0,
                error_message,
            ),
        )
        self.touch_session(session_id)

    def _percentile_95(self, latencies: List[float]) -> float:
        if not latencies:
            return 0.0
        values = sorted(latencies)
        index = max(0, min(len(values) - 1, math.ceil(0.95 * len(values)) - 1))
        return values[index]

    def metrics(self, session_id: Optional[str] = None) -> Dict[str, float]:
        where = ""
        params: Tuple[str, ...] = ()
        if session_id:
            where = "WHERE session_id = ?"
            params = (session_id,)

        row = self.conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_requests,
                AVG(CASE WHEN latency_ms IS NOT NULL THEN latency_ms END) AS avg_latency,
                SUM(guardrail_triggered) AS guardrail_triggers,
                SUM(api_error) AS api_errors,
                SUM(total_tokens) AS token_usage
            FROM requests
            {where}
            """,
            params,
        ).fetchone()

        latency_rows = self.conn.execute(
            f"SELECT latency_ms FROM requests {where} AND latency_ms IS NOT NULL"
            if where
            else "SELECT latency_ms FROM requests WHERE latency_ms IS NOT NULL",
            params,
        ).fetchall()

        latencies = [float(r["latency_ms"]) for r in latency_rows if r["latency_ms"] is not None]

        return {
            "avg_latency": round(float(row["avg_latency"] or 0.0), 2),
            "p95_latency": round(self._percentile_95(latencies), 2),
            "total_requests": int(row["total_requests"] or 0),
            "guardrail_triggers": int(row["guardrail_triggers"] or 0),
            "api_errors": int(row["api_errors"] or 0),
            "token_usage": int(row["token_usage"] or 0),
        }

    def recent_sessions(self, limit: int = 10) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT session_id, topic, model, created_at, last_updated
            FROM sessions
            ORDER BY last_updated DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def set_session_summary(self, session_id: str, summary: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET summary = ?, last_updated = ? WHERE session_id = ?",
            (summary, utc_now_iso(), session_id),
        )
        self.conn.commit()

    def build_session_summary(self, session_id: str) -> str:
        metrics = self.metrics(session_id)
        return (
            f"Session {session_id}: "
            f"{metrics['total_requests']} requests, "
            f"avg latency {metrics['avg_latency']} ms, "
            f"p95 latency {metrics['p95_latency']} ms, "
            f"guardrail triggers {metrics['guardrail_triggers']}, "
            f"API errors {metrics['api_errors']}, "
            f"token usage {metrics['token_usage']}."
        )


def build_system_prompt(topic: str) -> str:
    return (
        "You are a helpful chatbot for topic-focused Q&A. "
        f"Primary topic: {topic}. "
        "Answer accurately and directly. If outside the topic, say so briefly. "
        "If unsure, say you do not know."
    )


def usage_to_tokens(usage_obj, fallback_prompt: str, fallback_answer: str) -> Tuple[int, int, int]:
    if usage_obj is not None:
        in_tokens = getattr(usage_obj, "prompt_tokens", None)
        out_tokens = getattr(usage_obj, "completion_tokens", None)
        total_tokens = getattr(usage_obj, "total_tokens", None)

        if in_tokens is not None and out_tokens is not None and total_tokens is not None:
            return int(in_tokens), int(out_tokens), int(total_tokens)

    estimated_input = estimate_tokens(fallback_prompt)
    estimated_output = estimate_tokens(fallback_answer)
    return estimated_input, estimated_output, estimated_input + estimated_output


def ask_llm(
    client: OpenAI,
    model: str,
    api_messages: List[Dict[str, str]],
    stream: bool,
    temperature: float = 0.2,
) -> Tuple[str, int, int, int]:
    joined_prompt = "\n".join(m["content"] for m in api_messages)

    if stream:
        response = client.chat.completions.create(
            model=model,
            messages=api_messages,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

        chunks: List[str] = []
        usage_obj = None
        print("assistant> ", end="", flush=True)

        for chunk in response:
            if getattr(chunk, "usage", None) is not None:
                usage_obj = chunk.usage
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                print(content, end="", flush=True)
                chunks.append(content)

        print()
        answer = "".join(chunks).strip()
        in_tokens, out_tokens, total_tokens = usage_to_tokens(usage_obj, joined_prompt, answer)
        return answer, in_tokens, out_tokens, total_tokens

    completion = client.chat.completions.create(
        model=model,
        messages=api_messages,
        temperature=temperature,
    )

    answer = (completion.choices[0].message.content or "").strip()
    print(f"assistant> {answer}")
    in_tokens, out_tokens, total_tokens = usage_to_tokens(completion.usage, joined_prompt, answer)
    return answer, in_tokens, out_tokens, total_tokens


def dashboard_html(store: ObservabilityStore) -> str:
    overall = store.metrics()
    sessions = store.recent_sessions(limit=10)

    session_rows = "\n".join(
        (
            "<tr>"
            f"<td>{s['session_id']}</td>"
            f"<td>{s['topic']}</td>"
            f"<td>{s['model']}</td>"
            f"<td>{s['created_at']}</td>"
            f"<td>{s['last_updated']}</td>"
            "</tr>"
        )
        for s in sessions
    )

    if not session_rows:
        session_rows = "<tr><td colspan='5'>No sessions yet.</td></tr>"

    cards = [
        ("Avg Latency", f"{overall['avg_latency']} ms"),
        ("95th Percentile Latency", f"{overall['p95_latency']} ms"),
        ("Total Requests", str(overall["total_requests"])),
        ("Guardrail Triggers", str(overall["guardrail_triggers"])),
        ("API Errors", str(overall["api_errors"])),
        ("Token Usage", str(overall["token_usage"])),
    ]

    card_html = "\n".join(
        f"<div class='card'><h3>{title}</h3><p>{value}</p></div>" for title, value in cards
    )

    return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Chat Intelligence Dashboard</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --panel: #ffffff;
      --ink: #0f172a;
      --muted: #64748b;
      --accent: #0ea5e9;
      --border: #e2e8f0;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top right, #dbeafe 0%, var(--bg) 50%);
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1 {{ margin-top: 0; }}
    .subtitle {{ color: var(--muted); margin-bottom: 20px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
    }}
    .card h3 {{ margin: 0; font-size: 14px; color: var(--muted); font-weight: 600; }}
    .card p {{ margin: 10px 0 0; font-size: 24px; color: var(--accent); font-weight: 700; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
    }}
    th, td {{
      text-align: left;
      padding: 10px;
      border-bottom: 1px solid var(--border);
      font-size: 14px;
    }}
    th {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>Chat Intelligence Dashboard</h1>
    <p class=\"subtitle\">AI Chat Observability & Performance Monitoring</p>
    <div class=\"grid\">{card_html}</div>
    <h2>Recent Sessions</h2>
    <table>
      <thead>
        <tr>
          <th>Session ID</th>
          <th>Topic</th>
          <th>Model</th>
          <th>Created (UTC)</th>
          <th>Last Updated (UTC)</th>
        </tr>
      </thead>
      <tbody>{session_rows}</tbody>
    </table>
  </div>
</body>
</html>
"""


def make_dashboard_handler(store: ObservabilityStore):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in {"/", "/dashboard"}:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            html = dashboard_html(store).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return DashboardHandler


def run_dashboard_server(store: ObservabilityStore, host: str, port: int) -> None:
    handler = make_dashboard_handler(store)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Dashboard running at http://{host}:{port}/dashboard")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Context-aware chatbot with observability dashboard")
    parser.add_argument("--topic", default="Python programming")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--max-input-chars", type=int, default=4000)
    parser.add_argument("--db-path", default="chat_observability.db")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--mode", choices=["chat", "dashboard"], default="chat")
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=8765)
    parser.add_argument("--serve-dashboard", action="store_true")
    return parser.parse_args()


def main() -> int:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path)

    args = parse_args()
    store = ObservabilityStore(Path(args.db_path))

    if args.mode == "dashboard":
        run_dashboard_server(store, args.dashboard_host, args.dashboard_port)
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not found.")
        print("Make sure .env file exists and contains:")
        print("OPENAI_API_KEY=sk-xxxx")
        return 1

    session_id = args.session_id or datetime.now().strftime("session-%Y%m%d-%H%M%S")
    store.ensure_session(session_id=session_id, topic=args.topic, model=args.model)

    client = OpenAI(api_key=api_key)
    memory = ChatMemory(max_turns=args.max_turns, max_chars=args.max_chars)
    persisted_messages = store.load_messages(session_id)
    memory.load(persisted_messages)
    system_prompt = build_system_prompt(args.topic)

    if args.serve_dashboard:
        import threading

        threading.Thread(
            target=run_dashboard_server,
            args=(store, args.dashboard_host, args.dashboard_port),
            daemon=True,
        ).start()

    print("Simple Chatbot + Observability")
    print(f"Session: {session_id}")
    print(f"Topic: {args.topic}")
    print("Type /history, /reset, /summary or /exit")

    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        lower = user_text.lower()
        if lower in {"/exit", "exit", "quit"}:
            break

        if lower == "/history":
            if not memory.messages:
                print("assistant> No messages in memory.")
            else:
                for idx, msg in enumerate(memory.messages, start=1):
                    print(f"{idx}. {msg.role}> {msg.content}")
            continue

        if lower == "/reset":
            memory.clear()
            store.clear_messages(session_id)
            print("assistant> Conversation history reset for this session.")
            continue

        if lower == "/summary":
            summary_text = store.build_session_summary(session_id)
            print(f"assistant> {summary_text}")
            continue

        if not user_text:
            continue

        input_chars = len(user_text)
        if input_chars > args.max_input_chars:
            print(
                f"assistant> Your message is too long ({input_chars} chars). "
                f"Please shorten it below {args.max_input_chars} characters."
            )
            store.log_request(
                session_id=session_id,
                input_chars=input_chars,
                input_tokens=estimate_tokens(user_text),
                output_tokens=0,
                total_tokens=0,
                latency_ms=None,
                guardrail_triggered=True,
                api_error=False,
                error_message="Input exceeded max_input_chars",
            )
            continue

        memory.add("user", user_text)
        store.save_message(session_id, "user", user_text)

        try:
            api_messages = memory.as_api_messages(system_prompt)
            start = time.perf_counter()
            answer, in_tokens, out_tokens, total_tokens = ask_llm(
                client=client,
                model=args.model,
                api_messages=api_messages,
                stream=args.stream,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            store.log_request(
                session_id=session_id,
                input_chars=input_chars,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                total_tokens=total_tokens,
                latency_ms=elapsed_ms,
                guardrail_triggered=False,
                api_error=False,
            )

        except Exception as exc:
            print(f"assistant> API error: {exc}")
            store.log_request(
                session_id=session_id,
                input_chars=input_chars,
                input_tokens=estimate_tokens(user_text),
                output_tokens=0,
                total_tokens=0,
                latency_ms=None,
                guardrail_triggered=False,
                api_error=True,
                error_message=str(exc),
            )
            continue

        memory.add("assistant", answer)
        store.save_message(session_id, "assistant", answer)

    summary = store.build_session_summary(session_id)
    store.set_session_summary(session_id, summary)
    print(f"Session summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
