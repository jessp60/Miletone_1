import json, os, re, sqlite3, secrets, hashlib, time, urllib.parse, fcntl
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
DB='/app/huddle.db'
PORT=int(os.environ.get('PORT','8000')); NODE=(PORT-8000)
def db():
    c=sqlite3.connect(DB,timeout=30,isolation_level=None); c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    return c
def init():
    lock=open('/app/.init.lock','w'); fcntl.flock(lock,fcntl.LOCK_EX)
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,expires REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0,invited_user_id INTEGER);
    '''); c.commit(); c.close()
init()
def now(): return time.strftime('%Y-%m-%dT%H:%M:%S.000Z',time.gmtime())
def user(r):
    return {'id':r['id'],'username':r['username'],'display_name':r['display_name'] or r['username'],'timezone':r['timezone'] or 'UTC','avatar_url':r['avatar_url'] or '','status_text':r['status_text'] or '','status_emoji':r['status_emoji'] or ''}
def ws(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def ch(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':False,'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*x): pass
    def sendj(self,status,obj):
        b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); 
        try:return json.loads(self.rfile.read(n) or b'{}')
        except:return {}
    def auth(self,required=True):
        a=self.headers.get('Authorization','')
        if not a.startswith('Bearer '): return None
        c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(a[7:],)).fetchone(); c.close()
        return r
    def route(self):
        p=urllib.parse.urlsplit(self.path); path=p.path; q=urllib.parse.parse_qs(p.query); method=self.command; data=self.body()
        if path=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
        if path=='/api/ws' or path.startswith('/api/ws?'):
            return self.sendj(401,{'error':'unauthorized'})
        c=db(); me=self.auth(False)
        if path=='/api/auth/register' and method=='POST':
            u=data.get('username',''); pw=data.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(pw)<8:return self.sendj(400,{'error':'invalid input'})
            try:
                x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,pw,data.get('display_name') or u)); uid=x.lastrowid
                tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); return self.sendj(201,{'user':user(r),'token':tok})
            except sqlite3.IntegrityError:return self.sendj(409,{'error':'duplicate'})
        if path=='/api/auth/login' and method=='POST':
            r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(data.get('username'),data.get('password'))).fetchone()
            if not r:return self.sendj(401,{'error':'invalid credentials'})
            tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); c.commit(); return self.sendj(200,{'user':user(r),'token':tok})
        if path=='/api/auth/me' and method=='GET':
            return self.sendj(200,{'user':user(me)}) if me else self.sendj(401,{'error':'unauthorized'})
        if not me:return self.sendj(401,{'error':'unauthorized'})
        uid=me['id']
        if path=='/api/workspaces' and method=='GET':
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(uid,)).fetchall(); return self.sendj(200,{'workspaces':[ws(x) for x in rows]})
        if path=='/api/workspaces' and method=='POST':
            slug=data.get('slug',''); name=data.get('name','')
            if not re.fullmatch(r'[a-z0-9-]{2,32}',slug):return self.sendj(400,{'error':'invalid slug'})
            try:
                x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,uid)); wid=x.lastrowid
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner')); y=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=y.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,uid));c.commit()
                return self.sendj(201,{'workspace':ws(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
            except sqlite3.IntegrityError:return self.sendj(409,{'error':'duplicate'})
        m=re.fullmatch(r'/api/workspaces/([^/]+)(.*)',path)
        if m:
            slug,rest=m.group(1),m.group(2); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w:return self.sendj(404,{'error':'not found'})
            mr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone(); role=mr['role'] if mr else None
            if rest=='' and method=='GET':
                if not mr:return self.sendj(403,{'error':'forbidden'})
                cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))).fetchall()
                return self.sendj(200,{'workspace':ws(w),'channels':[ch(x) for x in cs],'read_state':{}})
            if rest=='/members' and method=='GET':
                if not mr:return self.sendj(403,{'error':'forbidden'})
                return self.sendj(200,{'members':[{'user_id':x['user_id'],'role':x['role']} for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
            if rest=='' and method=='PATCH':
                if role not in ('owner','admin'):return self.sendj(403,{'error':'forbidden'})
                if 'join_mode' in data and data['join_mode'] not in ('open','invite_only'):return self.sendj(400,{'error':'invalid join_mode'})
                vals=[]; args=[]
                for k in ('name','join_mode'):
                    if k in data: vals.append(k+'=?');args.append(data[k])
                if vals:c.execute('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',args+[w['id']]);c.commit()
                return self.sendj(200,{'workspace':ws(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if rest=='/channels' and method=='POST':
                if role not in ('owner','admin','member'):return self.sendj(403,{'error':'forbidden'})
                priv=bool(data.get('is_private',False))
                if priv and role not in ('owner','admin'):return self.sendj(403,{'error':'forbidden'})
                name=data.get('name','')
                if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return self.sendj(400,{'error':'invalid name'})
                if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():return self.sendj(409,{'error':'duplicate'})
                x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,priv,data.get('topic',''))); cid=x.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,uid));c.commit();return self.sendj(201,{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
            if rest.startswith('/members/') and method=='PATCH':
                target=int(rest.split('/')[2]); tr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone()
                if not tr:return self.sendj(404,{'error':'not member'})
                new=data.get('role')
                if new not in ('owner','admin','member','guest'):return self.sendj(400,{'error':'invalid role'})
                if new=='owner':return self.sendj(400,{'error':'use transfer'})
                if role not in ('owner','admin') or target==uid or tr['role']=='owner' or (role=='admin' and tr['role']=='admin'):return self.sendj(403,{'error':'forbidden'})
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(new,w['id'],target));c.commit();return self.sendj(200,{'member':{'user_id':target,'role':new}})
            if rest=='/transfer_ownership' and method=='POST':
                if role!='owner':return self.sendj(403,{'error':'forbidden'})
                target=int(data.get('new_owner_id',0)); tr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone()
                if not tr or tr['role']!='admin':return self.sendj(400,{'error':'target must be admin'})
                c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],uid));c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],target));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']));c.commit();return self.sendj(200,{'workspace':ws(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if rest=='/invitations' and method in ('GET','POST'):
                if role not in ('owner','admin'):return self.sendj(403,{'error':'forbidden'})
                if method=='GET':
                    rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0',(w['id'],)).fetchall();return self.sendj(200,{'invitations':[dict(x) for x in rows]})
                code=secrets.token_urlsafe(16); exp=time.time()+int(data.get('expires_in_seconds',31536000)); maxu=int(data.get('max_uses',1)); inv=data.get('invited_user_id');c.execute('INSERT INTO invitations(code,workspace_id,expires,max_uses,invited_user_id) VALUES(?,?,?,?,?)',(code,w['id'],exp,maxu,inv));c.commit();return self.sendj(201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':maxu}})
            if rest.startswith('/invitations/') and method=='DELETE':
                if role not in ('owner','admin'):return self.sendj(403,{'error':'forbidden'})
                code=rest.split('/')[2];c.execute('UPDATE invitations SET revoked=1 WHERE code=? AND workspace_id=?',(code,w['id']));c.commit();return self.sendj(200,{'deleted':True})
        mm=re.fullmatch(r'/api/channels/(\d+)/join',path)
        if mm and method=='POST':
            x=c.execute('SELECT * FROM channels WHERE id=?',(int(mm.group(1)),)).fetchone()
            if not x:return self.sendj(404,{'error':'not found'})
            mr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(x['workspace_id'],uid)).fetchone(); wr=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
            if x['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],uid)).fetchone():return self.sendj(403,{'error':'private'})
            if mr and mr['role']=='guest':return self.sendj(403,{'error':'guest'})
            if not mr:
                if wr['join_mode']!='open':return self.sendj(403,{'error':'invite only'})
                c.execute('INSERT INTO members VALUES(?,?,?)',(wr['id'],uid,'member'))
            c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(x['id'],uid));c.commit();return self.sendj(200,{'channel':ch(x)})
        if path.startswith('/api/invitations/') and path.endswith('/accept') and method=='POST':
            code=path.split('/')[3]; inv=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
            if not inv or inv['revoked'] or inv['uses']>=inv['max_uses'] or inv['expires']<time.time():return self.sendj(404,{'error':'invalid invitation'})
            if inv['invited_user_id'] and inv['invited_user_id']!=uid:return self.sendj(403,{'error':'targeted'})
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],uid,'member'));c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,));c.commit();return self.sendj(200,{'accepted':True})
        return self.sendj(404,{'error':'not found'})
    def do_GET(self): self.route()
    def do_POST(self): self.route()
    def do_PATCH(self): self.route()
    def do_DELETE(self): self.route()
if __name__=='__main__':
    ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
