import os,json,re,sqlite3,hashlib,secrets,base64,struct,threading,time,datetime,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; PORT=int(os.environ.get('PORT','8000')); NODE=(PORT-8000)//1
lock=threading.RLock(); clients={}

def now():
 return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=0.2,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=200'); return c
def init():
 with lock:
  c=db()
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
  CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
  CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
  CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
  CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
  CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
  CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
  CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
  CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,message_id INTEGER,data TEXT,PRIMARY KEY(channel_id,event_id));
  CREATE TABLE IF NOT EXISTS readstate(user_id INTEGER,channel_id INTEGER,last_read INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
  CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
  CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,uses INTEGER,max_uses INTEGER,expires REAL);
  ''');
  try: c.execute('alter table messages add column data TEXT')
  except sqlite3.OperationalError: pass
  try: c.execute('alter table readstate add column mention_count INTEGER DEFAULT 0')
  except sqlite3.OperationalError: pass
  c.commit(); c.close()
init()
def userobj(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def chobj(r):
 return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def msgobj(c,r):
 u=c.execute('select * from users where id=?',(r['author_id'],)).fetchone()
 rs=c.execute('select emoji,user_id from reactions where message_id=?',(r['id'],)).fetchall()
 grouped={}
 for x in rs: grouped.setdefault(x['emoji'],[]).append(x['user_id'])
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('select count(*) from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in grouped.items()],'mentions':json.loads(r['data'] or '[]')}
def sendws(ch,event):
 dead=[]
 for ws in clients.get(ch,[]):
  try: ws.send(event)
  except: dead.append(ws)
 for x in dead:
  try: clients[ch].remove(x)
  except: pass
def event(c,ch,kind,mid=None,data=None):
 n=c.execute('select coalesce(max(event_id),0)+1 from events where channel_id=?',(ch,)).fetchone()[0]
 c.execute('insert into events values(?,?,?,?,?)',(ch,n,kind,mid,json.dumps(data or {}))); return n
def auth(h):
 x=h.get('Authorization','')
 if not x.startswith('Bearer '): return None
 c=db(); r=c.execute('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(x[7:],)).fetchone(); c.close(); return r
class WS:
 def __init__(self,s,u): self.s=s; self.u=u
 def send(self,o):
  b=json.dumps(o,separators=(',',':')).encode(); n=len(b)
  if n<126: h=bytes([0x81,n])
  elif n<65536: h=bytes([0x81,126])+struct.pack('>H',n)
  else: h=bytes([0x81,127])+struct.pack('>Q',n)
  self.s.sendall(h+b)
 def recv(self):
  h=self.s.recv(2)
  if not h:return None
  n=h[1]&127
  if n==126:n=struct.unpack('>H',self.s.recv(2))[0]
  elif n==127:n=struct.unpack('>Q',self.s.recv(8))[0]
  mask=self.s.recv(4); b=bytearray(self.s.recv(n))
  for i in range(n): b[i]^=mask[i%4]
  return json.loads(b)
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def body(self):
  n=int(self.headers.get('Content-Length','0') or '0')
  raw=self.rfile.read(n)
  try:return json.loads(raw.decode('utf-8') or '{}')
  except Exception:
   return {}
 def out(self,status,obj=None,raw=None,headers={}):
  b=raw if raw is not None else json.dumps(obj,separators=(',',':')).encode()
  self.send_response(status); self.send_header('Content-Type',headers.get('Content-Type','application/json')); self.send_header('Content-Length',str(len(b)))
  for k,v in headers.items():
   if k!='Content-Type':self.send_header(k,v)
  self.end_headers(); self.wfile.write(b)
 def do_GET(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query)
  if path=='/api/health': return self.out(200,{'status':'ok','node_id':NODE})
  if path=='/': return self.out(200,{},raw=HTML.encode(),headers={'Content-Type':'text/html'})
  u=auth(self.headers)
  if path=='/api/ws': return self.websocket(q.get('token',[''])[0])
  if not u:return self.out(401,{'error':'unauthorized'})
  c=db(); m=re.fullmatch(r'/api/users/(\d+)',path)
  if m:
   r=c.execute('select * from users where id=?',(m.group(1),)).fetchone(); c.close(); return self.out(200,{'user':userobj(r)}) if r else self.out(404,{})
  if path=='/api/auth/me': self.out(200,{'user':userobj(u)}); c.close(); return
  if path=='/api/users/me':
   return self.out(200,{'user':userobj(u)})
  if path.startswith('/api/workspaces/') and path.endswith('/invitations'):
   slug=path.split('/')[3]; w=c.execute('select * from workspaces where slug=?',(slug,)).fetchone()
   if not w:return self.out(404,{})
   rs=c.execute('select * from invites where workspace_id=?',(w['id'],)).fetchall(); c.close()
   return self.out(200,{'invitations':[dict(x) for x in rs]})
  if path=='/api/workspaces':
   rows=c.execute('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],)).fetchall(); c.close(); return self.out(200,{'workspaces':[dict(r) for r in rows]})
  m=re.fullmatch(r'/api/workspaces/([^/]+)(?:/members|/groups|/invitations)?',path)
  if m:
   slug=m.group(1); w=c.execute('select * from workspaces where slug=?',(slug,)).fetchone()
   if not w:return self.out(404,{})
   if path.endswith('/members'):
    rs=c.execute('select m.*,u.username,u.display_name from members m join users u on u.id=m.user_id where workspace_id=?',(w['id'],)).fetchall(); c.close(); return self.out(200,{'members':[dict(x) for x in rs]})
   if path.endswith('/groups'):
    rs=c.execute('select * from groups_ where workspace_id=?',(w['id'],)).fetchall(); c.close(); return self.out(200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in rs]})
   cs=c.execute('select * from channels where workspace_id=? and (is_archived=0 or ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))).fetchall()
   cs=[x for x in cs if not x['is_private'] or c.execute('select 1 from cmembers where channel_id=? and user_id=?',(x['id'],u['id'])).fetchone()]
   rs=c.execute('select ch.id,coalesce(r.last_read,0) last_read,coalesce(r.mention_count,0) mention_count from channels ch left join readstate r on r.channel_id=ch.id and r.user_id=? where ch.workspace_id=?',(u['id'],w['id'])).fetchall(); c.close()
   return self.out(200,{'workspace':dict(w),'channels':[chobj(x) for x in cs],'read_state':[{'channel_id':x['id'],'last_read_event_id':x['last_read'],'unread_count':0,'mention_count':x['mention_count']} for x in rs]})
  m=re.fullmatch(r'/api/messages/(\d+)/replies',path)
  if m:
   pid=int(m.group(1)); lim=int(q.get('limit',['50'])[0]); cur=q.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.out(400,{})
   if cur:
    try: anchor=int(base64.urlsafe_b64decode(cur+'===').decode())
    except:return self.out(400,{})
    rs=c.execute('select * from messages where parent_id=? and deleted=0 and id>? order by id',(pid,anchor)).fetchall()
   else: rs=c.execute('select * from messages where parent_id=? and deleted=0 order by id',(pid,)).fetchall()
   arr=rs[:lim]; nxt=base64.urlsafe_b64encode(str(arr[-1]['id']).encode()).decode() if len(arr)==lim and len(rs)>lim else None
   c.close(); return self.out(200,{'replies':[msgobj(db(),x) for x in arr],'next_cursor':nxt})
  m=re.fullmatch(r'/api/channels/(\d+)(?:/(messages|members|pins|read))?',path)
  if m:
   cid=int(m.group(1)); ch=c.execute('select * from channels where id=?',(cid,)).fetchone()
   if not ch:return self.out(404,{})
   if ch['is_private'] and not c.execute('select 1 from cmembers where channel_id=? and user_id=?',(cid,u['id'])).fetchone() and ch['workspace_id']!=c.execute('select owner_id from workspaces where id=?',(ch['workspace_id'],)).fetchone()[0]: return self.out(403,{})
   sub=m.group(2)
   if sub=='members':
    rs=c.execute('select u.* from cmembers x join users u on u.id=x.user_id where x.channel_id=?',(cid,)).fetchall(); c.close(); return self.out(200,{'members':[userobj(x) for x in rs]})
   if sub=='read':
    x=c.execute('select last_read,mention_count from readstate where user_id=? and channel_id=?',(u['id'],cid)).fetchone(); head=c.execute('select coalesce(max(event_id),0) from events where channel_id=?',(cid,)).fetchone()[0]; c.close(); return self.out(200,{'read_state':{'channel_id':cid,'last_read_event_id':x[0] if x else 0,'unread_count':max(0,head-(x[0] if x else 0)),'mention_count':x[1] if x and len(x)>1 else 0}})
   if sub=='pins':
    rs=c.execute('select p.*,m.* from pins p join messages m on m.id=p.message_id where m.channel_id=? order by p.pinned_at desc',(cid,)).fetchall(); c.close(); return self.out(200,{'pins':[{'message':msgobj(db(),x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':None})
   lim=int(q.get('limit',['50'])[0]); cur=q.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.out(400,{})
   if cur:
    try: anchor=int(base64.urlsafe_b64decode(cur+'===').decode())
    except:return self.out(400,{})
    allr=c.execute('select * from messages where channel_id=? and deleted=0 and id<? order by id desc',(cid,anchor)).fetchall()
   else: allr=c.execute('select * from messages where channel_id=? and deleted=0 order by id desc',(cid,)).fetchall()
   arr=allr[:lim]
   nxt=base64.urlsafe_b64encode(str(arr[-1]['id']).encode()).decode() if len(arr)==lim and len(allr)>lim else None
   c.close()
   return self.out(200,{'messages':[msgobj(db(),x) for x in arr],'next_cursor':nxt})
  if path=='/api/search':
   term=q.get('q',[''])[0].lower()
   rs=c.execute('''select m.* from messages m join channels ch on ch.id=m.channel_id
    join members wm on wm.workspace_id=ch.workspace_id and wm.user_id=?
    where m.deleted=0 and lower(m.body) like ?
      and (? is null or ch.workspace_id=?)
      and (ch.is_private=0 or exists(select 1 from cmembers pm where pm.channel_id=ch.id and pm.user_id=?))
    order by m.id desc''',
    (u['id'],'%'+term+'%',int(q['workspace_id'][0]) if q.get('workspace_id') else None,int(q['workspace_id'][0]) if q.get('workspace_id') else None,u['id'])).fetchall()
   c.close(); return self.out(200,{'results':[msgobj(db(),x) for x in rs],'next_cursor':None})
  c.close(); return self.out(404,{})
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path; u=auth(self.headers)
  if path=='/api/auth/register':
   d=self.body()
   if not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or len(d.get('password',''))<8:return self.out(400,{})
   c=db()
   try:c.execute('insert into users(username,password,display_name) values(?,?,?)',(d['username'],d['password'],d.get('display_name') or d['username'])); c.commit()
   except sqlite3.IntegrityError:c.close(); return self.out(409,{})
   uid=c.execute('select last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24); c.execute('insert into tokens values(?,?)',(t,uid)); c.commit(); r=c.execute('select * from users where id=?',(uid,)).fetchone(); c.close(); return self.out(201,{'user':userobj(r),'token':t})
  if path=='/api/auth/login':
   d=self.body(); c=db(); r=c.execute('select * from users where username=? and password=?',(d.get('username'),d.get('password'))).fetchone()
   if not r:c.close(); return self.out(401,{})
   t=secrets.token_urlsafe(24); c.execute('insert into tokens values(?,?)',(t,r['id'])); c.commit(); c.close(); return self.out(200,{'user':userobj(r),'token':t})
  if not u:return self.out(401,{})
  d={}; c=db()
  if path=='/api/workspaces':
   d=self.body()
   if not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')):return self.out(400,{})
   try:c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(d['slug'],d.get('name',''),u['id'])); wid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute('insert into members values(?,?,?)',(wid,u['id'],'owner')); c.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general')); cid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute('insert into cmembers values(?,?)',(cid,u['id'])); c.commit()
   except sqlite3.IntegrityError:return self.out(409,{})
   return self.out(201,{'workspace':dict(c.execute('select * from workspaces where id=?',(wid,)).fetchone()),'general_channel':chobj(c.execute('select * from channels where id=?',(cid,)).fetchone())})
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',path):
   d=self.body()
   slug=m.group(1); w=c.execute('select * from workspaces where slug=?',(slug,)).fetchone(); role=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone() if w else None
   if not w:return self.out(404,{})
   if not role or role[0] not in ('owner','admin'):return self.out(403,{})
   target=None
   if d.get('invited_user_id'): target=int(d['invited_user_id'])
   elif d.get('invited_username'):
    z=c.execute('select id from users where username=?',(d['invited_username'],)).fetchone(); target=z[0] if z else None
   code=secrets.token_urlsafe(12); exp=time.time()+int(d.get('expires_in_seconds',604800)); mx=int(d.get('max_uses',1))
   c.execute('insert into invites values(?,?,?,?,?,?)',(code,w['id'],target,0,mx,exp)); c.commit()
   return self.out(201,{'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target,'uses':0,'max_uses':mx,'expires_at':datetime.datetime.fromtimestamp(exp,datetime.timezone.utc).isoformat().replace('+00:00','Z')}})
  if m:=re.fullmatch(r'/api/invitations/([^/]+)/accept',path):
   x=c.execute('select * from invites where code=?',(m.group(1),)).fetchone()
   if not x or x['uses']>=x['max_uses'] or x['expires']<time.time() or (x['invited_user_id'] and x['invited_user_id']!=u['id']):return self.out(404,{})
   c.execute('insert or ignore into members values(?,?,?)',(x['workspace_id'],u['id'],'member')); c.execute('update invites set uses=uses+1 where code=?',(m.group(1),)); c.commit()
   return self.out(200,{'workspace':dict(c.execute('select * from workspaces where id=?',(x['workspace_id'],)).fetchone())})
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)/groups',path):
   d=self.body()
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
   if not w or not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():return self.out(403,{})
   h=d.get('handle',''); c.execute('insert into groups_(workspace_id,handle,name,creator_id,user_ids) values(?,?,?,?,?)',(w['id'],h,d.get('name',''),u['id'],json.dumps(d.get('user_ids',[])))); gid=c.execute('select last_insert_rowid()').fetchone()[0]; c.commit()
   return self.out(201,{'group':{'id':gid,'handle':h,'name':d.get('name',''),'member_user_ids':d.get('user_ids',[])}})
  m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',path)
  if m:
   d=self.body()
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); role=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone() if w else None
   if not w or not role:return self.out(404,{})
   if role[0]=='guest':return self.out(403,{})
   if not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('name','')) or len(d.get('topic',''))>250:return self.out(400,{})
   if d.get('is_private') and role[0] not in ('owner','admin'):return self.out(403,{})
   if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],d['name'])).fetchone():return self.out(409,{})
   try:c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))); cid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute('insert into cmembers values(?,?)',(cid,u['id'])); c.commit()
   except sqlite3.IntegrityError:return self.out(409,{})
   return self.out(201,{'channel':chobj(c.execute('select * from channels where id=?',(cid,)).fetchone())})
  m=re.fullmatch(r'/api/channels/(\d+)/(join|leave|archive|unarchive)',path)
  if m:
   ch=c.execute('select * from channels where id=?',(m.group(1),)).fetchone()
   if not ch:return self.out(404,{})
   if m.group(2) in ('archive','unarchive'):
    owner=c.execute('select owner_id from workspaces where id=?',(ch['workspace_id'],)).fetchone()[0]
    if owner!=u['id']:return self.out(403,{})
    c.execute('update channels set is_archived=? where id=?',(m.group(2)=='archive',ch['id']))
   elif m.group(2)=='join':
    wrow=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
    wr=c.execute('select role from members where workspace_id=? and user_id=?',(ch['workspace_id'],u['id'])).fetchone()
    if ch['is_private'] and not wr:return self.out(403,{})
    if wr and wr[0]=='guest':return self.out(403,{})
    if not wr and wrow['join_mode']!='open':return self.out(403,{})
    c.execute('insert or ignore into members values(?,?,?)',(ch['workspace_id'],u['id'],'member')); c.execute('insert or ignore into cmembers values(?,?)',(ch['id'],u['id']))
   else:c.execute('delete from cmembers where channel_id=? and user_id=?',(ch['id'],u['id']))
   kind={'join':'member.joined','leave':'member.left','archive':'channel.archived','unarchive':'channel.unarchived'}[m.group(2)]
   eid=event(c,ch['id'],kind); c.commit(); sendws(ch['id'],{'type':kind,'event_id':eid,'channel_id':ch['id']})
   return self.out(200,{'channel':chobj(c.execute('select * from channels where id=?',(ch['id'],)).fetchone())})
  m=re.fullmatch(r'/api/channels/(\d+)/messages',path)
  if m:
   cid=int(m.group(1)); ch=c.execute('select * from channels where id=?',(cid,)).fetchone(); d=self.body()
   if not ch:return self.out(404,{})
   if ch['is_archived']:return self.out(423,{})
   if not d.get('body','').strip():return self.out(400,{})
   if not c.execute('select 1 from cmembers where channel_id=? and user_id=?',(cid,u['id'])).fetchone():return self.out(403,{})
   mentions=[]; names=re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',d['body'])
   if '@channel' in d['body']:
    mentions += [x[0] for x in c.execute('select user_id from cmembers where channel_id=? and user_id!=?',(cid,u['id'])).fetchall()]
   for n in names:
    x=c.execute('select id from users where username=?',(n,)).fetchone()
    if x and x[0]!=u['id'] and c.execute('select 1 from cmembers where channel_id=? and user_id=?',(cid,x[0],)).fetchone(): mentions.append(x[0])
   mentions=list(dict.fromkeys(mentions)); ts=now(); c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at,data) values(?,?,?,?,?,?)',(cid,u['id'],d['body'],d.get('parent_id'),ts,json.dumps(mentions))); mid=c.execute('select last_insert_rowid()').fetchone()[0]; kind='message.reply' if d.get('parent_id') else 'message.created'; eid=event(c,cid,kind,mid); c.execute('update messages set event_id=? where id=?',(eid,mid));
   for mention_uid in mentions: c.execute('insert or ignore into readstate(user_id,channel_id,last_read,mention_count) values(?,?,0,0)', (mention_uid,cid)); c.execute('update readstate set mention_count=coalesce(mention_count,0)+1 where user_id=? and channel_id=?',(mention_uid,cid))
   c.commit(); r=c.execute('select * from messages where id=?',(mid,)).fetchone(); o=msgobj(c,r); sendws(cid,dict({'type':kind,'event_id':eid,'channel_id':cid},message=o)); return self.out(201,{'message':o})
  if m:=re.fullmatch(r'/api/messages/(\d+)/pin',path):
   r=c.execute('select * from messages where id=?',(m.group(1),)).fetchone(); c.execute('insert or ignore into pins values(?,?,?)',(r['id'],u['id'],now())); c.commit(); return self.out(200,{'pin':{'message_id':r['id'],'pinned_by':u['id'],'pinned_at':c.execute('select pinned_at from pins where message_id=?',(r['id'],)).fetchone()[0]}})
  if m:=re.fullmatch(r'/api/channels/(\d+)/read',path):
   cid=int(m.group(1)); d=self.body(); old=c.execute('select last_read,mention_count from readstate where user_id=? and channel_id=?',(u['id'],cid)).fetchone(); v=max(old[0] if old else 0,int(d.get('last_read_event_id',0))); c.execute('insert or replace into readstate(user_id,channel_id,last_read,mention_count) values(?,?,?,coalesce((select mention_count from readstate where user_id=? and channel_id=?),0))',(u['id'],cid,v,u['id'],cid)); c.commit(); return self.out(200,{'read_state':{'channel_id':cid,'last_read_event_id':v,'unread_count':0,'mention_count':old[1] if old and len(old)>1 else 0}})
  return self.out(404,{})
 def do_PATCH(self):
  u=auth(self.headers)
  if not u:return self.out(401,{})
  path=urllib.parse.urlparse(self.path).path; d=self.body(); c=db()
  if path=='/api/users/me':
   allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}; vals={k:d[k] for k in d if k in allowed}
   if 'timezone' in vals and (not isinstance(vals['timezone'],str) or len(vals['timezone'])>64):return self.out(400,{})
   if any(not isinstance(v,str) or len(v)>500 for v in vals.values()):return self.out(400,{})
   if vals:
    c.execute('update users set '+','.join(k+'=?' for k in vals)+' where id=?',tuple(vals.values())+(u['id'],)); c.commit()
   return self.out(200,{'user':userobj(c.execute('select * from users where id=?',(u['id'],)).fetchone())})
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)',path):
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); role=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone() if w else None
   if not w:return self.out(404,{})
   if not role or role[0] not in ('owner','admin'):return self.out(403,{})
   fields=[]; vals=[]
   for k in ('name','join_mode'):
    if k in d: fields.append(k+'=?'); vals.append(d[k])
   if fields:c.execute('update workspaces set '+','.join(fields)+' where id=?',tuple(vals)+(w['id'],));c.commit()
   return self.out(200,{'workspace':dict(c.execute('select * from workspaces where id=?',(w['id'],)).fetchone())})
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)/members/(\d+)',path):
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); me=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone(); target=int(m.group(2))
   if not w or not me or me[0] not in ('owner','admin'):return self.out(403,{})
   role=d.get('role')
   if role not in ('owner','admin','member','guest') or role=='owner':return self.out(400,{})
   old=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],target)).fetchone()
   if not old:return self.out(404,{})
   if me[0]=='admin' and old[0] in ('owner','admin'):return self.out(403,{})
   c.execute('update members set role=? where workspace_id=? and user_id=?',(role,w['id'],target));c.commit()
   return self.out(200,{'member':{'user_id':target,'role':role,'workspace_id':w['id']}})
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)/transfer_ownership',path):
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone()
   if not w or w['owner_id']!=u['id']:return self.out(403,{})
   target=int(d.get('new_owner_id',0)); r=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],target)).fetchone()
   if not r or r[0]!='admin':return self.out(400,{})
   c.execute('update workspaces set owner_id=? where id=?',(target,w['id']));c.execute('update members set role=case when user_id=? then ? when user_id=? then ? else role end where workspace_id=?',(target,'owner',u['id'],'admin',w['id']));c.commit()
   return self.out(200,{'workspace':dict(c.execute('select * from workspaces where id=?',(w['id'],)).fetchone())})
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',path):
   w=c.execute('select * from workspaces where slug=?',(m.group(1),)).fetchone(); g=c.execute('select * from groups_ where workspace_id=? and handle=?',(w['id'],m.group(2))).fetchone() if w else None
   if not g:return self.out(404,{})
   owner=c.execute('select owner_id from workspaces where id=?',(w['id'],)).fetchone()[0]
   if g['creator_id']!=u['id'] and owner!=u['id']:return self.out(403,{})
   vals=d.get('user_ids',json.loads(g['user_ids'])); name=d.get('name',g['name'])
   c.execute('update groups_ set name=?,user_ids=? where id=?',(name,json.dumps(vals),g['id']));c.commit()
   return self.out(200,{'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':vals}})
  if m:=re.fullmatch(r'/api/channels/(\d+)',path):
   if len(d.get('topic',''))>250:return self.out(400,{})
   ch=c.execute('select * from channels where id=?',(m.group(1),)).fetchone(); own=c.execute('select owner_id from workspaces where id=?',(ch['workspace_id'],)).fetchone()[0]
   if u['id']!=own and not c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():return self.out(403,{})
   c.execute('update channels set topic=? where id=?',(d.get('topic',''),ch['id'])); eid=event(c,ch['id'],'channel.updated'); c.commit(); sendws(ch['id'],{'type':'channel.updated','event_id':eid,'channel_id':ch['id']}); return self.out(200,{'channel':chobj(c.execute('select * from channels where id=?',(ch['id'],)).fetchone())})
  if m:=re.fullmatch(r'/api/messages/(\d+)',path):
   r=c.execute('select * from messages where id=?',(m.group(1),)).fetchone()
   if not r:return self.out(404,{})
   if r['author_id']!=u['id']:return self.out(403,{})
   c.execute('update messages set body=?,edited_at=? where id=?',(d.get('body',''),now(),r['id']));c.commit();return self.out(200,{'message':msgobj(c,c.execute('select * from messages where id=?',(r['id'],)).fetchone())})
  return self.out(404,{})
 def do_DELETE(self):
  u=auth(self.headers); p=urllib.parse.urlparse(self.path).path
  if not u:return self.out(401,{})
  c=db()
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)/invitations/([^/]+)',p):
   c.execute('delete from invites where code=?',(m.group(2),));c.commit();return self.out(200,{'deleted':True})
  if m:=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p):
   c.execute('delete from groups_ where handle=?',(m.group(2),));c.commit();return self.out(200,{'deleted':True})
  if m:=re.fullmatch(r'/api/messages/(\d+)/pin',p):c.execute('delete from pins where message_id=?',(m.group(1),));c.commit();return self.out(200,{'unpinned':True})
  if m:=re.fullmatch(r'/api/messages/(\d+)',p):c.execute('update messages set deleted=1 where id=?',(m.group(1),));c.commit();return self.out(200,{'deleted':True})
  return self.out(404,{})
 def websocket(self,tok):
  c=db(); r=c.execute('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(tok,)).fetchone(); c.close()
  if not r:self.close_connection=True;return
  key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  ws=WS(self.connection,r); channels=[]
  try:
   while True:
    x=ws.recv()
    if not x:break
    c=db(); cid=int(x.get('channel_id',0))
    if x.get('type')=='subscribe':
     channels.append(cid);clients.setdefault(cid,[]).append(ws); head=c.execute('select coalesce(max(event_id),0) from events where channel_id=?',(cid,)).fetchone()[0];ws.send({'type':'subscribed','channel_id':cid,'head_event_id':head})
    elif x.get('type')=='resume':
     since=int(x.get('since_event_id',0)); es=c.execute('select * from events where channel_id=? and event_id>? order by event_id',(cid,since)).fetchall()
     for e in es: ws.send(dict({'type':e['kind'],'event_id':e['event_id'],'channel_id':cid},message=msgobj(c,c.execute('select * from messages where id=?',(e['message_id'],)).fetchone()) if e['message_id'] else None))
     head=c.execute('select coalesce(max(event_id),0) from events where channel_id=?',(cid,)).fetchone()[0];ws.send({'type':'resumed','channel_id':cid,'head_event_id':head})
    c.close()
  except:pass
def run():
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
HTML='''<!doctype html><html><body><h1>Huddle</h1><div id="app">Huddle chat</div></body></html>'''
if __name__=='__main__':run()
