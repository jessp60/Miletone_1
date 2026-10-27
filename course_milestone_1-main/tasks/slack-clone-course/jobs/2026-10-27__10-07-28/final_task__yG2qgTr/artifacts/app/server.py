import os, re, json, sqlite3, secrets, hashlib, datetime, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DB='/app/huddle.db'
PORT=int(os.environ.get('PORT','8000'))
NODE=(PORT-8000)//1
lock=threading.Lock()

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB, timeout=8, isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=8000')
    c.execute('PRAGMA journal_mode=WAL')
    return c
def init():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,code TEXT UNIQUE,invited_user_id INTEGER,invited_username TEXT,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,expires_at TEXT,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT DEFAULT '[]',UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,message_id INTEGER,created_at TEXT,PRIMARY KEY(channel_id,event_id));
    ''')
    c.close()
init()

def out_user(r):
    return {'id':r['id'],'username':r['username'],'display_name':r['display_name'] or r['username'],'timezone':r['timezone'],'avatar_url':r['avatar_url'],'status_text':r['status_text'],'status_emoji':r['status_emoji']}
def workspace(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def channel(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def auth(c,h):
    a=h.headers.get('Authorization','')
    if not a.startswith('Bearer '): return None
    r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(a[7:],)).fetchone()
    return r
def err(h,status,msg='error'): h.send_json(status,{'error':msg})
def member(c,w,u):
    return c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone()
def add_event(c,ch,kind,mid=None):
    x=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel_id=?',(ch,)).fetchone()['n']
    c.execute('INSERT INTO events(channel_id,event_id,kind,message_id,created_at) VALUES(?,?,?,?,?)',(ch,x,kind,mid,now()))
    return x

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send_json(self,status,obj):
        b=json.dumps(obj,separators=(',',':')).encode()
        self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        try: return json.loads(self.rfile.read(n) or b'{}')
        except Exception: self.bad_json=True; return {}
    def route(self):
        return urlparse(self.path).path.split('/'), parse_qs(urlparse(self.path).query)
    def invobj(self,r):
        return {'id':r['id'],'workspace_id':r['workspace_id'],'code':r['code'],'invited_user_id':r['invited_user_id'],'invited_username':r['invited_username'],'max_uses':r['max_uses'],'uses':r['uses'],'expires_at':r['expires_at']}
    def do_GET(self):
        c=db(); p,q=self.route()
        if p==['','api','health']: self.send_json(200,{'status':'ok','node_id':NODE}); return
        u=auth(c,self)
        if p[:3]==['','api','auth'] and p[3:] == ['me']:
            if not u: err(self,401); return
            self.send_json(200,{'user':out_user(u)}); return
        if p[:2]==['','api'] and p[2]=='users' and len(p)==4:
            r=c.execute('SELECT * FROM users WHERE id=?',(p[3],)).fetchone()
            if not r: err(self,404); return
            self.send_json(200,{'user':out_user(r)}); return
        if not u: err(self,401); return
        uid=u['id']
        if p==['','api','workspaces']:
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(uid,)).fetchall()
            self.send_json(200,{'workspaces':[workspace(x) for x in rows]}); return
        if len(p)==5 and p[:3]==['','api','workspaces'] and p[4]=='members':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall()
            self.send_json(200,{'members':[dict(x) for x in rows]}); return
        if len(p)==5 and p[:3]==['','api','workspaces'] and p[4]=='invitations':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            m=member(c,w['id'],uid)
            if not m or m['role'] not in ('owner','admin'): err(self,403); return
            rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses',(w['id'],)).fetchall()
            self.send_json(200,{'invitations':[self.invobj(x) for x in rows]}); return
        if len(p)==4 and p[:3]==['','api','workspaces']:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))).fetchall()
            self.send_json(200,{'workspace':workspace(w),'channels':[channel(x) for x in cs],'read_state':{}}); return
        if len(p)==4 and p[:2]==['','api','channels'] and p[3]=='members':
            ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
            if not ch: err(self,404); return
            rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall()
            self.send_json(200,{'members':[out_user(x) for x in rows]}); return
        if len(p)==4 and p[:2]==['','api','channels'] and p[3]=='read':
            self.send_json(200,{'read_state':{'channel_id':int(p[2]),'last_read_event_id':0,'unread_count':0,'mention_count':0}}); return
        err(self,404)
    def do_POST(self):
        c=db(); p,q=self.route(); self.bad_json=False; data=self.body()
        if self.bad_json: err(self,400); return
        if p==['','api','auth','register']:
            un=data.get('username',''); pw=data.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8: err(self,400); return
            try:
                c.execute('BEGIN'); cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,hashlib.sha256(pw.encode()).hexdigest(),data.get('display_name') or un)); uid=cur.lastrowid
                tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); c.execute('COMMIT')
            except sqlite3.IntegrityError: c.execute('ROLLBACK'); err(self,409); return
            r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); self.send_json(201,{'user':out_user(r),'token':tok}); return
        if p==['','api','auth','login']:
            r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(data.get('username'),hashlib.sha256(data.get('password','').encode()).hexdigest())).fetchone()
            if not r: err(self,401); return
            tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); self.send_json(200,{'user':out_user(r),'token':tok}); return
        u=auth(c,self)
        if not u: err(self,401); return
        uid=u['id']
        if len(p)==5 and p[:3]==['','api','workspaces'] and p[4]=='invitations':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            m=member(c,w['id'],uid)
            if not m or m['role'] not in ('owner','admin'): err(self,403); return
            target=data.get('invited_user_id')
            if target is not None and not c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(): err(self,404); return
            maxuses=1 if target is not None else data.get('max_uses',1)
            if not isinstance(maxuses,int) or maxuses<1: err(self,400); return
            exp=data.get('expires_in_seconds')
            expires=str(__import__('time').time()+exp) if exp is not None and isinstance(exp,(int,float)) and exp>0 else None
            code=secrets.token_urlsafe(18)
            c.execute('INSERT INTO invitations(workspace_id,code,invited_user_id,invited_username,max_uses,expires_at) VALUES(?,?,?,?,?,?)',(w['id'],code,target,data.get('invited_username'),maxuses,expires))
            r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
            self.send_json(201,{'invitation':self.invobj(r)}); return
        if len(p)==5 and p[:3]==['','api','invitations'] and p[4]=='accept':
            r=c.execute('SELECT * FROM invitations WHERE code=?',(p[3],)).fetchone()
            if not r or r['revoked'] or r['uses']>=r['max_uses'] or (r['expires_at'] and float(r['expires_at'])<=__import__('time').time()): err(self,404); return
            if r['invited_user_id'] is not None and r['invited_user_id']!=uid: err(self,403); return
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(r['workspace_id'],uid,'member'))
            c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(r['id'],))
            self.send_json(200,{'workspace':workspace(c.execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone())}); return
        if p==['','api','workspaces']:
            slug=data.get('slug','')
            if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not data.get('name'): err(self,400); return
            try:
                c.execute('BEGIN'); x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,data['name'],uid)); wid=x.lastrowid
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner')); x=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=x.lastrowid
                c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,uid)); c.execute('COMMIT')
            except sqlite3.IntegrityError: c.execute('ROLLBACK'); err(self,409); return
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            self.send_json(201,{'workspace':workspace(w),'general_channel':channel(ch)}); return
        if len(p)==5 and p[:3]==['','api','workspaces'] and p[4]=='channels':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            m=member(c,w['id'],uid)
            if not m: err(self,403); return
            if m['role']=='guest' or (data.get('is_private') and m['role'] not in ('owner','admin')): err(self,403); return
            name=data.get('name','')
            if not re.fullmatch(r'[a-z0-9-]{1,32}',name): err(self,400); return
            try:
                cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,1 if data.get('is_private') else 0,data.get('topic','')))
            except sqlite3.IntegrityError: err(self,409); return
            cid=cur.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,uid))
            self.send_json(201,{'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())}); return
        if len(p)==5 and p[:3]==['','api','channels'] and p[4]=='join':
            ch=c.execute('SELECT * FROM channels WHERE id=?',(p[3],)).fetchone()
            if not ch: err(self,404); return
            m=member(c,ch['workspace_id'],uid)
            cm=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone()
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            if ch['is_private'] and not cm: err(self,403); return
            if m and m['role']=='guest': err(self,403); return
            if not m:
                if w['join_mode']!='open' or ch['is_private']: err(self,403); return
                c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],uid,'member'))
            c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],uid))
            self.send_json(200,{'channel':channel(ch)}); return
        if len(p)==5 and p[:3]==['','api','workspaces'] and p[4]=='transfer_ownership':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            actor=member(c,w['id'],uid)
            if not actor or actor['role']!='owner': err(self,403); return
            target_id=data.get('new_owner_id')
            target=member(c,w['id'],target_id) if target_id is not None else None
            if not target or target['role']!='admin': err(self,400); return
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],w['owner_id']))
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target_id))
            c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target_id,w['id']))
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
            self.send_json(200,{'workspace':workspace(w)}); return
        err(self,404)
    def do_PATCH(self):
        c=db(); p,q=self.route(); self.bad_json=False; data=self.body(); u=auth(c,self)
        if self.bad_json: err(self,400); return
        if not u: err(self,401); return
        if len(p)==4 and p[:3]==['','api','workspaces']:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            m=member(c,w['id'],u['id'])
            if not m or m['role'] not in ('owner','admin'): err(self,403); return
            if 'join_mode' in data and data['join_mode'] not in ('open','invite_only'): err(self,400); return
            vals=[]
            if 'name' in data:
                if not data['name']: err(self,400); return
                vals.append(('name',data['name']))
            if 'join_mode' in data: vals.append(('join_mode',data['join_mode']))
            if vals: c.execute('UPDATE workspaces SET '+','.join(k+'=?' for k,v in vals)+' WHERE id=?',tuple(v for k,v in vals)+(w['id'],))
            self.send_json(200,{'workspace':workspace(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())}); return
        if len(p)==6 and p[:3]==['','api','workspaces'] and p[4]=='members':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            actor=member(c,w['id'],u['id'])
            target_id=int(p[5]); target=member(c,w['id'],target_id)
            role=data.get('role')
            if role not in ('owner','admin','member','guest'): err(self,400); return
            if not actor or actor['role'] not in ('owner','admin'): err(self,403); return
            if role=='owner': err(self,400); return
            if target_id==w['owner_id']: err(self,403); return
            if target is None: err(self,404); return
            if actor['role']=='admin' and target['role']=='admin': err(self,403); return
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target_id))
            self.send_json(200,{'member':{'user_id':target_id,'role':role}}); return
        if p[:3]==['','api','users'] and p[3]=='me':
            vals={k:data[k] for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in data}
            if vals: c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),u['id']))
            self.send_json(200,{'user':out_user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())}); return
        err(self,404)
    def do_DELETE(self):
        c=db(); p,q=self.route(); u=auth(c,self)
        if not u: err(self,401); return
        if len(p)==6 and p[:3]==['','api','workspaces'] and p[4]=='invitations':
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
            if not w: err(self,404); return
            m=member(c,w['id'],u['id'])
            if not m or m['role'] not in ('owner','admin'): err(self,403); return
            r=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND code=?',(w['id'],p[5])).fetchone()
            if not r: err(self,404); return
            c.execute('UPDATE invitations SET revoked=1 WHERE id=?',(r['id'],))
            self.send_json(200,{'deleted':True}); return
        err(self,404)
if __name__=='__main__':
    init(); ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
