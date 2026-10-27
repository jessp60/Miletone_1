import os,sys,json,re,sqlite3,secrets,hashlib,time,datetime,threading,base64,struct,socket,select
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True)
SCHEMA='''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT);
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER,is_dm INTEGER,topic TEXT,is_archived INTEGER);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,UNIQUE(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,created_by INTEGER,expires INTEGER,max_uses INTEGER,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0,target INTEGER);
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator INTEGER,user_ids TEXT,UNIQUE(workspace_id,handle));'''
def db():
 for attempt in range(12):
  try:
   c=sqlite3.connect(DB,timeout=8,check_same_thread=False,isolation_level=None); c.row_factory=sqlite3.Row
   c.execute('PRAGMA busy_timeout=8000'); c.execute('PRAGMA journal_mode=WAL'); c.executescript(SCHEMA); return c
  except sqlite3.OperationalError:
   if attempt==11: raise
   time.sleep(0.08)

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def uid(c): return c.execute('select last_insert_rowid()').fetchone()[0]
def user(c,i):
 r=c.execute('select * from users where id=?',(i,)).fetchone()
 return None if not r else {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def channel(c,i):
 r=c.execute('select * from channels where id=?',(i,)).fetchone()
 if not r:return None
 return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def ws(c,r):
 return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def msg(c,r):
 if not r:return None
 rs=c.execute('select emoji,user_id from reactions where message_id=?',(r['id'],)).fetchall(); em={}
 for x in rs: em.setdefault(x['emoji'],[]).append(x['user_id'])
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(c,r['author_id']),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('select count(*) from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in em.items()],'mentions':mention_ids(c,r['body'],r['author_id'],r['channel_id'])}
def mention_ids(c,b,author,ch):
 out=[]; cr=c.execute('select workspace_id from channels where id=?',(ch,)).fetchone()
 if not cr:return out
 members={x[0] for x in c.execute('select user_id from channel_members where channel_id=?',(ch,))}
 for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',b):
  if name in ('channel','here'): out += [x for x in members if x!=author]; continue
  r=c.execute('select id from users where username=?',(name,)).fetchone()
  if r and r[0] in members and r[0]!=author: out.append(r[0])
 return list(dict.fromkeys(out))
def event(c,ch,kind,payload):
 n=c.execute('select coalesce(max(event_id),0)+1 from events where channel_id=?',(ch,)).fetchone()[0]
 c.execute('insert into events(channel_id,event_id,kind,payload) values(?,?,?,?)',(ch,n,kind,json.dumps(payload)))
def jsonload(h):
 try:return json.loads(h)
 except:return None
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def sendj(self,status,obj):
  b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
  except:return None
 def auth(self,c):
  x=self.headers.get('Authorization','')
  if not x.startswith('Bearer '):return None
  r=c.execute('select user_id from tokens where token=?',(x[7:],)).fetchone(); return r[0] if r else None
 def require(self,c):
  u=self.auth(c)
  if not u: self.sendj(401,{'error':'unauthorized'})
  return u
 def do_GET(self): self.route()
 def do_POST(self): self.route()
 def do_PATCH(self): self.route()
 def do_DELETE(self): self.route()
 def route(self):
  c=db(); p=urlparse(self.path); path=p.path; q=parse_qs(p.query); method=self.command
  if path=='/api/health': self.sendj(200,{'status':'ok','node_id':int(os.getenv('NODE','0'))}); return
  if path=='/api/ws':
   self.websocket(c); return
  if path=='/api/auth/register' and method=='POST':
   d=self.body()
   if not d or not re.fullmatch(r'[A-Za-z0-9_]+',str(d.get('username',''))) or len(d.get('password',''))<8:self.sendj(400,{'error':'invalid'});return
   try:c.execute('insert into users(username,password,display_name,timezone,avatar_url,status_text,status_emoji) values(?,?,?,?,?,?,?)',(d['username'],d['password'],d.get('display_name',d['username']),'UTC','','','')); c.commit()
   except sqlite3.IntegrityError:self.sendj(409,{'error':'exists'});return
   i=c.execute('select last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,i));c.commit();self.sendj(201,{'user':user(c,i),'token':t});return
  if path=='/api/auth/login' and method=='POST':
   d=self.body()
   if not d or 'username' not in d or 'password' not in d:self.sendj(400,{'error':'invalid'});return
   r=c.execute('select id from users where username=? and password=?',(d['username'],d['password'])).fetchone()
   if not r:self.sendj(401,{'error':'invalid'});return
   t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r[0]));c.commit();self.sendj(200,{'user':user(c,r[0]),'token':t});return
  u=self.require(c)
  if not u:return
  if path=='/api/auth/me':self.sendj(200,{'user':user(c,u)});return
  if path=='/api/users/me' and method=='PATCH':
   d=self.body() or {}; allowed=['display_name','timezone','avatar_url','status_text','status_emoji']; vals=[d[x] for x in allowed if x in d]; 
   if 'timezone' in d and d['timezone'] not in ('UTC','America/New_York','Europe/London','Asia/Tokyo'):self.sendj(400,{'error':'timezone'});return
   if vals:
    sets=','.join(x+'=?' for x in allowed if x in d);c.execute('update users set '+sets+' where id=?',tuple(d[x] for x in allowed if x in d)+(u,));c.commit()
   self.sendj(200,{'user':user(c,u)});return
  m=re.match(r'^/api/workspaces/([^/]+)(.*)$',path)
  if m:
   slug,rest=m.group(1),m.group(2); w=c.execute('select * from workspaces where slug=?',(slug,)).fetchone()
   if not w:self.sendj(404,{});return
   mid=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u)).fetchone(); role=mid[0] if mid else None
   if rest=='' and method=='GET':
    cs=[] 
    for x in c.execute('select * from channels where workspace_id=? and (is_archived=0 or ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))):
     if not x['is_private'] or c.execute('select 1 from channel_members where channel_id=? and user_id=?',(x['id'],u)).fetchone():cs.append(channel(c,x['id']))
    self.sendj(200,{'workspace':ws(c,w),'channels':cs,'read_state':[{'channel_id':x['id'],'last_read_event_id':(c.execute('select last_read_event_id from reads where user_id=? and channel_id=?',(u,x['id'])).fetchone() or [0])[0]} for x in c.execute('select * from channels where workspace_id=?',(w['id'],))]});return
   if rest=='/members' and method=='GET':
    self.sendj(200,{'members':[{'user_id':x['user_id'],'role':x['role']} for x in c.execute('select * from members where workspace_id=?',(w['id'],))]});return
   if rest.startswith('/members/') and method=='PATCH':
    try: target=int(rest.split('/')[2])
    except: self.sendj(404,{});return
    if not c.execute('select id from users where id=?',(target,)).fetchone():self.sendj(404,{});return
    if role not in ('owner','admin'):self.sendj(403,{});return
    d=self.body() or {}; nr=d.get('role')
    if nr not in ('owner','admin','member','guest'):self.sendj(400,{});return
    old=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],target)).fetchone()
    if not old:self.sendj(404,{});return
    if target==w['owner_id'] or target==u or nr=='owner' or (role=='admin' and old[0]=='admin'):self.sendj(403 if nr!='owner' else 400,{});return
    c.execute('update members set role=? where workspace_id=? and user_id=?',(nr,w['id'],target));c.commit();self.sendj(200,{'member':{'user_id':target,'role':nr}});return
   if rest=='/transfer_ownership' and method=='POST':
    if w['owner_id']!=u:self.sendj(403,{});return
    d=self.body() or {}; target=d.get('new_owner_id')
    r=c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],target)).fetchone()
    if not r or r[0]!='admin':self.sendj(400,{});return
    c.execute('update members set role="admin" where workspace_id=? and user_id=?',(w['id'],u));c.execute('update members set role="owner" where workspace_id=? and user_id=?',(w['id'],target));c.execute('update workspaces set owner_id=? where id=?',(target,w['id']));c.commit();self.sendj(200,{'workspace':ws(c,c.execute('select * from workspaces where id=?',(w['id'],)).fetchone())});return
   if rest=='/invitations' and method=='POST':
    if role not in ('owner','admin'):self.sendj(403,{});return
    d=self.body() or {}; code=secrets.token_urlsafe(12); exp=int(time.time())+int(d.get('expires_in_seconds',86400)); mx=int(d.get('max_uses',1)); target=d.get('invited_user_id')
    c.execute('insert into invitations values(?,?,?,?,?,?,?,?)',(code,w['id'],u,exp,mx,0,0,target));c.commit();self.sendj(201,{'invitation':{'code':code,'workspace_id':w['id'],'expires_at':datetime.datetime.fromtimestamp(exp,datetime.timezone.utc).isoformat().replace('+00:00','Z'),'max_uses':mx,'uses':0}});return
   if rest=='/invitations' and method=='GET':
    if role not in ('owner','admin'):self.sendj(403,{});return
    rows=c.execute('select * from invitations where workspace_id=? and revoked=0 and uses<max_uses and expires>?',(w['id'],int(time.time()))).fetchall()
    self.sendj(200,{'invitations':[{'code':x['code'],'workspace_id':x['workspace_id'],'max_uses':x['max_uses'],'uses':x['uses']} for x in rows]});return
   if rest.startswith('/invitations/') and method=='DELETE':
    if role not in ('owner','admin'):self.sendj(403,{});return
    code=rest.split('/')[2]; c.execute('update invitations set revoked=1 where code=? and workspace_id=?',(code,w['id']));c.commit();self.sendj(200,{'deleted':True});return
   if rest=='' and method=='PATCH':
    if role not in ('owner','admin'):self.sendj(403,{});return
    d=self.body() or {}; 
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):self.sendj(400,{});return
    c.execute('update workspaces set name=coalesce(?,name),join_mode=coalesce(?,join_mode) where id=?',(d.get('name'),d.get('join_mode'),w['id']));c.commit();self.sendj(200,{'workspace':ws(c,c.execute('select * from workspaces where id=?',(w['id'],)).fetchone())});return
   if rest=='/channels' and method=='POST':
    d=self.body()
    if not d or not re.fullmatch(r'[a-z0-9-]{1,32}',str(d.get('name',''))) or len(d.get('topic',''))>250:self.sendj(400,{});return
    if role=='guest' or (d.get('is_private') and role not in ('owner','admin')):self.sendj(403,{});return
    if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],d['name'])).fetchone():self.sendj(409,{});return
    c.execute('insert into channels values(null,?,?,?,?,?,0)',(w['id'],d['name'],int(bool(d.get('is_private'))),0,d.get('topic','')));i=uid(c);c.execute('insert into channel_members values(?,?)',(i,u));c.commit();self.sendj(201,{'channel':channel(c,i)});return
  if path=='/api/workspaces' and method=='GET':
   self.sendj(200,{'workspaces':[ws(c,x) for x in c.execute('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u,))]});return
  if path=='/api/workspaces' and method=='POST':
   d=self.body()
   if not d or not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))) or not d.get('name'):self.sendj(400,{});return
   try:c.execute('insert into workspaces values(null,?,?,?,"open")',(d['slug'],d['name'],u)); wid=uid(c);c.execute('insert into members values(?,?,?)',(wid,u,'owner'));c.execute('insert into channels values(null,?,?,?,?,?,0)',(wid,'general',0,0,''));gid=uid(c);c.execute('insert into channel_members values(?,?)',(gid,u));c.commit();self.sendj(201,{'workspace':ws(c,c.execute('select * from workspaces where id=?',(wid,)).fetchone()),'general_channel':channel(c,gid)})
   except sqlite3.IntegrityError:self.sendj(409,{})
   return
  im=re.match(r'^/api/invitations/([^/]+)/accept$',path)
  if im and method=='POST':
   code=im.group(1); inv=c.execute('select * from invitations where code=?',(code,)).fetchone()
   if not inv or inv['revoked'] or inv['uses']>=inv['max_uses'] or inv['expires']<int(time.time()):self.sendj(404,{});return
   if inv['target'] is not None and inv['target']!=u:self.sendj(403,{});return
   c.execute('insert or ignore into members values(?,?,?)',(inv['workspace_id'],u,'member'));c.execute('update invitations set uses=uses+1,revoked=case when target is not null then 1 else revoked end where code=?',(code,));c.commit();self.sendj(200,{'workspace_id':inv['workspace_id']});return
  cm=re.match(r'^/api/channels/(\d+)(.*)$',path)
  if cm:
   cid=int(cm.group(1)); rest=cm.group(2); ch=channel(c,cid)
   if not ch:self.sendj(404,{});return
   w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone(); role=(c.execute('select role from members where workspace_id=? and user_id=?',(w['id'],u)).fetchone() or [None])[0]; joined=bool(c.execute('select 1 from channel_members where channel_id=? and user_id=?',(cid,u)).fetchone())
   if rest=='/join' and method=='POST':
    if ch['is_private'] and not joined:self.sendj(403,{});return
    if role=='guest' and not joined:self.sendj(403,{});return
    if not joined and not role:
     if w['join_mode']!='open':self.sendj(403,{});return
     c.execute('insert into members values(?,?,?)',(w['id'],u,'member'))
    c.execute('insert or ignore into channel_members values(?,?)',(cid,u));event(c,cid,'member.joined',{'user_id':u});c.commit();self.sendj(200,{'channel':channel(c,cid)});return
   if rest=='/leave' and method=='POST':c.execute('delete from channel_members where channel_id=? and user_id=?',(cid,u));event(c,cid,'member.left',{'user_id':u});c.commit();self.sendj(200,{'channel':channel(c,cid)});return
   if rest=='/members' and method=='GET':self.sendj(200,{'members':[user(c,x[0]) for x in c.execute('select user_id from channel_members where channel_id=?',(cid,))]});return
   if rest in ('/archive','/unarchive') and method=='POST':
    if w['owner_id']!=u:self.sendj(403,{});return
    v=rest=='/archive';c.execute('update channels set is_archived=? where id=?',(v,cid));event(c,cid,'channel.archived' if v else 'channel.unarchived',channel(c,cid));c.commit();self.sendj(200,{'channel':channel(c,cid)});return
   if rest=='' and method=='PATCH':
    if not joined and w['owner_id']!=u:self.sendj(403,{});return
    d=self.body() or {}
    if len(d.get('topic',''))>250:self.sendj(400,{});return
    c.execute('update channels set topic=? where id=?',(d.get('topic',''),cid));event(c,cid,'channel.updated',channel(c,cid));c.commit();self.sendj(200,{'channel':channel(c,cid)});return
   if rest=='/pins' and method=='GET':
    if ch['is_private'] and not joined:self.sendj(403,{});return
    limit=int(q.get('limit',['50'])[0])
    if limit<1 or limit>200:self.sendj(400,{});return
    rows=list(c.execute('select p.*,m.* from pins p join messages m on m.id=p.message_id where m.channel_id=? order by p.message_id desc',(cid,)))
    cur=q.get('cursor',[None])[0]
    if cur:
     try: rows=[x for x in rows if x['message_id']<int(base64.urlsafe_b64decode(cur+'=='))]
     except: self.sendj(400,{});return
    page=rows[:limit]; nxt=base64.urlsafe_b64encode(str(page[-1]['message_id']).encode()).decode().rstrip('=') if len(rows)>limit else None
    self.sendj(200,{'pins':[{'message':msg(c,c.execute('select * from messages where id=?',(x['message_id'],)).fetchone()),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in page],'next_cursor':nxt});return
   if rest=='/messages' and method in ('GET','POST'):
    if method=='POST':
     if ch['is_archived']:self.sendj(423,{});return
     d=self.body()
     if not d or not d.get('body'):self.sendj(400,{});return
     b=d['body']; 
     if b.startswith('/') and not b.startswith('//') and b.split()[0] not in ('/me','/shrug'):self.sendj(400,{});return
     if b.startswith('/shrug'):b=(b[7:].strip()+' ¯\\_(ツ)_/¯').strip()
     c.execute('insert into messages values(null,?,?,?,?,?,null,0)',(cid,u,b,d.get('parent_id'),now()));i=uid(c);r=c.execute('select * from messages where id=?',(i,)).fetchone();event(c,cid,'message.reply' if d.get('parent_id') else 'message.created',msg(c,r));c.commit();self.sendj(201,{'message':msg(c,r)});return
    limit=int(q.get('limit',['50'])[0]);cur=q.get('cursor',[None])[0]
    if limit<1 or limit>200:self.sendj(400,{});return
    rows=list(c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 order by id desc',(cid,)))
    if cur:
     try: rows=[r for r in rows if r['id']<int(base64.urlsafe_b64decode(cur+'=='))]
     except:self.sendj(400,{});return
    page=rows[:limit]; nxt=base64.urlsafe_b64encode(str(page[-1]['id']).encode()).decode().rstrip('=') if len(rows)>limit else None
    self.sendj(200,{'messages':[msg(c,r) for r in page],'next_cursor':nxt});return
  mm=re.match(r'^/api/messages/(\d+)(.*)$',path)
  if mm:
   mid=int(mm.group(1)); rest=mm.group(2); r=c.execute('select * from messages where id=?',(mid,)).fetchone()
   if not r:self.sendj(404,{});return
   if rest=='/replies' and method=='GET':
    limit=int(q.get('limit',['50'])[0])
    if limit<1 or limit>200:self.sendj(400,{});return
    rows=list(c.execute('select * from messages where parent_id=? and deleted=0 order by id',(mid,)))
    cur=q.get('cursor',[None])[0]
    if cur:
     try: rows=[x for x in rows if x['id']>int(base64.urlsafe_b64decode(cur+'=='))]
     except: self.sendj(400,{});return
    page=rows[:limit]; nxt=base64.urlsafe_b64encode(str(page[-1]['id']).encode()).decode().rstrip('=') if len(rows)>limit else None
    self.sendj(200,{'replies':[msg(c,x) for x in page],'next_cursor':nxt});return
   if rest=='/pin' and method=='POST':
    c.execute('insert or ignore into pins values(?,?,?)',(mid,u,now()));c.commit();self.sendj(200,{'pin':{'message_id':mid,'pinned_by':u,'pinned_at':now()}});return
   if rest=='/pin' and method=='DELETE':c.execute('delete from pins where message_id=?',(mid,));c.commit();self.sendj(200,{'unpinned':True});return
   if not rest and method=='PATCH':
    if r['deleted']:self.sendj(409,{});return
    if r['author_id']!=u:self.sendj(403,{});return
    d=self.body() or {};c.execute('update messages set body=?,edited_at=? where id=?',(d.get('body',''),now(),mid));event(c,r['channel_id'],'message.updated',{'message_id':mid});c.commit();self.sendj(200,{'message':msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone())});return
   if not rest and method=='DELETE':
    w=c.execute('select owner_id from workspaces w join channels x on x.workspace_id=w.id where x.id=?',(r['channel_id'],)).fetchone()
    if r['author_id']!=u and w[0]!=u:self.sendj(403,{});return
    c.execute('update messages set deleted=1 where id=?',(mid,));event(c,r['channel_id'],'message.deleted',{'message_id':mid});c.commit();self.sendj(200,{'deleted':True});return
   if rest=='/reactions' and method=='POST':
    d=self.body() or {};c.execute('insert or ignore into reactions values(?,?,?)',(mid,u,d.get('emoji','')));event(c,r['channel_id'],'reaction.added',{'message_id':mid,'emoji':d.get('emoji',''),'user_id':u});c.commit();self.sendj(200,{'reactions':msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone())['reactions']});return
   if rest.startswith('/reactions/') and method=='DELETE':
    e=unquote(rest[11:]);c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(mid,u,e));event(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':e,'user_id':u});c.commit();self.sendj(200,{'reactions':msg(c,c.execute('select * from messages where id=?',(mid,)).fetchone())['reactions']});return
  if path=='/api/search':
   text=q.get('q',[''])[0]; limit=int(q.get('limit',['50'])[0])
   if limit<1 or limit>200:self.sendj(400,{});return
   wid=q.get('workspace_id',[None])[0]; sql='select m.* from messages m join channel_members cm on cm.channel_id=m.channel_id join channels sc on sc.id=m.channel_id where cm.user_id=? and m.deleted=0 and m.body like ?'; params=[u,'%'+text+'%'];
   if wid: sql+=' and sc.workspace_id=?'; params.append(int(wid))
   rows=c.execute(sql+' order by m.id desc',params).fetchall()
   cur=q.get('cursor',[None])[0]
   if cur:
    try: rows=[r for r in rows if r['id']<int(base64.urlsafe_b64decode(cur+'=='))]
    except: self.sendj(400,{});return
   page=rows[:limit]; nxt=base64.urlsafe_b64encode(str(page[-1]['id']).encode()).decode().rstrip('=') if len(rows)>limit else None
   self.sendj(200,{'results':[msg(c,x) for x in page],'next_cursor':nxt});return
  if re.match(r'^/api/channels/\d+/read$',path):
   cid=int(path.split('/')[3]); old=(c.execute('select last_read_event_id from reads where user_id=? and channel_id=?',(u,cid)).fetchone() or [0])[0]
   if method=='POST': old=max(old,int((self.body() or {}).get('last_read_event_id',0)));c.execute('insert or replace into reads values(?,?,?)',(u,cid,old));c.commit()
   head=c.execute('select coalesce(max(event_id),0) from events where channel_id=?',(cid,)).fetchone()[0]; uname=c.execute('select username from users where id=?',(u,)).fetchone()[0]; mention_count=sum(1 for x in c.execute('select author_id,body from messages where channel_id=? and deleted=0',(cid,)) if x['author_id']!=u and (u in mention_ids(c,x['body'],x['author_id'],cid))); self.sendj(200,{'read_state':{'channel_id':cid,'last_read_event_id':old,'unread_count':max(0,head-old),'mention_count':mention_count}});return
  self.sendj(404,{})
 def websocket(self,c):
  tok=parse_qs(urlparse(self.path).query).get('token',[''])[0]; r=c.execute('select user_id from tokens where token=?',(tok,)).fetchone()
  if not r:self.send_error(401);return
  key=self.headers.get('Sec-WebSocket-Key',''); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
  lock=threading.Lock(); subs={}; alive=[True]
  def send(d):
   with lock:
    try:self.wsout(d)
    except (BrokenPipeError,ConnectionError,OSError):alive[0]=False
  def poll():
   pc=db()
   while alive[0]:
    try:
     for cid,since in list(subs.items()):
      rows=pc.execute('select event_id,kind,payload from events where channel_id=? and event_id>? order by event_id',(cid,since)).fetchall()
      for x in rows:
       z=json.loads(x['payload']);z.update(type=x['kind'],event_id=x['event_id'],channel_id=cid);send(z)
       subs[cid]=x['event_id']
    except Exception: pass
    time.sleep(.03)
  threading.Thread(target=poll,daemon=True).start()
  while alive[0]:
   try:
    h=self.rfile.read(2)
    if len(h)<2:break
    n=h[1]&127
    if n==126:n=struct.unpack('>H',self.rfile.read(2))[0]
    elif n==127:n=struct.unpack('>Q',self.rfile.read(8))[0]
    mask=self.rfile.read(4);raw=self.rfile.read(n)
    if len(raw)!=n:break
    d=json.loads(bytes(x^mask[i%4] for i,x in enumerate(raw)));cid=d.get('channel_id')
    if d.get('type')=='subscribe':
     head=c.execute('select coalesce(max(event_id),0) from events where channel_id=?',(cid,)).fetchone()[0];subs[cid]=head;send({'type':'subscribed','channel_id':cid,'head_event_id':head})
    elif d.get('type')=='resume':
     since=int(d.get('since_event_id',0)); rows=c.execute('select event_id,kind,payload from events where channel_id=? and event_id>? order by event_id',(cid,since)).fetchall()
     for x in rows:
      z=json.loads(x['payload']);z.update(type=x['kind'],event_id=x['event_id'],channel_id=cid);send(z)
     head=c.execute('select coalesce(max(event_id),0) from events where channel_id=?',(cid,)).fetchone()[0];subs[cid]=head;send({'type':'resumed','channel_id':cid,'head_event_id':head})
   except (ConnectionError,OSError,ValueError):break
  alive[0]=False
 def wsout(self,d):
  b=json.dumps(d,separators=(',',':')).encode(); n=len(b)
  if n<126: h=bytes([129,n])
  elif n<65536: h=bytes([129,126])+struct.pack('>H',n)
  else: h=bytes([129,127])+struct.pack('>Q',n)
  self.wfile.write(h+b); self.wfile.flush()
if __name__=='__main__':
 db().close(); port=int(sys.argv[1]); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
