import os,sys,json,re,sqlite3,secrets,hashlib,base64,struct,socket,threading,time,urllib.parse
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True)
NODE=int(sys.argv[1]) if len(sys.argv)>1 else 0
WS=[]; WSL=threading.Lock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=5); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); return c
def init():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS wm(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,event_seq INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cm(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,UNIQUE(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));'''); c.commit(); c.close()
init()
def userobj(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def channel(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def auth(h):
 a=h.headers.get('Authorization','')
 if not a.startswith('Bearer '): return None
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(a[7:],)).fetchone(); c.close(); return r
def emit(c,ch,kind,payload):
 c.execute('UPDATE channels SET event_seq=event_seq+1 WHERE id=?',(ch,)); eid=c.execute('SELECT event_seq FROM channels WHERE id=?',(ch,)).fetchone()[0]
 c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(ch,eid,kind,json.dumps(payload)))
 c.commit(); frame={'type':kind,'event_id':eid,'channel_id':ch}; frame.update(payload)
 with WSL:
  for s,u,subs in list(WS):
   if ch in subs:
    try: ws_send(s,frame)
    except: pass
 return eid
def msgobj(c,r):
 if not r:return None
 author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 reps=[]
 for x in c.execute('SELECT emoji,COUNT(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
  reps.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[z[0] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(r['id'],x['emoji']))]})
 cnt=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
 members=c.execute('SELECT u.* FROM users u JOIN cm ON cm.user_id=u.id WHERE cm.channel_id=?',(r['channel_id'],)).fetchall()
 names={x['username']:x['id'] for x in members}; mentions=[]
 if re.search(r'(?<![A-Za-z0-9_])@channel(?![A-Za-z0-9_])',r['body']):
  mentions=[x['id'] for x in members if x['id']!=r['author_id']]
 for n in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',r['body']):
  if n in names and names[n]!=r['author_id'] and names[n] not in mentions: mentions.append(names[n])
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(author),'body':r['body'],'parent_id':r['parent_id'],'reply_count':cnt,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reps,'mentions':mentions}
def ws_send(s,o):
 b=json.dumps(o,separators=(',',':')).encode(); s.sendall(b'\x81'+(bytes([len(b)]) if len(b)<126 else b'\x7e'+struct.pack('>H',len(b)))+b)
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def sendj(self,status,o):
  b=json.dumps(o).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')) or 0))
  except:return None
 def do_GET(self):
  if self.path=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
  if self.path.startswith('/api/ws'): return self.websocket()
  u=auth(self)
  if self.path=='/api/auth/me': return self.sendj(200,{'user':userobj(u)}) if u else self.sendj(401,{})
  if not u:return self.sendj(401,{})
  c=db(); p=self.path.split('?')[0]; q=dict(x.split('=',1) for x in self.path.split('?',1)[1].split('&') if '=' in x) if '?' in self.path else {}
  if p=='/api/workspaces':
   rows=c.execute('SELECT w.* FROM workspaces w JOIN wm ON wm.workspace_id=w.id WHERE wm.user_id=?',(u['id'],)).fetchall(); return self.sendj(200,{'workspaces':[dict(x) for x in rows]})
  m=re.match(r'/api/workspaces/([^/]+)$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m[1],)).fetchone()
   if not w:return self.sendj(404,{})
   chans=[] 
   for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],q.get('include_archived') in ('1','true'))):
    if not x['is_private'] or c.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone():chans.append(channel(x))
   states=[]
   for x in chans:
    rr=c.execute('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone()
    states.append({'channel_id':x['id'],'last_read_event_id':rr['last_read_event_id'] if rr else 0,'unread_count':0,'mention_count':rr['mention_count'] if rr else 0})
   return self.sendj(200,{'workspace':dict(w),'channels':chans,'read_state':states})
  m=re.match(r'/api/channels/(\d+)/members$',p)
  if m:
   rows=c.execute('SELECT u.* FROM users u JOIN cm ON cm.user_id=u.id WHERE cm.channel_id=?',(m[1],)).fetchall(); return self.sendj(200,{'members':[userobj(x) for x in rows]})
  m=re.match(r'/api/channels/(\d+)/messages$',p)
  if m:
   ch=int(m[1]); lim=int(q.get('limit','50')); cur=q.get('cursor')
   if lim<1 or lim>200:return self.sendj(400,{})
   if cur and not cur.isdigit():return self.sendj(400,{})
   if cur:
    rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND id<? AND deleted=0 ORDER BY id DESC',(ch,int(cur))).fetchall()
   else:
    rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC',(ch,)).fetchall()
   page=rows[:lim]; out=[msgobj(c,x) for x in page]
   nxt=str(page[-1]['id']) if len(rows)>lim else None
   return self.sendj(200,{'messages':out,'next_cursor':nxt})
  m=re.match(r'/api/messages/(\d+)/replies$',p)
  if m:
   lim=int(q.get('limit','50'))
   if lim<1 or lim>200:return self.sendj(400,{})
   cur=q.get('cursor')
   if cur and not cur.isdigit():return self.sendj(400,{})
   if cur: rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND id>? AND deleted=0 ORDER BY id',(m[1],int(cur))).fetchall()
   else: rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(m[1],)).fetchall()
   page=rows[:lim]
   return self.sendj(200,{'replies':[msgobj(c,x) for x in page],'next_cursor':str(page[-1]['id']) if len(rows)>lim else None})
  m=re.match(r'/api/search$',p)
  if m:
   term=q.get('q',''); lim=int(q.get('limit','50')); cur=q.get('cursor')
   if lim<1 or lim>200 or (cur and not cur.isdigit()): return self.sendj(400,{})
   extra=''; args=[u['id'],'%'+term+'%']
   if q.get('workspace_id'): extra+=' AND c.workspace_id=?'; args.append(int(q['workspace_id']))
   if cur: extra+=' AND m.id<?'; args.append(int(cur))
   rows=c.execute('SELECT m.* FROM messages m JOIN cm ON cm.channel_id=m.channel_id JOIN channels c ON c.id=m.channel_id WHERE cm.user_id=? AND m.deleted=0 AND m.body LIKE ?'+extra+' ORDER BY m.id DESC',args).fetchall()
   page=rows[:lim]; return self.sendj(200,{'results':[msgobj(c,x) for x in page],'next_cursor':str(page[-1]['id']) if len(rows)>lim else None})
  m=re.match(r'/api/channels/(\d+)/pins$',p)
  if m:
   ch=int(m[1]); x=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
   if not x:return self.sendj(404,{})
   if x['is_private'] and not c.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(ch,u['id'])).fetchone():return self.sendj(403,{})
   lim=int(q.get('limit','50')); cur=q.get('cursor')
   if lim<1 or lim>200 or (cur and not cur.isdigit()):return self.sendj(400,{})
   extra=''; args=[ch]
   if cur:extra=' AND m.id<?';args.append(int(cur))
   rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'+extra+' ORDER BY m.id DESC',args).fetchall()
   page=rows[:lim]; return self.sendj(200,{'pins':[{'message':msgobj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in page],'next_cursor':str(page[-1]['id']) if len(rows)>lim else None})
  m=re.match(r'/api/channels/(\d+)/read$',p)
  if m:
   r=c.execute('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],m[1])).fetchone(); return self.sendj(200,{'read_state':{'channel_id':int(m[1]),'last_read_event_id':r['last_read_event_id'] if r else 0,'unread_count':0,'mention_count':r['mention_count'] if r else 0}})
  c.close(); return self.sendj(404,{})
 def do_POST(self):
  if self.path=='/api/auth/register':
   d=self.body() or {}; un=d.get('username',''); pw=d.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.sendj(400,{})
   c=db()
   try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,pw,d.get('display_name') or un)); c.commit()
   except:return self.sendj(409,{})
   uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); return self.sendj(201,{'user':userobj(r),'token':t})
  if self.path=='/api/auth/login':
   d=self.body() or {}; c=db();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),d.get('password'))).fetchone()
   if not r:return self.sendj(401,{})
   t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();return self.sendj(200,{'user':userobj(r),'token':t})
  if self.path=='/api/workspaces':
   u=auth(self)
   if not u:return self.sendj(401,{})
   d=self.body() or {}; slug=d.get('slug',''); name=d.get('name','')
   if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not name:return self.sendj(400,{})
   c=db()
   if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(slug,)).fetchone():return self.sendj(409,{})
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id']))
   wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('INSERT INTO wm VALUES(?,?,?)',(wid,u['id'],'owner'))
   c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'))
   gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('INSERT INTO cm VALUES(?,?)',(gid,u['id'])); c.commit()
   w=dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone())
   g=channel(c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone())
   return self.sendj(201,{'workspace':w,'general_channel':g})
  u=auth(self)
  if not u:return self.sendj(401,{})
  c=db(); p=self.path.split('?')[0]; d=self.body()
  m=re.match(r'/api/workspaces/([^/]+)/channels$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m[1],)).fetchone(); name=(d or {}).get('name',''); topic=(d or {}).get('topic','')
   if not w:return self.sendj(404,{})
   if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or len(topic)>250:return self.sendj(400,{})
   if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():return self.sendj(409,{})
   if (d or {}).get('is_private') and c.execute('SELECT role FROM wm WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()['role'] not in ('owner','admin'):return self.sendj(403,{})
   c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool((d or {}).get('is_private'))),topic)); ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cm VALUES(?,?)',(ch,u['id']));c.commit();return self.sendj(201,{'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
  m=re.match(r'/api/channels/(\d+)/read$',p)
  if m:
   ch=int(m[1]); x=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
   if not x:return self.sendj(404,{})
   val=(d or {}).get('last_read_event_id')
   if not isinstance(val,int):return self.sendj(400,{})
   r=c.execute('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],ch)).fetchone()
   if not r:c.execute('INSERT INTO reads(user_id,channel_id,last_read_event_id,mention_count) VALUES(?,?,?,0)',(u['id'],ch,val))
   else:c.execute('UPDATE reads SET last_read_event_id=MAX(last_read_event_id,?) WHERE user_id=? AND channel_id=?',(val,u['id'],ch))
   c.commit(); r=c.execute('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],ch)).fetchone()
   return self.sendj(200,{'read_state':{'channel_id':ch,'last_read_event_id':r['last_read_event_id'],'unread_count':0,'mention_count':r['mention_count']}})
  m=re.match(r'/api/channels/(\d+)/(join|leave|archive|unarchive)$',p)
  if m:
   ch=int(m[1]); x=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
   if not x:return self.sendj(404,{})
   role=c.execute('SELECT role FROM wm WHERE workspace_id=? AND user_id=?',(x['workspace_id'],u['id'])).fetchone()
   if m[2] in ('archive','unarchive') and (not role or role['role']!='owner'):return self.sendj(403,{})
   if m[2]=='join':
    c.execute('INSERT OR IGNORE INTO cm VALUES(?,?)',(ch,u['id'],)); c.execute('INSERT OR IGNORE INTO wm VALUES(?,?,?)',(x['workspace_id'],u['id'],'member'))
   elif m[2]=='leave':c.execute('DELETE FROM cm WHERE channel_id=? AND user_id=?',(ch,u['id']))
   else:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(m[2]=='archive',ch))
   c.commit(); return self.sendj(200,{'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
  m=re.match(r'/api/channels/(\d+)/messages$',p)
  if m:
   ch=int(m[1]); x=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
   if not x:return self.sendj(404,{})
   if x['is_archived']:return self.sendj(423,{})
   if not d or not isinstance(d.get('body'),str) or not d['body']:return self.sendj(400,{})
   c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u['id'],d['body'],d.get('parent_id'),now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); mo=msgobj(c,r)
   for mentioned in mo['mentions']:
    c.execute('INSERT OR IGNORE INTO reads(user_id,channel_id,last_read_event_id,mention_count) VALUES(?,?,0,0)',(mentioned,ch))
    c.execute('UPDATE reads SET mention_count=mention_count+1 WHERE user_id=? AND channel_id=?',(mentioned,ch))
   c.commit()
   emit(c,ch,'message.reply' if d.get('parent_id') else 'message.created',{'message':mo});return self.sendj(201,{'message':mo})
  m=re.match(r'/api/messages/(\d+)/reactions$',p)
  if m:
   x=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(m[1],)).fetchone()
   if not x:return self.sendj(404,{})
   emoji=(d or {}).get('emoji','')
   if not emoji:return self.sendj(400,{})
   c.execute('INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(m[1],u['id'],emoji));c.commit()
   return self.sendj(200,{'reactions':msgobj(c,x)['reactions']})
  m=re.match(r'/api/messages/(\d+)/pin$',p)
  if m:
   c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(m[1],u['id'],now()));c.commit();r=c.execute('SELECT * FROM pins WHERE message_id=?',(m[1],)).fetchone();return self.sendj(200,{'pin':dict(r)})
  c.close();return self.sendj(404,{})
 def do_PATCH(self):
  u=auth(self)
  if not u:return self.sendj(401,{})
  c=db();p=self.path;d=self.body() or {}
  m=re.match(r'/api/channels/(\d+)$',p)
  if m:
   x=c.execute('SELECT * FROM channels WHERE id=?',(m[1],)).fetchone(); role=c.execute('SELECT role FROM wm WHERE workspace_id=? AND user_id=?',(x['workspace_id'],u['id'])).fetchone()
   if not role or (not c.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(m[1],u['id'])).fetchone() and role['role']!='owner'):return self.sendj(403,{})
   if len(d.get('topic',''))>250:return self.sendj(400,{})
   c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),m[1]));c.commit();x=c.execute('SELECT * FROM channels WHERE id=?',(m[1],)).fetchone();emit(c,int(m[1]),'channel.updated',{'channel':channel(x)});return self.sendj(200,{'channel':channel(x)})
  m=re.match(r'/api/messages/(\d+)$',p)
  if m:
   x=c.execute('SELECT * FROM messages WHERE id=?',(m[1],)).fetchone()
   if not x:return self.sendj(404,{})
   if x['deleted']:return self.sendj(409,{})
   if x['author_id']!=u['id']:return self.sendj(403,{})
   c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d.get('body',''),now(),m[1]));c.commit();mo=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(m[1],)).fetchone());emit(c,x['channel_id'],'message.updated',{'message':mo});return self.sendj(200,{'message':mo})
  return self.sendj(404,{})
 def do_DELETE(self):
  u=auth(self)
  if not u:return self.sendj(401,{})
  c=db();
  if self.path.startswith('/api/messages/') and self.path.endswith('/pin'):
   mid=self.path.split('/')[3]; c.execute('DELETE FROM pins WHERE message_id=?',(mid,)); c.commit(); return self.sendj(200,{'unpinned':True})
  m=re.match(r'/api/messages/(\d+)(?:/reactions/(.*))?$',self.path)
  if not m:return self.sendj(404,{})
  x=c.execute('SELECT * FROM messages WHERE id=?',(m[1],)).fetchone()
  if m[2] is not None:
   emoji=urllib.parse.unquote(m[2]); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m[1],u['id'],emoji));c.commit();return self.sendj(200,{'reactions':msgobj(c,x)['reactions']})
  if x and not x['deleted'] and x['author_id']!=u['id']:
   role=c.execute('SELECT role FROM wm WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?) AND user_id=?',(x['channel_id'],u['id'])).fetchone()
   if not role or role['role']!='owner':return self.sendj(403,{})
  if x:c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m[1],));c.commit()
  return self.sendj(200,{'deleted':True})
 def websocket(self):
  u=auth(self)
  if not u:
   qs=self.path.split('?',1)[-1]; tok=dict(x.split('=',1) for x in qs.split('&') if '=' in x).get('token','');c=db();u=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok,)).fetchone()
  if not u:return self.send_error(401); 
  key=self.headers.get('Sec-WebSocket-Key'); self.send_response(101);self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB-11B6DCB11F1').encode()).digest()).decode());self.end_headers()
  s=self.connection; subs=set()
  with WSL:WS.append((s,u['id'],subs))
  try:
   while True:
    h=s.recv(2)
    if not h:break
    n=h[1]&127; mask=s.recv(4); data=s.recv(n); data=bytes(data[i]^mask[i%4] for i in range(n)); x=json.loads(data)
    if x.get('type')=='subscribe':
     ch=int(x['channel_id']);subs.add(ch); c=db();head=c.execute('SELECT event_seq FROM channels WHERE id=?',(ch,)).fetchone();ws_send(s,{'type':'subscribed','channel_id':ch,'head_event_id':head[0] if head else 0})
    elif x.get('type')=='resume':
     ch=int(x['channel_id']);since=int(x.get('since_event_id',0));c=db()
     for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)):
      z=json.loads(e['payload']);z.update(type=e['kind'],event_id=e['event_id'],channel_id=ch);ws_send(s,z)
     head=c.execute('SELECT event_seq FROM channels WHERE id=?',(ch,)).fetchone()[0];ws_send(s,{'type':'resumed','channel_id':ch,'head_event_id':head})
  except:pass
  with WSL:
   try:WS.remove((s,u['id'],subs))
   except:pass

if __name__ == '__main__':
    port=8000+NODE
    ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
