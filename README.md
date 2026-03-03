# Chat Intelligence Dashboard (CLI + Web Dashboard)

A Python chatbot with built-in observability and performance monitoring.

## Features

- Response time tracking (per request latency)
- Input size tracking (character count)
- Token count tracking (API usage when available, estimated fallback)
- Error tracking (API failures)
- Guardrail trigger tracking (oversized input)
- Conversation persistence (SQLite)
- Session summary generation
- Web dashboard with key metrics

## Dashboard Metrics

- Avg latency
- 95th percentile latency
- Total requests
- Guardrail triggers
- API errors
- Token usage

## Setup

### Prerequisites
- Python 3.9+
- OpenAI API key (get one from https://platform.openai.com/api-keys)

### Installation Steps

1. Clone the repository:
```bash
git clone <your-repo-url>
cd AiDemo1
```

2. Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

3. Install dependencies:

```powershell
pip install -r requirements.txt
```

4. Configure your API key (do NOT commit `.env` to version control):

```powershell
copy .env.example .env
# Edit .env in your text editor and add your OPENAI_API_KEY
# .env is automatically ignored by git
```

## Run Chat Mode

```powershell
python chatbot.py --mode chat --topic "Python programming" --model gpt-4o-mini
```

Streaming mode:

```powershell
python chatbot.py --mode chat --stream
```

Chat + dashboard in one process:

```powershell
python chatbot.py --mode chat --serve-dashboard --dashboard-port 8765
```

## Run Dashboard Only

```powershell
python chatbot.py --mode dashboard --dashboard-port 8765
```

Open: `http://127.0.0.1:8765/dashboard`

## Session Persistence

- Data is stored in `chat_observability.db` by default.
- Reuse a session across runs:

```powershell
python chatbot.py --session-id session-20260302-120000
```

## Chat Commands

- `/history` show current retained messages
- `/reset` clear current session messages
- `/summary` print current session summary
- `/exit` quit

## Useful Flags

- `--db-path chat_observability.db`
- `--max-input-chars 4000`
- `--max-turns 10`
- `--max-chars 12000`
- `--dashboard-host 127.0.0.1`
- `--dashboard-port 8765`
