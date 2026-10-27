#!/usr/bin/env python3
import os,sys,json,re,socket,sqlite3,secrets,hashlib,base64,struct,threading,datetime,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data/state.db'; os.makedirs('/app/data',exist_ok=True)
lock=threading.RLock()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=10000'); return c
def init():
 c=db()
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires INTEGER,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS message_mentions(message_id INTEGER,user_id INTEGER,PRIMARY KEY(message_id,user_id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT);''')
 c.execute('ALTER TABLE reads ADD COLUMN mention_count INTEGER DEFAULT 0') if 'mention_count' not in [x['name'] for x in c.execute('PRAGMA table_info(reads)')] else None
 c.commit(); c.close()
init()
def q(sql,args=(),one=False):
 c=db(); r=c.execute(sql,args); x=r.fetchone() if one else r.fetchall(); c.close(); return x
def ex(sql,args=()):
 c=db(); r=c.execute(sql,args); c.commit(); x=r.lastrowid; c.close(); return x
def userobj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(h):
 m=re.match(r'Bearer\s+(.+)',h or ''); r=q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(m.group(1),),True) if m else None
 return r
def wsobj(r): return dict(r)
def chobj(r): 
 x=dict(r); x['is_private']=bool(x['is_private']); x['is_dm']=bool(x['is_dm']); x['is_archived']=bool(x['is_archived']); return x
def event(channel_id,kind,payload):
 eid=ex('INSERT INTO events(channel_id,event_id,kind,payload) SELECT ?,COALESCE(MAX(event_id),0)+1,?,? FROM events WHERE channel_id=?',(channel_id,kind,json.dumps(payload),channel_id))
 return q('SELECT event_id FROM events WHERE id=?',(eid,),True)['event_id']
def evframe(r):
 x=json.loads(r['payload']); x.update({'type':r['kind'],'event_id':r['event_id'],'channel_id':r['channel_id']}); return x
def msgobj(r):
 x=dict(r); x['author']=userobj(q('SELECT * FROM users WHERE id=?',(r['author_id'],),True)); x['files']=[]; x['reactions']=[]
 for z in q('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
  x['reactions'].append({'emoji':z['emoji'],'count':z['n'],'user_ids':[int(a) for a in z['ids'].split(',')]})
 x['reply_count']=q('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],),True)['n']; x['mentions']=[z['user_id'] for z in q('SELECT user_id FROM message_mentions WHERE message_id=?',(r['id'],))]
 return x
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def send(self,status,obj=None,raw=None,typ='application/json'):
  data=raw if raw is not None else json.dumps(obj or {}).encode()
  self.send_response(status); self.send_header('Content-Type',typ); self.send_header('Content-Length',str(len(data))); self.send_header('Connection','close'); self.end_headers(); self.wfile.write(data)
 def body(self):
  try:
   return json.loads(self.rfile.read(int(self.headers.get('Content-Length',0)) or 0) or b'{}')
  except:
   self.bad_json=True
   return {}
 def path_parts(self):
  u=urllib.parse.urlparse(self.path); return u.path.split('/'); return
 def reqauth(self):
  u=auth(self.headers.get('Authorization')); 
  if not u:self.send(401,{'error':'unauthorized'})
  return u
 def do_GET(self):
  self.bad_json=False
  p=self.path_parts(); query=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
  if p[:3]==['','api','ws']:
   tok=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('token',[''])[0]
   if not q('SELECT 1 FROM tokens WHERE token=?',(tok,),True): self.send_response(401); self.end_headers(); return
   key=self.headers.get('Sec-WebSocket-Key',''); import hashlib,base64,select,time
   accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
   self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers(); self.wfile.flush()
   sock=self.connection; sock.setblocking(False); buf=b''; subs={}
   def sendf(obj):
    d=json.dumps(obj).encode(); n=len(d)
    if n<126:h=bytes([129,n])
    elif n<65536:h=bytes([129,126])+struct.pack('>H',n)
    else:h=bytes([129,127])+struct.pack('>Q',n)
    sock.sendall(h+d)
   def frames():
    nonlocal buf
    try: buf+=sock.recv(65536)
    except BlockingIOError:return []
    out=[]
    while len(buf)>=2:
     n=buf[1]&127; off=2
     if n==126:
      if len(buf)<4:return out
      n=struct.unpack('>H',buf[2:4])[0];off=4
     if len(buf)<off+4+n:return out
     mask=buf[off:off+4]; d=buf[off+4:off+4+n];buf=buf[off+4+n:]
     try: out.append(json.loads(bytes(c^mask[i%4] for i,c in enumerate(d))))
     except: pass
    return out
   while True:
    try:
     for f in frames():
      cid=int(f.get('channel_id')); typ=f.get('type')
      if typ=='subscribe':
       head=q('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,),True)['n']; subs[cid]=head;sendf({'type':'subscribed','channel_id':cid,'head_event_id':head})
      elif typ=='resume':
       since=int(f.get('since_event_id',0))
       for r in q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)):sendf(evframe(r))
       head=q('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,),True)['n'];subs[cid]=head;sendf({'type':'resumed','channel_id':cid,'head_event_id':head})
     for cid,last in list(subs.items()):
      rows=q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last,))
      for r in rows:sendf(evframe(r));subs[cid]=r['event_id']
     select.select([sock],[],[],.05)
    except Exception:return
   return
  if p[:3]==['','api','health']: return self.send(200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
  if p[:3]==['','api','auth'] and len(p)>3 and p[3]=='me':
   u=self.reqauth(); return u and self.send(200,{'user':userobj(u)})
  u=self.reqauth()
  if not u:return
  if p[:3]==['','api','users'] and len(p)==4:
   r=q('SELECT * FROM users WHERE id=?',(p[3],),True); return self.send(200,{'user':userobj(r)}) if r else self.send(404)
  if p[:3]==['','api','workspaces'] and len(p)==3:
   rows=q('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)); return self.send(200,{'workspaces':[wsobj(x) for x in rows]})
  if p[:3]==['','api','workspaces'] and len(p)==4:
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   mem=q('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
   if not mem:return self.send(403)
   include_arch=query.get('include_archived',['0'])[0] in ('1','true')
   cs=[]
   for x in q('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],include_arch)):
    if not x['is_private'] or q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(x['id'],u['id']),True):
     cs.append(chobj(x))
   rs=[dict(x) for x in q('SELECT * FROM reads WHERE user_id=? AND channel_id IN (SELECT id FROM channels WHERE workspace_id=?)',(u['id'],w['id']))]
   return self.send(200,{'workspace':wsobj(w),'channels':cs,'read_state':rs})
  if len(p)>=5 and p[1:3]==['api','workspaces'] and p[4]=='members':
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   return self.send(200,{'members':[dict(x) for x in q('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
  if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
   if not m or m['role'] not in ('owner','admin'):return self.send(403)
   import time
   rows=q('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires=0 OR expires>?)',(w['id'],int(time.time())))
   return self.send(200,{'invitations':[dict(x) for x in rows]})
  if p[:3]==['','api','search']:
   if 'offset' in query or 'page' in query:return self.send(400)
   try: limit=int(query.get('limit',['50'])[0])
   except: return self.send(400)
   if limit<1 or limit>200:return self.send(400)
   term=query.get('q',[''])[0]; wid=query.get('workspace_id',[None])[0]; cur=query.get('cursor',[None])[0]
   if cur is not None and not re.fullmatch(r's[0-9]+',cur):return self.send(400)
   sql="SELECT m.* FROM messages m JOIN channels c ON c.id=m.channel_id WHERE m.deleted=0 AND m.body LIKE ? AND m.channel_id IN (SELECT cm.channel_id FROM cmembers cm WHERE cm.user_id=?)"; args=[f'%{term}%',u['id']]
   if wid is not None: sql+=' AND c.workspace_id=?'; args.append(wid)
   if cur is not None: sql+=' AND m.id<?'; args.append(int(cur[1:]))
   sql+=' ORDER BY m.id DESC LIMIT ?';args.append(limit+1); rows=q(sql,tuple(args));more=len(rows)>limit;rows=rows[:limit]
   return self.send(200,{'results':[msgobj(x) for x in rows],'next_cursor':('s'+str(rows[-1]['id'])) if more and rows else None})
  if p[:3]==['','api','channels'] and len(p)>=4:
   cid=p[3]; c=q('SELECT * FROM channels WHERE id=?',(cid,),True)
   if not c:return self.send(404)
   if len(p)==5 and p[4]=='members': return self.send(200,{'members':[userobj(x) for x in q('SELECT u.* FROM users u JOIN cmembers m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
   if len(p)==5 and p[4]=='pins':
    if c['is_private'] and not q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id']),True):return self.send(403)
    try: limit=int(query.get('limit',['50'])[0])
    except: return self.send(400)
    if limit<1 or limit>200:return self.send(400)
    cur=query.get('cursor',[None])[0]
    if cur is not None and not re.fullmatch(r'p[0-9]+',cur):return self.send(400)
    sql='SELECT p.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';args=[cid]
    if cur is not None:sql+=' AND p.message_id<?';args.append(int(cur[1:]))
    sql+=' ORDER BY p.message_id DESC LIMIT ?';args.append(limit+1); rows=q(sql,tuple(args));more=len(rows)>limit;rows=rows[:limit]
    return self.send(200,{'pins':[{'message':msgobj(q('SELECT * FROM messages WHERE id=?',(x['message_id'],),True)),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':('p'+str(rows[-1]['message_id'])) if more and rows else None})
   if len(p)==5 and p[4]=='read':
    r=q('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid),True); return self.send(200,{'read_state':{'channel_id':int(cid),'last_read_event_id':r['last_read_event_id'] if r else 0,'unread_count':0,'mention_count':r['mention_count'] if r and 'mention_count' in r.keys() else 0}})
   if len(p)==5 and p[4]=='messages':
    if 'offset' in query or 'page' in query:return self.send(400)
    try: limit=int(query.get('limit',['50'])[0])
    except: return self.send(400)
    if limit<1 or limit>200:return self.send(400)
    cur=query.get('cursor',[None])[0]; before=None
    if cur is not None:
     if not re.fullmatch(r'c[0-9]+',cur):return self.send(400)
     before=int(cur[1:])
    sql='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'
    args=[cid]
    if before is not None:sql+=' AND id<?';args.append(before)
    sql+=' ORDER BY id DESC LIMIT ?';args.append(limit+1)
    rows=q(sql,tuple(args)); more=len(rows)>limit; rows=rows[:limit]
    nxt=('c'+str(rows[-1]['id'])) if more and rows else None
    return self.send(200,{'messages':[msgobj(x) for x in rows],'next_cursor':nxt})
  if p[:3]==['','api','messages'] and len(p)==5 and p[4]=='replies':
   if not q('SELECT id FROM messages WHERE id=?',(p[3],),True):return self.send(404)
   query=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
   try: limit=int(query.get('limit',['50'])[0])
   except: return self.send(400)
   if limit<1 or limit>200:return self.send(400)
   cur=query.get('cursor',[None])[0]; after=None
   if cur is not None:
    if not re.fullmatch(r'r[0-9]+',cur):return self.send(400)
    after=int(cur[1:])
   sql='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[p[3]]
   if after is not None:sql+=' AND id>?';args.append(after)
   sql+=' ORDER BY id LIMIT ?';args.append(limit+1)
   rows=q(sql,tuple(args));more=len(rows)>limit;rows=rows[:limit]
   nxt=('r'+str(rows[-1]['id'])) if more and rows else None
   return self.send(200,{'replies':[msgobj(x) for x in rows],'next_cursor':nxt})
  return self.send(404)
 def do_POST(self):
  self.bad_json=False
  p=self.path_parts(); b=self.body()
  if p[:4]==['','api','auth','register']:
   if self.bad_json or 'password' not in b or not re.fullmatch(r'[A-Za-z0-9_]+',str(b.get('username',''))) or len(b.get('password',''))<8:return self.send(400)
   if q('SELECT id FROM users WHERE username=?',(b['username'],),True):return self.send(409)
   uid=ex('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],b['password'],b.get('display_name') or b['username'])); tok=secrets.token_urlsafe(24); ex('INSERT INTO tokens VALUES(?,?)',(tok,uid)); return self.send(201,{'user':userobj(q('SELECT * FROM users WHERE id=?',(uid,),True)),'token':tok})
  if p[:4]==['','api','auth','login']:
   if self.bad_json:return self.send(400)
   r=q('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password')),True)
   if not r:return self.send(401)
   tok=secrets.token_urlsafe(24); ex('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); return self.send(200,{'user':userobj(r),'token':tok})
  u=self.reqauth()
  if not u:return
  if p[:3]==['','api','workspaces'] and len(p)==3:
   if self.bad_json or not b.get('name') or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return self.send(400)
   if q('SELECT id FROM workspaces WHERE slug=?',(b['slug'],),True):return self.send(409)
   wid=ex('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id'])); ex('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); cid=ex('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); ex('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); return self.send(201,{'workspace':wsobj(q('SELECT * FROM workspaces WHERE id=?',(wid,),True)),'general_channel':chobj(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
  if len(p)>=5 and p[1:3]==['api','workspaces'] and p[4]=='channels':
   if self.bad_json or not b.get('name') or not re.fullmatch(r'[a-z0-9-]{1,32}',str(b.get('name'))) or len(str(b.get('topic',''))) > 250:return self.send(400)
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True); m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True) if w else None
   if not w:return self.send(404)
   if not m:return self.send(403)
   if m['role']=='guest' or (b.get('is_private') and m['role'] not in ('owner','admin')):return self.send(403)
   if q('SELECT id FROM channels WHERE workspace_id=? AND name=?',(w['id'],b.get('name')),True):return self.send(409)
   cid=ex('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))); ex('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); return self.send(201,{'channel':chobj(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
  if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='join':
   c=q('SELECT * FROM channels WHERE id=?',(p[3],),True)
   if not c:return self.send(404)
   wm=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(c['workspace_id'],u['id']),True)
   if wm and wm['role']=='guest' and not q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(c['id'],u['id']),True):return self.send(403)
   if c['is_private'] and not q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(c['id'],u['id']),True):return self.send(403)
   w=q('SELECT * FROM workspaces WHERE id=?',(c['workspace_id'],),True)
   if not q('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True):
    if w['join_mode']!='open' or c['is_private']:return self.send(403)
    ex('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
   ex('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(c['id'],u['id'])); event(c['id'],'member.joined',{'user_id':u['id']}); return self.send(200,{'channel':chobj(c)})
  if p[:3]==['','api','channels'] and len(p)==5 and p[4] in ('leave','archive','unarchive'):
   c=q('SELECT * FROM channels WHERE id=?',(p[3],),True)
   if not c:return self.send(404)
   w=q('SELECT * FROM workspaces WHERE id=?',(c['workspace_id'],),True); m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(c['workspace_id'],u['id']),True)
   if p[4] in ('archive','unarchive'):
    if not m or m['role']!='owner':return self.send(403)
    ex('UPDATE channels SET is_archived=? WHERE id=?',(1 if p[4]=='archive' else 0,c['id'])); event(c['id'],'channel.archived' if p[4]=='archive' else 'channel.unarchived',{})
   else: ex('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(c['id'],u['id'])); event(c['id'],'member.left',{'user_id':u['id']})
   return self.send(200,{'channel':chobj(q('SELECT * FROM channels WHERE id=?',(c['id'],),True))})
  if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='read':
   c=q('SELECT id FROM channels WHERE id=?',(p[3],),True)
   if not c:return self.send(404)
   old=q('SELECT last_read_event_id,mention_count FROM reads WHERE user_id=? AND channel_id=?',(u['id'],p[3]),True); val=max(int(b.get('last_read_event_id',0)),old['last_read_event_id'] if old else 0)
   ex('INSERT INTO reads(user_id,channel_id,last_read_event_id,mention_count) VALUES(?,?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=?',(u['id'],p[3],val,old['mention_count'] if old else 0,val))
   return self.send(200,{'read_state':{'channel_id':int(p[3]),'last_read_event_id':val,'unread_count':0,'mention_count':old['mention_count'] if old else 0}})
  if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='messages':
   c=q('SELECT * FROM channels WHERE id=?',(p[3],),True)
   if not c:return self.send(404)
   if c['is_archived']:return self.send(423)
   if not b.get('body'):return self.send(400)
   if not q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(c['id'],u['id']),True):return self.send(403)
   eid=(q('SELECT COALESCE(MAX(event_id),0)+1 n FROM messages WHERE channel_id=?',(c['id'],),True)['n']); mid=ex('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,event_id) VALUES(?,?,?,?,?,?)',(c['id'],u['id'],b['body'],b.get('parent_id'),now(),eid)); event(c['id'],'message.reply' if b.get('parent_id') else 'message.created',{'message_id':mid})
   names=set(re.findall(r'(?<![A-Za-z0-9_@])@([A-Za-z0-9_]+)',b['body']))
   ids=set()
   for nm in names:
    if nm=='channel':
     ids.update(x['user_id'] for x in q('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=?',(c['id'],u['id'])))
    else:
     z=q('SELECT id FROM users WHERE username=?',(nm,),True)
     if z and z['id']!=u['id'] and q('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(c['workspace_id'],z['id']),True) and q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(c['id'],z['id']),True):ids.add(z['id'])
   for uid in ids:
    ex('INSERT OR IGNORE INTO message_mentions VALUES(?,?)',(mid,uid)); ex('INSERT INTO reads(user_id,channel_id,mention_count) VALUES(?,?,1) ON CONFLICT(user_id,channel_id) DO UPDATE SET mention_count=mention_count+1',(uid,c['id']))
   return self.send(201,{'message':msgobj(q('SELECT * FROM messages WHERE id=?',(mid,),True))})
  if p[:3]==['','api','messages'] and len(p)==5 and p[4]=='pin':
   m=q('SELECT * FROM messages WHERE id=?',(p[3],),True)
   if not m:return self.send(404)
   ex('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(m['id'],u['id'],now())); return self.send(200,{'pin':{'message_id':m['id'],'pinned_by':u['id'],'pinned_at':q('SELECT pinned_at FROM pins WHERE message_id=?',(m['id'],),True)['pinned_at']}})
  if p[:3]==['','api','messages'] and len(p)==5 and p[4]=='reactions':
   if not q('SELECT id FROM messages WHERE id=?',(p[3],),True):return self.send(404)
   ex('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(p[3],u['id'],b.get('emoji',''))); event(q('SELECT channel_id FROM messages WHERE id=?',(p[3],),True)['channel_id'],'reaction.added',{'message_id':int(p[3]),'emoji':b.get('emoji','')}); return self.send(200,{'reactions':msgobj(q('SELECT * FROM messages WHERE id=?',(p[3],),True))['reactions']})
  if p[:3]==['','api','dms']:
   other=b.get('user_id'); a,bid=sorted((u['id'],int(other))); r=q('SELECT c.id FROM channels c WHERE c.is_dm=1 AND c.name=?',(f'dm-{a}-{bid}',),True)
   if r:return self.send(200,{'channel_id':r['id']})
   cid=ex('INSERT INTO channels(workspace_id,name,is_dm) VALUES(0,?,1)',(f'dm-{a}-{bid}',)); ex('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,a)); ex('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,bid)); return self.send(200,{'channel_id':cid})
  if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True) if w else None
   if not w:return self.send(404)
   if not m or m['role'] not in ('owner','admin'):return self.send(403)
   code=secrets.token_hex(12); exp=int(__import__('time').time())+int(b.get('expires_in_seconds',0)) if b.get('expires_in_seconds') else 0
   target=b.get('invited_user_id'); maxuses=1 if target else int(b.get('max_uses',1))
   ex('INSERT INTO invitations(code,workspace_id,invited_user_id,expires,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],target,exp,maxuses))
   return self.send(201,{'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target,'max_uses':maxuses}})
  if len(p)==5 and p[1:3]==['api','invitations'] and p[4]=='accept':
   inv=q('SELECT * FROM invitations WHERE code=?',(p[3],),True)
   if not inv:return self.send(404)
   import time
   if inv['revoked'] or inv['uses']>=inv['max_uses'] or (inv['expires'] and inv['expires']<int(time.time())):return self.send(404)
   if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return self.send(403)
   ex('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'))
   ex('UPDATE invitations SET uses=uses+1 WHERE code=?',(p[3],))
   return self.send(200,{'workspace':wsobj(q('SELECT * FROM workspaces WHERE id=?',(inv['workspace_id'],),True))})
  if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='transfer_ownership':
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   own=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
   if not own or own['role']!='owner':return self.send(403)
   target=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],b.get('new_owner_id')),True)
   if not target or target['role']!='admin':return self.send(400)
   ex('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
   ex('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],b.get('new_owner_id')))
   ex('UPDATE workspaces SET owner_id=? WHERE id=?',(b.get('new_owner_id'),w['id']))
   return self.send(200,{'workspace':wsobj(q('SELECT * FROM workspaces WHERE id=?',(w['id'],),True))})
  return self.send(404)
 def do_PATCH(self):
  self.bad_json=False
  p=self.path_parts(); b=self.body(); u=self.reqauth()
  if not u:return
  if p[:3]==['','api','channels'] and len(p)==4:
   c=q('SELECT * FROM channels WHERE id=?',(p[3],),True)
   if not c:return self.send(404)
   if self.bad_json or 'topic' not in b or len(str(b.get('topic','')))>250:return self.send(400)
   w=q('SELECT * FROM workspaces WHERE id=?',(c['workspace_id'],),True); m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(c['workspace_id'],u['id']),True)
   if not m or (not q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(c['id'],u['id']),True) and m['role']!='owner'):return self.send(403)
   ex('UPDATE channels SET topic=? WHERE id=?',(b['topic'],c['id'])); event(c['id'],'channel.updated',{'topic':b['topic']})
   return self.send(200,{'channel':chobj(q('SELECT * FROM channels WHERE id=?',(c['id'],),True))})
  if len(p)==4 and p[1:3]==['api','workspaces']:
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
   if not m or m['role'] not in ('owner','admin'):return self.send(403)
   if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return self.send(400)
   vals=[]; args=[]
   if 'name' in b: vals.append('name=?'); args.append(b['name'])
   if 'join_mode' in b: vals.append('join_mode=?'); args.append(b['join_mode'])
   if vals: ex('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',tuple(args+[w['id']]))
   return self.send(200,{'workspace':wsobj(q('SELECT * FROM workspaces WHERE id=?',(w['id'],),True))})
  if p[:3]==['','api','users'] and len(p)==4:
   vals={k:b[k] for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in b}
   if vals: ex('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',tuple(vals.values())+(u['id'],))
   return self.send(200,{'user':userobj(q('SELECT * FROM users WHERE id=?',(u['id'],),True))})
  if len(p)==6 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
   if not m or m['role'] not in ('owner','admin'):return self.send(403)
   ex('UPDATE invitations SET revoked=1 WHERE workspace_id=? AND code=?',(w['id'],p[5]) if len(p)>5 else (w['id'],''))
   return self.send(200,{'deleted':True})
  if p[:3]==['','api','messages'] and len(p)==4:
   m=q('SELECT * FROM messages WHERE id=?',(p[3],),True)
   if not m:return self.send(404)
   if m['deleted']:return self.send(409)
   if m['author_id']!=u['id']:return self.send(403)
   ex('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b.get('body',''),now(),p[3])); event(m['channel_id'],'message.updated',{'message_id':int(p[3])}); return self.send(200,{'message':msgobj(q('SELECT * FROM messages WHERE id=?',(p[3],),True))})
  if len(p)>=5 and p[1:3]==['api','workspaces'] and p[4]=='members':
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   own=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
   role=b.get('role')
   if role not in ('owner','admin','member','guest'):return self.send(400)
   if role=='owner':return self.send(400)
   target=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],p[5]),True)
   if not target:return self.send(404)
   if not own:return self.send(403)
   if int(p[5])==u['id'] or target['role']=='owner':return self.send(403)
   if own['role']=='member' or own['role']=='guest':return self.send(403)
   if own['role']=='admin' and target['role']=='admin':return self.send(403)
   ex('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],p[5]))
   return self.send(200,{'member':{'user_id':int(p[5]),'role':role}})
  return self.send(404)
 def do_DELETE(self):
  self.bad_json=False
  p=self.path_parts(); u=self.reqauth()
  if not u:return
  if p[:3]==['','api','messages'] and len(p)==5 and p[4]=='pin':
   if not q('SELECT id FROM messages WHERE id=?',(p[3],),True):return self.send(404)
   ex('DELETE FROM pins WHERE message_id=?',(p[3],)); return self.send(200,{'unpinned':True})
  if len(p)==6 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
   w=q('SELECT * FROM workspaces WHERE slug=?',(p[3],),True)
   if not w:return self.send(404)
   m=q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id']),True)
   if not m or m['role'] not in ('owner','admin'):return self.send(403)
   ex('UPDATE invitations SET revoked=1 WHERE workspace_id=? AND code=?',(w['id'],p[5]) if len(p)>5 else (w['id'],''))
   return self.send(200,{'deleted':True})
  if p[:3]==['','api','messages'] and len(p)==4:
   m=q('SELECT * FROM messages WHERE id=?',(p[3],),True)
   if not m:return self.send(200,{'deleted':True})
   if not m['deleted'] and m['author_id']!=u['id']:
    c=q('SELECT workspace_id FROM channels WHERE id=?',(m['channel_id'],),True)
    owner=q('SELECT 1 FROM workspaces w WHERE w.id=? AND w.owner_id=?',(c['workspace_id'],u['id']),True) if c else None
    if not owner:return self.send(403)
   ex('UPDATE messages SET deleted=1 WHERE id=?',(p[3],)); event(m['channel_id'],'message.deleted',{'message_id':int(p[3])}); return self.send(200,{'deleted':True})
  if p[:3]==['','api','messages'] and len(p)==6 and p[4]=='reactions':
   ex('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(p[3],u['id'],urllib.parse.unquote(p[5]))); event(q('SELECT channel_id FROM messages WHERE id=?',(p[3],),True)['channel_id'],'reaction.removed',{'message_id':int(p[3]),'emoji':urllib.parse.unquote(p[5])}); return self.send(200,{'reactions':[]})
  return self.send(404)
 def log_message(self,*a): pass
def run():
 port=int(os.environ.get('PORT','8000')); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__': run()
