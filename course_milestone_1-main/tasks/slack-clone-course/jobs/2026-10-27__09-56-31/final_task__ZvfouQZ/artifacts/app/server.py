import json, os, re, secrets, sqlite3, sys, hashlib, time, base64, struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

DB='/app/huddle.sqlite3'
PORT=int(sys.argv[1]) if len(sys.argv)>1 else 8000
NODE=(PORT-8000)//1_000 if PORT>=8000 else 0
if PORT in (8000,8001,8002): NODE=PORT-8000

def db():
    c=sqlite3.connect(DB,timeout=8,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=8000')
    return c
def init():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,expires REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0,invited_user_id INTEGER);
    '''); c.close()
init()

def out_user(r):
    return {'id':r['id'],'username':r['username'],'display_name':r['display_name'] or r['username'],'timezone':r['timezone'] or 'UTC','avatar_url':r['avatar_url'] or '','status_text':r['status_text'] or '','status_emoji':r['status_emoji'] or ''}
def out_ws(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def out_ch(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def js(h,code,obj): h.send_response(code); h.send_header('Content-Type','application/json'); h.end_headers(); h.wfile.write(json.dumps(obj).encode())
def body(h):
    try:
        n=int(h.headers.get('Content-Length','0')); return json.loads(h.rfile.read(n) or b'{}')
    except: raise ValueError()
def auth(h,c):
    x=h.headers.get('Authorization','')
    if not x.startswith('Bearer '): return None
    r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone()
    return r

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self): self.go('GET')
    def do_POST(self): self.go('POST')
    def do_PATCH(self): self.go('PATCH')
    def do_DELETE(self): self.go('DELETE')
    def go(self,method):
      p=urlparse(self.path).path; seg=[unquote(x) for x in p.split('/') if x]; c=db()
      try:
        if p=='/api/health': return js(self,200,{'status':'ok','node_id':NODE})
        if p.startswith('/api/ws'):
            return self.websocket(c)
        if len(seg)>=3 and seg[:3]==['api','auth','register'] and method=='POST':
            try: x=body(self)
            except: return js(self,400,{'error':'invalid json'})
            u=x.get('username'); pw=x.get('password')
            if not isinstance(u,str) or not re.fullmatch(r'[A-Za-z0-9_]+',u or '') or not isinstance(pw,str) or len(pw)<8:return js(self,400,{'error':'invalid'})
            try:
              c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,pw,x.get('display_name') or u))
            except sqlite3.IntegrityError:return js(self,409,{'error':'duplicate'})
            uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid))
            return js(self,201,{'user':out_user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':tok})
        if len(seg)>=3 and seg[:3]==['api','auth','login'] and method=='POST':
            try:x=body(self)
            except:return js(self,400,{'error':'invalid json'})
            r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),x.get('password'))).fetchone()
            if not r:return js(self,401,{'error':'unauthorized'})
            tok=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id']));return js(self,200,{'user':out_user(r),'token':tok})
        u=auth(self,c)
        if not u:return js(self,401,{'error':'unauthorized'})
        if p=='/api/auth/me':return js(self,200,{'user':out_user(u)})
        if seg[:2]==['api','users'] and len(seg)==3 and seg[2]=='me' and method=='PATCH':
            try:x=body(self)
            except:return js(self,400,{'error':'invalid json'})
            allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
            if 'timezone' in x and (not isinstance(x['timezone'],str) or len(x['timezone'])>80):return js(self,400,{'error':'invalid'})
            vals={k:x[k] for k in allowed if k in x}
            if vals:c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),u['id']))
            return js(self,200,{'user':out_user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
        if p=='/api/workspaces' and method=='GET':
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();return js(self,200,{'workspaces':[out_ws(x) for x in rows]})
        if p=='/api/workspaces' and method=='POST':
            try:x=body(self)
            except:return js(self,400,{'error':'invalid json'})
            if not isinstance(x.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',x.get('slug','')) or not x.get('name'):return js(self,400,{'error':'invalid'})
            try:c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id']))
            except sqlite3.IntegrityError:return js(self,409,{'error':'duplicate'})
            wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone()
            return js(self,201,{'workspace':out_ws(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':out_ch(ch)})
        if len(seg)>=3 and seg[0:2]==['api','workspaces']:
            slug=seg[2]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return js(self,404,{'error':'not found'})
            mr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); role=mr['role'] if mr else None
            if len(seg)==4 and seg[3]=='members' and method=='GET':
                if not mr:return js(self,403,{'error':'forbidden'})
                rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();return js(self,200,{'members':[dict(x) for x in rows]})
            if len(seg)==3 and method=='PATCH':
                if role not in ('owner','admin'):return js(self,403,{'error':'forbidden'})
                try:x=body(self)
                except:return js(self,400,{'error':'invalid json'})
                if 'join_mode' in x and x['join_mode'] not in ('open','invite_only'):return js(self,400,{'error':'invalid'})
                vals=[] 
                if 'name' in x: vals.append(('name',x['name']))
                if 'join_mode' in x: vals.append(('join_mode',x['join_mode']))
                if vals:c.execute('UPDATE workspaces SET '+','.join(a+'=?' for a,b in vals)+' WHERE id=?',tuple(b for a,b in vals)+(w['id'],))
                return js(self,200,{'workspace':out_ws(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(seg)==4 and seg[3]=='transfer_ownership' and method=='POST':
                if role!='owner':return js(self,403,{'error':'forbidden'})
                try:x=body(self)
                except:return js(self,400,{'error':'invalid'})
                t=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],x.get('new_owner_id'))).fetchone()
                if not t or t['role']!='admin':return js(self,400,{'error':'target'})
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']));c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],x['new_owner_id']));return js(self,200,{'ok':True})
            if len(seg)==4 and seg[3]=='channels' and method=='POST':
                if role not in ('owner','admin','member'):return js(self,403,{'error':'forbidden'})
                try:x=body(self)
                except:return js(self,400,{'error':'invalid json'})
                if not isinstance(x.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',x.get('name','')):return js(self,400,{'error':'invalid'})
                if x.get('is_private') and role not in ('owner','admin'):return js(self,403,{'error':'forbidden'})
                try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')))
                except sqlite3.IntegrityError:return js(self,409,{'error':'duplicate'})
                newch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(newch['id'],u['id'])); return js(self,201,{'channel':out_ch(newch)})
            if len(seg)==4 and seg[3]=='invitations' and method in ('GET','POST'):
                if role not in ('owner','admin'):return js(self,403,{'error':'forbidden'})
                if method=='GET':
                    rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND (expires IS NULL OR expires>?) AND uses<max_uses',(w['id'],time.time())).fetchall()
                    return js(self,200,{'invitations':[dict(x) for x in rows]})
                try:x=body(self)
                except:return js(self,400,{'error':'invalid json'})
                mx=1 if x.get('invited_user_id') else x.get('max_uses',1); exp=x.get('expires_in_seconds'); code=secrets.token_urlsafe(18)
                c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,?)',(code,w['id'],u['id'],time.time()+exp if exp else None,int(mx),0,0,x.get('invited_user_id')));return js(self,201,{'invitation':dict(c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone())})
            if len(seg)==5 and seg[3]=='members' and method=='PATCH':
                if role not in ('owner','admin'):return js(self,403,{'error':'forbidden'})
                tid=int(seg[4]); target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],tid)).fetchone()
                if not c.execute('SELECT id FROM users WHERE id=?',(tid,)).fetchone():return js(self,404,{'error':'not found'})
                try:x=body(self); nr=x['role']
                except:return js(self,400,{'error':'invalid'})
                if nr not in ('owner','admin','member','guest'):return js(self,400,{'error':'invalid'})
                if nr=='owner':return js(self,400,{'error':'transfer'})
                if tid==u['id'] or not target:return js(self,403,{'error':'forbidden'})
                if role=='admin' and target['role'] in ('admin','owner'):return js(self,403,{'error':'forbidden'})
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(nr,w['id'],tid));return js(self,200,{'member':{'user_id':tid,'role':nr}})
        if len(seg)>=3 and seg[0:2]==['api','invitations'] and seg[-1]=='accept' and method=='POST':
            inv=c.execute('SELECT * FROM invitations WHERE code=?',(seg[2],)).fetchone()
            if not inv or inv['revoked'] or inv['uses']>=inv['max_uses'] or (inv['expires'] and inv['expires']<time.time()):return js(self,404,{'error':'not found'})
            if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return js(self,403,{'error':'forbidden'})
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'));c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(seg[2],));return js(self,200,{'ok':True})
        if len(seg)>=4 and seg[0:2]==['api','workspaces'] and seg[3]=='invitations' and len(seg)==5 and method=='DELETE':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(seg[2],)).fetchone();mr=w and c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not w:return js(self,404,{'error':'not found'})
            if not mr or mr['role'] not in ('owner','admin'):return js(self,403,{'error':'forbidden'})
            c.execute('UPDATE invitations SET revoked=1 WHERE code=? AND workspace_id=?',(seg[4],w['id']));return js(self,200,{'deleted':True})
        if len(seg)>=3 and seg[0:2]==['api','channels']:
            try:cid=int(seg[2])
            except:return js(self,404,{'error':'not found'})
            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch:return js(self,404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); mr=c.execute('SELECT m.role FROM members m WHERE m.workspace_id=? AND m.user_id=?',(w['id'],u['id'])).fetchone()
            if seg[-1]=='join' and method=='POST':
                if ch['is_private'] and (not mr or not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()):return js(self,403,{'error':'forbidden'})
                if mr and mr['role']=='guest':return js(self,403,{'error':'forbidden'})
                if not mr:
                    if w['join_mode']!='open' or ch['is_private']:return js(self,403,{'error':'forbidden'})
                    c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
                c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']));return js(self,200,{'channel':out_ch(ch)})
        return js(self,404,{'error':'not found'})
      except Exception as e:
        return js(self,400,{'error':'bad request'})
      finally:c.close()
    def websocket(self,c):
      # Validate query token before completing the websocket handshake.
      q=urlparse(self.path).query; tok=''
      for z in q.split('&'):
        if z.startswith('token='):tok=z[6:]
      if not c.execute('SELECT 1 FROM tokens WHERE token=?',(tok,)).fetchone():
        self.send_error(401); return
      key=self.headers.get('Sec-WebSocket-Key')
      if not key:self.send_error(400);return
      import hashlib
      accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
      self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
      try:
        while self.rfile.read(2): pass
      except: pass

ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
