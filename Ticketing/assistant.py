"""Helix assistant: instant rule-based understanding (typo tolerant), optional LLM intent routing,
and conversational ticket creation with explicit confirmation.
Plug-in for main.py:  import assistant; assistant.register(app, globals())"""
import difflib, json, re, time
from datetime import date, datetime, timedelta, timezone

GL = {"open": "open", "progress": "in progress", "done": "resolved"}
PL = {"low": "Low", "normal": "Medium", "high": "High"}
GREET = ("hi", "hii", "hello", "helo", "hey", "hiya", "yo", "greetings", "morning", "afternoon", "evening", "howdy")
THANKS = ("thanks", "thank", "thx", "cheers", "ty", "appreciated")
TICK = ("ticket", "tickets", "case", "cases", "request", "requests")
PROG = ("progress", "pending", "working", "ongoing", "waiting", "inprogress")
DONE = ("closed", "done", "resolved", "finished", "completed", "solved", "fixed")
OPEN_ = ("open", "opened", "new", "unresolved")
ACTIVE = ("active", "remaining", "outstanding")
HIGHW = ("high", "urgent", "critical", "important")
COUNTW = ("count", "total", "number")
SUMW = ("summary", "summarize", "summarise", "overview", "recap")
LATEST = ("latest", "last", "recent", "newest")
OLDEST = ("oldest", "earliest", "first")
PROBLEM_WORDS = {"broken", "crash", "crashed", "crashing", "error", "errors", "slow", "down", "unable", "issue",
                 "problem", "failed", "failing", "fails", "stuck", "flickering", "flicker", "freezing", "freeze",
                 "frozen", "disconnecting", "disconnected", "blank", "locked", "virus", "hacked", "lost", "missing",
                 "expired", "laggy", "overheating", "noisy", "dead", "cracked", "leaking", "malfunctioning"}
PROBLEM_PHRASES = ("not working", "doesn't work", "doesnt work", "does not work", "can't", "cant ", "cannot",
                   "won't", "wont ", "not responding", "no internet", "not connecting", "not loading",
                   "not opening", "not turning on", "keeps ", "not able")
STOP = (set(GREET + THANKS + TICK + PROG + DONE + OPEN_ + ACTIVE + HIGHW + COUNTW + SUMW + LATEST + OLDEST)
        | set("the a an of my me is it to for on in and what whats how many much have i do there was are with this "
              "that please show tell give any update updates about which who when where list all status ticket "
              "can you could would like want need help about some one".split()))
CATKW = [("Network", ("wifi", "wi-fi", "network", "vpn", "internet", "lan", "router", "ethernet", "connection")),
         ("Account", ("password", "login", "log in", "sign in", "signin", "account", "locked out", "permission", "2fa", "mfa")),
         ("Hardware", ("printer", "laptop", "monitor", "keyboard", "mouse", "screen", "display", "battery", "charger",
                       "hardware", "headset", "webcam", "docking")),
         ("Billing", ("invoice", "billing", "payment", "refund", "bill")),
         ("Feature Request", ("feature", "enhancement", "suggestion", "would be nice")),
         ("Technical", ("software", "install", "app", "application", "crash", "error", "bug", "outlook", "email",
                        "excel", "teams", "update", "slow", "server", "database", "website"))]
C1 = re.compile(r"\b(?:create|raise|make|submit|log|file|start|send|add|register)\b\s*(?:me\s+)?"
                r"(?:a\s+|an\s+|new\s+|another\s+|one\s+|my\s+)*(?:ticket|case|request|incident)\b", re.I)
C2 = re.compile(r"\bopen\s+(?:a|an|new|another)\s+(?:new\s+)?(?:ticket|case|request|incident)\b", re.I)
C3 = re.compile(r"\bnew\s+(?:ticket|case|request|incident)\b", re.I)
ROUTER = ("Classify the message sent to a support-ticket portal assistant. Reply with JSON only.\n"
          'Keys: intent (count|list|detail|summary|create|greeting|help|other), status (open|progress|resolved|any), '
          'query (ticket number or keywords, else ""), issue (the problem if the user reports one, else "").\n'
          'Examples:\n"how many cases are still pending" -> {"intent":"count","status":"progress","query":"","issue":""}\n'
          '"my wifi keeps dropping" -> {"intent":"create","status":"any","query":"","issue":"my wifi keeps dropping"}\n'
          '"what happened with the printer one" -> {"intent":"detail","status":"any","query":"printer","issue":""}')


def grp(s):
    return "done" if s in ("closed", "merged") else "open" if s == "new" else "progress"


def ppri(p):
    return re.sub(r"^\d\s*", "", str(p)).lower()


def short(t):
    return "#" + t["number"] + " " + t["title"]


def line(t):
    return f"{short(t)} is {GL[grp(t['state'])]}, {PL.get(ppri(t['priority']), 'Medium')} priority, updated {t['updated'][:10]}."


def counts(ts):
    n = {"open": 0, "progress": 0, "done": 0}
    for t in ts:
        n[grp(t["state"])] += 1
    return n


def summ(ts):
    n = counts(ts)
    return f"You have {len(ts)} tickets: {n['open']} open, {n['progress']} in progress, {n['done']} resolved."


def toks(msg):
    return re.findall(r"[a-z0-9']+", msg.lower().replace("\u2019", "'"))


def fz(w, vocab):
    if w in vocab:
        return True
    return len(w) >= 5 and any(v[0] == w[0] for v in difflib.get_close_matches(w, vocab, n=3, cutoff=0.86))


def has(words, vocab):
    return any(fz(w, vocab) for w in words)


def stem(w):
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def content(text):
    return {stem(w) for w in toks(text) if w not in STOP and len(w) > 2}


def by_number(text, ts):
    for num in re.findall(r"\d{4,}", text):
        for t in ts:
            if t["number"] == num:
                return t
    return None


def by_title(text, ts, need=1):
    words = content(text)
    best, score = None, 0
    for t in ts:
        s = len(words & {stem(w) for w in toks(t["title"])})
        if s > score:
            best, score = t, s
    return best if score >= need else None


def fmt_items(items, n=8):
    s = "; ".join(short(t) for t in items[:n])
    return s + (f"; and {len(items) - n} more" if len(items) > n else "")


def status_want(words, low):
    if "in progress" in low or has(words, PROG):
        return "progress"
    if has(words, DONE):
        return "done"
    if has(words, ACTIVE):
        return "active"
    if has(words, OPEN_):
        return "open"
    return None


def status_reply(ts, want):
    sets = {"open": ["open"], "progress": ["progress"], "done": ["done"], "active": ["open", "progress"]}
    label = {"open": "open", "progress": "in progress", "done": "resolved", "active": "active"}
    items = [t for t in ts if grp(t["state"]) in sets[want]]
    if not items:
        return f"You have no {label[want]} tickets. {summ(ts)}"
    n = len(items)
    return f"You have {n} {label[want]} ticket" + ("" if n == 1 else "s") + ": " + fmt_items(items) + "."


def period_reply(ts, per):
    today = datetime.now(timezone.utc).date()
    start = {"today": today, "yesterday": today - timedelta(days=1), "week": today - timedelta(days=6),
             "month": today - timedelta(days=29)}[per]
    end = today - timedelta(days=1) if per == "yesterday" else today
    label = {"today": "today", "yesterday": "yesterday", "week": "in the last 7 days", "month": "in the last 30 days"}[per]
    items = [t for t in ts if start <= date.fromisoformat(t["created"][:10]) <= end]
    if not items:
        return f"You haven't created any tickets {label}."
    return f"You created {len(items)} ticket" + ("" if len(items) == 1 else "s") + f" {label}: " + fmt_items(items) + "."


def understand(msg, ts):
    low = msg.lower().replace("\u2019", "'")
    words = toks(low)
    if not words:
        return None, None
    wset = set(words)
    tick = has(words, TICK)
    if len(words) <= 4 and has(words, THANKS) and not tick:
        return "thanks", None
    w0 = words[1] if words[0] == "good" and len(words) > 1 else words[0]
    if w0 in GREET and len(words) <= 5 and not tick and not (wset & {"many", "much", "create", "raise"}):
        return "greeting", None
    m = C1.search(low) or C2.search(low) or C3.search(low)
    if m:
        rest = re.sub(r"^\s*(?:for|about|regarding|on|that|saying|because|:|-|,)\s*", "", msg[m.end():], flags=re.I).strip(" :,-")
        before = msg[:m.start()].strip(" :,-.")
        before = make_title(before)
        if len(before.split()) < 2:
            before = ""
        return "create", (rest if len(rest.split()) >= len(before.split()) else before)
    if "help" in wset and len(words) <= 3 or "what can you do" in low or wset & {"commands", "menu", "options"}:
        return "help", None
    if any(w in PROBLEM_WORDS for w in words) or any(p in low for p in PROBLEM_PHRASES):
        if not tick and not (wset & {"how", "many", "which", "what", "status", "summary"}):
            return "problem", msg
    if re.search(r"\d{4,}", low):
        return "detail", by_number(low, ts)
    if has(words, SUMW):
        return "summary", None
    for per, keys in (("today", ("today",)), ("yesterday", ("yesterday",)), ("week", ("week", "weekly")), ("month", ("month", "monthly"))):
        if wset & set(keys) and (tick or wset & {"many", "much", "create", "created", "raised", "sent"}):
            return "period", per
    if has(words, HIGHW) and (tick or len(words) <= 3):
        return "high", None
    if has(words, OLDEST) and (tick or len(words) <= 3):
        return "oldest", None
    if has(words, LATEST) and (tick or len(words) <= 3):
        return "latest", None
    want = status_want(words, low)
    counting = ("how" in wset and bool(wset & {"many", "much"})) or has(words, COUNTW)
    t = by_title(low, ts)
    if t and not counting:
        return "detail", t
    if counting:
        return "count", want or "all"
    if want:
        return "list", want
    if tick and not content(low):
        return ("list", "all") if wset & {"list", "show", "all"} else ("summary", None)
    if "status" in wset and not content(low):
        return "summary", None
    return None, None


# ---------------------------------------------------------------- ticket drafting
def guess_priority(text):
    t = text.lower()
    if re.search(r"\b(urgent|asap|critical|emergency|immediately|outage)\b|\b(server|system|network|website|site|service)\b.{0,20}\bdown\b", t):
        return "high"
    if re.search(r"\b(minor|low priority|whenever|no rush|not urgent|cosmetic|when you can)\b", t):
        return "low"
    return "normal"


def guess_category(text):
    t = text.lower()
    for cat, kws in CATKW:
        if any(re.search(r"\b" + re.escape(k) + r"\b", t) for k in kws):
            return cat
    return "General"


FILLER = re.compile(r"^(?:please|pls|kindly|can you|could you|i want to|i would like to|i need to|i need|i want|help me|"
                    r"i have|there is|there's|my issue is|issue:|problem:)\s+", re.I)


def make_title(text):
    t = re.sub(r"\s+", " ", text).strip()
    prev = None
    while prev != t:
        prev = t
        t = FILLER.sub("", t)
    t = t.strip(" .!?,-:")
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    return t if len(t) <= 80 else t[:80].rsplit(" ", 1)[0] + "..."


def similar(text, ts):
    live = [t for t in ts if grp(t["state"]) != "done"]
    return by_title(text, live, need=2)


def draft_msg(d, ts, intro):
    rows = [intro, "", "Title: " + d["title"], "Category: " + d["category"], "Priority: " + PL[d["priority"]]]
    if d["body"].strip().lower() != d["title"].lower():
        rows.append("Details: " + d["body"][:200])
    sim = similar(d["body"], ts)
    if sim:
        rows += ["", f"Note: you already have {short(sim)} ({GL[grp(sim['state'])]})."]
    rows += ["", "Say yes to submit, or tell me what to change (priority, category or title)."]
    return "\n".join(rows)


def build_draft(text):
    body = re.sub(r"\s+", " ", text).strip()
    return {"title": make_title(body), "body": body[:900], "priority": guess_priority(body),
            "category": guess_category(body), "stage": "confirm", "t": time.time()}


CONFIRM = ("yes", "y", "yep", "yeah", "yup", "confirm", "confirmed", "submit", "ok", "okay", "sure", "proceed", "send", "correct", "approve")


def is_confirm(words, low):
    if not words:
        return False
    if any(p in low for p in ("go ahead", "create it", "do it", "looks good", "that's right", "thats right", "submit it", "send it")):
        return True
    return len(words) <= 3 and words[0] in CONFIRM


def is_cancel(words, low):
    if not words:
        return False
    if any(p in low for p in ("never mind", "nevermind", "forget it", "don't create", "dont create", "do not create", "discard")):
        return True
    if "cancel" in words:
        return True
    return len(words) <= 3 and (words[0] in ("nope", "nah", "stop") or
                                (words[0] == "no" and (len(words) == 1 or words[1] in ("thanks", "thank", "cancel", "stop"))))


def apply_edits(p, msg, cats):
    m = msg.lower()
    n = len(toks(m))
    changed = False
    mt = re.match(r"^\s*(?:change |set |make |update )?(?:the )?(?:title|subject)(?: to| as| is)?\s*[:\-]?\s*(.+)$", msg, re.I)
    if mt:
        t = mt.group(1).strip(" .")[:120]
        p["title"] = t[:1].upper() + t[1:]
        changed = True
    pm = re.search(r"\b(low|medium|normal|high|urgent|critical)\b", m)
    if pm and (re.search(r"priority|prio|make it|set it|mark it|change", m) or n <= 2):
        p["priority"] = {"low": "low", "medium": "normal", "normal": "normal", "high": "high", "urgent": "high", "critical": "high"}[pm.group(1)]
        changed = True
    for c in cats:
        if re.search(r"\b" + re.escape(c.lower()) + r"\b", m) and (re.search(r"categor|change|set|make|mark|file", m) or n <= 3):
            p["category"] = c
            changed = True
            break
    return changed


def register(app, ns):
    from fastapi import Depends
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
    import os

    cur, get_tickets, create, New, zam = ns["cur"], ns["get_tickets"], ns["create"], ns["New"], ns["zam"]
    cats = ns.get("CATS", ["Account", "Technical", "Network", "Hardware", "Billing", "Feature Request", "General"])
    DEF = [["How many tickets?", "How many tickets do I have?"], ["In progress", "Which tickets are in progress?"],
           ["Summary", "Summarize my tickets"], ["Create a ticket", "Create a ticket"]]
    DRAFT = [["Yes, submit", "yes"], ["High priority", "make it high priority"], ["Cancel", "cancel"]]

    def R(reply, chips=None, created=None):
        out = {"reply": reply, "chips": chips if chips is not None else DEF, "ai": False}
        if created:
            out["created"] = created
        return out

    class In(BaseModel):
        message: str

    async def last_agent_msg(email, tid):
        try:
            arts = await run_in_threadpool(zam, "GET", f"/ticket_articles/by_ticket/{tid}?expand=true", email)
        except Exception:
            return ""
        for a in reversed(arts):
            if a.get("internal") or a.get("sender") == "Customer":
                continue
            body = re.sub(r"<[^>]+>", "", a.get("body") or "").strip()
            if body:
                return f"\nLast reply from {a.get('from') or 'support'} ({a['created_at'][:10]}): \"{body[:200]}\""
        return ""

    async def llm_route(msg, ts):
        try:
            r = await ns["async_client"].post(f"{ns['OLLAMA']}/api/chat", timeout=15.0, json={
                "model": ns["MODEL"], "stream": False, "think": False, "format": "json", "keep_alive": "60m",
                "options": ns["ollama_opts"](temperature=0.0, num_predict=80),
                "messages": [{"role": "system", "content": ROUTER}, {"role": "user", "content": msg[:300]}]})
            r.raise_for_status()
            j = json.loads(r.json()["message"]["content"])
            intent = str(j.get("intent", "other")).lower()
            st = {"open": "open", "progress": "progress", "resolved": "done", "done": "done"}.get(str(j.get("status", "")).lower(), "all")
            q, issue = str(j.get("query") or ""), str(j.get("issue") or "")
        except Exception:
            return "other", None
        if intent in ("count", "list"):
            return intent, st
        if intent in ("summary", "greeting", "help"):
            return intent, None
        if intent == "detail":
            return "detail", by_number(q, ts) or by_title(q, ts)
        if intent == "create":
            return "create", issue
        return "other", None

    async def do_create(u, p):
        try:
            body = New(title=p["title"], body=p["body"] + "\n\n(Created via the Helix assistant.)",
                       priority=p["priority"], category=p["category"], files=[])
            res = await run_in_threadpool(create, body, u)
        except Exception as e:
            return R("I couldn't create the ticket: " + str(getattr(e, "detail", e)) + " Please try again.", DRAFT)
        u.pop("pending", None)
        return R(f"Done! I created ticket #{res['number']}: {p['title']} ({PL[p['priority']]} priority). "
                 "You can follow it under My Tickets.", [["How many tickets?", "How many tickets do I have?"], ["Summary", "Summarize my tickets"]],
                 created={"id": res["id"], "number": res["number"]})

    def start_draft(u, ts, text, intro):
        d = build_draft(text)
        if not d["title"]:
            u["pending"] = {"stage": "issue", "t": time.time()}
            return R("Please describe the problem in a sentence or two.", [["Cancel", "cancel"]])
        u["pending"] = d
        return R(draft_msg(d, ts, intro), DRAFT)

    async def act(u, intent, d, msg, ts):
        first = u["name"].split(" ")[0]
        if intent == "greeting":
            return R(f"Hi {first}! I can check your tickets, show their status, or create a ticket for you. What do you need?")
        if intent == "thanks":
            return R("You're welcome! Anything else I can help with?")
        if intent == "help":
            return R("I can tell you how many tickets you have, list open, in-progress or resolved ones, show the status of a "
                     "specific ticket (try its number or a few words from the title), and create a ticket for you. "
                     "Just describe your problem, or say 'create a ticket'.")
        if intent == "create":
            if not (d or "").strip():
                u["pending"] = {"stage": "issue", "t": time.time()}
                return R("Sure. Describe the problem in a sentence or two and I'll prepare the ticket.", [["Cancel", "cancel"]])
            return start_draft(u, ts, d, "Sure. Here's the ticket I'll create:")
        if intent == "problem":
            return start_draft(u, ts, d, "That sounds like an issue. I can raise a ticket for you:")
        if intent == "other" or intent is None:
            return R("I'm not sure I understood that. I can check your tickets and their status, or create a new ticket for you. "
                     "Try 'summary', 'which tickets are open?' or describe a problem.")
        if not ts:
            return R("You have no tickets yet. Describe a problem and I'll create one for you.", [["Create a ticket", "Create a ticket"]])
        if intent == "summary":
            s = summ(ts)
            hi = [x for x in ts if grp(x["state"]) != "done" and ppri(x["priority"]) == "high"]
            if hi:
                s += " High priority and active: " + ", ".join(short(x) for x in hi[:5]) + "."
            return R(s + " Latest update: " + short(ts[0]) + " is " + GL[grp(ts[0]["state"])] + ".")
        if intent == "count":
            return R(summ(ts) if d == "all" else status_reply(ts, d))
        if intent == "list":
            if d == "all":
                return R("Your tickets: " + "; ".join(f"{short(t)} ({GL[grp(t['state'])]})" for t in ts[:8]) + ".")
            return R(status_reply(ts, d))
        if intent == "detail":
            if not d:
                return R("I couldn't find a matching ticket. Try the ticket number, for example #" + ts[0]["number"] + ".")
            return R(line(d) + await last_agent_msg(u["email"], d["id"]))
        if intent == "latest":
            return R("Your latest update: " + line(ts[0]))
        if intent == "oldest":
            return R("Your oldest ticket: " + line(min(ts, key=lambda x: x["created"])))
        if intent == "high":
            hi = [x for x in ts if grp(x["state"]) != "done" and ppri(x["priority"]) == "high"]
            return R(("High priority tickets still active: " + fmt_items(hi) + ".") if hi else "You have no active high priority tickets.")
        if intent == "period":
            return R(period_reply(ts, d))
        return R("I'm not sure I understood that.")

    async def respond(u, msg, ts):
        intent, d = understand(msg, ts)
        ai = False
        if intent is None:
            intent, d = await llm_route(msg, ts)
            ai = intent != "other"
        out = await act(u, intent, d, msg, ts)
        out["ai"] = ai
        return out

    def pending_step(u, p, msg, ts):
        words, low = toks(msg), msg.lower().strip()
        if is_cancel(words, low):
            u.pop("pending", None)
            return R("Okay, I didn't create a ticket. Anything else?")
        if p["stage"] == "issue":
            if understand(msg, ts)[0] in ("count", "list", "summary", "detail", "latest", "oldest", "high", "period", "greeting", "help", "thanks"):
                return None
            d = build_draft(msg)
            if not d["title"]:
                return R("Please describe the problem in a sentence or two.", [["Cancel", "cancel"]])
            u["pending"] = d
            return R(draft_msg(d, ts, "Got it. Here's the ticket I'll create:"), DRAFT)
        if is_confirm(words, low):
            return "CONFIRM"
        if apply_edits(p, msg, cats):
            p["t"] = time.time()
            return R(draft_msg(p, ts, "Updated. Here's the ticket now:"), DRAFT)
        if understand(msg, ts)[0] in ("count", "list", "summary", "detail", "latest", "oldest", "high", "period", "greeting", "help", "thanks"):
            return None
        return R("Say yes to submit this ticket, tell me what to change, or say cancel.", DRAFT)

    @app.post("/api/assistant")
    async def assistant(b: In, u=Depends(cur)):
        msg = b.message.strip()[:500]
        if not msg:
            return R("Type a message and I'll help.")
        ts = await run_in_threadpool(get_tickets, u["email"])
        p = u.get("pending")
        if p and time.time() - p["t"] > 600:
            u.pop("pending", None)
            p = None
        if p:
            out = pending_step(u, p, msg, ts)
            if out == "CONFIRM":
                return await do_create(u, p)
            if out:
                return out
            reply = await respond(u, msg, ts)
            if u.get("pending"):
                reply["reply"] += "\n\nYour draft ticket is still waiting. Say yes to submit or cancel."
                reply["chips"] = DRAFT
            return reply
        return await respond(u, msg, ts)

    here = os.path.dirname(os.path.abspath(__file__))

    @app.get("/assistant.js")
    def assistant_js():
        return FileResponse(os.path.join(here, "assistant.js"), media_type="application/javascript")
