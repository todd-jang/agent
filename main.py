"""ReAct agent service: FastAPI + LangGraph + Ollama.

Run:  uvicorn main:app --host 0.0.0.0 --port 80
"""
import ast
import hmac
import json
import logging
import math
import operator
import os
import random
import re
import sqlite3
from dotenv import load_dotenv

load_dotenv()
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Annotated, Callable, Sequence, TypedDict

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agent")

# ----------------------------------------------------------------- settings
#OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434"
)
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:7b")
NUM_CTX = int(os.getenv("NUM_CTX", "4096"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "300"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "10"))
DB_PATH = os.getenv("DB_PATH", "/data/sample_shop.db" if os.path.isdir("/data") else "./sample_shop.db")           # SQLite (default)
DATABASE_URL = os.getenv("DATABASE_URL", "")                       # PostgreSQL (optional, shared DB for multi-node)
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
API_KEY = os.getenv("API_KEY")
TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")

# -------------------------------------------------------------------- tools
def _clean_results(results):
    """Drop error pages and truncate content (shorter context = faster)."""
    cleaned = []
    for r in results if isinstance(results, list) else []:
        content = str(r.get("content", ""))
        if "API key" in content or content.lstrip().startswith("{'error'"):
            continue
        cleaned.append({"title": r.get("title", ""), "url": r.get("url", ""), "content": content[:300]})
    return cleaned or [{"title": "no results", "url": "", "content": "No usable results found."}]


    

def make_search_tool():
    from langchain_community.tools.tavily_search import TavilySearchResults

    search = TavilySearchResults(
        max_results=3,
        search_depth="basic"
    )

    @tool("search_tool", description="Search the web for current information using a query.")
    def search_tool(query: str):
        """Search the web using Tavily and return relevant search results."""
        try:
            log.info("[SEARCH] query=%s", query)

            raw_results = search.invoke(query)
            log.info("[SEARCH] raw_results=%s", raw_results)

            return _clean_results(raw_results)

        except Exception as e:
            log.exception("[SEARCH] failed")
            return [{
                "title": "search error",
                "url": "",
                "content": f"Search failed: {e}"
            }]

    return search_tool



@tool
def recommend_clothing(weather: str) -> str:
    """
    Returns a clothing recommendation based on a short weather description.

    :param weather: A brief description of the weather (e.g., "Cloudy, 15°C")
    :return: Clothing recommendation
    """
    w = weather.lower()
    temp_c = None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°|degrees?)?\s*([cf])\b", w)
    if m:
        t = float(m.group(1))
        temp_c = (t - 32) * 5 / 9 if m.group(2) == "f" else t

    if "snow" in w or "freezing" in w or (temp_c is not None and temp_c <= 0):
        return "Wear a heavy coat, gloves, and boots."
    if any(k in w for k in ("rain", "wet", "drizzle", "shower")):
        return "Bring a raincoat and waterproof shoes."
    if "hot" in w or (temp_c is not None and temp_c >= 28):
        return "T-shirt, shorts, and sunscreen recommended."
    if "cold" in w or (temp_c is not None and temp_c <= 10):
        return "Wear a warm jacket or sweater."
    return "A light jacket should be fine."


_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS = {n: getattr(math, n) for n in
          ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "exp", "floor", "ceil")}
_FUNCS.update({"abs": abs, "round": round})
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval(node.right)) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError("Unsupported expression")


@tool
def calculator_tool(expression: str) -> str:
    """
    Safely evaluate mathematical expressions.
    Supports + - * / ** % //, parentheses, sqrt, sin, cos, tan, log, exp, abs, round, pi, e.

    :param expression: A mathematical expression as a string (e.g., "2 + 3 * 4")
    :return: The result of the calculation
    """
    try:
        expr = expression.replace("π", "pi").replace("^", "**")
        return str(_eval(ast.parse(expr.strip(), mode="eval")))
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as e:
        return f"Error: could not evaluate '{expression}' ({e})"


@tool
def news_summarizer_tool(news_content: str) -> str:
    """
    Summarize news articles from search results (pass the raw search results as a JSON string).

    :param news_content: Raw news content or search results
    :return: A formatted summary of the top 3 articles
    """
    try:
        data = json.loads(news_content)
        if isinstance(data, dict):
            data = [data]
    except Exception:
        data = [{"title": f"Article {i + 1}", "content": p.strip()}
                for i, p in enumerate(news_content.split("\n\n")) if p.strip()]

    lines = []
    for i, a in enumerate(data[:3], 1):
        title = a.get("title") or f"Article {i}"
        text = (a.get("content") or "").strip().replace("\n", " ")
        sentences = [s.strip() for s in text.split(". ") if s.strip()][:3]
        lines.append(f"{i}. {title}")
        if a.get("published_date") or a.get("date"):
            lines.append(f"   Date: {a.get('published_date') or a.get('date')}")
        for s in sentences:
            lines.append(f"   - {s.rstrip('.')}.")
        if a.get("url"):
            lines.append(f"   Source: {a['url']}")
    return "\n".join(lines) if lines else "No articles to summarize."


# ---------------------------------------------------------------------- SQL
def ensure_db(path: str = None):
    """Create the sample database if it does not exist yet (replace with your real DB in production)."""
    path = path or DB_PATH
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE products  (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
    CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
    CREATE TABLE orders    (id INTEGER PRIMARY KEY, customer_id INTEGER, product_id INTEGER,
                            quantity INTEGER, order_date TEXT,
                            FOREIGN KEY(customer_id) REFERENCES customers(id),
                            FOREIGN KEY(product_id)  REFERENCES products(id));
    """)
    con.executemany("INSERT INTO products VALUES (?,?,?,?)", [
        (1, "Laptop", "Electronics", 1200.0), (2, "Phone", "Electronics", 800.0),
        (3, "Headphones", "Electronics", 150.0), (4, "Desk", "Furniture", 300.0),
        (5, "Chair", "Furniture", 180.0), (6, "Notebook", "Stationery", 5.0),
        (7, "Pen Set", "Stationery", 12.0), (8, "Backpack", "Accessories", 60.0)])
    con.executemany("INSERT INTO customers VALUES (?,?,?)", [
        (1, "Alice", "Seoul"), (2, "Bob", "Busan"), (3, "Chloe", "Seoul"),
        (4, "Daniel", "Incheon"), (5, "Eunji", "Busan"), (6, "Frank", "Goyang")])
    rng = random.Random(42)
    start = date(2026, 8, 1)
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", [
        (i, rng.randint(1, 6), rng.randint(1, 8), rng.randint(1, 4),
         (start + timedelta(days=rng.randint(0, 85))).isoformat()) for i in range(1, 41)])
    con.commit()
    con.close()


def _ro_connect():
    # read-only connection: writes are rejected by SQLite itself
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)


def _pg_connect():
    """PostgreSQL connection that is read-only at the session level, with a statement timeout.
    Also use a DB role that only has SELECT (see db/init/02_readonly_role.sh): defense in depth."""
    import psycopg
    con = psycopg.connect(
        DATABASE_URL,
        connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",
    )
    con.read_only = True
    return con


def _schema_sqlite() -> str:
    con = _ro_connect()
    try:
        out = ["-- dialect: SQLite"]
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            cols = ", ".join(f"{c[1]} {c[2]}" for c in con.execute(f"PRAGMA table_info({name})"))
            n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            out.append(f"{name}({cols}) -- {n} rows")
        return "\n".join(out)
    finally:
        con.close()


def _schema_pg() -> str:
    with _pg_connect() as con, con.cursor() as cur:
        cur.execute("""
            SELECT c.relname, c.reltuples::bigint
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
            ORDER BY c.relname""")
        out = ["-- dialect: PostgreSQL"]
        for name, est in cur.fetchall():
            cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""", (name,))
            cols = ", ".join(f"{a} {b}" for a, b in cur.fetchall())
            out.append(f"{name}({cols})" + (f" -- ~{est} rows" if est >= 0 else ""))
        return "\n".join(out)


def _query_sqlite(q: str):
    con = _ro_connect()
    try:
        cur = con.execute(f"SELECT * FROM ({q}) LIMIT 20")
        return [d[0] for d in cur.description], cur.fetchall()
    finally:
        con.close()


def _query_pg(q: str):
    with _pg_connect() as con, con.cursor() as cur:
        cur.execute(f"SELECT * FROM ({q}) AS _t LIMIT 20")   # no params: '%' in LIKE patterns is safe
        return [d.name for d in cur.description], cur.fetchall()


@tool
def list_tables_schema(dummy: str = "") -> str:
    """
    List all tables in the database with their columns (and approximate row counts).
    The first line states the SQL dialect. Always call this before writing a SQL query.

    :param dummy: unused, can be empty
    :return: Schema description of every table
    """
    return _schema_pg() if DATABASE_URL else _schema_sqlite()


@tool
def run_sql_query(query: str) -> str:
    """
    Run ONE read-only SQL SELECT query (use the dialect stated by list_tables_schema) and return up to 20 rows.
    On error, the error message is returned so you can fix the query and retry.

    :param query: A single SQL SELECT statement
    :return: Column names and rows, or an error message
    """
    q = query.strip().rstrip(";").strip()
    if ";" in q:
        return "Error: only one statement is allowed."
    if not q.lower().startswith(("select", "with")):
        return "Error: only SELECT queries are allowed."
    try:
        cols, rows = _query_pg(q) if DATABASE_URL else _query_sqlite(q)
        return json.dumps({"columns": cols, "rows": rows}, ensure_ascii=False, default=str)
    except Exception as e:
        return f"SQL error: {e}"


# -------------------------------------------------------------------- agent
SYSTEM_PROMPT = """
당신은 한국어 기반의 ReAct AI Agent입니다.

사용자의 질문을 분석하고 필요한 경우 도구를 선택하여 실행합니다.

[핵심 원칙]

1. 최신 정보가 필요한 질문:
   반드시 search_tool을 사용합니다.
   예: 최신 뉴스, 경제, 정치, 기업, 기술, 날씨

2. 수학 계산:
   반드시 calculator_tool을 사용합니다.
   암산하거나 계산 결과를 추측하지 않습니다.

3. 날씨와 의류 추천:
   먼저 search_tool로 날씨를 확인하고,
   recommend_clothing에 간단한 날씨 정보만 전달합니다.

4. 데이터베이스 질문:
   반드시 list_tables_schema를 먼저 실행합니다.
   확인한 스키마만 이용하여 run_sql_query를 실행합니다.
   SELECT 쿼리만 사용합니다.

5. 뉴스 요약:
   검색 결과를 바탕으로 핵심 내용과 출처를 정리합니다.

[Agent 동작 방식]

- 도구가 필요하면 도구를 호출합니다.
- 도구 실행 결과를 확인한 후 다음 행동을 결정합니다.
- 충분한 정보가 확보되면 최종 답변을 작성합니다.
- 사용하지 않은 도구를 사용했다고 주장하지 않습니다.
- 모르는 정보는 만들어내지 않습니다.

[기술 개념의 정확성]

LangGraph는 상태 기반 그래프를 이용하여
AI Agent의 워크플로를 구성하는 프레임워크입니다.

LangChain은 LLM, 프롬프트, 도구 등을 연결하는
애플리케이션 개발 프레임워크입니다.

두 기술을 혼동하지 않습니다.

최종 답변은 한국어로 작성합니다.
"""


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


def build_tools():
    tools = [recommend_clothing, calculator_tool, news_summarizer_tool, list_tables_schema, run_sql_query]
    if TAVILY_API_KEY:
        tools.insert(0, make_search_tool())
    else:
        log.warning("TAVILY_API_KEY not set: search_tool is disabled")
    return tools


def build_graph(model, tools):
    tools_by_name = {t.name: t for t in tools}
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="scratch_pad"),
    ])
    model_react = prompt | model.bind_tools(tools)

    def call_model(state: AgentState):
        return {"messages": [model_react.invoke({"scratch_pad": state["messages"]})]}

    def _run_tool(tool_call):
        try:
            result = tools_by_name[tool_call["name"]].invoke(tool_call["args"])
        except Exception as e:
            result = f"Tool error: {e}"
        return ToolMessage(content=json.dumps(result, ensure_ascii=False),
                           name=tool_call["name"], tool_call_id=tool_call["id"])

    def tool_node(state: AgentState):
        calls = state["messages"][-1].tool_calls
        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as ex:
            return {"messages": list(ex.map(_run_tool, calls))}

    def should_continue(state: AgentState):
        return "continue" if state["messages"][-1].tool_calls else "end"

    wf = StateGraph(AgentState)
    wf.add_node("agent", call_model)
    wf.add_node("tools", tool_node)
    wf.add_edge("tools", "agent")
    wf.add_conditional_edges("agent", should_continue, {"continue": "tools", "end": END})
    wf.set_entry_point("agent")
    return wf.compile()


def make_model():
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=MODEL_NAME,
        base_url=OLLAMA_BASE_URL,
        temperature=0,
        num_predict=400,
        num_ctx=NUM_CTX,
        keep_alive="30m",
        client_kwargs={"timeout": LLM_TIMEOUT},
    )


# ---------------------------------------------------------------------- API
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class GuestChatRequest(ChatRequest):
    captcha_token: str = Field(min_length=1)


class ChatResponse(BaseModel):
    answer: str
    tools_used: list[str]


def create_app(model_factory: Callable = make_model) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not os.getenv("API_KEY"):
            raise RuntimeError("API_KEY environment variable is required")
        if not DATABASE_URL:
            ensure_db()      # PostgreSQL is a pre-provisioned shared DB: nodes must not seed it
        app.state.graph = build_graph(model_factory(), build_tools())
        log.info("agent ready (model=%s, ollama=%s)", MODEL_NAME, OLLAMA_BASE_URL)
        yield

    app = FastAPI(title="ReAct Agent", lifespan=lifespan)

    def require_api_key(x_api_key: str = Header(default="")):
        if not hmac.compare_digest(x_api_key.encode(), os.environ["API_KEY"].encode()):
            raise HTTPException(status_code=401, detail="invalid API key")

    def run_chat(req: ChatRequest):
        try:
            result = app.state.graph.invoke(
                {"messages": [HumanMessage(content=req.message)]},
                config={"recursion_limit": RECURSION_LIMIT},
            )
        except GraphRecursionError:
            raise HTTPException(status_code=504, detail="agent did not finish within the step limit")
        except Exception:
            log.exception("agent failure")
            raise HTTPException(status_code=502, detail="model backend error")
        msgs = result["messages"]
        tools_used = [c["name"] for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls]
        return ChatResponse(answer=str(msgs[-1].content), tools_used=tools_used)

    @app.get("/health")
    def health():
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        """Readiness: Ollama is reachable and the model is pulled."""
        try:
            tags = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2).json()
            names = {m["name"] for m in tags.get("models", [])}
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"ollama unreachable: {e}")
        if not any(n == MODEL_NAME or n.startswith(MODEL_NAME + ":") for n in names):
            raise HTTPException(status_code=503, detail=f"model {MODEL_NAME} not pulled yet")
        try:
            if DATABASE_URL:
                _pg_connect().close()
            elif not os.path.exists(DB_PATH):
                raise FileNotFoundError(DB_PATH)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"database unreachable: {e}")
        return {"status": "ready", "model": MODEL_NAME, "db": "postgresql" if DATABASE_URL else "sqlite"}

    @app.get("/guest/config")
    def guest_config():
        enabled = bool(TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY)
        return {
            "enabled": enabled,
            "site_key": TURNSTILE_SITE_KEY if enabled else "",
        }

    @app.post("/guest/chat", response_model=ChatResponse)
    def guest_chat(req: GuestChatRequest):
        if not TURNSTILE_SITE_KEY or not TURNSTILE_SECRET_KEY:
            raise HTTPException(status_code=503, detail="guest chat is not configured")
        try:
            verification = requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": TURNSTILE_SECRET_KEY, "response": req.captcha_token},
                timeout=5,
            )
            verification.raise_for_status()
            verification_data = verification.json()
        except (requests.RequestException, ValueError):
            log.exception("Turnstile verification request failed")
            raise HTTPException(status_code=503, detail="captcha verification is unavailable")
        if not verification_data.get("success"):
            raise HTTPException(status_code=400, detail="captcha verification failed")
        return run_chat(ChatRequest(message=req.message))

    # plain `def`: FastAPI runs it in a worker thread, so blocking LLM calls don't stall the event loop
    @app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
    def chat(req: ChatRequest):
        return run_chat(req)

    return app


app = create_app()
