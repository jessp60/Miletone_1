import json, os, re, sqlite3, sys, time, secrets, hashlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

DB='/app/data.db'
def db():
    c=sqlite3.connect(DB, timeout=10, check_same_thread=False)
    c.row_factory=sqlite3.Row
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
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,created_by INTEGER,expires REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_invites(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    '''); c.commit(); c.close()
def user_obj(r):
    return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def ws_obj(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def ch_obj(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def sendj(self,status,obj):
        b=json.dumps(obj,separators=(',',':')).encode()
        self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        try:
            n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
        except: return {}
    def auth(self, required=True):
        a=self.headers.get('Authorization','')
        if not a.startswith('Bearer '):
            if required: self.sendj(401,{'error':'unauthorized'})
            return None
        c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(a[7:],)).fetchone(); c.close()
        if not r and required: self.sendj(401,{'error':'unauthorized'})
        return r
    def fail(self,s,msg='error'): self.sendj(s,{'error':msg})
    def route(self):
        p=unquote(urlparse(self.path).path); parts=p.strip('/').split('/')
        method=self.command; data=self.body() if method in ('POST','PATCH','PUT') else {}
        if p=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(self.server.server_port)-8000})
        if p=='/api/auth/register' and method=='POST':
            u=data.get('username',''); pw=data.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(pw)<8: return self.fail(400)
            c=db()
            try:
                cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,pw,data.get('display_name') or u)); uid=cur.lastrowid
                tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return self.sendj(201,{'user':user_obj(r),'token':tok})
            except sqlite3.IntegrityError: c.close(); return self.fail(409)
        if p=='/api/auth/login' and method=='POST':
            c=db(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(data.get('username'),data.get('password'))).fetchone()
            if not r: c.close(); return self.fail(401)
            tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); c.commit(); c.close(); return self.sendj(200,{'user':user_obj(r),'token':tok})
        u=self.auth()
        if not u:return
        if p=='/api/auth/me': return self.sendj(200,{'user':user_obj(u)})
        c=db()
        if p=='/api/workspaces' and method=='GET':
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close(); return self.sendj(200,{'workspaces':[ws_obj(x) for x in rows]})
        if p=='/api/workspaces' and method=='POST':
            slug=data.get('slug',''); name=data.get('name','')
            if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not name: c.close(); return self.fail(400)
            try:
                x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id'])); wid=x.lastrowid
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); x=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?,?)",(wid,'general')); cid=x.lastrowid
                c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.commit(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.sendj(201,{'workspace':ws_obj(w),'general_channel':ch_obj(ch)})
            except sqlite3.IntegrityError: c.close(); return self.fail(409)
        if len(parts)>=3 and parts[1]=='workspaces':
            slug=parts[2]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w: c.close(); return self.fail(404)
            mr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); role=mr['role'] if mr else None
            if len(parts)==4 and parts[3]=='members' and method=='GET':
                if not mr: c.close(); return self.fail(403)
                rows=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)).fetchall(); c.close(); return self.sendj(200,{'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in rows]})
            if len(parts)==3 and method=='GET':
                if not mr: c.close(); return self.fail(403)
                chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall(); c.close(); return self.sendj(200,{'workspace':ws_obj(w),'channels':[ch_obj(x) for x in chans],'read_state':{}})
            if len(parts)==3 and method=='PATCH':
                if role not in ('owner','admin'): c.close(); return self.fail(403)
                if 'join_mode' in data and data['join_mode'] not in ('open','invite_only'): c.close(); return self.fail(400)
                vals=[]; args=[]
                for k in ('name','join_mode'):
                    if k in data: vals.append(k+'=?'); args.append(data[k])
                if vals: c.execute('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',args+[w['id']])
                c.commit(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone(); c.close(); return self.sendj(200,{'workspace':ws_obj(w)})
            if len(parts)==4 and parts[3]=='channels' and method=='POST':
                if role not in ('owner','admin','member') or (role=='member' and data.get('is_private',False)): c.close(); return self.fail(403)
                name=data.get('name','')
                if not re.fullmatch(r'[a-z0-9-]{1,32}',name): c.close(); return self.fail(400)
                try:
                    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(data.get('is_private'))),data.get('topic',''))); cid=x.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.commit(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.sendj(201,{'channel':ch_obj(ch)})
                except sqlite3.IntegrityError: c.close(); return self.fail(409)
            if len(parts)==5 and parts[3]=='members' and method=='PATCH':
                target=int(parts[4]); new=data.get('role')
                if new not in ('owner','admin','member','guest'): c.close(); return self.fail(400)
                old=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone()
                if not old: c.close(); return self.fail(404)
                if new=='owner': c.close(); return self.fail(400)
                if role not in ('owner','admin') or target==u['id'] or (role=='admin' and old['role'] in ('admin','owner')): c.close(); return self.fail(403)
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(new,w['id'],target)); c.commit(); c.close(); return self.sendj(200,{'member':{'user_id':target,'role':new}})
            if len(parts)==4 and parts[3]=='invitations':
                if role not in ('owner','admin'): c.close(); return self.fail(403)
                if method=='GET':
                    rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND (expires IS NULL OR expires>?)',(w['id'],time.time())).fetchall(); c.close(); return self.sendj(200,{'invitations':[dict(x) for x in rs]})
                if method=='POST':
                    exp=data.get('expires_in_seconds'); code=secrets.token_urlsafe(12); expv=time.time()+int(exp) if exp is not None else None
                    c.execute('INSERT INTO invitations(code,workspace_id,created_by,expires,max_uses,invited_user_id) VALUES(?,?,?,?,?,?)',(code,w['id'],u['id'],expv,int(data.get('max_uses',1)),data.get('invited_user_id'))); c.commit(); r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone(); c.close(); return self.sendj(201,{'invitation':dict(r)})
            if len(parts)==5 and parts[3]=='invitations' and method=='DELETE':
                if role not in ('owner','admin'): c.close(); return self.fail(403)
                c.execute('UPDATE invitations SET revoked=1 WHERE workspace_id=? AND code=?',(w['id'],parts[4])); c.commit(); c.close(); return self.sendj(200,{'deleted':True})
            if len(parts)==4 and parts[3]=='transfer_ownership' and method=='POST':
                if role!='owner': c.close(); return self.fail(403)
                tid=data.get('new_owner_id'); tr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],tid)).fetchone()
                if not tr or tr['role']!='admin': c.close(); return self.fail(400)
                c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])); c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],tid)); c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(tid,w['id'])); c.commit(); c.close(); return self.sendj(200,{'ok':True})
        if len(parts)>=4 and parts[1]=='invitations' and parts[3]=='accept' and method=='POST':
            code=parts[2]; inv=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
            if not inv or inv['revoked'] or (inv['expires'] and inv['expires']<=time.time()) or inv['uses']>=inv['max_uses']: c.close(); return self.fail(404)
            if inv['invited_user_id'] and inv['invited_user_id']!=u['id']: c.close(); return self.fail(403)
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member')); c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,)); c.commit(); c.close(); return self.sendj(200,{'accepted':True})
        if len(parts)>=3 and parts[1]=='channels':
            try: cid=int(parts[2])
            except: cid=0
            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch: c.close(); return self.fail(404)
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); mr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if len(parts)==4 and parts[3]=='join' and method=='POST':
                if ch['is_private']:
                    allowed=c.execute('SELECT 1 FROM channel_invites WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
                    if not allowed: c.close(); return self.fail(403)
                if mr and mr['role']=='guest': c.close(); return self.fail(403)
                if not mr:
                    if w['join_mode']!='open': c.close(); return self.fail(403)
                    c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
                c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id'])); c.commit(); c.close(); return self.sendj(200,{'channel':ch_obj(ch)})
            c.close()
        c.close(); return self.fail(404)
    def do_GET(self): self.route()
    def do_POST(self): self.route()
    def do_PATCH(self): self.route()
    def do_DELETE(self): self.route()
    def log_message(self,*a): pass
if __name__=='__main__':
    init(); port=int(sys.argv[1]); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
