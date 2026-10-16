"""Helix admin add-on: all-ticket timeline + create customer accounts.
Plug in at the very end of main.py (after the assistant lines):
    import admin
    admin.register(app, globals())
"""
import os, re, time
from concurrent.futures import ThreadPoolExecutor

# Zammad roles allowed into the admin view. Add "Agent" if agents should see it too.
ADMIN_ROLES = {"Admin"}


def register(app, ns):
    from fastapi import Depends, HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel

    cur, zam, CATS = ns["cur"], ns["zam"], ns["CATS"]
    tag_cache = {}                  # ticket id -> category (tags rarely change)
    snap = {"t": 0.0, "rows": []}   # short cache of the full ticket list

    def check(u):
        """True if the signed-in portal user has an admin role in Zammad (cached in the session)."""
        if "is_admin" not in u:
            me = zam("GET", "/users/me?expand=true", u["email"])
            u["is_admin"] = bool(ADMIN_ROLES & set(me.get("roles") or []))
        return u["is_admin"]

    def admin(u=Depends(cur)):
        if not check(u):
            raise HTTPException(403, "Admins only.")
        return u

    def category(tid):
        if tid in tag_cache:
            return tag_cache[tid]
        try:
            res = zam("GET", f"/tags?object=Ticket&o_id={tid}")
            tags = res.get("tags", []) if isinstance(res, dict) else res
            cat = next((x for x in tags if x in CATS), "General")
        except Exception:
            return "General"        # don't cache a failed lookup
        tag_cache[tid] = cat
        return cat

    def all_tickets(fresh=False):
        if not fresh and time.time() - snap["t"] < 15:
            return snap["rows"]
        raw, page = [], 1
        while page <= 10:           # up to 1000 tickets
            data = zam("GET", f"/tickets?expand=true&per_page=100&page={page}")
            raw += data
            if len(data) < 100:
                break
            page += 1
        todo = [t["id"] for t in raw if t["id"] not in tag_cache]
        if todo:
            with ThreadPoolExecutor(8) as ex:
                list(ex.map(category, todo))
        rows = []
        for t in raw:
            done = t["state"] in ("closed", "merged")
            rows.append({
                "id": t["id"], "number": t["number"], "title": t["title"], "state": t["state"],
                "priority": t["priority"], "created": t["created_at"], "updated": t["updated_at"],
                "closed": (t.get("close_at") or t["updated_at"]) if done else None,
                "owner": t.get("owner"), "customer": t.get("customer"),
                "category": tag_cache.get(t["id"], "General"),
            })
        rows.sort(key=lambda r: r["created"])
        snap.update(t=time.time(), rows=rows)
        return rows

    class NewUser(BaseModel):
        firstname: str
        lastname: str = ""
        email: str
        password: str

    @app.get("/api/admin/me")
    def admin_me(u=Depends(cur)):
        return {"admin": check(u)}

    @app.get("/api/admin/tickets")
    def admin_tickets(refresh: int = 0, u=Depends(admin)):
        return all_tickets(fresh=bool(refresh))

    @app.post("/api/admin/users")
    def admin_new_user(b: NewUser, u=Depends(admin)):
        email = b.email.strip().lower()
        if not b.firstname.strip():
            raise HTTPException(422, "Add a first name.")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise HTTPException(422, "Enter a valid email address.")
        if len(b.password) < 8:
            raise HTTPException(422, "Password must be at least 8 characters.")
        rid = next((r["id"] for r in zam("GET", "/roles") if r.get("name") == "Customer"), None)
        if rid is None:
            raise HTTPException(500, "Customer role not found in Zammad.")
        zam("POST", "/users", json={
            "firstname": b.firstname.strip(), "lastname": b.lastname.strip(), "email": email,
            "login": email, "password": b.password, "role_ids": [rid], "active": True})
        return {"ok": True, "email": email}

    class Edit(BaseModel):
        title: str
        priority: str = "normal"
        category: str = "General"
        owner_id: int = 1            # 1 = unassigned in Zammad
        note: str = ""               # optional note shown to the customer
        close: bool = False
        closed_at: str = ""          # ISO time picked in the calendar (applied only if Zammad accepts it)

    @app.get("/api/admin/agents")
    def admin_agents(u=Depends(admin)):
        data = zam("GET", "/users/search?query=*&limit=200&expand=true")
        out = []
        for x in data:
            if x["id"] == 1 or not x.get("active", True) or not ({"Agent", "Admin"} & set(x.get("roles") or [])):
                continue
            nm = f"{x.get('firstname', '')} {x.get('lastname', '')}".strip() or x["email"]
            out.append({"id": x["id"], "name": nm, "email": x.get("email"), "login": x.get("login")})
        return out

    @app.put("/api/admin/tickets/{tid}")
    def admin_edit(tid: int, b: Edit, u=Depends(admin)):
        if not b.title.strip():
            raise HTTPException(422, "Add a subject.")
        body = {"title": b.title.strip()[:120], "priority": ns["PRIOS"].get(b.priority.lower(), "2 normal"),
                "owner_id": b.owner_id}
        if b.close:
            body["state"] = "closed"
        if b.note.strip():
            body["article"] = {"subject": "Ticket update", "body": b.note.strip()[:1000],
                               "type": "note", "internal": False}
        zam("PUT", f"/tickets/{tid}", json=body)
        cat = b.category if b.category in CATS else "General"
        old = category(tid)
        if cat != old:
            for path, item in (("/tags/remove", old), ("/tags/add", cat)):
                try:
                    zam("POST", path, json={"object": "Ticket", "o_id": tid, "item": item})
                except Exception:
                    pass
            tag_cache[tid] = cat
        applied = None
        if b.close and b.closed_at:
            try:
                zam("PUT", f"/tickets/{tid}", json={"close_at": b.closed_at})
                applied = (zam("GET", f"/tickets/{tid}").get("close_at") or "")[:16] == b.closed_at[:16]
            except Exception:
                applied = False
        snap["t"] = 0.0
        return {"ok": True, "close_at_applied": applied}

    class UserEdit(BaseModel):
        firstname: str
        lastname: str = ""
        password: str = ""           # blank = keep the current password

    def customer(uid):
        """Load a user and make sure it is a plain customer (never edit/remove admins or agents from here)."""
        x = zam("GET", f"/users/{uid}?expand=true")
        roles = set(x.get("roles") or [])
        if "Customer" not in roles or roles & {"Admin", "Agent"}:
            raise HTTPException(403, "Only customer accounts can be changed here.")
        return x

    @app.get("/api/admin/users")
    def admin_users(u=Depends(admin)):
        """All accounts (admins, agents, customers). Only customers can be edited/removed from the portal."""
        out = []
        for x in zam("GET", "/users/search?query=*&limit=300&expand=true"):
            roles = set(x.get("roles") or [])
            role = "admin" if "Admin" in roles else "agent" if "Agent" in roles else "customer" if "Customer" in roles else None
            if x["id"] == 1 or not x.get("email") or not role:
                continue
            nm = f"{x.get('firstname', '')} {x.get('lastname', '')}".strip() or x["email"]
            out.append({"id": x["id"], "name": nm, "firstname": x.get("firstname") or "",
                        "lastname": x.get("lastname") or "", "email": x["email"], "role": role,
                        "active": bool(x.get("active", True))})
        out.sort(key=lambda r: ({"admin": 0, "agent": 1, "customer": 2}[r["role"]], r["name"].lower()))
        return out

    @app.put("/api/admin/users/{uid}")
    def admin_edit_user(uid: int, b: UserEdit, u=Depends(admin)):
        if not b.firstname.strip():
            raise HTTPException(422, "Add a first name.")
        if b.password and len(b.password) < 8:
            raise HTTPException(422, "Password must be at least 8 characters.")
        customer(uid)
        body = {"firstname": b.firstname.strip(), "lastname": b.lastname.strip()}
        if b.password:
            body["password"] = b.password
        zam("PUT", f"/users/{uid}", json=body)
        return {"ok": True}

    @app.delete("/api/admin/users/{uid}")
    def admin_del_user(uid: int, u=Depends(admin)):
        x = customer(uid)
        deactivated = False
        try:
            zam("DELETE", f"/users/{uid}")
        except HTTPException:        # Zammad refuses to delete customers that still have tickets
            zam("PUT", f"/users/{uid}", json={"active": False})
            deactivated = True
        except Exception:
            pass                     # deleted, empty response body
        email = x.get("email")
        for k in [k for k, v in ns["sessions"].items() if v["email"] == email]:
            ns["sessions"].pop(k, None)
        ns["cache"].pop(email, None)
        return {"ok": True, "deactivated": deactivated}

    here = os.path.dirname(os.path.abspath(__file__))

    @app.get("/admin.js")
    def admin_js():
        return FileResponse(os.path.join(here, "admin.js"), media_type="application/javascript")
