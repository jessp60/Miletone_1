import os, re, json, time, uuid, sqlite3, hashlib, secrets, base64, socket, struct, threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
DB='/app/data/huddle.sqlite3'; os.makedirs('/app/data',exist_ok=True)
PORT=int(os.environ.get('PORT','8000')); NODE=(PORT-8000)//1
LOCK=threading.RLock(); clients=[]

def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def conn():
 c=sqlite3.connect(DB,check_same_thread=False,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; return c
def init():
 with conn() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',mention_count INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
  CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
  CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
  CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
  CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
  CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
  CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,UNIQUE(channel_id,event_id));
  CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read INTEGER DEFAULT 0,mentions INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
  CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator INTEGER,user_ids TEXT);
  CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,uses INTEGER DEFAULT 0,max_uses INTEGER DEFAULT 1,expires INTEGER);
  CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
  ''')
init()

def userobj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def channel(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def auth(h):
 x=h.headers.get('Authorization','')
 tok=x[7:] if x.startswith('Bearer ') else parse_qs(urlparse(h.path).query).get('token',[''])[0]
 if not tok:return None
 with conn() as c:return c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok,)).fetchone()
def parsebody(h):
 n=int(h.headers.get('Content-Length','0') or 0); 
 try:return json.loads(h.rfile.read(n) or b'{}')
 except:return {}
def page(qs, items, key, newest=True):
 try: lim=int(qs.get('limit',['50'])[0])
 except: raise ValueError()
 if lim<1 or lim>200 or 'offset' in qs or 'page' in qs: raise ValueError()
 cur=qs.get('cursor',[None])[0]
 if cur:
  try:
   z=json.loads(base64.urlsafe_b64decode(cur.encode()).decode()); ids=z['ids']; pos=int(z['pos']); items=[x for x in items if x[key] in ids]
  except: raise ValueError()
 else: ids=[x[key] for x in items]; pos=0
 out=items[pos:pos+lim]
 nxt=None if pos+lim>=len(items) else base64.urlsafe_b64encode(json.dumps({'ids':ids,'pos':pos+lim},separators=(',',':')).encode()).decode()
 return out,nxt
def member(c,chan,uid):
 return c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(chan,uid)).fetchone() is not None
def can(c,ch,uid):
 r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
 return r and (member(c,ch,uid) or c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=? AND role IN ("owner","admin")',(r['workspace_id'],uid)).fetchone())
def msgobj(c,r):
 a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 reps=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
 rx=[]
 for x in c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
  ids=[z['user_id'] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(r['id'],x['emoji']))]
  rx.append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':rx,'mentions':[]}
def emit(c,ch,kind,payload):
 n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel_id=?',(ch,)).fetchone()['n']
 c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(ch,n,kind,json.dumps(payload)))
 return n
def broadcast_loop():
 last={}
 while True:
  time.sleep(.04)
  try:
   with conn() as c:
    for r in c.execute('SELECT * FROM events ORDER BY id'):
     if r['id']<=last.get('id',0): continue
     last['id']=r['id']
     frame=json.loads(r['payload']); frame.update(type=r['kind'],event_id=r['event_id'],channel_id=r['channel_id'])
     raw=wsframe(json.dumps(frame))
     for s,ch in list(clients):
      if ch==r['channel_id']:
       try:s.sendall(raw)
       except:
        try: clients.remove((s,ch))
        except: pass
  except: pass
threading.Thread(target=broadcast_loop,daemon=True).start()
def wsframe(x):
 b=x.encode(); n=len(b)
 if n<126:return bytes([129,n])+b
 return bytes([129,126])+struct.pack('!H',n)+b
def wsread(s):
 h=s.recv(2)
 if len(h)<2:return None
 n=h[1]&127
 if n==126:n=struct.unpack('!H',s.recv(2))[0]
 if n==127:n=struct.unpack('!Q',s.recv(8))[0]
 mask=s.recv(4); d=bytearray(s.recv(n))
 for i in range(n):d[i]^=mask[i%4]
 return json.loads(d)
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def send(self,code,obj=None,headers=None):
  b=b'' if obj is None else json.dumps(obj).encode()
  self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers()
  if b:self.wfile.write(b)
 def route(self):
  p=urlparse(self.path); parts=p.path.strip('/').split('/'); qs=parse_qs(p.query); method=self.command
  if p.path=='/api/health':return self.send(200,{'status':'ok','node_id':NODE})
  if p.path=='/' : return self.send(200,HTML,{'Content-Type':'text/html'})
  if p.path=='/api/auth/register' and method=='POST':
   d=parsebody(self)
   if not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or len(d.get('password',''))<8:return self.send(400,{'error':'invalid'})
   try:
    with conn() as c:
     c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],hashlib.sha256(d['password'].encode()).hexdigest(),d.get('display_name') or d['username']))
     u=c.execute('SELECT * FROM users WHERE username=?',(d['username'],)).fetchone(); t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']))
    return self.send(201,{'user':userobj(u),'token':t})
   except sqlite3.IntegrityError:return self.send(409,{'error':'duplicate'})
  if p.path=='/api/auth/login' and method=='POST':
   d=parsebody(self)
   with conn() as c:u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),hashlib.sha256(d.get('password','').encode()).hexdigest())).fetchone()
   if not u:return self.send(401,{'error':'invalid'})
   with conn() as c:t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']))
   return self.send(200,{'user':userobj(u),'token':t})
  u=auth(self)
  if not u:return self.send(401,{'error':'unauthorized'})
  if p.path=='/api/auth/me':return self.send(200,{'user':userobj(u)})
  if p.path=='/api/users/me' and method=='PATCH':
   d=parsebody(self); allowed=['display_name','timezone','avatar_url','status_text','status_emoji']; vals=[d[k] for k in allowed if k in d]
   if 'timezone' in d and not isinstance(d['timezone'],str):return self.send(400,{'error':'timezone'})
   with conn() as c:
    if vals:c.execute('UPDATE users SET '+','.join(k+'=?' for k in allowed if k in d)+' WHERE id=?',vals+[u['id']]); r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
   return self.send(200,{'user':userobj(r)})
  if p.path=='/api/workspaces' and method=='POST':
   d=parsebody(self)
   if not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')):return self.send(400,{'error':'slug'})
   try:
    with conn() as c:
     c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',''),u['id'])); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(d['slug'],)).fetchone(); c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w['id'],'general')); g=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchone()
    return self.send(201,{'workspace':dict(w),'general_channel':channel(g)})
   except sqlite3.IntegrityError:return self.send(409,{'error':'duplicate'})
  with conn() as c:
   if p.path=='/api/workspaces' and method=='GET':
    rows=c.execute('SELECT DISTINCT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();return self.send(200,{'workspaces':[dict(x) for x in rows]})
   if len(parts)>=3 and parts[0]=='api' and parts[1]=='workspaces':
    slug=parts[2]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return self.send(404,{'error':'not found'})
    role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
    if len(parts)==3 and method=='GET':
     cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],qs.get('include_archived',['0'])[0] in ('1','true'))).fetchall()
     cs=[channel(x) for x in cs if not x['is_private'] or member(c,x['id'],u['id']) or role and role['role'] in ('owner','admin')]
     rs={x['id']: {'channel_id':x['id'],'last_read_event_id':(c.execute('SELECT last_read FROM reads WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone() or {'last_read':0})['last_read'],'unread_count':0,'mention_count':(c.execute('SELECT mentions FROM reads WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone() or {'mentions':0})['mentions']} for x in c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],))}
     return self.send(200,{'workspace':dict(w),'channels':cs,'read_state':rs})
    if len(parts)>=4 and parts[3]=='channels' and method=='POST':
     d=parsebody(self); role=role['role'] if role else None
     if role=='guest' or (d.get('is_private') and role not in ('owner','admin')):return self.send(403,{'error':'forbidden'})
     if not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('name','')) or len(d.get('topic',''))>250:return self.send(400,{'error':'invalid'})
     if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return self.send(409,{'error':'duplicate'})
     c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))); ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); c.execute('INSERT INTO cmembers VALUES(?,?)',(ch['id'],u['id']));return self.send(201,{'channel':channel(ch)})
  if parts[0:2]==['api','channels'] and len(parts)>=3:
   cid=int(parts[2]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not ch:return self.send(404,{'error':'not found'})
   ws=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ws['id'],u['id'])).fetchone()
   if parts[3:] and parts[3]=='join' and method=='POST':
    if role is None:
     if ws['join_mode']!='open' or ch['is_private']:return self.send(403,{'error':'forbidden'})
     c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ws['id'],u['id'],'member'))
    if role and role['role']=='guest':return self.send(403,{'error':'forbidden'})
    c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,u['id'])); emit(c,cid,'member.joined',{'user_id':u['id']});return self.send(200,{'channel':channel(ch)})
   if parts[3:] and parts[3]=='leave' and method=='POST':
    c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id']));emit(c,cid,'member.left',{'user_id':u['id']});return self.send(200,{'channel':channel(ch)})
   if parts[3:] and parts[3]=='members' and method=='GET':
    rs=c.execute('SELECT u.* FROM users u JOIN cmembers m ON m.user_id=u.id WHERE m.channel_id=?',(cid,)).fetchall();return self.send(200,{'members':[userobj(x) for x in rs]})
   if parts[3:] and parts[3] in ('archive','unarchive') and method=='POST':
    if not role or role['role']!='owner':return self.send(403,{'error':'forbidden'})
    val=parts[3]=='archive';c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid));emit(c,cid,'channel.archived' if val else 'channel.unarchived',{});return self.send(200,{'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if parts[3:] and parts[3]=='read':
    if method=='GET':
     r=c.execute('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone();return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':r['last_read'] if r else 0,'unread_count':0,'mention_count':r['mentions'] if r else 0}})
    d=parsebody(self);r=c.execute('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); old=r['last_read'] if r else 0; val=max(old,int(d.get('last_read_event_id',0))); c.execute('INSERT OR REPLACE INTO reads VALUES(?,?,?,?)',(u['id'],cid,val,r['mentions'] if r else 0));return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':val,'unread_count':0,'mention_count':r['mentions'] if r else 0}})
   if parts[3:] and parts[3]=='messages':
    if ch['is_archived']:return self.send(423,{'error':'archived'})
    if not member(c,cid,u['id']):return self.send(403,{'error':'membership'})
    if method=='POST':
     d=parsebody(self); body=d.get('body','')
     if not body:return self.send(400,{'error':'empty'})
     if body.startswith('/shrug'):body=(body[6:].strip()+' ¯\\_(ツ)_/¯').strip()
     mentions=[]; names=re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body)
     for n in names:
      if n=='channel': mentions += [x['user_id'] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=?',(cid,u['id']))]
      else:
       x=c.execute('SELECT id FROM users WHERE username=?',(n,)).fetchone()
       if x and x['id']!=u['id'] and c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],x['id'])).fetchone():mentions.append(x['id'])
     mentions=list(dict.fromkeys(mentions)); cur=now(); c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,d.get('parent_id'),cur)); m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone(); o=msgobj(c,m);o['mentions']=mentions
     emit(c,cid,'message.reply' if d.get('parent_id') else 'message.created',o)
     for x in mentions:c.execute('INSERT OR IGNORE INTO reads(user_id,channel_id,last_read,mentions) VALUES(?,?,0,0)',(x,cid));c.execute('UPDATE reads SET mentions=mentions+1 WHERE user_id=? AND channel_id=?',(x,cid))
     return self.send(201,{'message':o})
    rows=list(c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(cid,)));out,nxt=page(qs,rows,'id');return self.send(200,{'messages':[msgobj(c,x) for x in out],'next_cursor':nxt})
   if parts[3:] and parts[3]=='pins':
    if not can(c,cid,u['id']):return self.send(403,{'error':'membership'})
    rows=list(c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC',(cid,)));out,nxt=page(qs,rows,'id');return self.send(200,{'pins':[{'message':msgobj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in out],'next_cursor':nxt})
   if method=='PATCH':
    d=parsebody(self)
    if not (member(c,cid,u['id']) or role and role['role']=='owner'):return self.send(403,{'error':'forbidden'})
    if len(d.get('topic',''))>250:return self.send(400,{'error':'topic'})
    c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),cid));emit(c,cid,'channel.updated',{});return self.send(200,{'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
  if parts[0:2]==['api','messages'] and len(parts)>=3:
   mid=int(parts[2]); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   if not m:return self.send(404,{'error':'not found'})
   if len(parts)>3 and parts[3]=='replies':
    rows=list(c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)));out,nxt=page(qs,rows,'id');return self.send(200,{'replies':[msgobj(c,x) for x in out],'next_cursor':nxt})
   if len(parts)>3 and parts[3]=='pin' and method=='POST':
    c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],now()));r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone();return self.send(200,{'pin':dict(r)})
   if len(parts)>3 and parts[3]=='pin' and method=='DELETE':c.execute('DELETE FROM pins WHERE message_id=?',(mid,));return self.send(200,{'unpinned':True})
  if p.path=='/api/search':
   q=qs.get('q',[''])[0]; sql='SELECT m.* FROM messages m JOIN cmembers cm ON cm.channel_id=m.channel_id JOIN channels sc ON sc.id=m.channel_id WHERE cm.user_id=? AND m.deleted=0 AND m.body LIKE ?'; args=[u['id'],'%'+q+'%']; sql += ' AND sc.workspace_id=?' if 'workspace_id' in qs else ''; args += [int(qs['workspace_id'][0])] if 'workspace_id' in qs else []; sql += ' GROUP BY m.id ORDER BY m.id DESC'; rows=list(c.execute(sql,args));out,nxt=page(qs,rows,'id');return self.send(200,{'results':[msgobj(c,x) for x in out],'next_cursor':nxt})
  return self.send(404,{'error':'not found'})
 def do_GET(self):
  if self.path.startswith('/api/ws'): return self.websocket()
  try:self.route()
  except ValueError:self.send(400,{'error':'invalid pagination'})
  except Exception as e:self.send(500,{'error':str(e)})
 def do_POST(self): 
  try:self.route()
  except ValueError:self.send(400,{'error':'invalid'})
  except Exception as e:self.send(500,{'error':str(e)})
 def do_PATCH(self):
  try:self.route()
  except Exception as e:self.send(500,{'error':str(e)})
 def do_DELETE(self):
  try:self.route()
  except Exception as e:self.send(500,{'error':str(e)})
 def websocket(self):
  u=auth(self)
  if not u:return self.send(401,{'error':'unauthorized'})
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.send(400)
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  s=self.connection; subscribed=None; clients.append((s,0))
  try:
   while True:
    d=wsread(s)
    if not d:break
    if d.get('type') in ('subscribe','resume'):
     cid=int(d['channel_id']); subscribed=cid
     for i,x in enumerate(clients):
      if x[0] is s:clients[i]=(s,cid)
     with conn() as c:
      head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']
      if d['type']=='resume':
       for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,int(d.get('since_event_id',0)))):
        f=json.loads(r['payload']);f.update(type=r['kind'],event_id=r['event_id'],channel_id=cid);s.sendall(wsframe(json.dumps(f)))
       s.sendall(wsframe(json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head})))
      else:s.sendall(wsframe(json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head})))
  except:pass
  try:clients.remove((s,subscribed or 0))
  except:pass
HTML=b'''<!doctype html><html><head><title>Huddle</title><style>body{font:16px Arial;margin:0;background:#f8f8f8}button{padding:8px;background:#007a5a;color:#fff;border:0}#app{padding:30px}</style></head><body><div id="app"><h1>Huddle</h1><p>Team chat</p><form id="auth-form"><input name="username" placeholder="username"><input name="password" type="password" placeholder="password"><button id="auth-submit" data-button-role="primary">Login</button></form></div></body></html>'''
if __name__=='__main__':
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
