#!/usr/bin/env python3
import os, re, json, sqlite3, hashlib, secrets, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, parse_qs

DB='/app/state.sqlite3'
PORT=int(os.environ.get('PORT','8000'))
NODE=(PORT-8000)//1_000 if False else PORT-8000
db_lock=threading.Lock()

def conn():
    c=sqlite3.connect(DB, timeout=8, isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA busy_timeout=8000')
    return c

def init():
    c=conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,code TEXT UNIQUE,invited_user_id INTEGER,uses INTEGER DEFAULT 0,max_uses INTEGER DEFAULT 1,expires_at REAL,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS readstate(user_id INTEGER,channel_id INTEGER,mention_count INTEGER DEFAULT 0,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,UNIQUE(channel_id,event_id));
    ''')
    try: c.execute('ALTER TABLE invitations ADD COLUMN expires_at REAL')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE invitations ADD COLUMN revoked INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    c.close()

def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}

def channel_obj(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}

def iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def mention_ids(c,r):
    import re
    members=c.execute('SELECT u.id,u.username FROM channel_members cm JOIN users u ON u.id=cm.user_id WHERE cm.channel_id=?',(r['channel_id'],)).fetchall()
    by={x['username']:x['id'] for x in members}; ids=[]
    if '@channel' in r['body']:
        ids=[x['id'] for x in members if x['id']!=r['author_id']]
    for name in re.findall(r'(?<![A-Za-z0-9_@])@([A-Za-z0-9_]+)',r['body']):
        if name in by and by[name]!=r['author_id'] and by[name] not in ids: ids.append(by[name])
    return ids
def msg_obj(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    reactions=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in (x['ids'] or '').split(',') if z]} for x in rr]
    n=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions,'mentions':mention_ids(c,r)}
def add_event(c,channel_id,kind,payload):
    x=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel_id=?',(channel_id,)).fetchone()['n']
    c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(channel_id,x,kind,json.dumps(payload)))
    return x
def ws_frame(obj):
    b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
    if n<126: h=bytes([129,n])
    elif n<65536: h=bytes([129,126])+n.to_bytes(2,'big')
    else: h=bytes([129,127])+n.to_bytes(8,'big')
    return h+b
def ws_read(sock):
    h=sock.recv(2)
    if len(h)<2:return None
    ln=h[1]&127
    if ln==126: ln=int.from_bytes(sock.recv(2),'big')
    elif ln==127: ln=int.from_bytes(sock.recv(8),'big')
    mask=sock.recv(4); data=b''
    while len(data)<ln:
        z=sock.recv(ln-len(data))
        if not z:return None
        data+=z
    return bytes(data[i]^mask[i%4] for i in range(ln))
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def sendj(self,status,obj):
        b=json.dumps(obj,separators=(',',':')).encode()
        self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def readj(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        try: return json.loads(self.rfile.read(n) or b'{}')
        except: raise ValueError('malformed_json')
    def auth(self):
        x=self.headers.get('Authorization','')
        if not x.startswith('Bearer '): return None
        c=conn(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close()
        return r
    def route(self):
        return [unquote(x) for x in urlparse(self.path).path.split('/') if x]
    def websocket(self):
        import base64,hashlib,socket,time
        q=parse_qs(urlparse(self.path).query); tok=q.get('token',[''])[0]
        c=conn(); u=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok,)).fetchone(); c.close()
        if not u:
            self.send_response(401); self.send_header('Content-Length','0'); self.end_headers(); return
        key=self.headers.get('Sec-WebSocket-Key',''); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
        sock=self.connection; sock.settimeout(.15); channel=None; last=0
        try:
            while True:
                try:
                    raw=ws_read(sock)
                    if raw:
                        d=json.loads(raw); typ=d.get('type'); channel=int(d.get('channel_id',0))
                        c=conn(); head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(channel,)).fetchone()['n']
                        if typ=='subscribe':
                            last=head; sock.sendall(ws_frame({'type':'subscribed','channel_id':channel,'head_event_id':head}))
                        elif typ=='resume':
                            since=int(d.get('since_event_id',0)); rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel,since)).fetchall()
                            for r in rows: sock.sendall(ws_frame({'type':r['kind'],'event_id':r['event_id'],'channel_id':channel,**json.loads(r['payload'])})); last=r['event_id']
                            head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(channel,)).fetchone()['n']; sock.sendall(ws_frame({'type':'resumed','channel_id':channel,'head_event_id':head}))
                        c.close()
                except socket.timeout: pass
                if channel:
                    c=conn(); rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel,last)).fetchall(); c.close()
                    for r in rows:
                        sock.sendall(ws_frame({'type':r['kind'],'event_id':r['event_id'],'channel_id':channel,**json.loads(r['payload'])})); last=r['event_id']
        except Exception: return
    def do_GET(self):
        if self.headers.get('Upgrade','').lower()=='websocket':
            return self.websocket()
        try:
            p=self.route()
            if p==['api','health']: return self.sendj(200,{'status':'ok','node_id':NODE})
            if p[:3]==['api','auth','me']:
                u=self.auth()
                return self.sendj(200,{'user':user_obj(u)}) if u else self.sendj(401,{'error':'unauthorized'})
            if p==['api','workspaces']:
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
                return self.sendj(200,{'workspaces':[dict(r) for r in rows]})
            if len(p)==4 and p[:2]==['api','workspaces'] and p[3]=='members':
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                ok=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not ok:c.close(); return self.sendj(403,{'error':'forbidden'})
                rs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
                return self.sendj(200,{'members':[dict(r) for r in rs]})
            if len(p)==3 and p[:2]==['api','workspaces']:
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                mem=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not mem:c.close(); return self.sendj(403,{'error':'forbidden'})
                include=urlparse(self.path).query in ('include_archived=1','include_archived=true')
                q='SELECT c.* FROM channels c WHERE c.workspace_id=? AND (? OR c.is_archived=0) AND (c.is_private=0 OR c.id IN (SELECT channel_id FROM channel_members WHERE user_id=?) OR c.workspace_id IN (SELECT workspace_id FROM members WHERE workspace_id=? AND user_id=? AND role="owner"))'
                ch=c.execute(q,(w['id'],int(include),u['id'],w['id'],u['id'])).fetchall()
                states={}
                for x in ch:
                    z=c.execute('SELECT * FROM readstate WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone()
                    states[str(x['id'])]={'channel_id':x['id'],'last_read_event_id':z['last_read_event_id'] if z else 0,'unread_count':0,'mention_count':z['mention_count'] if z else 0}
                c.close()
                return self.sendj(200,{'workspace':dict(w),'channels':[channel_obj(x) for x in ch],'read_state':states})
            if len(p)>=2 and p[0:2]==['api','search']:
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                from urllib.parse import parse_qs
                z=parse_qs(urlparse(self.path).query); term=z.get('q',[''])[0]; lim=int(z.get('limit',['50'])[0])
                if lim<1 or lim>200:c=conn(); c.close(); return self.sendj(400,{'error':'invalid_paging'})
                cur=z.get('cursor',[None])[0]
                try: before=int(cur) if cur else 999999999999
                except: return self.sendj(400,{'error':'invalid_cursor'})
                c=conn(); wid=z.get('workspace_id',[None])[0]
                sql='SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? WHERE m.deleted=0 AND m.body LIKE ? AND m.id<?'
                args=[u['id'],'%'+term+'%',before]
                if wid: sql+=' AND ch.workspace_id=?'; args.append(wid)
                sql+=' AND (ch.is_private=0 OR EXISTS(SELECT 1 FROM channel_members x WHERE x.channel_id=ch.id AND x.user_id=?)) ORDER BY m.id DESC LIMIT ?'; args.extend([u['id'],lim+1])
                rs=c.execute(sql,args).fetchall(); more=len(rs)>lim; rs=rs[:lim]; out=[msg_obj(c,x) for x in rs]; nxt=str(rs[-1]['id']) if more and rs else None; c.close()
                return self.sendj(200,{'results':out,'next_cursor':nxt})
            if len(p)==4 and p[0:2]==['api','channels'] and p[3]=='pins':
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                from urllib.parse import parse_qs
                z=parse_qs(urlparse(self.path).query); lim=int(z.get('limit',['50'])[0]); cur=z.get('cursor',[None])[0]
                if lim<1 or lim>200:return self.sendj(400,{'error':'invalid_paging'})
                try: before=int(cur) if cur else 999999999999
                except: return self.sendj(400,{'error':'invalid_cursor'})
                c=conn(); ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():c.close(); return self.sendj(403,{'error':'forbidden'})
                rs=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND p.message_id<? ORDER BY p.message_id DESC LIMIT ?',(ch['id'],before,lim+1)).fetchall(); more=len(rs)>lim; rs=rs[:lim]; out=[{'message':msg_obj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs]; nxt=str(rs[-1]['message_id']) if more and rs else None; c.close()
                return self.sendj(200,{'pins':out,'next_cursor':nxt})
            if len(p)==4 and p[0:2]==['api','channels'] and p[3]=='read':
                u=self.auth(); 
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn();
                if not c.execute('SELECT id FROM channels WHERE id=?',(p[2],)).fetchone(): c.close(); return self.sendj(404,{'error':'not_found'})
                x=c.execute('SELECT * FROM readstate WHERE user_id=? AND channel_id=?',(u['id'],p[2])).fetchone()
                if not x:c.execute('INSERT OR IGNORE INTO readstate(user_id,channel_id) VALUES(?,?)',(u['id'],p[2])); x=c.execute('SELECT * FROM readstate WHERE user_id=? AND channel_id=?',(u['id'],p[2])).fetchone()
                c.close(); return self.sendj(200,{'read_state':{'channel_id':int(p[2]),'last_read_event_id':x['last_read_event_id'],'unread_count':0,'mention_count':x['mention_count']}})
            if len(p)==4 and p[0:2]==['api','channels'] and p[3]=='messages':
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn(); ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                cm=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
                if not cm:c.close(); return self.sendj(403,{'error':'forbidden'})
                q=urlparse(self.path).query; from urllib.parse import parse_qs
                z=parse_qs(q); lim=int(z.get('limit',['50'])[0])
                if lim<1 or lim>200 or 'offset' in z or 'page' in z:c.close(); return self.sendj(400,{'error':'invalid_paging'})
                cur=z.get('cursor',[None])[0]; before=None
                if cur:
                    try: before=int(cur)
                    except: c.close(); return self.sendj(400,{'error':'invalid_cursor'})
                sql='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'+(' AND id<?' if before is not None else '')+' ORDER BY id DESC LIMIT ?'
                args=[ch['id']]+(([before]) if before is not None else [])+[lim+1]
                rs=c.execute(sql,args).fetchall(); more=len(rs)>lim; rs=rs[:lim]; out=[msg_obj(c,x) for x in rs]; nxt=str(rs[-1]['id']) if more and rs else None; c.close()
                return self.sendj(200,{'messages':out,'next_cursor':nxt})
            if len(p)==4 and p[0:2]==['api','messages'] and p[3]=='replies':
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn(); parent=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not parent:c.close(); return self.sendj(404,{'error':'not_found'})
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(parent['channel_id'],u['id'])).fetchone():c.close(); return self.sendj(403,{'error':'forbidden'})
                from urllib.parse import parse_qs
                z=parse_qs(urlparse(self.path).query); lim=int(z.get('limit',['50'])[0])
                if lim<1 or lim>200:c.close(); return self.sendj(400,{'error':'invalid_paging'})
                cur=z.get('cursor',[None])[0]
                try: after=int(cur) if cur else 0
                except: c.close(); return self.sendj(400,{'error':'invalid_cursor'})
                rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id ASC LIMIT ?',(parent['id'],after,lim+1)).fetchall(); more=len(rs)>lim; rs=rs[:lim]; out=[msg_obj(c,x) for x in rs]; nxt=str(rs[-1]['id']) if more and rs else None; c.close()
                return self.sendj(200,{'replies':out,'next_cursor':nxt})
            if len(p)==4 and p[:2]==['api','channels'] and p[3]=='members':
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn(); ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                rs=c.execute('SELECT u.* FROM channel_members cm JOIN users u ON u.id=cm.user_id WHERE cm.channel_id=?',(ch['id'],)).fetchall(); c.close()
                return self.sendj(200,{'members':[user_obj(x) for x in rs]})
            if len(p)==4 and p[0:2]==['api','messages'] and p[3]=='reactions':
                c=conn(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:c.close(); return self.sendj(404,{'error':'not_found'})
                c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r['id'],u['id'],d.get('emoji',''))); rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall(); c.close()
                return self.sendj(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in (x['ids'] or '').split(',') if z]} for x in rows]})
            if len(p)==4 and p[0:2]==['api','workspaces'] and p[3]=='invitations':
                u=self.auth()
                if not u:return self.sendj(401,{'error':'unauthorized'})
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not m or m['role'] not in ('owner','admin'):c.close(); return self.sendj(403,{'error':'forbidden'})
                import time
                rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses < CASE WHEN invited_user_id IS NULL THEN max_uses ELSE 1 END AND (expires_at IS NULL OR expires_at>?)',(w['id'],time.time())).fetchall(); c.close()
                return self.sendj(200,{'invitations':[dict(x) for x in rows]})
            return self.sendj(404,{'error':'not_found'})
        except Exception as e: return self.sendj(500,{'error':str(e)})
    def do_PATCH(self):
        try:
            p=self.route(); d=self.readj(); u=self.auth()
            if not u:return self.sendj(401,{'error':'unauthorized'})
            if len(p)==3 and p[0:2]==['api','workspaces']:
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                a=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not a or a['role'] not in ('owner','admin'):c.close(); return self.sendj(403,{'error':'forbidden'})
                if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):c.close(); return self.sendj(400,{'error':'invalid'})
                sets=[]; vals=[]
                if 'name' in d: sets.append('name=?'); vals.append(d['name'])
                if 'join_mode' in d: sets.append('join_mode=?'); vals.append(d['join_mode'])
                if sets: vals.append(w['id']); c.execute('UPDATE workspaces SET '+','.join(sets)+' WHERE id=?',vals)
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone(); c.close()
                return self.sendj(200,{'workspace':dict(w)})
            if len(p)==3 and p[0:2]==['api','channels']:
                c=conn(); ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone()
                if not m:c.close(); return self.sendj(403,{'error':'forbidden'})
                if len(d.get('topic',''))>250:c.close(); return self.sendj(400,{'error':'invalid_topic'})
                owner=m['role']=='owner'; cm=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
                if not cm and not owner:c.close(); return self.sendj(403,{'error':'forbidden'})
                add_event(c,ch['id'],'channel.updated',{'topic':d.get('topic','')}); c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),ch['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone(); c.close()
                return self.sendj(200,{'channel':channel_obj(ch)})
            if len(p)==3 and p[0:2]==['api','messages']:
                c=conn(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:c.close(); return self.sendj(404,{'error':'not_found'})
                if r['deleted']:c.close(); return self.sendj(409,{'error':'deleted'})
                if r['author_id']!=u['id']:c.close(); return self.sendj(403,{'error':'forbidden'})
                if not isinstance(d.get('body'),str) or not d['body'].strip():c.close(); return self.sendj(400,{'error':'empty_body'})
                c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],iso(),r['id'])); add_event(c,r['channel_id'],'message.updated',{'message_id':r['id']}); r=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone(); out=msg_obj(c,r); c.close()
                return self.sendj(200,{'message':out})
            if len(p)==5 and p[:3]==['api','workspaces',p[2]] and p[3]=='members':
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                actor=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],p[4])).fetchone()
                role=d.get('role')
                if not target:c.close(); return self.sendj(404,{'error':'not_found'})
                if role not in ('owner','admin','member','guest'):c.close(); return self.sendj(400,{'error':'invalid_role'})
                if role=='owner':c.close(); return self.sendj(400,{'error':'invalid_role'})
                if not actor or actor['role'] not in ('owner','admin'):c.close(); return self.sendj(403,{'error':'forbidden'})
                if int(p[4])==u['id'] or target['role']=='owner' or (actor['role']=='admin' and target['role']=='admin'):
                    c.close(); return self.sendj(403,{'error':'forbidden'})
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],p[4])); c.close()
                return self.sendj(200,{'member':{'user_id':int(p[4]),'role':role}})
            return self.sendj(404,{'error':'not_found'})
        except Exception as e:return self.sendj(500,{'error':str(e)})
    def do_POST(self):
        try:
            p=self.route(); d=self.readj()
            if p==['api','auth','register']:
                un=d.get('username',''); pw=d.get('password','')
                if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.sendj(400,{'error':'invalid'})
                c=conn()
                try:
                    c.execute('BEGIN'); c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,hashlib.sha256(pw.encode()).hexdigest(),d.get('display_name') or un)); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); c.execute('COMMIT')
                except sqlite3.IntegrityError:c.execute('ROLLBACK'); c.close(); return self.sendj(409,{'error':'duplicate'})
                r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return self.sendj(201,{'user':user_obj(r),'token':tok})
            if p==['api','auth','login']:
                c=conn(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username',''),hashlib.sha256(d.get('password','').encode()).hexdigest())).fetchone()
                if not r:c.close(); return self.sendj(401,{'error':'invalid_credentials'})
                tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); c.close(); return self.sendj(200,{'user':user_obj(r),'token':tok})
            u=self.auth()
            if not u:return self.sendj(401,{'error':'unauthorized'})
            if len(p)==4 and p[:2]==['api','workspaces'] and p[3]=='transfer_ownership':
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                actor=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not actor or actor['role']!='owner':c.close(); return self.sendj(403,{'error':'forbidden'})
                target_id=d.get('new_owner_id'); target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target_id)).fetchone()
                if not target or target['role']!='admin':c.close(); return self.sendj(400,{'error':'target_must_be_admin'})
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target_id))
                c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target_id,w['id'])); c.close()
                return self.sendj(200,{'workspace':dict(w)})
            if len(p)==4 and p[0:2]==['api','workspaces'] and p[3]=='invitations':
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not m or m['role'] not in ('owner','admin'):c.close(); return self.sendj(403,{'error':'forbidden'})
                import time
                code=secrets.token_urlsafe(12); target=d.get('invited_user_id'); maxu=int(d.get('max_uses',1)); exp=None
                if d.get('expires_in_seconds') is not None: exp=time.time()+float(d['expires_in_seconds'])
                c.execute('INSERT INTO invitations(workspace_id,code,invited_user_id,max_uses,expires_at) VALUES(?,?,?,?,?)',(w['id'],code,target,maxu,exp)); c.close()
                return self.sendj(201,{'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target,'max_uses':maxu}})
            if len(p)==4 and p[0:2]==['api','invitations'] and p[3]=='accept':
                c=conn(); inv=c.execute('SELECT * FROM invitations WHERE code=?',(p[2],)).fetchone()
                import time
                if not inv or inv['revoked'] or inv['uses'] >= (1 if inv['invited_user_id'] else inv['max_uses']) or (inv['expires_at'] and time.time()>inv['expires_at']):c.close(); return self.sendj(404,{'error':'not_found'})
                if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:c.close(); return self.sendj(403,{'error':'forbidden'})
                c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member')); c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(inv['id'],)); c.close()
                return self.sendj(200,{'accepted':True})
            if p==['api','workspaces']:
                slug=d.get('slug',''); name=d.get('name','')
                if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not name:return self.sendj(400,{'error':'invalid'})
                c=conn()
                try:
                    c.execute('BEGIN'); c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.execute('COMMIT')
                except sqlite3.IntegrityError:c.execute('ROLLBACK'); c.close(); return self.sendj(409,{'error':'duplicate'})
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.sendj(201,{'workspace':dict(w),'general_channel':channel_obj(ch)})
            if len(p)==4 and p[:2]==['api','workspaces'] and p[3]=='channels':
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not m or m['role']=='guest':c.close(); return self.sendj(403,{'error':'forbidden'})
                name=d.get('name','')
                if not re.fullmatch(r'[a-z0-9-]{1,32}',name):c.close(); return self.sendj(400,{'error':'invalid'})
                if len(d.get('topic',''))>250:c.close(); return self.sendj(400,{'error':'invalid_topic'})
                if d.get('is_private') and m['role'] not in ('owner','admin'):c.close(); return self.sendj(403,{'error':'forbidden'})
                try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(d.get('is_private'))),d.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.commit()
                except sqlite3.IntegrityError:c.close(); return self.sendj(409,{'error':'duplicate'})
                ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.sendj(201,{'channel':channel_obj(ch)})
            if len(p)==4 and p[:2]==['api','channels'] and p[3]=='join':
                c=conn(); ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone()
                if m and m['role']=='guest':c.close(); return self.sendj(403,{'error':'forbidden'})
                w=c.execute('SELECT join_mode FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if not m and w['join_mode']=='invite_only':c.close(); return self.sendj(403,{'error':'forbidden'})
                if ch['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():c.close(); return self.sendj(403,{'error':'forbidden'})
                if not m:c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(): add_event(c,ch['id'],'member.joined',{'user_id':u['id']})
                c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id'])); c.close(); return self.sendj(200,{'channel':channel_obj(ch)})
            if len(p)==4 and p[0:2]==['api','channels'] and p[3] in ('leave','archive','unarchive'):
                c=conn(); ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone()
                if not m:c.close(); return self.sendj(403,{'error':'forbidden'})
                if p[3]=='leave':
                    c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])); add_event(c,ch['id'],'member.left',{'user_id':u['id']})
                else:
                    if m['role']!='owner':c.close(); return self.sendj(403,{'error':'forbidden'})
                    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if p[3]=='archive' else 0,ch['id'])); add_event(c,ch['id'],'channel.archived' if p[3]=='archive' else 'channel.unarchived',{})
                ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone(); c.close()
                return self.sendj(200,{'channel':channel_obj(ch)})
            if len(p)==4 and p[0:2]==['api','messages'] and p[3]=='reactions':
                c=conn(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:c.close(); return self.sendj(404,{'error':'not_found'})
                emoji=d.get('emoji','')
                if not emoji:c.close(); return self.sendj(400,{'error':'invalid_emoji'})
                c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r['id'],u['id'],emoji)); add_event(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':emoji})
                rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall(); c.close()
                return self.sendj(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in (x['ids'] or '').split(',') if z]} for x in rows]})
            if len(p)==4 and p[0:2]==['api','messages'] and p[3]=='pin':
                c=conn(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:c.close(); return self.sendj(404,{'error':'not_found'})
                now=iso(); c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(r['id'],u['id'],now)); x=c.execute('SELECT * FROM pins WHERE message_id=?',(r['id'],)).fetchone(); c.close()
                return self.sendj(200,{'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
            if len(p)==4 and p[0:2]==['api','channels'] and p[3]=='read':
                c=conn(); ch=c.execute('SELECT id FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                x=c.execute('SELECT * FROM readstate WHERE user_id=? AND channel_id=?',(u['id'],p[2])).fetchone()
                if not x:c.execute('INSERT OR IGNORE INTO readstate(user_id,channel_id) VALUES(?,?)',(u['id'],p[2])); x=c.execute('SELECT * FROM readstate WHERE user_id=? AND channel_id=?',(u['id'],p[2])).fetchone()
                try: val=int(d.get('last_read_event_id'))
                except: c.close(); return self.sendj(400,{'error':'invalid'})
                if val>x['last_read_event_id']: c.execute('UPDATE readstate SET last_read_event_id=? WHERE user_id=? AND channel_id=?',(val,u['id'],p[2]))
                x=c.execute('SELECT * FROM readstate WHERE user_id=? AND channel_id=?',(u['id'],p[2])).fetchone(); c.close()
                return self.sendj(200,{'read_state':{'channel_id':int(p[2]),'last_read_event_id':x['last_read_event_id'],'unread_count':0,'mention_count':x['mention_count']}})
            if len(p)==4 and p[0:2]==['api','channels'] and p[3]=='messages':
                c=conn(); ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:c.close(); return self.sendj(404,{'error':'not_found'})
                if ch['is_archived']:c.close(); return self.sendj(423,{'error':'archived'})
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():c.close(); return self.sendj(403,{'error':'forbidden'})
                body=d.get('body','')
                if not isinstance(body,str) or not body.strip():c.close(); return self.sendj(400,{'error':'empty_body'})
                parent=d.get('parent_id'); now=iso()
                c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],body,parent,now)); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                add_event(c,ch['id'],'message.reply' if parent else 'message.created',{'message_id':mid})
                temp=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                for uid in mention_ids(c,temp): c.execute('INSERT OR IGNORE INTO readstate(user_id,channel_id) VALUES(?,?)',(uid,ch['id'])); c.execute('UPDATE readstate SET mention_count=mention_count+1 WHERE user_id=? AND channel_id=?',(uid,ch['id']))
                r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); out=msg_obj(c,r); c.close()
                return self.sendj(201,{'message':out})
            return self.sendj(404,{'error':'not_found'})
        except ValueError: return self.sendj(400,{'error':'malformed_json'})
        except Exception as e:return self.sendj(500,{'error':str(e)})
    def do_DELETE(self):
        try:
            p=self.route(); u=self.auth()
            if not u:return self.sendj(401,{'error':'unauthorized'})
            if len(p)==4 and p[0:2]==['api','messages'] and p[3]=='pin':
                c=conn(); r=c.execute('SELECT id FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:c.close(); return self.sendj(404,{'error':'not_found'})
                c.execute('DELETE FROM pins WHERE message_id=?',(p[2],)); c.close(); return self.sendj(200,{'unpinned':True})
            if len(p)==3 and p[0:2]==['api','messages']:
                c=conn(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:c.close(); return self.sendj(404,{'error':'not_found'})
                owner=c.execute('SELECT role FROM members WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?) AND user_id=?',(r['channel_id'],u['id'])).fetchone()
                if r['author_id']!=u['id'] and (not owner or owner['role']!='owner'):c.close(); return self.sendj(403,{'error':'forbidden'})
                c.execute('UPDATE messages SET deleted=1,body=? WHERE id=?',('',r['id'])); add_event(c,r['channel_id'],'message.deleted',{'message_id':r['id']}); c.close(); return self.sendj(200,{'deleted':True})
            if len(p)==5 and p[0:2]==['api','messages'] and p[3]=='reactions':
                c=conn(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:c.close(); return self.sendj(404,{'error':'not_found'})
                c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(p[2],u['id'],p[4])); add_event(c,r['channel_id'],'reaction.removed',{'message_id':r['id'],'emoji':p[4]}); rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(p[2],)).fetchall(); c.close()
                return self.sendj(200,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in (x['ids'] or '').split(',') if z]} for x in rows]})
            if len(p)==5 and p[0:2]==['api','workspaces'] and p[3]=='invitations':
                c=conn(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:c.close(); return self.sendj(404,{'error':'not_found'})
                m=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not m or m['role'] not in ('owner','admin'):c.close(); return self.sendj(403,{'error':'forbidden'})
                c.execute('UPDATE invitations SET revoked=1 WHERE workspace_id=? AND code=?',(w['id'],p[4])); c.close()
                return self.sendj(200,{'deleted':True})
            return self.sendj(404,{'error':'not_found'})
        except ValueError: return self.sendj(400,{'error':'malformed_json'})
        except Exception as e:return self.sendj(500,{'error':str(e)})
if __name__=='__main__':
    init(); ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
