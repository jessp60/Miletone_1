#!/usr/bin/env python3
import json, os, re, sqlite3, sys, hashlib, secrets, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

DB="/app/data.sqlite3"
PORT=int(sys.argv[1]) if len(sys.argv)>1 else 8000
NODE=(PORT-8000)//1_000 if False else PORT-8000
NODE={8000:0,8001:1,8002:2}.get(PORT,0)
os.makedirs("/app",exist_ok=True)

def db():
    c=sqlite3.connect(DB,timeout=8)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=8000")
    return c
def init():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0);
    """)
    c.commit(); c.close()
init()

def userobj(r):
    return {k:r[k] for k in ("id","username","display_name","timezone","avatar_url","status_text","status_emoji")}
def wsobj(r): return {k:r[k] for k in ("id","slug","name","owner_id","join_mode")}
def chobj(r): return {k:(bool(r[k]) if k in ("is_private","is_dm","is_archived") else r[k]) for k in ("id","workspace_id","name","is_private","is_dm","topic","is_archived")}
def body(h):
    try:
        n=int(h.headers.get("Content-Length","0")); raw=h.rfile.read(n)
        if not raw:return {}
        return json.loads(raw)
    except Exception:
        return None
class H(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*a): pass
    def send(self,status,obj=None,headers=None):
        b=(json.dumps(obj,separators=(",",":")).encode() if obj is not None else b"")
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(b)))
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        if b:self.wfile.write(b)
    def auth(self,required=True):
        x=self.headers.get("Authorization","")
        tok=x[7:] if x.startswith("Bearer ") else ""
        c=db(); r=c.execute("SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?",(tok,)).fetchone(); c.close()
        if not r and required: self.send(401,{"error":"unauthorized"}); return None
        return r
    def route(self): return [unquote(x) for x in urlparse(self.path).path.split("/") if x]
    def do_GET(self):
        p=self.route()
        if p==["api","health"]: return self.send(200,{"status":"ok","node_id":NODE})
        if p[:3]==["api","auth","me"]:
            u=self.auth(); return u and self.send(200,{"user":userobj(u)})
        u=self.auth()
        if not u:return
        c=db()
        try:
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="invitations":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                if not me or me["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                rows=c.execute("SELECT * FROM invitations WHERE workspace_id=? AND revoked=0",(w["id"],)).fetchall()
                out=[{"code":x["code"],"workspace_id":x["workspace_id"],"max_uses":x["max_uses"],"uses":x["uses"],"invited_user_id":x["invited_user_id"]} for x in rows if not (x["expires_at"] and x["expires_at"]<time.time()) and x["uses"] < (1 if x["invited_user_id"] else x["max_uses"])]
                return self.send(200,{"invitations":out})
            if p==["api","workspaces"]:
                rows=c.execute("SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?",(u["id"],)).fetchall()
                return self.send(200,{"workspaces":[wsobj(x) for x in rows]})
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="members":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                rows=c.execute("SELECT user_id,role FROM members WHERE workspace_id=?",(w["id"],)).fetchall()
                return self.send(200,{"members":[dict(x) for x in rows]})
            if len(p)==3 and p[:2]==["api","workspaces"]:
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                chans=c.execute("SELECT * FROM channels WHERE workspace_id=? AND (is_private=0 OR id IN (SELECT channel_id FROM channel_members WHERE user_id=?))",(w["id"],u["id"])).fetchall()
                return self.send(200,{"workspace":wsobj(w),"channels":[chobj(x) for x in chans]})
        finally:c.close()
        self.send(404,{"error":"not found"})
    def do_DELETE(self):
        p=self.route(); u=self.auth()
        if not u:return
        c=db()
        try:
            if len(p)==5 and p[:2]==["api","workspaces"] and p[3]=="invitations":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                if not me or me["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                inv=c.execute("SELECT 1 FROM invitations WHERE code=? AND workspace_id=?",(p[4],w["id"])).fetchone()
                if not inv:return self.send(404,{"error":"not found"})
                c.execute("UPDATE invitations SET revoked=1 WHERE code=?",(p[4],)); c.commit(); return self.send(200,{"deleted":True})
        finally:c.close()
        self.send(404,{"error":"not found"})
    def do_PATCH(self):
        p=self.route(); d=body(self); u=self.auth()
        if not u:return
        if d is None:return self.send(400,{"error":"malformed json"})
        c=db()
        try:
            if len(p)==3 and p[:2]==["api","workspaces"]:
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                if not me or me["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                if "join_mode" in d and d["join_mode"] not in ("open","invite_only"):return self.send(400,{"error":"invalid join_mode"})
                if "name" in d and not d["name"]:return self.send(400,{"error":"invalid"})
                vals=[]; sets=[]
                if "name" in d: sets.append("name=?"); vals.append(d["name"])
                if "join_mode" in d: sets.append("join_mode=?"); vals.append(d["join_mode"])
                if sets:
                    vals.append(w["id"]); c.execute("UPDATE workspaces SET "+",".join(sets)+" WHERE id=?",vals); c.commit()
                return self.send(200,{"workspace":wsobj(c.execute("SELECT * FROM workspaces WHERE id=?",(w["id"],)).fetchone())})
            if len(p)==5 and p[:2]==["api","workspaces"] and p[3]=="members":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                actor=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                target_id=int(p[4]); target=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],target_id)).fetchone()
                role=d.get("role")
                if not actor or actor["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                if role not in ("owner","admin","member","guest"):return self.send(400,{"error":"invalid role"})
                if role=="owner":return self.send(400,{"error":"use transfer_ownership"})
                if not target:return self.send(404,{"error":"not found"})
                if target_id==u["id"] or target["role"]=="owner":return self.send(403,{"error":"forbidden"})
                if actor["role"]=="admin" and target["role"]=="admin":return self.send(403,{"error":"forbidden"})
                c.execute("UPDATE members SET role=? WHERE workspace_id=? AND user_id=?",(role,w["id"],target_id)); c.commit()
                return self.send(200,{"member":{"user_id":target_id,"role":role}})
        finally:c.close()
        self.send(404,{"error":"not found"})
    def do_POST(self):
        p=self.route(); d=body(self)
        if d is None and p in (["api","auth","register"],["api","auth","login"]): return self.send(400,{"error":"malformed json"})
        if p==["api","auth","register"]:
            un=str(d.get("username","")); pw=str(d.get("password",""))
            if not re.fullmatch(r"[A-Za-z0-9_]+",un) or len(pw)<8:return self.send(400,{"error":"invalid"})
            c=db()
            try:
                c.execute("INSERT INTO users(username,password,display_name) VALUES(?,?,?)",(un,pw,d.get("display_name") or un)); uid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
                tok=secrets.token_urlsafe(24); c.execute("INSERT INTO tokens VALUES(?,?)",(tok,uid)); c.commit()
                r=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone(); return self.send(201,{"user":userobj(r),"token":tok})
            except sqlite3.IntegrityError:return self.send(409,{"error":"duplicate"})
            finally:c.close()
        if p==["api","auth","login"]:
            c=db(); r=c.execute("SELECT * FROM users WHERE username=? AND password=?",(d.get("username"),d.get("password"))).fetchone()
            if not r:c.close(); return self.send(401,{"error":"invalid credentials"})
            tok=secrets.token_urlsafe(24); c.execute("INSERT INTO tokens VALUES(?,?)",(tok,r["id"])); c.commit(); c.close()
            return self.send(200,{"user":userobj(r),"token":tok})
        u=self.auth()
        if not u:return
        if d is None:return self.send(400,{"error":"malformed json"})
        c=db()
        try:
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="invitations":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                if not me or me["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                invited=d.get("invited_user_id")
                if invited is not None and not c.execute("SELECT 1 FROM users WHERE id=?",(invited,)).fetchone():return self.send(404,{"error":"user not found"})
                code=secrets.token_urlsafe(18); mx=int(d.get("max_uses",1)); exp=d.get("expires_in_seconds")
                if mx<1:return self.send(400,{"error":"invalid"})
                ea=time.time()+int(exp) if exp is not None else None
                c.execute("INSERT INTO invitations(code,workspace_id,creator_id,expires_at,max_uses,invited_user_id) VALUES(?,?,?,?,?,?)",(code,w["id"],u["id"],ea,mx,invited)); c.commit()
                return self.send(201,{"invitation":{"code":code,"workspace_id":w["id"],"max_uses":mx,"uses":0,"invited_user_id":invited}})
            if len(p)==4 and p[:2]==["api","invitations"] and p[3]=="accept":
                inv=c.execute("SELECT * FROM invitations WHERE code=?",(p[2],)).fetchone()
                if not inv or inv["revoked"] or inv["uses"]>= (1 if inv["invited_user_id"] else inv["max_uses"]) or (inv["expires_at"] and inv["expires_at"]<time.time()):return self.send(404,{"error":"not found"})
                if inv["invited_user_id"] is not None and inv["invited_user_id"]!=u["id"]:return self.send(403,{"error":"forbidden"})
                if c.execute("SELECT 1 FROM members WHERE workspace_id=? AND user_id=?",(inv["workspace_id"],u["id"])).fetchone():return self.send(404,{"error":"already member"})
                c.execute("INSERT INTO members VALUES(?,?,?)",(inv["workspace_id"],u["id"],"member")); c.execute("UPDATE invitations SET uses=uses+1 WHERE code=?",(p[2],)); c.commit()
                return self.send(200,{"accepted":True})
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="invitations":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                if not me or me["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                invited=d.get("invited_user_id")
                if invited is not None and not c.execute("SELECT 1 FROM users WHERE id=?",(invited,)).fetchone():return self.send(404,{"error":"user not found"})
                code=secrets.token_urlsafe(18); mx=int(d.get("max_uses",1)); exp=d.get("expires_in_seconds")
                if mx<1:return self.send(400,{"error":"invalid"})
                ea=time.time()+int(exp) if exp is not None else None
                c.execute("INSERT INTO invitations(code,workspace_id,creator_id,expires_at,max_uses,invited_user_id) VALUES(?,?,?,?,?,?)",(code,w["id"],u["id"],ea,mx,invited)); c.commit()
                return self.send(201,{"invitation":{"code":code,"workspace_id":w["id"],"max_uses":mx,"uses":0,"invited_user_id":invited}})
            if len(p)==4 and p[:2]==["api","invitations"] and p[3]=="accept":
                inv=c.execute("SELECT * FROM invitations WHERE code=?",(p[2],)).fetchone()
                if not inv or inv["revoked"] or inv["uses"]>= (1 if inv["invited_user_id"] else inv["max_uses"]) or (inv["expires_at"] and inv["expires_at"]<time.time()):return self.send(404,{"error":"not found"})
                if inv["invited_user_id"] is not None and inv["invited_user_id"]!=u["id"]:return self.send(403,{"error":"forbidden"})
                if c.execute("SELECT 1 FROM members WHERE workspace_id=? AND user_id=?",(inv["workspace_id"],u["id"])).fetchone():return self.send(404,{"error":"already member"})
                c.execute("INSERT INTO members VALUES(?,?,?)",(inv["workspace_id"],u["id"],"member")); c.execute("UPDATE invitations SET uses=uses+1 WHERE code=?",(p[2],)); c.commit()
                return self.send(200,{"accepted":True})
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="transfer_ownership":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                tid=int(d.get("new_owner_id",0)); target=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],tid)).fetchone()
                if not me or me["role"]!="owner":return self.send(403,{"error":"forbidden"})
                if not target or target["role"]!="admin":return self.send(400,{"error":"target must be admin"})
                c.execute("UPDATE members SET role='admin' WHERE workspace_id=? AND user_id=?",(w["id"],u["id"]))
                c.execute("UPDATE members SET role='owner' WHERE workspace_id=? AND user_id=?",(w["id"],tid))
                c.execute("UPDATE workspaces SET owner_id=? WHERE id=?",(tid,w["id"])); c.commit()
                return self.send(200,{"workspace":wsobj(c.execute("SELECT * FROM workspaces WHERE id=?",(w["id"],)).fetchone())})
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="invitations":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                if not me or me["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                rows=c.execute("SELECT * FROM invitations WHERE workspace_id=? AND revoked=0",(w["id"],)).fetchall()
                out=[{"code":x["code"],"workspace_id":x["workspace_id"],"max_uses":x["max_uses"],"uses":x["uses"],"invited_user_id":x["invited_user_id"]} for x in rows if not (x["expires_at"] and x["expires_at"]<time.time()) and x["uses"] < (1 if x["invited_user_id"] else x["max_uses"])]
                return self.send(200,{"invitations":out})
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="transfer_ownership":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                me=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                tid=int(d.get("new_owner_id",0)); target=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],tid)).fetchone()
                if not me or me["role"]!="owner":return self.send(403,{"error":"forbidden"})
                if not target or target["role"]!="admin":return self.send(400,{"error":"target must be admin"})
                c.execute("UPDATE members SET role='admin' WHERE workspace_id=? AND user_id=?",(w["id"],u["id"]))
                c.execute("UPDATE members SET role='owner' WHERE workspace_id=? AND user_id=?",(w["id"],tid))
                c.execute("UPDATE workspaces SET owner_id=? WHERE id=?",(tid,w["id"])); c.commit()
                return self.send(200,{"workspace":wsobj(c.execute("SELECT * FROM workspaces WHERE id=?",(w["id"],)).fetchone())})
            if p==["api","workspaces"]:
                slug=str(d.get("slug","")); name=str(d.get("name",""))
                if not re.fullmatch(r"[a-z0-9-]{2,32}",slug) or not name:return self.send(400,{"error":"invalid"})
                try:
                    c.execute("INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)",(slug,name,u["id"])); wid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
                    c.execute("INSERT INTO members VALUES(?,?,?)",(wid,u["id"],"owner")); c.execute("INSERT INTO channels(workspace_id,name) VALUES(?,?)",(wid,"general")); cid=c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.execute("INSERT INTO channel_members VALUES(?,?)",(cid,u["id"])); c.commit()
                    w=c.execute("SELECT * FROM workspaces WHERE id=?",(wid,)).fetchone(); ch=c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone()
                    return self.send(201,{"workspace":wsobj(w),"general_channel":chobj(ch)})
                except sqlite3.IntegrityError:return self.send(409,{"error":"duplicate"})
            if len(p)==4 and p[:2]==["api","workspaces"] and p[3]=="channels":
                w=c.execute("SELECT * FROM workspaces WHERE slug=?",(p[2],)).fetchone()
                if not w:return self.send(404,{"error":"not found"})
                m=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone()
                if not m or m["role"]=="guest":return self.send(403,{"error":"forbidden"})
                private=bool(d.get("is_private",False))
                if private and m["role"] not in ("owner","admin"):return self.send(403,{"error":"forbidden"})
                name=str(d.get("name",""))
                if not re.fullmatch(r"[a-z0-9-]{1,32}",name) or len(str(d.get("topic","")))>250:return self.send(400,{"error":"invalid"})
                try:
                    c.execute("INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)",(w["id"],name,int(private),d.get("topic",""))); cid=c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.execute("INSERT INTO channel_members VALUES(?,?)",(cid,u["id"])); c.commit()
                    return self.send(201,{"channel":chobj(c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone())})
                except sqlite3.IntegrityError:return self.send(409,{"error":"duplicate"})
            if len(p)==4 and p[:2]==["api","channels"] and p[3]=="join":
                ch=c.execute("SELECT * FROM channels WHERE id=?",(p[2],)).fetchone()
                if not ch:return self.send(404,{"error":"not found"})
                m=c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?",(ch["workspace_id"],u["id"])).fetchone()
                cm=c.execute("SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?",(ch["id"],u["id"])).fetchone()
                if ch["is_private"] and not cm:return self.send(403,{"error":"forbidden"})
                if m and m["role"]=="guest":return self.send(403,{"error":"forbidden"})
                if not m:
                    w=c.execute("SELECT join_mode FROM workspaces WHERE id=?",(ch["workspace_id"],)).fetchone()
                    if w["join_mode"]!="open" or ch["is_private"]:return self.send(403,{"error":"forbidden"})
                    c.execute("INSERT INTO members VALUES(?,?,?)",(ch["workspace_id"],u["id"],"member"))
                c.execute("INSERT OR IGNORE INTO channel_members VALUES(?,?)",(ch["id"],u["id"])); c.commit()
                return self.send(200,{"channel":chobj(ch)})
        finally:c.close()
        self.send(404,{"error":"not found"})
if __name__=="__main__":
    ThreadingHTTPServer(("127.0.0.1",PORT),H).serve_forever()
