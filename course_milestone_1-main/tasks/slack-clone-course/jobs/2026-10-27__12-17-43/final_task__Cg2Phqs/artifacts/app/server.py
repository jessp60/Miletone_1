import os, re, json, uuid, sqlite3, hashlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

DB='/app/huddle.sqlite3'
def db():
    c=sqlite3.connect(DB, timeout=10, isolation_level=None)
    c.row_factory=sqlite3.Row
    try: c.execute('PRAGMA journal_mode=WAL')
    except sqlite3.OperationalError: pass
    c.execute('PRAGMA busy_timeout=10000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS readstates(user_id INTEGER,channel_id INTEGER,mention_count INTEGER DEFAULT 0,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,channel_id INTEGER,user_id INTEGER,code TEXT UNIQUE,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0,invited_user_id INTEGER);
    ''')
    for col,typ in [('code','TEXT'),('expires_at','REAL'),('max_uses','INTEGER DEFAULT 1'),('uses','INTEGER DEFAULT 0'),('revoked','INTEGER DEFAULT 0'),('invited_user_id','INTEGER')]:
        try: c.execute(f'ALTER TABLE invites ADD COLUMN {col} {typ}')
        except sqlite3.OperationalError: pass
    return c
def user(r):
    return {'id':r['id'],'username':r['username'],'display_name':r['display_name'] or r['username'],'timezone':r['timezone'] or 'UTC','avatar_url':r['avatar_url'] or '','status_text':r['status_text'] or '','status_emoji':r['status_emoji'] or ''}
def ws(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def ch(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def message(c,r):
    au=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rc=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    rr=c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    reactions=[]
    for x in rr:
        ids=[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(r['id'],x['emoji'])).fetchall()]
        reactions.append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
    import re
    ids=[]
    w=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    names={x['username']:x['id'] for x in c.execute('SELECT u.id,u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.workspace_id=?',(w['workspace_id'],)).fetchall()} if w else {}
    for nm in re.findall(r'(?<![A-Za-z0-9_@])@([A-Za-z0-9_]+)',r['body']):
        if nm in names and names[nm]!=r['author_id']: ids.append(names[nm])
    if re.search(r'(?<![A-Za-z0-9_])@channel(?![A-Za-z0-9_])',r['body']):
        ids += [x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(r['channel_id'],r['author_id'])).fetchall()]
    ids=list(dict.fromkeys(ids))
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(au),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions,'mentions':ids}
def event(c, channel_id, kind, extra=None):
    import json
    c.execute('BEGIN IMMEDIATE')
    n=c.execute('SELECT coalesce(max(event_id),0)+1 n FROM events WHERE channel_id=?',(channel_id,)).fetchone()['n']
    d={'type':kind,'event_id':n,'channel_id':channel_id}
    if extra:d.update(extra)
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(channel_id,n,json.dumps(d),iso()))
    c.execute('COMMIT')
    return d
def limit_cursor(qs, default=50):
    from urllib.parse import parse_qs
    q=parse_qs(qs)
    if 'offset' in q or 'page' in q: return None,None,None,'unsupported'
    try: n=int(q.get('limit',[str(default)])[0])
    except: return None,None,None,'bad'
    if n<1 or n>200:return None,None,None,'bad'
    cur=q.get('cursor',[None])[0]
    return n,cur,q,'ok'
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def send(self,status,obj=None,headers=None):
        b=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode()
        self.send_response(status); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(b)))
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        if b:self.wfile.write(b)
    def body(self):
        self.badjson=False
        try:
            n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
        except:
            self.badjson=True
            return {}
    def auth(self):
        x=self.headers.get('Authorization','')
        if not x.startswith('Bearer '): return None
        c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close()
        return r
    def need(self):
        u=self.auth()
        if not u:self.send(401,{'error':'unauthorized'})
        return u
    def reqpath(self): return unquote(urlparse(self.path).path)
    def websocket(self):
        import base64,hashlib,select,time,json,struct
        from urllib.parse import parse_qs
        tok=parse_qs(urlparse(self.path).query).get('token',[''])[0]
        c=db(); r=c.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone()
        if not r:
            self.send_response(401); self.end_headers(); return
        key=self.headers.get('Sec-WebSocket-Key','')
        accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.request.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n').encode())
        self.request.settimeout(.2); subs=set(); seen={}; buf=b''
        def send(o):
            d=json.dumps(o,separators=(',',':')).encode(); n=len(d)
            h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('>H',n)
            self.request.sendall(h+d)
        def frame():
            nonlocal buf
            try:
                while len(buf)<2: buf+=self.request.recv(4096)
                b0,b1=buf[0],buf[1]; n=b1&127; off=2
                if n==126:
                    while len(buf)<4: buf+=self.request.recv(4096)
                    n=struct.unpack('>H',buf[2:4])[0];off=4
                while len(buf)<off+4+n: buf+=self.request.recv(4096)
                mask=buf[off:off+4]; data=buf[off+4:off+4+n];buf=buf[off+4+n:]
                return json.loads(bytes(x^mask[i%4] for i,x in enumerate(data)))
            except (socket.timeout,BlockingIOError): return None
            except: return {'type':'close'}
        import socket
        try:
            while True:
                x=frame()
                if x:
                    if x.get('type')=='close': break
                    ch=x.get('channel_id')
                    if x.get('type')=='subscribe' and ch:
                        subs.add(int(ch)); head=c.execute('SELECT coalesce(max(event_id),0) n FROM events WHERE channel_id=?',(ch,)).fetchone()['n'];seen[int(ch)]=head
                        send({'type':'subscribed','channel_id':int(ch),'head_event_id':head})
                    elif x.get('type')=='resume' and ch:
                        since=int(x.get('since_event_id',0)); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall()
                        for z in rows: send(json.loads(z['payload']))
                        head=c.execute('SELECT coalesce(max(event_id),0) n FROM events WHERE channel_id=?',(ch,)).fetchone()['n']; send({'type':'resumed','channel_id':int(ch),'head_event_id':head});seen[int(ch)]=head
                for ch in list(subs):
                    rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,seen.get(ch,0))).fetchall()
                    for z in rows: send(json.loads(z['payload']))
                    if rows: seen[ch]=json.loads(rows[-1]['payload'])['event_id']
        finally:c.close()
    def do_GET(self):
        if self.headers.get('Upgrade','').lower()=='websocket' and self.reqpath()=='/api/ws':
            self.websocket(); return
        p=self.reqpath(); c=db()
        if p=='/api/health':
            self.send(200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))}); c.close(); return
        if p=='/':
            html='''<!doctype html><html><head><title>Huddle</title></head><body><div id="app">Huddle</div></body></html>'''
            self.send(200,html,{'Content-Type':'text/html'}); c.close(); return
        u=self.auth()
        if p=='/api/auth/me':
            if not u:self.send(401,{'error':'unauthorized'})
            else:self.send(200,{'user':user(u)})
            c.close(); return
        if p=='/api/workspaces':
            if not u:self.send(401,{'error':'unauthorized'}); c.close(); return
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
            self.send(200,{'workspaces':[ws(x) for x in rows]}); c.close(); return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            rm=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not rm or rm['role'] not in ('owner','admin'):self.send(403,{'error':'forbidden'});c.close();return
            import time
            rows=c.execute('SELECT * FROM invites WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(w['id'],time.time())).fetchall()
            self.send(200,{'invitations':[{'id':x['id'],'code':x['code'],'workspace_id':x['workspace_id'],'max_uses':x['max_uses'],'uses':x['uses'],'invited_user_id':x['invited_user_id'],'expires_at':x['expires_at']} for x in rows]});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/members',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall()
            self.send(200,{'members':[dict(x) for x in rows]});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            member=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not member:self.send(403,{'error':'forbidden'});c.close();return
            include=urlparse(self.path).query in ('include_archived=1','include_archived=true')
            rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],int(include))).fetchall()
            visible=[]
            for x in rows:
                if not x['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone() or u['id']==w['owner_id']: visible.append(ch(x))
            states={x['id']:{'channel_id':x['id'],'last_read_event_id':(c.execute('SELECT last_read_event_id FROM readstates WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone() or {'last_read_event_id':0})['last_read_event_id'],'unread_count':0,'mention_count':(c.execute('SELECT mention_count FROM readstates WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone() or {'mention_count':0})['mention_count']} for x in rows if x['id'] in [v['id'] for v in [c.execute('SELECT * FROM channels WHERE id=?',(v['id'],)).fetchone() for v in rows] if v]}
            self.send(200,{'workspace':ws(w),'channels':visible,'read_state':states});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)/pin',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:self.send(404,{'error':'not found'});c.close();return
            c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(r['id'],u['id'],iso()))
            x=c.execute('SELECT * FROM pins WHERE message_id=?',(r['id'],)).fetchone()
            self.send(200,{'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)/reactions',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:self.send(404,{'error':'not found'});c.close();return
            emoji=data.get('emoji')
            if not emoji:self.send(400,{'error':'emoji'});c.close();return
            c.execute('INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(r['id'],u['id'],emoji))
            event(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':emoji})
            self.send(200,{'reactions':message(c,r)['reactions']});c.close();return
        if p=='/api/search':
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            q=urlparse(self.path).query; from urllib.parse import parse_qs
            z=parse_qs(q); n,cur,_,err=limit_cursor(q)
            if err!='ok':self.send(400,{'error':err});c.close();return
            try: pos=int(cur) if cur else 0
            except: self.send(400,{'error':'cursor'});c.close();return
            term=z.get('q',[''])[0]; wid=z.get('workspace_id',[None])[0]
            rows=c.execute('SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? WHERE m.deleted=0 AND m.body LIKE ? AND (? IS NULL OR ch.workspace_id=?) ORDER BY m.id DESC LIMIT ? OFFSET ?',(u['id'],'%'+term+'%',wid,wid,n,pos)).fetchall()
            more=len(rows)==n
            self.send(200,{'results':[message(c,x) for x in rows],'next_cursor':str(pos+n) if more else None});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/read',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            if not c.execute('SELECT 1 FROM channels WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'}); c.close(); return
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m.group(1),u['id'])).fetchone():self.send(403,{'error':'forbidden'});c.close();return
            r=c.execute('SELECT * FROM readstates WHERE user_id=? AND channel_id=?',(u['id'],m.group(1))).fetchone()
            self.send(200,{'read_state':{'channel_id':int(m.group(1)),'last_read_event_id':r['last_read_event_id'] if r else 0,'unread_count':0,'mention_count':r['mention_count'] if r else 0}});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/pins',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            if not c.execute('SELECT 1 FROM channels WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'}); c.close(); return
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m.group(1),u['id'])).fetchone():self.send(403,{'error':'forbidden'});c.close();return
            n,cur,_,err=limit_cursor(urlparse(self.path).query)
            if err!='ok':self.send(400,{'error':err});c.close();return
            try: pos=int(cur) if cur else 0
            except: self.send(400,{'error':'cursor'});c.close();return
            rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC LIMIT ? OFFSET ?',(m.group(1),n,pos)).fetchall()
            out=[{'message':message(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows]
            more=c.execute('SELECT 1 FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC LIMIT 1 OFFSET ?',(m.group(1),pos+n)).fetchone()
            self.send(200,{'pins':out,'next_cursor':str(pos+n) if more else None});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/messages',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            if not c.execute('SELECT 1 FROM channels WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'}); c.close(); return
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query); n,cur,_,err=limit_cursor(urlparse(self.path).query)
            if err!='ok':self.send(400,{'error':err});c.close();return
            import base64
            try:
                if cur:
                    raw=base64.urlsafe_b64decode(cur.encode()).decode(); chx,snap,pos=raw.split(':'); snap=int(snap); pos=int(pos)
                else:
                    chx=m.group(1); snap=c.execute('SELECT coalesce(max(id),0) x FROM messages WHERE channel_id=?',(chx,)).fetchone()['x']; pos=0
                if str(chx)!=m.group(1): raise ValueError()
            except: self.send(400,{'error':'cursor'});c.close();return
            rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND id<=? AND parent_id IS NULL ORDER BY id DESC LIMIT ? OFFSET ?',(m.group(1),snap,n,pos)).fetchall()
            more=len(rows)==n and c.execute('SELECT 1 FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<=? ORDER BY id DESC LIMIT 1 OFFSET ?',(m.group(1),snap,pos+n)).fetchone()
            nxt=None if not more else base64.urlsafe_b64encode(f'{m.group(1)}:{snap}:{pos+n}'.encode()).decode()
            self.send(200,{'messages':[message(c,x) for x in rows],'next_cursor':nxt});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)/replies',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            if not c.execute('SELECT 1 FROM messages WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'}); c.close(); return
            n,cur,_,err=limit_cursor(urlparse(self.path).query)
            if err!='ok':self.send(400,{'error':err});c.close();return
            import base64
            try:
                if cur: mid,snap,pos=base64.urlsafe_b64decode(cur.encode()).decode().split(':'); snap=int(snap);pos=int(pos)
                else: mid=m.group(1);snap=c.execute('SELECT coalesce(max(id),0) x FROM messages WHERE parent_id=?',(mid,)).fetchone()['x'];pos=0
            except: self.send(400,{'error':'cursor'});c.close();return
            rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id<=? ORDER BY id ASC LIMIT ? OFFSET ?',(mid,snap,n,pos)).fetchall()
            more=len(rows)==n and c.execute('SELECT 1 FROM messages WHERE parent_id=? AND deleted=0 AND id<=? LIMIT 1 OFFSET ?',(mid,snap,pos+n)).fetchone()
            nxt=None if not more else base64.urlsafe_b64encode(f'{mid}:{snap}:{pos+n}'.encode()).decode()
            self.send(200,{'replies':[message(c,x) for x in rows],'next_cursor':nxt});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/members',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            if not c.execute('SELECT 1 FROM channels WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'}); c.close(); return
            rows=c.execute('SELECT u.* FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(m.group(1),)).fetchall()
            self.send(200,{'members':[user(x) for x in rows]});c.close();return
        self.send(404,{'error':'not found'});c.close()
    def do_POST(self):
        p=self.reqpath(); data=self.body(); c=db()
        if self.badjson and (p in ('/api/auth/register','/api/auth/login','/api/workspaces') or re.fullmatch(r'/api/workspaces/[^/]+/channels',p)):
            self.send(400,{'error':'malformed json'}); c.close(); return
        if p=='/api/auth/register':
            un=data.get('username',''); pw=data.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:self.send(400,{'error':'invalid'});c.close();return
            try:
                cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,pw,data.get('display_name') or un))
                uid=cur.lastrowid; tok=uuid.uuid4().hex+uuid.uuid4().hex;c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid))
                r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();self.send(201,{'user':user(r),'token':tok})
            except sqlite3.IntegrityError:self.send(409,{'error':'duplicate'})
            c.close();return
        if p=='/api/auth/login':
            r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(data.get('username'),data.get('password'))).fetchone()
            if not r:self.send(401,{'error':'invalid credentials'});c.close();return
            tok=uuid.uuid4().hex+uuid.uuid4().hex;c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id']));self.send(200,{'user':user(r),'token':tok});c.close();return
        u=self.auth()
        if not u:self.send(401,{'error':'unauthorized'});c.close();return
        if p.startswith('/api/workspaces/') and p.endswith('/transfer_ownership'):
            slug=p.split('/')[3]
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            own=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            target_id=data.get('new_owner_id')
            target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target_id)).fetchone()
            if not own or own['role']!='owner':
                self.send(403,{'error':'forbidden'});c.close();return
            if not target or target['role']!='admin':
                self.send(400,{'error':'target must be admin'});c.close();return
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target_id))
            self.send(200,{'workspace':ws(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())});c.close();return
        m=re.fullmatch(r'/api/invitations/([^/]+)/accept',p)
        if m:
            import time
            x=c.execute('SELECT * FROM invites WHERE code=?',(m.group(1),)).fetchone()
            if not x or x['revoked'] or x['uses']>=x['max_uses'] or (x['expires_at'] is not None and x['expires_at']<=time.time()):
                self.send(404,{'error':'not found'});c.close();return
            if x['invited_user_id'] is not None and x['invited_user_id']!=u['id']:
                self.send(403,{'error':'forbidden'});c.close();return
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(x['workspace_id'],u['id'],'member'))
            c.execute('UPDATE invites SET uses=uses+1,revoked=CASE WHEN invited_user_id IS NOT NULL THEN 1 ELSE revoked END WHERE id=?',(x['id'],))
            self.send(200,{'accepted':True});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            rm=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not rm or rm['role'] not in ('owner','admin'):self.send(403,{'error':'forbidden'});c.close();return
            import time
            exp=data.get('expires_in_seconds'); maxu=data.get('max_uses',1)
            try:maxu=max(1,int(maxu))
            except: self.send(400,{'error':'invalid'});c.close();return
            code=uuid.uuid4().hex
            expires=None if exp is None else time.time()+max(0,int(exp))
            target=data.get('invited_user_id')
            if target is not None and not c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone():self.send(404,{'error':'not found'});c.close();return
            c.execute('INSERT INTO invites(workspace_id,code,expires_at,max_uses,invited_user_id) VALUES(?,?,?,?,?)',(w['id'],code,expires,1 if target is not None else maxu,target))
            self.send(201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':1 if target is not None else maxu,'uses':0,'invited_user_id':target,'expires_at':expires}});c.close();return
        if p=='/api/workspaces':
            slug=data.get('slug','')
            if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not data.get('name'):self.send(400,{'error':'invalid'});c.close();return
            try:
                cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,data['name'],u['id'])); wid=cur.lastrowid
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=cur.lastrowid
                c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); cc=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                self.send(201,{'workspace':ws(w),'general_channel':ch(cc)})
            except sqlite3.IntegrityError:self.send(409,{'error':'duplicate'})
            c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            mem=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not mem or mem['role']=='guest':self.send(403,{'error':'forbidden'});c.close();return
            name=data.get('name','')
            if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or len(name)>32 or len(data.get('topic',''))>250 or (data.get('is_private',False) and mem['role'] not in ('owner','admin')):self.send(403 if data.get('is_private',False) else 400,{'error':'forbidden'});c.close();return
            try:
                cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(data.get('is_private'))),data.get('topic','')));cid=cur.lastrowid
                c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
                self.send(201,{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
            except sqlite3.IntegrityError:self.send(409,{'error':'duplicate'})
            c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)/pin',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:self.send(404,{'error':'not found'});c.close();return
            c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(r['id'],u['id'],iso()))
            x=c.execute('SELECT * FROM pins WHERE message_id=?',(r['id'],)).fetchone()
            self.send(200,{'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)/reactions',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:self.send(404,{'error':'not found'});c.close();return
            emoji=data.get('emoji')
            if not emoji:self.send(400,{'error':'emoji'});c.close();return
            c.execute('INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(r['id'],u['id'],emoji))
            event(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':emoji})
            self.send(200,{'reactions':message(c,r)['reactions']});c.close();return
        if p=='/api/search':
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            q=urlparse(self.path).query; from urllib.parse import parse_qs
            z=parse_qs(q); n,cur,_,err=limit_cursor(q)
            if err!='ok':self.send(400,{'error':err});c.close();return
            try: pos=int(cur) if cur else 0
            except: self.send(400,{'error':'cursor'});c.close();return
            term=z.get('q',[''])[0]; wid=z.get('workspace_id',[None])[0]
            rows=c.execute('SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? WHERE m.deleted=0 AND m.body LIKE ? AND (? IS NULL OR ch.workspace_id=?) ORDER BY m.id DESC LIMIT ? OFFSET ?',(u['id'],'%'+term+'%',wid,wid,n,pos)).fetchall()
            more=len(rows)==n
            self.send(200,{'results':[message(c,x) for x in rows],'next_cursor':str(pos+n) if more else None});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/pins',p)
        if m:
            if not u:self.send(401,{'error':'unauthorized'});c.close();return
            if not c.execute('SELECT 1 FROM channels WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'}); c.close(); return
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m.group(1),u['id'])).fetchone():self.send(403,{'error':'forbidden'});c.close();return
            n,cur,_,err=limit_cursor(urlparse(self.path).query)
            if err!='ok':self.send(400,{'error':err});c.close();return
            try: pos=int(cur) if cur else 0
            except: self.send(400,{'error':'cursor'});c.close();return
            rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC LIMIT ? OFFSET ?',(m.group(1),n,pos)).fetchall()
            out=[{'message':message(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows]
            more=c.execute('SELECT 1 FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC LIMIT 1 OFFSET ?',(m.group(1),pos+n)).fetchone()
            self.send(200,{'pins':out,'next_cursor':str(pos+n) if more else None});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/read',p)
        if m:
            if not c.execute('SELECT 1 FROM channels WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'});c.close();return
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m.group(1),u['id'])).fetchone(): self.send(403,{'error':'forbidden'});c.close();return
            val=data.get('last_read_event_id')
            if not isinstance(val,int): self.send(400,{'error':'last_read_event_id'});c.close();return
            c.execute('INSERT INTO readstates(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=MAX(last_read_event_id,excluded.last_read_event_id)',(u['id'],m.group(1),val))
            r=c.execute('SELECT * FROM readstates WHERE user_id=? AND channel_id=?',(u['id'],m.group(1))).fetchone()
            self.send(200,{'read_state':{'channel_id':int(m.group(1)),'last_read_event_id':r['last_read_event_id'],'unread_count':0,'mention_count':r['mention_count']}});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/messages',p)
        if m:
            x=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone()
            if not x:self.send(404,{'error':'not found'});c.close();return
            if x['is_archived']:self.send(423,{'error':'archived'});c.close();return
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone() and u['id']!=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()['owner_id']:
                self.send(403,{'error':'forbidden'});c.close();return
            body=data.get('body')
            if not isinstance(body,str) or not body:self.send(400,{'error':'empty body'});c.close();return
            parent=data.get('parent_id')
            if parent is not None and not c.execute('SELECT 1 FROM messages WHERE id=? AND channel_id=?',(parent,x['id'])).fetchone():self.send(400,{'error':'parent'});c.close();return
            cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(x['id'],u['id'],body,parent,iso()))
            r=c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone()
            for mid in message(c,r)['mentions']: c.execute('INSERT INTO readstates(user_id,channel_id,mention_count) VALUES(?,?,1) ON CONFLICT(user_id,channel_id) DO UPDATE SET mention_count=mention_count+1',(mid,x['id']))
            ev=event(c,x['id'],'message.reply' if parent else 'message.created',{'message':message(c,r)})
            self.send(201,{'message':message(c,r)});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/(leave|archive|unarchive)',p)
        if m:
            x=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone()
            if not x:self.send(404,{'error':'not found'});c.close();return
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
            action=m.group(2)
            if action=='leave':
                c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id']))
                event(c,x['id'],'member.left',{'user_id':u['id']})
                self.send(200,{'channel':ch(x)});c.close();return
            if u['id']!=w['owner_id']:self.send(403,{'error':'forbidden'});c.close();return
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if action=='archive' else 0,x['id']))
            event(c,x['id'],'channel.archived' if action=='archive' else 'channel.unarchived')
            self.send(200,{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(x['id'],)).fetchone())});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/join',p)
        if m:
            x=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone()
            if not x:self.send(404,{'error':'not found'});c.close();return
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone(); mem=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if x['is_private']:
                if not mem or not c.execute('SELECT 1 FROM invites WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone():self.send(403,{'error':'forbidden'});c.close();return
            elif not mem:
                if w['join_mode']!='open':self.send(403,{'error':'forbidden'});c.close();return
                c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
            elif mem['role']=='guest':
                self.send(403,{'error':'forbidden'});c.close();return
            c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(x['id'],u['id'])); event(c,x['id'],'member.joined',{'user_id':u['id']}); self.send(200,{'channel':ch(x)});c.close();return
        self.send(404,{'error':'not found'});c.close()
    def do_DELETE(self):
        p=self.reqpath(); u=self.auth(); c=db()
        if not u:self.send(401,{'error':'unauthorized'});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            rm=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            x=c.execute('SELECT * FROM invites WHERE workspace_id=? AND code=?',(w['id'],m.group(2))).fetchone()
            if not x:self.send(404,{'error':'not found'});c.close();return
            if not rm or rm['role'] not in ('owner','admin'):self.send(403,{'error':'forbidden'});c.close();return
            c.execute('UPDATE invites SET revoked=1 WHERE id=?',(x['id'],));self.send(200,{'deleted':True});c.close();return
        self.send(404,{'error':'not found'});c.close()
    def do_DELETE(self):
        p=self.reqpath();u=self.auth();c=db()
        if not u:self.send(401,{'error':'unauthorized'});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            rm=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            x=c.execute('SELECT * FROM invites WHERE workspace_id=? AND code=?',(w['id'],m.group(2))).fetchone()
            if not x:self.send(404,{'error':'not found'});c.close();return
            if not rm or rm['role'] not in ('owner','admin'):self.send(403,{'error':'forbidden'});c.close();return
            c.execute('UPDATE invites SET revoked=1 WHERE id=?',(x['id'],))
            self.send(200,{'deleted':True});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)/pin',p)
        if m:
            if not c.execute('SELECT 1 FROM messages WHERE id=?',(m.group(1),)).fetchone(): self.send(404,{'error':'not found'}); c.close(); return
            c.execute('DELETE FROM pins WHERE message_id=?',(m.group(1),));self.send(200,{'unpinned':True});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)/reactions/(.+)',p)
        if m:
            import urllib.parse
            emoji=urllib.parse.unquote(m.group(2)); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m.group(1),u['id'],emoji))
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:self.send(404,{'error':'not found'});c.close();return
            event(c,r['channel_id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji})
            self.send(200,{'reactions':message(c,r)['reactions']});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:self.send(404,{'error':'not found'});c.close();return
            w=c.execute('SELECT owner_id FROM workspaces WHERE id=(SELECT workspace_id FROM channels WHERE id=?)',(r['channel_id'],)).fetchone()
            if r['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):self.send(403,{'error':'forbidden'});c.close();return
            c.execute('UPDATE messages SET deleted=1,body=? WHERE id=?',('',r['id']));event(c,r['channel_id'],'message.deleted',{'message_id':r['id']});self.send(200,{'deleted':True});c.close();return
        self.send(404,{'error':'not found'});c.close()
    def do_PATCH(self):
        p=self.reqpath();u=self.auth();c=db()
        if not u:self.send(401,{'error':'unauthorized'});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            rm=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not rm or rm['role'] not in ('owner','admin'):self.send(403,{'error':'forbidden'});c.close();return
            d=self.body()
            if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):self.send(400,{'error':'invalid'});c.close();return
            vals=[] 
            if 'name' in d: vals.append(('name',d['name']))
            if 'join_mode' in d: vals.append(('join_mode',d['join_mode']))
            if vals:c.execute('UPDATE workspaces SET '+','.join(k+'=?' for k,v in vals)+' WHERE id=?',tuple(v for k,v in vals)+(w['id'],))
            self.send(200,{'workspace':ws(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())});c.close();return
        m=re.fullmatch(r'/api/messages/(\d+)',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
            if not r:self.send(404,{'error':'not found'});c.close();return
            if r['deleted']:self.send(409,{'error':'deleted'});c.close();return
            if r['author_id']!=u['id']:self.send(403,{'error':'forbidden'});c.close();return
            d=self.body()
            if not isinstance(d.get('body'),str) or not d['body']:self.send(400,{'error':'empty'});c.close();return
            c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],iso(),r['id']))
            event(c,r['channel_id'],'message.updated',{'message':message(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone())}); self.send(200,{'message':message(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone())});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)',p)
        if m:
            x=c.execute('SELECT * FROM channels WHERE id=?',(m.group(1),)).fetchone()
            if not x:self.send(404,{'error':'not found'});c.close();return
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
            rm=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone()
            if not rm and u['id']!=w['owner_id']:self.send(403,{'error':'forbidden'});c.close();return
            d=self.body()
            if 'topic' not in d or not isinstance(d['topic'],str) or len(d['topic'])>250:self.send(400,{'error':'invalid topic'});c.close();return
            c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],x['id']))
            event(c,x['id'],'channel.updated',{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(x['id'],)).fetchone())})
            self.send(200,{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(x['id'],)).fetchone())});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/members/(\d+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            actor=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],m.group(2))).fetchone()
            role=self.body().get('role')
            if not target or role not in ('owner','admin','member','guest'): self.send(400 if role not in ('owner','admin','member','guest') else 404,{'error':'invalid'});c.close();return
            if role=='owner' or not actor or actor['role'] not in ('owner','admin') or int(m.group(2))==u['id'] or target['role']=='owner' or (actor['role']=='admin' and target['role']=='admin'):
                self.send(400 if role=='owner' else 403,{'error':'forbidden'});c.close();return
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],m.group(2)))
            self.send(200,{'member':{'user_id':int(m.group(2)),'role':role}});c.close();return
        if p.startswith('/api/workspaces/') and p.endswith('/transfer_ownership'):
            slug=p.split('/')[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:self.send(404,{'error':'not found'});c.close();return
            own=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            target_id=self.body().get('new_owner_id'); target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target_id)).fetchone()
            if not own or own['role']!='owner':self.send(403,{'error':'forbidden'});c.close();return
            if not target or target['role']!='admin':self.send(400,{'error':'target must be admin'});c.close();return
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target_id))
            self.send(200,{'workspace':ws(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())});c.close();return
        m=re.fullmatch(r'/api/users/me',p)
        if m:
            d=self.body(); allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}; vals={k:v for k,v in d.items() if k in allowed}
            if vals:
                q='UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?';c.execute(q,(*vals.values(),u['id']))
            self.send(200,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())});c.close();return
        self.send(404,{'error':'not found'});c.close()
if __name__=='__main__':
    db().close()
    port=int(os.environ.get('PORT','8000'))
    ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
