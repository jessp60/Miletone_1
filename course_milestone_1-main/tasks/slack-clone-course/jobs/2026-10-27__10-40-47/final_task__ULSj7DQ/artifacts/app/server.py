import os,sys,json,re,sqlite3,secrets,hashlib,base64,datetime,threading,time,struct,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data.sqlite3'; NODE=int(os.environ.get('NODE_ID','0'))
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=8); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=8000'); return c
def init():
 c=db()
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cm(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));''')
 c.commit(); c.close()
init()
def user(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def channel(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def msg(c,r):
 a=c.execute('select * from users where id=?',(r['author_id'],)).fetchone()
 rs=[]
 for x in c.execute('select emoji,count(*) n from reactions where message_id=? group by emoji',(r['id'],)):
  ids=[z[0] for z in c.execute('select user_id from reactions where message_id=? and emoji=?',(r['id'],x['emoji']))]
  rs.append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('select count(*) from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':rs,'mentions':mention_ids(c,r)}
def mention_ids(c,r):
 ch=c.execute('select workspace_id from channels where id=?',(r['channel_id'],)).fetchone()
 if not ch:return []
 members={x[0] for x in c.execute('select user_id from members where workspace_id=?',(ch[0],))}
 names={x['username']:x['id'] for x in c.execute('select id,username from users where id in (%s)'%(','.join('?'*len(members)) if members else '0'),tuple(members))}
 out=[]; text=r['body']
 if '@channel' in text:
  out += [x[0] for x in c.execute('select user_id from cm where channel_id=?',(r['channel_id'],)) if x[0]!=r['author_id']]
 for n in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',text):
  if n in names and names[n]!=r['author_id']: out.append(names[n])
 return list(dict.fromkeys(out))
def event(c,ch,kind,payload):
 q=c.execute('select coalesce(max(event_id),0)+1 from events where channel_id=?',(ch,)).fetchone()[0]
 c.execute('insert into events values(?,?,?,?,?)',(ch,q,kind,json.dumps(payload),now())); return q
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def send(self,code,obj=None,ctype='application/json'):
  b=b'' if obj is None else (obj if isinstance(obj,bytes) else json.dumps(obj).encode())
  self.send_response(code); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b))); self.send_header('Connection','close'); self.end_headers(); self.wfile.write(b)
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
  except:return None
 def auth(self):
  h=self.headers.get('Authorization',''); t=h[7:] if h.startswith('Bearer ') else ''
  c=db(); r=c.execute('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(t,)).fetchone(); c.close(); return r
 def route(self):
  return urllib.parse.urlparse(self.path)
 def req(self):
  p=self.route(); return p.path.split('/'),urllib.parse.parse_qs(p.query)
 def do_GET(self):
  p,q=self.req()
  if p==['','api','health']: return self.send(200,{'status':'ok','node_id':NODE})
  if p[:3]==['','api','ws']: return self.websocket(q)
  u=self.auth()
  if not u:return self.send(401,{'error':'unauthorized'})
  c=db()
  try:
   if p[2:4]==['auth','me']: return self.send(200,{'user':user(u)})
   if p[2]=='users' and len(p)>3:
    r=c.execute('select * from users where id=?',(p[3],)).fetchone(); return self.send(200,{'user':user(r)}) if r else self.send(404,{})
   if p[2]=='workspaces' and len(p)==3:
    rs=c.execute('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],)).fetchall(); return self.send(200,{'workspaces':[dict(x) for x in rs]})
   if p[2]=='workspaces' and len(p)>=4:return self.workspace(c,u,p[3],q)
   if p[2]=='channels' and len(p)>3:return self.channelget(c,u,int(p[3]),p[4:] if len(p)>4 else [],q)
   if p[2]=='messages' and len(p)>3:return self.messageget(c,u,int(p[3]),p[4:] if len(p)>4 else [],q)
   if p[2]=='search': return self.search(c,u,q)
   return self.send(404,{})
  finally:c.close()
 def workspace(self,c,u,slug,q):
  w=c.execute('select * from workspaces where slug=?',(slug,)).fetchone()
  if not w:return self.send(404,{})
  rs=c.execute('select c.* from channels c join cm on cm.channel_id=c.id where c.workspace_id=? and cm.user_id=?',(w['id'],u['id'])).fetchall()
  if q.get('include_archived') not in (['1'],['true']):rs=[x for x in rs if not x['is_archived']]
  states=[]
  for x in rs:
   z=c.execute('select * from reads where user_id=? and channel_id=?',(u['id'],x['id'])).fetchone()
   states.append({'channel_id':x['id'],'last_read_event_id':z['last_read'] if z else 0,'unread_count':0,'mention_count':z['mention_count'] if z else 0})
  return self.send(200,{'workspace':dict(w),'channels':[channel(x) for x in rs],'read_state':states})
 def channelget(self,c,u,i,tail,q):
  ch=c.execute('select * from channels where id=?',(i,)).fetchone()
  if not ch:return self.send(404,{})
  if tail and tail[0]=='members':
   if ch['is_private'] and not c.execute('select 1 from cm where channel_id=? and user_id=?',(i,u['id'])).fetchone():return self.send(403,{})
   rs=[user(x) for x in c.execute('select u.* from users u join cm on cm.user_id=u.id where cm.channel_id=?',(i,))]; return self.send(200,{'members':rs})
  if tail and tail[0]=='messages':return self.messages(c,u,ch,q)
  if tail and tail[0]=='pins':return self.pins(c,u,ch,q)
  if tail and tail[0]=='read':return self.read(c,u,ch)
  return self.send(404,{})
 def page(self,items,q,key='id',new=True):
  try: lim=int(q.get('limit',['50'])[0])
  except: return None
  if lim<1 or lim>200:return None
  cur=q.get('cursor',[None])[0]
  if cur:
   try:
    raw=base64.urlsafe_b64decode(cur.encode()).decode()
    boundary=int(raw.split(':',1)[1]) if raw.startswith('id:') else None
    if boundary is None:return 'bad'
    descending=bool(len(items)>1 and items[0][key]>items[1][key])
    items=[x for x in items if (x[key]<boundary if descending else x[key]>boundary)]
   except:return 'bad'
  page=items[:lim]
  nxt=None
  if len(items)>lim:
   nxt=base64.urlsafe_b64encode(('id:'+str(page[-1][key])).encode()).decode()
  return page,nxt
 def messages(self,c,u,ch,q):
  if ch['is_private'] and not c.execute('select 1 from cm where channel_id=?',(ch['id'],)).fetchone():return self.send(403,{})
  rs=c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 order by id desc',(ch['id'],)).fetchall(); z=self.page(rs,q)
  if z=='bad' or z is None:return self.send(400,{})
  return self.send(200,{'messages':[msg(c,x) for x in z[0]],'next_cursor':z[1]})
 def messageget(self,c,u,i,tail,q):
  r=c.execute('select m.*,c.workspace_id from messages m join channels c on c.id=m.channel_id where m.id=? and m.deleted=0',(i,)).fetchone()
  if not r:return self.send(404,{})
  if tail and tail[0]=='replies':
   rs=c.execute('select * from messages where parent_id=? and deleted=0 order by id',(i,)).fetchall(); z=self.page(rs,q)
   if z=='bad' or z is None:return self.send(400,{})
   return self.send(200,{'replies':[msg(c,x) for x in z[0]],'next_cursor':z[1]})
  return self.send(404,{})
 def pins(self,c,u,ch,q):
  if ch['is_private'] and not c.execute('select 1 from cm where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():return self.send(403,{})
  rs=c.execute('select p.*,m.* from pins p join messages m on m.id=p.message_id where m.channel_id=? order by p.pinned_at desc',(ch['id'],)).fetchall(); z=self.page(rs,q)
  if z=='bad' or z is None:return self.send(400,{})
  return self.send(200,{'pins':[{'message':msg(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in z[0]],'next_cursor':z[1]})
 def search(self,c,u,q):
  text=q.get('q',[''])[0]
  wid=q.get('workspace_id',[None])[0]
  if wid is not None:
   rs=c.execute("select m.* from messages m join cm on cm.channel_id=m.channel_id join channels ch on ch.id=m.channel_id where cm.user_id=? and ch.workspace_id=? and m.deleted=0 and m.body like ? order by m.id desc",(u['id'],wid,'%'+text+'%')).fetchall()
  else:
   rs=c.execute("select m.* from messages m join cm on cm.channel_id=m.channel_id where cm.user_id=? and m.deleted=0 and m.body like ? order by m.id desc",(u['id'],'%'+text+'%')).fetchall()
  z=self.page(rs,q)
  if z=='bad' or z is None:return self.send(400,{})
  return self.send(200,{'results':[msg(c,x) for x in z[0]],'next_cursor':z[1]})
 def do_POST(self):
  p,q=self.req()
  if p==['','api','auth','register']:return self.register()
  if p==['','api','auth','login']:return self.login()
  u=self.auth()
  if not u:return self.send(401,{})
  c=db(); b=self.body()
  if b is None: c.close(); return self.send(400,{})
  try:
   if p[2]=='workspaces' and len(p)==3:return self.createws(c,u,b)
   if p[2]=='workspaces' and len(p)>=5 and p[4]=='channels':return self.createchan(c,u,p[3],b)
   if p[2]=='channels' and len(p)>=4:
    i=int(p[3]); ch=c.execute('select * from channels where id=?',(i,)).fetchone()
    if not ch:return self.send(404,{})
    if len(p)>4 and p[4]=='messages':return self.createmsg(c,u,ch,b)
    if len(p)>4 and p[4] in ('join','leave'):return self.joinleave(c,u,ch,p[4])
    if len(p)>4 and p[4] in ('archive','unarchive'):return self.arch(c,u,ch,p[4])
    if len(p)>4 and p[4]=='read':return self.readpost(c,u,ch,b)
   if p[2]=='messages' and len(p)>4 and p[4]=='pin':return self.pin(c,u,int(p[3]))
   if p[2]=='messages' and len(p)>4 and p[4]=='reactions':return self.react(c,u,int(p[3]),b,True)
   return self.send(404,{})
  finally:c.close()
 def register(self):
  b=self.body() or {}; un=b.get('username',''); pw=b.get('password','')
  if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.send(400,{})
  c=db()
  try:
   c.execute('insert into users(username,password,display_name) values(?,?,?)',(un,pw,b.get('display_name') or un)); uid=c.execute('select last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24); c.execute('insert into tokens values(?,?)',(t,uid)); c.commit(); r=c.execute('select * from users where id=?',(uid,)).fetchone(); return self.send(201,{'user':user(r),'token':t})
  except sqlite3.IntegrityError:return self.send(409,{})
  finally:c.close()
 def login(self):
  b=self.body() or {}; c=db(); r=c.execute('select * from users where username=? and password=?',(b.get('username'),b.get('password'))).fetchone()
  if not r:c.close(); return self.send(401,{})
  t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r['id']));c.commit();c.close();return self.send(200,{'user':user(r),'token':t})
 def createws(self,c,u,b):
  if not b or not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.send(400,{})
  try:
   c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(b['slug'],b.get('name',''),u['id'])); wid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into members values(?,?,?)',(wid,u['id'],'owner'));c.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general'));gid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cm values(?,?)',(gid,u['id']));c.commit();return self.send(201,{'workspace':dict(c.execute('select * from workspaces where id=?',(wid,)).fetchone()),'general_channel':channel(c.execute('select * from channels where id=?',(gid,)).fetchone())})
  except sqlite3.IntegrityError:return self.send(409,{})
 def createchan(self,c,u,slug,b):
  w=c.execute('select * from workspaces where slug=?',(slug,)).fetchone()
  if not w:return self.send(404,{})
  role=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone()
  if not role or role['role']=='guest' or (b.get('is_private',False) and role['role'] not in ('owner','admin')):return self.send(403,{})
  if not b or not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')) or len(b.get('topic',''))>250:return self.send(400,{})
  if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],b['name'])).fetchone():return self.send(409,{})
  c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))); i=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute('insert into cm values(?,?)',(i,u['id'])); c.commit(); ch=c.execute('select * from channels where id=?',(i,)).fetchone(); return self.send(201,{'channel':channel(ch)})
 def joinleave(self,c,u,ch,act):
  if act=='join':
   w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone(); m=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone()
   if ch['is_private'] or (m and m['role']=='guest') or (not m and w['join_mode']!='open'):return self.send(403,{})
   if not m:c.execute('insert into members values(?,?,?)',(w['id'],u['id'],'member'))
   c.execute('insert or ignore into cm values(?,?)',(ch['id'],u['id']));event(c,ch['id'],'member.joined',{'user_id':u['id']});c.commit()
  else:c.execute('delete from cm where channel_id=? and user_id=?',(ch['id'],u['id']));event(c,ch['id'],'member.left',{'user_id':u['id']});c.commit()
  return self.send(200,{'channel':channel(ch)})
 def arch(self,c,u,ch,act):
  w=c.execute('select owner_id from workspaces where id=?',(ch['workspace_id'],)).fetchone()
  if w['owner_id']!=u['id']:return self.send(403,{})
  v=act=='archive';c.execute('update channels set is_archived=? where id=?',(v,ch['id']));event(c,ch['id'],'channel.archived' if v else 'channel.unarchived',{});c.commit();return self.send(200,{'channel':channel(c.execute('select * from channels where id=?',(ch['id'],)).fetchone())})
 def createmsg(self,c,u,ch,b):
  if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return self.send(400,{})
  if ch['is_archived']:return self.send(423,{})
  if not c.execute('select 1 from cm where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():return self.send(403,{})
  par=b.get('parent_id'); body=b['body']
  if body.startswith('/shrug'):body=(body[6:].strip()+' ¯\\_(ツ)_/¯').strip()
  if body.startswith('/') and not body.startswith('//') and not body.startswith('/me') and not body.startswith('/shrug'):return self.send(400,{})
  c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at) values(?,?,?,?,?)',(ch['id'],u['id'],body,par,now()));i=c.execute('select last_insert_rowid()').fetchone()[0];r=c.execute('select * from messages where id=?',(i,)).fetchone()
  mids=mention_ids(c,r)
  for uid in mids:
   z=c.execute('select mention_count from reads where user_id=? and channel_id=?',(uid,ch['id'])).fetchone()
   if z: c.execute('update reads set mention_count=mention_count+1 where user_id=? and channel_id=?',(uid,ch['id']))
   else: c.execute('insert into reads(user_id,channel_id,last_read,mention_count) values(?,?,0,1)',(uid,ch['id']))
  kind='message.reply' if par else 'message.created'; ev=event(c,ch['id'],kind,msg(c,r));c.commit(); out=msg(c,r);out['event_id']=ev;return self.send(201,{'message':out})
 def pin(self,c,u,i):
  r=c.execute('select * from messages where id=?',(i,)).fetchone()
  if not r:return self.send(404,{})
  c.execute('insert or ignore into pins values(?,?,?)',(i,u['id'],now()));c.commit();p=c.execute('select * from pins where message_id=?',(i,)).fetchone();return self.send(200,{'pin':{'message_id':i,'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']}})
 def react(self,c,u,i,b,add):
  if not b or not b.get('emoji'):return self.send(400,{})
  c.execute('insert or ignore into reactions values(?,?,?)',(i,u['id'],b['emoji']));c.commit();r=c.execute('select * from messages where id=?',(i,)).fetchone();return self.send(200,{'reactions':msg(c,r)['reactions']})
 def read(self,c,u,ch):
  z=c.execute('select * from reads where user_id=? and channel_id=?',(u['id'],ch['id'])).fetchone();return self.send(200,{'read_state':{'channel_id':ch['id'],'last_read_event_id':z['last_read'] if z else 0,'unread_count':0,'mention_count':z['mention_count'] if z else 0}})
 def readpost(self,c,u,ch,b):
  n=int((b or {}).get('last_read_event_id',0));z=c.execute('select * from reads where user_id=? and channel_id=?',(u['id'],ch['id'])).fetchone();old=z['last_read'] if z else 0
  c.execute('insert or replace into reads values(?,?,?,?)',(u['id'],ch['id'],max(n,old),z['mention_count'] if z else 0));c.commit();return self.read(c,u,ch)
 def do_PATCH(self):
  p,q=self.req();u=self.auth()
  if not u:return self.send(401,{})
  c=db();b=self.body() or {}
  try:
   if p[2]=='channels' and len(p)>3:
    ch=c.execute('select * from channels where id=?',(p[3],)).fetchone()
    if not ch:return self.send(404,{})
    w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone(); member=c.execute('select 1 from cm where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone()
    if not member and w['owner_id']!=u['id']:return self.send(403,{})
    if len(b.get('topic',''))>250:return self.send(400,{})
    c.execute('update channels set topic=? where id=?',(b.get('topic',ch['topic']),ch['id']));event(c,ch['id'],'channel.updated',{});c.commit();return self.send(200,{'channel':channel(c.execute('select * from channels where id=?',(ch['id'],)).fetchone())})
   if p[2]=='messages':
    r=c.execute('select * from messages where id=?',(p[3],)).fetchone()
    if not r:return self.send(404,{})
    if r['author_id']!=u['id']:return self.send(403,{})
    if not b.get('body','').strip():return self.send(400,{})
    c.execute('update messages set body=?,edited_at=? where id=?',(b['body'],now(),p[3]));c.commit();return self.send(200,{'message':msg(c,c.execute('select * from messages where id=?',(p[3],)).fetchone())})
   return self.send(404,{})
  finally:c.close()
 def do_DELETE(self):
  p,q=self.req();u=self.auth()
  if not u:return self.send(401,{})
  c=db()
  if p[2]=='messages' and len(p)>3 and p[4]=='pin':
   if not c.execute('select 1 from pins where message_id=?',(p[3],)).fetchone(): c.close(); return self.send(404,{})
   c.execute('delete from pins where message_id=?',(p[3],));c.commit();return self.send(200,{'unpinned':True})
  if p[2]=='messages' and len(p)>3:
   r=c.execute('select * from messages where id=?',(p[3],)).fetchone()
   if not r:return self.send(404,{})
   c.execute('update messages set deleted=1 where id=?',(p[3],));c.commit();return self.send(200,{'deleted':True})
  return self.send(404,{})
 def websocket(self,q):
  t=q.get('token',[''])[0].replace('Bearer ','');c=db();u=c.execute('select user_id from tokens where token=?',(t,)).fetchone()
  if not u:return self.send(401,{})
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.send(400,{})
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  self.connection.settimeout(30); sub=None; last=0
  self.connection.settimeout(0.1)
  def frame(x):
   b=json.dumps(x).encode();return b'\x81'+(bytes([len(b)]) if len(b)<126 else b'\x7e'+struct.pack('!H',len(b)))+b
  try:
   while True:
    try:
     h=self.connection.recv(2)
     if not h:break
     ln=h[1]&127
     if ln==126:ln=struct.unpack('!H',self.connection.recv(2))[0]
     mask=self.connection.recv(4); data=self.connection.recv(ln); data=bytes(data[i]^mask[i%4] for i in range(ln)); z=json.loads(data)
     if z.get('type') in ('subscribe','resume'):
      sub=int(z['channel_id']); requested=int(z.get('since_event_id',0)); head=c.execute('select coalesce(max(event_id),0) from events where channel_id=?',(sub,)).fetchone()[0]
      if z['type']=='resume':
       last=requested
       rows=c.execute('select event_id,kind,payload from events where channel_id=? and event_id>? order by event_id',(sub,last)).fetchall()
       for r in rows:
        x=json.loads(r['payload']); x.update({'type':r['kind'],'event_id':r['event_id'],'channel_id':sub}); self.connection.sendall(frame(x)); last=r['event_id']
       self.connection.sendall(frame({'type':'resumed','channel_id':sub,'head_event_id':head}))
      else:
       last=head
       self.connection.sendall(frame({'type':'subscribed','channel_id':sub,'head_event_id':head}))
    except socket.timeout: pass
    if sub:
     rows=c.execute('select event_id,kind,payload from events where channel_id=? and event_id>? order by event_id',(sub,last)).fetchall()
     for r in rows:
      x=json.loads(r['payload']);x.update({'type':r['kind'],'event_id':r['event_id'],'channel_id':sub});self.connection.sendall(frame(x));last=r['event_id']
  except:pass
  finally:c.close()
import socket
if __name__=='__main__':
 init(); ThreadingHTTPServer(('127.0.0.1',8000+NODE),H).serve_forever()
