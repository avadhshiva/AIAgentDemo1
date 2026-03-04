# AI Chatbot Observability Dashboard (Python + OpenAI)

An AI-powered conversational assistant built in Python with integrated observability and performance monitoring.

This project demonstrates how LLM-based chat systems can be instrumented with real-time telemetry such as response latency, token usage, guardrail triggers, and error tracking while providing both CLI interaction and a web-based monitoring dashboard.

## Key Capabilities

• AI conversational assistant powered by OpenAI models  
• Built-in observability for LLM performance monitoring  
• Real-time metrics dashboard for chat sessions  
• Guardrails for input validation and safety  
• Session persistence and conversation analytics

## Observability Features

### Observability & Monitoring
- **Response Latency Tracking** – Measures per-request response time for each AI interaction to monitor system performance.
- **Input Size Monitoring** – Tracks prompt length and character counts to analyze usage patterns and enforce input constraints.
- **LLM Token Usage Tracking** – Records token consumption for API calls with estimated fallback when usage metadata is unavailable.
- **Error & Failure Monitoring** – Captures API errors and response failures for debugging and reliability analysis.
- **Guardrail Trigger Detection** – Detects oversized inputs and enforces configurable input validation limits.
- **LLM Performance Telemetry** – Collects runtime metrics for AI interactions enabling analysis of latency, reliability, and token efficiency.

### Conversation Intelligence

- **Session Persistence (SQLite)** – Stores conversation history for reproducibility, debugging, and analysis.
- **Automated Session Summaries** – Generates concise summaries of chat sessions using the LLM.

### Visualization & Analytics

- **Real-Time Web Dashboard** – Interactive dashboard displaying operational metrics including latency, token usage, error rates, and guardrail triggers.
  
## Architecture

```
User Input
   ↓
CLI Chat Interface
   ↓
Prompt Processing Layer
   ↓
OpenAI API (LLM)
   ↓
Response Generation
   ↓
Observability Layer
   • latency metrics
   • token tracking
   • error monitoring
   ↓
SQLite Persistence
   ↓
Web Dashboard Visualization
```

## Dashboard Metrics

- Avg latency
- 95th percentile latency
- Total requests
- Guardrail triggers
- API errors
- Token usage

## Installation

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

<img width="1504" height="620" alt="image" src="https://github.com/user-attachments/assets/febe37f5-b5d7-4e2e-8c6c-3768aa313341" />

