"""Helix customer portal: thin FastAPI proxy in front of Zammad's REST API.
Zammad stays the ticket engine; this adds a branded customer UI, per-account QR login
and a JSON API the AI chatbot uses."""
import io, json, os, re, secrets, threading, time
from html import escape
from http.cookiejar import CookieJar
from urllib.parse import quote
import httpx, qrcode, qrcode.image.svg
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

ZAMMAD = os.getenv("ZAMMAD_URL", "http://10.0.16.126:8080").rstrip("/")
TOKEN = os.getenv("ZAMMAD_TOKEN", "")          # admin API token (needs admin.user + ticket.agent)
GROUP = os.getenv("ZAMMAD_GROUP", "Users")     # group new tickets land in
OLLAMA = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "512"))
NUM_THREADS = int(os.getenv("OLLAMA_THREADS", str(os.cpu_count() or 4)))
SESSION_SECS = int(os.getenv("SESSION_MINUTES", "90")) * 60
CACHE_SECS = int(os.getenv("TICKET_CACHE_SECONDS", "20"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")      # e.g. http://10.0.16.50:8000
ADMIN_KEY = os.getenv("ADMIN_KEY", "")                    # enables /admin/qr
QR_FILE = os.getenv("QR_FILE", "qr_tokens.json")
QR_SECS = int(os.getenv("QR_DAYS", "30")) * 86400
PRIOS = {"low": "1 low", "normal": "2 normal", "high": "3 high"}
CATS = ["Account", "Technical", "Network", "Hardware", "Billing", "Feature Request", "General"]

app = FastAPI(title="Helix Customer Portal")
sessions: dict = {}
recent: dict = {}   # blocks accidental duplicate ticket submits
cache: dict = {}    # email -> (timestamp, tickets)
qr_lock = threading.Lock()


class NoCookies(CookieJar):
    """Never store cookies, so one customer's Zammad session can't leak into another's request."""
    def set_cookie(self, cookie):
        pass


http_client = httpx.Client(timeout=15.0, cookies=NoCookies())
async_client = httpx.AsyncClient(timeout=15.0, cookies=NoCookies())


def ollama_opts(**extra):
    o = {"num_ctx": NUM_CTX, **extra}
    if NUM_THREADS > 0:
        o["num_thread"] = NUM_THREADS
    return o


def zam(method, path, email=None, **kw):
    h = {"Authorization": f"Token token={TOKEN}"}
    if email:
        h["X-On-Behalf-Of"] = email
    r = http_client.request(method, f"{ZAMMAD}/api/v1{path}", headers=h, **kw)
    if r.status_code >= 400:
        try:
            j = r.json()
            msg = j.get("error_human") or j.get("error") or r.text[:200]
        except Exception:
            msg = r.text[:200]
        raise HTTPException(r.status_code, str(msg)[:300])
    return r.json()


def cur(sid: str = Cookie(None)):
    u = sessions.get(sid)
    if not u or u["exp"] < time.time():
        sessions.pop(sid, None)
        raise HTTPException(401, "Session expired. Please sign in again.")
    return u


def start_session(resp, me):
    """Create a portal session for a Zammad user dict and set the cookie on resp."""
    name = f"{me.get('firstname', '')} {me.get('lastname', '')}".strip() or me["email"]
    sid = secrets.token_urlsafe(32)
    for k in [k for k, v in sessions.items() if v["exp"] < time.time()]:
        sessions.pop(k, None)
    sessions[sid] = {"email": me["email"], "name": name, "exp": time.time() + SESSION_SECS}
    resp.set_cookie("sid", sid, httponly=True, samesite="lax", max_age=SESSION_SECS)
    return name


class Login(BaseModel):
    email: str
    password: str


class New(BaseModel):
    title: str
    body: str
    priority: str = "normal"
    category: str = "General"
    files: list[dict] = []


class Chat(BaseModel):
    message: str
    history: list[dict] = []


class Reply(BaseModel):
    body: str = ""
    files: list[dict] = []


def pack(files):
    """Convert uploaded files (name/type/base64 data) into Zammad article attachments."""
    out = []
    for f in files[:3]:
        d = str(f.get("data", ""))
        if not d:
            continue
        if len(d) > 7_000_000:
            raise HTTPException(413, "Each file must be under 5 MB.")
        out.append({"filename": str(f.get("name", "file"))[:120], "data": d,
                    "mime-type": str(f.get("type") or "application/octet-stream")[:100]})
    return out


@app.on_event("startup")
def warm():
    """Load the model into memory at startup with matching options to avoid inference lag."""
    def go():
        try:
            http_client.post(
                f"{OLLAMA}/api/generate", timeout=120,
                json={"model": MODEL, "prompt": "hi", "stream": False, "keep_alive": "60m",
                      "options": ollama_opts(num_predict=1)})
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


@app.on_event("shutdown")
async def shutdown():
    await async_client.aclose()
    http_client.close()


# ---------------------------------------------------------------- auth
@app.post("/api/login")
def login(b: Login, resp: Response):
    r = http_client.get(f"{ZAMMAD}/api/v1/users/me", auth=(b.email, b.password))
    if r.status_code != 200:
        raise HTTPException(401, "Wrong email or password")
    me = r.json()
    name = start_session(resp, me)
    return {"email": me["email"], "name": name}


@app.post("/api/logout")
def logout(resp: Response, sid: str = Cookie(None)):
    sessions.pop(sid, None)
    resp.delete_cookie("sid")
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(cur)):
    return {"email": u["email"], "name": u["name"]}


# ---------------------------------------------------------------- per-account QR login
def _qr_load():
    try:
        with open(QR_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _qr_save(d):
    tmp = QR_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, QR_FILE)


def qr_token(email, new=False):
    """Return this account's QR token (create or rotate it). One active token per account."""
    with qr_lock:
        now = time.time()
        d = {k: v for k, v in _qr_load().items() if v["exp"] > now}
        mine = [k for k, v in d.items() if v["email"] == email]
        if mine and not new:
            _qr_save(d)
            return mine[0]
        for k in mine:
            d.pop(k)
        t = secrets.token_urlsafe(32)
        d[t] = {"email": email, "exp": now + QR_SECS}
        _qr_save(d)
        return t


def base(request: Request):
    return PUBLIC_URL or str(request.base_url).rstrip("/")


@app.get("/api/qr/link")
def qr_link(request: Request, u=Depends(cur)):
    return {"url": f"{base(request)}/qr-login?t={qr_token(u['email'])}", "days": QR_SECS // 86400}


@app.post("/api/qr/regenerate")
def qr_regen(request: Request, u=Depends(cur)):
    return {"url": f"{base(request)}/qr-login?t={qr_token(u['email'], new=True)}", "days": QR_SECS // 86400}


@app.get("/qr-login")
def qr_login(t: str):
    v = _qr_load().get(t)
    if not v or v["exp"] < time.time():
        return RedirectResponse("/?qr=invalid", status_code=303)
    try:
        who = zam("GET", "/users/me", v["email"])
    except HTTPException:
        return RedirectResponse("/?qr=invalid", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    start_session(resp, who)
    return resp


@app.get("/admin/qr")
def admin_qr(request: Request, key: str = "", email: str = ""):
    """Printable QR cards: all customers, or one with ?email=. Needs ADMIN_KEY."""
    if not ADMIN_KEY or not secrets.compare_digest(key, ADMIN_KEY):
        raise HTTPException(403, "Forbidden")
    if email:
        users = [{"email": email, "name": email}]
    else:
        data = zam("GET", "/users/search?query=*&limit=300&expand=true")
        users = []
        for x in data:
            roles = x.get("roles") or []
            if x.get("active", True) and x.get("email") and "Customer" in roles \
                    and "Agent" not in roles and "Admin" not in roles:
                nm = f"{x.get('firstname', '')} {x.get('lastname', '')}".strip() or x["email"]
                users.append({"email": x["email"], "name": nm})
    cards = ""
    for x in users:
        link = f"{base(request)}/qr-login?t={qr_token(x['email'])}"
        cards += (f'<div class="c"><img src="/qr.svg?url={quote(link, safe="")}" width="170" height="170" alt="QR">'
                  f'<b>{escape(x["name"])}</b><small>{escape(x["email"])}</small><span>Scan to open Helix</span></div>')
    page = ("<!doctype html><meta charset=utf-8><title>Helix QR stickers</title><style>"
            "body{font:14px system-ui;margin:24px;background:#f4f7fc}h1{font-size:20px}"
            ".g{display:flex;flex-wrap:wrap;gap:16px}.c{background:#fff;border:1px solid #dde3ee;border-radius:12px;"
            "padding:16px;width:210px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:4px;break-inside:avoid}"
            "small{color:#667}span{margin-top:6px;font-weight:600;color:#2563eb}"
            "@media print{button{display:none}body{background:#fff}}</style>"
            f"<h1>Helix QR stickers ({len(users)})</h1><button onclick=print()>Print</button><br><br>"
            f'<div class="g">{cards or "No customers found."}</div>')
    return HTMLResponse(page)


# ---------------------------------------------------------------- tickets
def brief(t):
    return {"id": t["id"], "number": t["number"], "title": t["title"], "state": t["state"],
            "priority": t["priority"], "created": t["created_at"], "updated": t["updated_at"],
            "owner": t.get("owner"), "group": t.get("group")}


def get_tickets(email, fresh=False):
    hit = cache.get(email)
    if hit and not fresh and time.time() - hit[0] < CACHE_SECS:
        return hit[1]
    data = zam("GET", "/tickets?expand=true&per_page=100", email)
    out = sorted(map(brief, data), key=lambda t: t["updated"], reverse=True)
    cache[email] = (time.time(), out)
    return out


@app.get("/api/tickets")
def tickets(u=Depends(cur)):
    return get_tickets(u["email"], fresh=True)


@app.get("/api/tickets/{tid}")
def detail(tid: int, u=Depends(cur)):
    t = zam("GET", f"/tickets/{tid}?expand=true", u["email"])
    arts = zam("GET", f"/ticket_articles/by_ticket/{tid}?expand=true", u["email"])
    clean = lambda a: re.sub(r"<[^>]+>", "", a["body"]) if a.get("content_type") == "text/html" else a["body"]
    cat = "General"
    try:
        res = zam("GET", f"/tags?object=Ticket&o_id={tid}")
        tags = res.get("tags", []) if isinstance(res, dict) else res
        cat = next((x for x in tags if x in CATS), "General")
    except Exception:
        pass
    return {**brief(t), "category": cat, "articles": [
        {"id": a["id"], "from": a.get("from") or "", "sender": a.get("sender"), "body": clean(a),
         "created": a["created_at"],
         "attachments": [{"id": f["id"], "name": f.get("filename", "file"), "size": f.get("size")}
                         for f in (a.get("attachments") or [])]}
        for a in arts if not a["internal"]]}


@app.get("/api/tickets/{tid}/files/{aid}/{fid}")
def download(tid: int, aid: int, fid: int, u=Depends(cur)):
    zam("GET", f"/tickets/{tid}", u["email"])
    r = http_client.get(f"{ZAMMAD}/api/v1/ticket_attachment/{tid}/{aid}/{fid}",
                        headers={"Authorization": f"Token token={TOKEN}", "X-On-Behalf-Of": u["email"]})
    if r.status_code >= 400:
        raise HTTPException(404, "File not found")
    disp = r.headers.get("content-disposition", "attachment").replace("inline", "attachment", 1)
    return Response(r.content, media_type=r.headers.get("content-type", "application/octet-stream"),
                    headers={"Content-Disposition": disp, "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "sandbox"})


@app.post("/api/tickets")
def create(b: New, u=Depends(cur)):
    if not b.title.strip():
        raise HTTPException(422, "Add a title.")
    if not b.body.strip():
        raise HTTPException(422, "Add some details.")
    key = (u["email"], b.title.strip().lower(), b.body.strip())
    hit = recent.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    pri = PRIOS.get(b.priority.lower(), "2 normal")
    art = {"subject": b.title, "body": b.body, "type": "web", "internal": False}
    att = pack(b.files)
    if att:
        art["attachments"] = att
    t = zam("POST", "/tickets", u["email"], json={
        "title": b.title, "group": GROUP, "customer": u["email"], "priority": pri, "article": art})
    if pri != "2 normal":
        try:
            zam("PUT", f"/tickets/{t['id']}", json={"priority": pri})
        except Exception:
            pass
    cat = b.category if b.category in CATS else "General"
    try:
        zam("POST", "/tags/add", json={"object": "Ticket", "o_id": t["id"], "item": cat})
    except Exception:
        pass
    cache.pop(u["email"], None)
    out = {"id": t["id"], "number": t["number"]}
    recent[key] = (time.time(), out)
    return out


@app.post("/api/tickets/{tid}/reply")
def reply(tid: int, b: Reply, u=Depends(cur)):
    zam("GET", f"/tickets/{tid}", u["email"])
    if not b.body.strip() and not b.files:
        raise HTTPException(422, "Type a message.")
    art = {"ticket_id": tid, "body": b.body.strip() or "(attachment)", "type": "web", "internal": False}
    att = pack(b.files)
    if att:
        art["attachments"] = att
    zam("POST", "/ticket_articles", u["email"], json=art)
    cache.pop(u["email"], None)
    return {"ok": True}


@app.post("/api/tickets/{tid}/close")
def close(tid: int, u=Depends(cur)):
    zam("GET", f"/tickets/{tid}", u["email"])
    zam("PUT", f"/tickets/{tid}", json={"state": "closed"})
    cache.pop(u["email"], None)
    return {"ok": True}


# ---------------------------------------------------------------- chatbot engine
GL = {"open": "open", "progress": "in progress", "done": "resolved"}


def grp(s):
    return "done" if s in ("closed", "merged") else "open" if s == "new" else "progress"


def plain_pri(p):
    return re.sub(r"^\d\s*", "", p)


def short(t):
    return "#" + t["number"] + " " + t["title"]


def line(t):
    return f"{short(t)} is {GL[grp(t['state'])]}, {plain_pri(t['priority'])} priority, updated {t['updated'][:10]}."


def counts(ts):
    n = {"open": 0, "progress": 0, "done": 0}
    for t in ts:
        n[grp(t["state"])] += 1
    return n


def summ(ts):
    n = counts(ts)
    return f"You have {len(ts)} tickets: {n['open']} open, {n['progress']} in progress, {n['done']} resolved."


STOP = {"the", "a", "an", "of", "my", "me", "is", "it", "to", "for", "on", "in", "and", "what", "whats",
        "status", "ticket", "tickets", "case", "cases", "issue", "about", "show", "tell", "give", "any",
        "update", "updates", "open", "closed", "pending", "done", "new", "how", "many", "have", "i",
        "do", "there", "was", "are", "with", "this", "that", "please", "latest", "oldest", "high",
        "progress", "resolved", "active", "which"}


def by_number(msg, ts):
    for num in re.findall(r"\d{4,}", msg):
        for t in ts:
            if t["number"] == num:
                return t
    return None


def by_title(msg, ts):
    words = {w for w in re.findall(r"[a-z0-9]+", msg.lower()) if w not in STOP and len(w) > 2}
    if not words:
        return None
    best, score = None, 0
    for t in ts:
        s = len(words & set(re.findall(r"[a-z0-9]+", t["title"].lower())))
        if s > score:
            best, score = t, s
    return best if score else None


def quick(msg, ts):
    """Instant answers for common questions, no AI needed. Returns None if not matched."""
    if not ts:
        return "You have no tickets yet."
    m = msg.lower()
    words = set(re.findall(r"[a-z]+", m))
    live = [t for t in ts if grp(t["state"]) != "done"]
    t = by_number(m, ts)
    if t:
        return line(t)
    if words & {"summary", "summarize", "summarise", "overview"}:
        s = summ(ts)
        hi = [x for x in live if plain_pri(x["priority"]).lower() == "high"]
        if hi:
            s += " High priority and active: " + ", ".join(short(x) for x in hi[:5]) + "."
        return s + " Latest update: " + short(ts[0]) + " is " + GL[grp(ts[0]["state"])] + "."
    if words & {"high", "urgent", "critical"}:
        hi = [x for x in live if plain_pri(x["priority"]).lower() == "high"]
        return ("High priority tickets active: " + "; ".join(short(x) for x in hi[:8]) + ".") if hi \
            else "You have no active high priority tickets."
    if words & {"oldest", "first", "earliest"}:
        return "Your oldest ticket: " + line(min(ts, key=lambda x: x["created"]))
    want = None
    if words & {"progress", "pending", "working", "ongoing"}:
        want = ("in progress", ["progress"])
    elif words & {"closed", "done", "resolved", "finished", "completed"}:
        want = ("resolved", ["done"])
    elif words & {"active", "unresolved", "remaining"}:
        want = ("active", ["open", "progress"])
    elif words & {"open", "new"}:
        want = ("open", ["open"])
    if want:
        items = [x for x in ts if grp(x["state"]) in want[1]]
        head = f"You have {len(items)} {want[0]} ticket" + ("" if len(items) == 1 else "s")
        return head + (": " + "; ".join(short(x) for x in items[:8]) + "." if items else ".")
    if "how many" in m or words & {"count", "total", "number"}:
        return summ(ts)
    t = by_title(m, ts)
    if t:
        return line(t)
    if words & {"latest", "last", "recent", "newest"}:
        return "Your latest update: " + line(ts[0])
    return None


SYSTEM = (
    "Role: Helix ITSM Customer Assistant.\n"
    "Task: Answer customer queries regarding support tickets using ONLY the DATA block.\n\n"
    "Strict Constraints:\n"
    "1. Length: Exactly 1 to 2 concise sentences.\n"
    "2. Tone: Objective, factual, direct.\n"
    "3. No Fluff: NEVER output greetings ('Hello', 'Hi'), acknowledgments ('Sure', 'Certainly'), or sign-offs.\n"
    "4. Format: Refer to tickets as '#<number> <Title>' (e.g., '#75009 Server down').\n"
    "5. Ground Truth: Never speculate, invent ETAs, or guess technician actions not listed in DATA.\n"
    "6. Fallback: If answer is not in DATA or unrelated to support tickets, output exactly: "
    "'I can only assist with your existing Helix support tickets.'"
)


def facts(ts, query=""):
    """Prioritizes relevant matching tickets ahead of raw recency."""
    n = counts(ts)
    q_words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOP and len(w) > 2}

    def relevance_score(t):
        if not q_words:
            return 0
        title_words = set(re.findall(r"[a-z0-9]+", t["title"].lower()))
        return len(q_words & title_words)

    ordered = sorted(ts, key=lambda t: (relevance_score(t), t["updated"]), reverse=True)[:5]
    lines = [
        f"- Ticket #{t['number']}: \"{t['title']}\" | {GL[grp(t['state'])]} | {plain_pri(t['priority'])} | Updated: {t['updated'][:10]}"
        for t in ordered
    ]
    return (
        f"COUNTS: Open={n['open']}, In Progress={n['progress']}, Resolved={n['done']}\n"
        f"TICKETS:\n" + ("\n".join(lines) if lines else "None")
    )


@app.post("/api/chat")
async def chat(b: Chat, u=Depends(cur)):
    ts = await run_in_threadpool(get_tickets, u["email"])
    q = b.message.strip()[:200]

    # Instant deterministic bypass (0ms)
    fast = quick(q, ts)
    if fast:
        return {"reply": fast, "ai": False}

    # Pass only immediate previous interaction turn to avoid context-length slowdown
    hist = [
        {"role": m["role"], "content": str(m.get("content", ""))[:120]}
        for m in b.history[-2:] if m.get("role") in ("user", "assistant")
    ]

    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "keep_alive": "60m",
        "options": ollama_opts(
            temperature=0.0,
            top_k=20,
            top_p=0.7,
            num_predict=45
        ),
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{SYSTEM}\n\n"
                    f"DATA:\n{facts(ts, query=q)}\n\n"
                    "FEW-SHOT EXAMPLES:\n"
                    "User: update on display\n"
                    "Assistant: #75010 Laptop display flickering is currently in progress with normal priority.\n"
                    "User: who won the match?\n"
                    "Assistant: I can only assist with your existing Helix support tickets."
                )
            },
            *hist,
            {"role": "user", "content": q},
        ],
    }

    try:
        r = await async_client.post(f"{OLLAMA}/api/chat", json=payload, timeout=12.0)
        r.raise_for_status()
        text = re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S).strip()
        if not text:
            raise ValueError("empty")
        return {"reply": text, "ai": True}
    except Exception:
        return {"reply": "The AI is busy right now. " + summ(ts), "ai": False}


@app.get("/qr.svg")
def qr(url: str):
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgImage).save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


@app.get("/")
def index():
    return FileResponse("static/index.html")
import assistant
assistant.register(app, globals())

import admin
admin.register(app, globals())