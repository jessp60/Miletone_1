import os,sys,json,re,sqlite3,secrets,hashlib,base64,threading, time, urllib.parse, socket, struct
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from datetime import datetime,timezone
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True)
NODE=int(os.environ.get('NODE_ID','0')); PORT=8000+NODE
dbinit=threading.Lock()
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def conn():
 c=sqlite3.connect(DB,timeout=10,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA busy_timeout=10000'); return c
def init():
 with dbinit:
  c=conn(); c.executescript('''
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
  CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
  CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
  CREATE TABLE IF NOT EXISTS wm(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
  CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS cm(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
  CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
  CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
  CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
  CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
  '''); c.commit(); c.close()
init()
subs={}; sublock=threading.Lock()
def userobj(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def channel(r): return {k:(bool(r[k]) if k in ['is_private','is_dm','is_archived'] else r[k]) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def getuser(c,uid): return c.execute('select * from users where id=?',(uid,)).fetchone()
def auth(h):
 m=re.match(r'Bearer\s+(.+)',h or '')
 if not m:return None
 c=conn(); r=c.execute('select user_id from tokens where token=?',(m.group(1),)).fetchone(); c.close()
 return r['user_id'] if r else None
def member(c,ch,uid):
 return c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,uid)).fetchone() is not None
def can(c,ch,uid):
 r=c.execute('select w.owner_id,w.id from channels x join workspaces w on w.id=x.workspace_id where x.id=?',(ch,)).fetchone()
 return r and (member(c,ch,uid) or r['owner_id']==uid)
def event(c,ch,kind,payload):
 q=c.execute('select coalesce(max(event_id),0)+1 n from events where channel_id=?',(ch,)).fetchone()['n']
 c.execute('insert into events values(?,?,?,?,?)',(ch,q,kind,json.dumps(payload),now())); c.commit()
 frame={'type':kind,'event_id':q,'channel_id':ch}; frame.update(payload)
 return q
def msgobj(c,r):
 if not r:return None
 rs=c.execute('select emoji,count(*) n,group_concat(user_id) ids from reactions where message_id=? group by emoji',(r['id'],)).fetchall()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(getuser(c,r['author_id'])),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('select count(*) n from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()['n'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rs],'mentions':[]}
def sendws(s,obj):
 b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
 if n<126:h=bytes([0x81,n])
 else:h=bytes([0x81,126])+struct.pack('!H',n)
 s.sendall(h+b)
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')) or 0))
  except:return None
 def out(self,status,obj=None,raw=None,typ='application/json'):
  b=raw if raw is not None else json.dumps(obj,separators=(',',':')).encode()
  self.send_response(status); self.send_header('Content-Type',typ); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def run(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query); method=self.command
  if path=='/api/health': return self.out(200,{'status':'ok','node_id':NODE})
  if path=='/': return self.out(200,{'service':'huddle'})
  if path=='/api/auth/register' and method=='POST':
   d=self.body() or {}; u=d.get('username',''); pw=d.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(pw)<8:return self.out(400,{'error':'invalid'})
   c=conn()
   try:c.execute('insert into users(username,password,display_name) values(?,?,?)',(u,pw,d.get('display_name') or u)); c.commit()
   except sqlite3.IntegrityError:c.close();return self.out(409,{'error':'duplicate'})
   uid=c.execute('select last_insert_rowid() x').fetchone()['x']; t=secrets.token_urlsafe(24); c.execute('insert into tokens values(?,?)',(t,uid));c.commit(); r=getuser(c,uid);c.close();return self.out(201,{'user':userobj(r),'token':t})
  if path=='/api/auth/login' and method=='POST':
   d=self.body() or {};c=conn();r=c.execute('select * from users where username=? and password=?',(d.get('username'),d.get('password'))).fetchone()
   if not r:c.close();return self.out(401,{'error':'bad credentials'})
   t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r['id']));c.commit();c.close();return self.out(200,{'user':userobj(r),'token':t})
  uid=auth(self.headers.get('Authorization'))
  if path=='/api/ws' and not uid:
   uid=auth('Bearer '+q.get('token',[''])[0])
  if path=='/api/ws': return self.websocket(uid)
  if not uid:return self.out(401,{'error':'unauthorized'})
  c=conn(); parts=path.strip('/').split('/')
  try:
   if path=='/api/auth/me': return self.out(200,{'user':userobj(getuser(c,uid))})
   if path=='/api/workspaces' and method=='POST':
    d=self.body() or {}; 
    if not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')):return self.out(400,{'error':'invalid'})
    try:
     c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(d['slug'],d.get('name',''),uid)); wid=c.execute('select last_insert_rowid() x').fetchone()['x'];c.execute('insert into wm values(?,?,?)',(wid,uid,'owner'));c.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general'));gid=c.execute('select last_insert_rowid() x').fetchone()['x'];c.execute('insert into cm values(?,?)',(gid,uid));c.commit()
    except sqlite3.IntegrityError:return self.out(409,{'error':'duplicate'})
    w=c.execute('select * from workspaces where id=?',(wid,)).fetchone();return self.out(201,{'workspace':dict(w),'general_channel':channel(c.execute('select * from channels where id=?',(gid,)).fetchone())})
   if path=='/api/workspaces' and method=='GET':
    rs=c.execute('select w.* from workspaces w join wm m on m.workspace_id=w.id where m.user_id=?',(uid,)).fetchall();return self.out(200,{'workspaces':[dict(x) for x in rs]})
   if len(parts)>=3 and parts[1]=='workspaces':
    slug=parts[2]; w=c.execute('select * from workspaces where slug=?',(slug,)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    if len(parts)==3 and method=='GET':
     arch='0' if q.get('include_archived',['0'])[0] not in ('1','true') else '1'; cs=c.execute('select x.* from channels x join cm m on m.channel_id=x.id where x.workspace_id=? and m.user_id=? and (x.is_archived=0 or ?=\'1\') union select x.* from channels x where x.workspace_id=? and (x.is_archived=0 or ?=\'1\') and x.is_private=0 and exists(select 1 from wm where workspace_id=? and user_id=?)',(w['id'],uid,arch,w['id'],arch,w['id'],uid)).fetchall()
     states=[]
     for x in cs:
      rr=c.execute('select * from reads where user_id=? and channel_id=?',(uid,x['id'])).fetchone(); states.append({'channel_id':x['id'],'last_read_event_id':rr['last_read_event_id'] if rr else 0,'unread_count':0,'mention_count':rr['mention_count'] if rr else 0})
     return self.out(200,{'workspace':dict(w),'channels':[channel(x) for x in cs],'read_state':states})
    if len(parts)>=4 and parts[3]=='channels' and method=='POST':
     d=self.body() or {}; name=d.get('name','');topic=d.get('topic','')
     if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or len(topic)>250:return self.out(400,{'error':'invalid'})
     if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],name)).fetchone():return self.out(409,{'error':'duplicate'})
     priv=1 if d.get('is_private') else 0
     role=c.execute('select role from wm where workspace_id=? and user_id=?',(w['id'],uid)).fetchone()
     if not role or (priv and role['role'] not in ('owner','admin')):return self.out(403,{'error':'forbidden'})
     c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],name,priv,topic));ch=c.execute('select last_insert_rowid() x').fetchone()['x'];c.execute('insert into cm values(?,?)',(ch,uid));c.commit();return self.out(201,{'channel':channel(c.execute('select * from channels where id=?',(ch,)).fetchone())})
   if parts[1]=='channels':
    ch=int(parts[2]); r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    w=c.execute('select * from workspaces where id=?',(r['workspace_id'],)).fetchone(); role=c.execute('select role from wm where workspace_id=? and user_id=?',(w['id'],uid)).fetchone()
    if len(parts)==3 and method=='PATCH':
     d=self.body() or {}; 
     if len(d.get('topic',''))>250:return self.out(400,{'error':'invalid'})
     if not can(c,ch,uid) and (not role or role['role']!='owner'):return self.out(403,{'error':'forbidden'})
     c.execute('update channels set topic=? where id=?',(d.get('topic',r['topic']),ch));c.commit(); out=channel(c.execute('select * from channels where id=?',(ch,)).fetchone());event(c,ch,'channel.updated',{'channel':out});return self.out(200,{'channel':out})
    if len(parts)==4 and parts[3]=='members' and method=='GET':
     if r['is_private'] and not member(c,ch,uid):return self.out(403,{'error':'forbidden'})
     rs=c.execute('select u.* from users u join cm m on m.user_id=u.id where m.channel_id=?',(ch,)).fetchall();return self.out(200,{'members':[userobj(x) for x in rs]})
    if len(parts)==4 and parts[3] in ('join','leave') and method=='POST':
     if parts[3]=='join':
      if r['is_private'] and not role:return self.out(403,{'error':'forbidden'})
      c.execute('insert or ignore into cm values(?,?)',(ch,uid));c.execute('insert or ignore into wm values(?,?,?)',(w['id'],uid,'member'));c.commit();event(c,ch,'member.joined',{'user_id':uid})
     else:c.execute('delete from cm where channel_id=? and user_id=?',(ch,uid));c.commit();event(c,ch,'member.left',{'user_id':uid})
     return self.out(200,{'channel':channel(c.execute('select * from channels where id=?',(ch,)).fetchone())})
    if len(parts)==4 and parts[3] in ('archive','unarchive') and method=='POST':
     if w['owner_id']!=uid:return self.out(403,{'error':'forbidden'})
     v=1 if parts[3]=='archive' else 0;c.execute('update channels set is_archived=? where id=?',(v,ch));c.commit();event(c,ch,'channel.'+('archived' if v else 'unarchived'),{'channel':channel(c.execute('select * from channels where id=?',(ch,)).fetchone())});return self.out(200,{'channel':channel(c.execute('select * from channels where id=?',(ch,)).fetchone())})
    if len(parts)>=4 and parts[3]=='messages':
     if not member(c,ch,uid) and w['owner_id']!=uid:return self.out(403,{'error':'forbidden'})
     if r['is_archived']:return self.out(423,{'error':'archived'})
     if method=='POST':
      d=self.body()
      if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return self.out(400,{'error':'body required'})
      c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at) values(?,?,?,?,?)',(ch,uid,d['body'],d.get('parent_id'),now()));mid=c.execute('select last_insert_rowid() x').fetchone()['x'];c.commit();m=msgobj(c,c.execute('select * from messages where id=?',(mid,)).fetchone())
      ids=set()
      if '@channel' in d['body']: ids.update(x['id'] for x in c.execute('select user_id id from cm where channel_id=? and user_id<>?',(ch,uid)))
      for nm in re.findall(r'(?<![A-Za-z0-9_@])@([A-Za-z0-9_]+)',d['body']):
       x=c.execute('select id from users where username=?',(nm,)).fetchone()
       if x and x['id']!=uid and member(c,ch,x['id']): ids.add(x['id'])
      m['mentions']=sorted(ids)
      for x in ids:c.execute('insert or ignore into reads(user_id,channel_id,mention_count) values(?,?,0)',(x,ch));c.execute('update reads set mention_count=mention_count+1 where user_id=? and channel_id=?',(x,ch))
      c.commit();event(c,ch,'message.reply' if d.get('parent_id') else 'message.created',{'message':m});return self.out(201,{'message':m})
     if method=='GET':
      lim=int(q.get('limit',['50'])[0]); 
      if lim<1 or lim>200:return self.out(400,{'error':'limit'})
      cur=q.get('cursor',[None])[0]
      if cur and (not cur.isdigit() or int(cur)<=0):return self.out(400,{'error':'cursor'})
      if cur:
       rs=c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 and id<? order by id desc limit ?',(ch,int(cur),lim)).fetchall()
      else:
       rs=c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 order by id desc limit ?',(ch,lim)).fetchall()
      nxt=str(rs[-1]['id']) if len(rs)==lim and c.execute('select 1 from messages where channel_id=? and parent_id is null and deleted=0 and id<?',(ch,rs[-1]['id'])).fetchone() else None
      return self.out(200,{'messages':[msgobj(c,x) for x in rs],'next_cursor':nxt})
   if parts[1]=='messages':
    mid=int(parts[2]); mr=c.execute('select * from messages where id=?',(mid,)).fetchone()
    if not mr:return self.out(404,{'error':'not found'})
    ch=mr['channel_id'];w=c.execute('select w.* from workspaces w join channels x on x.workspace_id=w.id where x.id=?',(ch,)).fetchone()
    if len(parts)>=4 and parts[3]=='replies':
     lim=int(q.get('limit',['50'])[0])
     if lim<1 or lim>200:return self.out(400,{'error':'limit'})
     cur=q.get('cursor',[None])[0]
     if cur and (not cur.isdigit() or int(cur)<=0):return self.out(400,{'error':'cursor'})
     if cur:
      rs=c.execute('select * from messages where parent_id=? and deleted=0 and id>? order by id limit ?',(mid,int(cur),lim)).fetchall()
     else:
      rs=c.execute('select * from messages where parent_id=? and deleted=0 order by id limit ?',(mid,lim)).fetchall()
     nxt=str(rs[-1]['id']) if len(rs)==lim and c.execute('select 1 from messages where parent_id=? and deleted=0 and id>?',(mid,rs[-1]['id'])).fetchone() else None
     return self.out(200,{'replies':[msgobj(c,x) for x in rs],'next_cursor':nxt})
    if len(parts)>=4 and parts[3]=='reactions':
     d=self.body() or {}; em=urllib.parse.unquote(parts[4]) if len(parts)>4 else d.get('emoji')
     if method=='POST':c.execute('insert or ignore into reactions values(?,?,?)',(mid,uid,em));c.commit();m=msgobj(c,mr);event(c,ch,'reaction.added',{'message_id':mid,'reactions':m['reactions']});return self.out(200,{'reactions':m['reactions']})
     c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(mid,uid,em));c.commit();m=msgobj(c,mr);event(c,ch,'reaction.removed',{'message_id':mid,'reactions':m['reactions']});return self.out(200,{'reactions':m['reactions']})
    if method=='PATCH':
     if mr['deleted']:return self.out(409,{'error':'deleted'})
     if mr['author_id']!=uid:return self.out(403,{'error':'forbidden'})
     d=self.body() or {}; 
     if not d.get('body','').strip():return self.out(400,{'error':'body'})
     c.execute('update messages set body=?,edited_at=? where id=?',(d['body'],now(),mid));c.commit();m=msgobj(c,c.execute('select * from messages where id=?',(mid,)).fetchone());event(c,ch,'message.updated',{'message':m});return self.out(200,{'message':m})
    if method=='DELETE' and not (len(parts)>=4 and parts[3]=='pin'):
     if mr['author_id']!=uid and w['owner_id']!=uid:return self.out(403,{'error':'forbidden'})
     c.execute('update messages set deleted=1 where id=?',(mid,));c.commit();event(c,ch,'message.deleted',{'message_id':mid});return self.out(200,{'deleted':True})
   if path=='/api/search':
    text=q.get('q',[''])[0]; lim=int(q.get('limit',['50'])[0])
    if lim<1 or lim>200:return self.out(400,{'error':'limit'})
    cur=q.get('cursor',[None])[0]
    if cur and not cur.isdigit():return self.out(400,{'error':'cursor'})
    wid=q.get('workspace_id',[None])[0]
    cond=' and x.workspace_id=?' if wid else ''
    args=[uid,'%'+text+'%']+([int(wid)] if wid else [])
    if cur: cond+=' and m.id<?';args.append(int(cur))
    rs=c.execute('select m.* from messages m join channels x on x.id=m.channel_id join cm z on z.channel_id=x.id and z.user_id=? where m.body like ? and m.deleted=0'+cond+' order by m.id desc limit ?',args+[lim]).fetchall()
    more=rs and c.execute('select 1 from messages m join channels x on x.id=m.channel_id join cm z on z.channel_id=x.id and z.user_id=? where m.body like ? and m.deleted=0'+cond+' and m.id<?',args+[rs[-1]['id']]).fetchone()
    return self.out(200,{'results':[msgobj(c,x) for x in rs],'next_cursor':str(rs[-1]['id']) if more else None})
   if len(parts)>=4 and parts[3]=='pin' and method in ('POST','DELETE'):
    if not can(c,ch,uid):return self.out(403,{'error':'forbidden'})
    if method=='POST':
     c.execute('insert or ignore into pins values(?,?,?)',(mid,uid,now()));c.commit();x=c.execute('select * from pins where message_id=?',(mid,)).fetchone();return self.out(200,{'pin':{'message_id':mid,'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
    c.execute('delete from pins where message_id=?',(mid,));c.commit();return self.out(200,{'unpinned':True})
   if len(parts)==4 and parts[3]=='pins':
    if not can(c,int(parts[2]),uid):return self.out(403,{'error':'forbidden'})
    lim=int(q.get('limit',['50'])[0]);cur=q.get('cursor',[None])[0]
    if lim<1 or lim>200 or (cur and not cur.isdigit()):return self.out(400,{'error':'pagination'})
    args=[int(parts[2])]
    extra=''
    if cur:extra=' and m.id<?';args.append(int(cur))
    rs=c.execute('select p.*,m.* from pins p join messages m on m.id=p.message_id where m.channel_id=?'+extra+' order by m.id desc limit ?',args+[lim]).fetchall()
    nxt=str(rs[-1]['id']) if len(rs)==lim and rs[-1] and c.execute('select 1 from pins p join messages m on m.id=p.message_id where m.channel_id=? and m.id<?',(int(parts[2]),rs[-1]['id'])).fetchone() else None
    return self.out(200,{'pins':[{'message':msgobj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':nxt})
   if len(parts)==4 and parts[3]=='read':
    cid=int(parts[2])
    if method=='POST':
     d=self.body() or {}; val=d.get('last_read_event_id')
     if not isinstance(val,int):return self.out(400,{'error':'last_read_event_id'})
     c.execute('insert or ignore into reads(user_id,channel_id,last_read_event_id,mention_count) values(?,?,?,0)',(uid,cid,val));c.execute('update reads set last_read_event_id=max(last_read_event_id,?) where user_id=? and channel_id=?',(val,uid,cid));c.commit()
    rr=c.execute('select * from reads where user_id=? and channel_id=?',(uid,cid)).fetchone();return self.out(200,{'read_state':{'channel_id':cid,'last_read_event_id':rr['last_read_event_id'] if rr else 0,'unread_count':0,'mention_count':rr['mention_count'] if rr else 0}})
  except Exception as e:
   try:c.close()
   except:pass
   return self.out(400,{'error':'bad request'})
  finally:c.close()

 def do_GET(self): self.run()
 def do_POST(self): self.run()
 def do_PATCH(self): self.run()
 def do_DELETE(self): self.run()
 def websocket(self,uid):
  if not uid:return self.closews()
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.closews()
  self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
  s=self.connection
  while True:
   h=s.recv(2)
   if not h:break
   n=h[1]&127
   if n==126:n=struct.unpack('!H',s.recv(2))[0]
   elif n==127:n=struct.unpack('!Q',s.recv(8))[0]
   mask=s.recv(4); data=s.recv(n); data=bytes(data[i]^mask[i%4] for i in range(n)); d=json.loads(data)
   ch=int(d.get('channel_id',0)); c=conn()
   head=c.execute('select coalesce(max(event_id),0) n from events where channel_id=?',(ch,)).fetchone()['n']
   if d.get('type')=='resume':
    since=int(d.get('since_event_id',0)); rows=c.execute('select * from events where channel_id=? and event_id>? order by event_id',(ch,since)).fetchall()
    for x in rows:
     z={'type':x['kind'],'event_id':x['event_id'],'channel_id':ch};z.update(json.loads(x['payload']));sendws(s,z)
    sendws(s,{'type':'resumed','channel_id':ch,'head_event_id':head}); last=head
   else:
    sendws(s,{'type':'subscribed','channel_id':ch,'head_event_id':head}); last=head
   c.close()
   with sublock: subs.setdefault(ch,[]).append(s)
   def poll(sock,cid,initial):
    seen=initial
    while True:
     try:
      db=conn(); rows=db.execute('select * from events where channel_id=? and event_id>? order by event_id',(cid,seen)).fetchall(); db.close()
      for x in rows:
       z={'type':x['kind'],'event_id':x['event_id'],'channel_id':cid};z.update(json.loads(x['payload']));sendws(sock,z);seen=x['event_id']
      time.sleep(.03)
     except Exception: break
   threading.Thread(target=poll,args=(s,ch,last),daemon=True).start()
 def closews(self):self.send_response(403);self.end_headers()
if __name__=='__main__':
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
