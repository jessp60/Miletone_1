import os,sys,json,re,sqlite3,secrets,hashlib,base64,struct,threading,time,datetime,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data.db'; NODE=int(os.environ.get('NODE_ID','0')); PORT=8000+NODE
WSLOCK=threading.Lock(); CLIENTS={}
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row
 c.execute('PRAGMA busy_timeout=10000')
 return c
def init():
 c=db()
 for _ in range(40):
  try:
   c.execute('PRAGMA journal_mode=WAL'); break
  except sqlite3.OperationalError: time.sleep(0.05)
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',mention_count INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER,mentions TEXT);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
 CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at REAL,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER);
 CREATE TABLE IF NOT EXISTS groupmembers(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));''')
 c.commit(); c.close()
def userobj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def chan(r):
 d=dict(r); d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived']); return d
def auth(h):
 x=h.get('Authorization','')
 if not x.startswith('Bearer '): return None
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
def send_event(cid,kind,payload):
 c=db(); c.execute('BEGIN IMMEDIATE'); q=c.execute('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?',(cid,)).fetchone()[0]; c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(cid,q,kind,json.dumps(payload))); c.commit(); c.close()
 return q
def broadcast(cid,o):
 with WSLOCK:
  for w in list(CLIENTS.get(cid,[])):
   try: ws_send(w,o)
   except: pass
def ws_send(s,o):
 b=json.dumps(o,separators=(',',':')).encode(); n=len(b)
 if n<126: h=bytes([0x81,n])
 elif n<65536: h=bytes([0x81,126])+struct.pack('>H',n)
 else: h=bytes([0x81,127])+struct.pack('>Q',n)
 s.sendall(h+b)
def msgobj(c,r):
 au=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 rs=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(r['id'],)).fetchall(); agg={}
 for x in rs: agg.setdefault(x['emoji'],[]).append(x['user_id'])
 mentions=json.loads(r['mentions']) if 'mentions' in r.keys() and r['mentions'] else []
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(au),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in agg.items()],'mentions':mentions}
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def json(self,code,d):
  b=json.dumps(d,separators=(',',':')).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  try: return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
  except: raise ValueError()
 def do_GET(self): self.route('GET')
 def do_POST(self): self.route('POST')
 def do_PATCH(self): self.route('PATCH')
 def do_DELETE(self): self.route('DELETE')
 def route(self,method):
  p=urllib.parse.urlparse(self.path); path=p.path; qs=urllib.parse.parse_qs(p.query)
  if path=='/api/health': return self.json(200,{'status':'ok','node_id':NODE})
  if path=='/api/ws': return self.websocket(qs)
  u=auth(self.headers)
  public=path in ('/api/auth/register','/api/auth/login')
  if not u and not public: return self.json(401,{'error':'unauthorized'})
  try: b=self.body() if method in ('POST','PATCH') else {}
  except: return self.json(400,{'error':'bad json'})
  c=db()
  try: self.process(c,method,path,qs,b,u)
  except Exception as e:
   c.rollback(); self.json(500,{'error':str(e)})
  finally: c.close()
 def process(self,c,m,p,q,b,u):
  if p=='/api/auth/register' and m=='POST':
   un=b.get('username',''); pw=b.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8: return self.json(400,{'error':'invalid'})
   try: c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,pw,b.get('display_name') or un)); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); return self.json(201,{'user':userobj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':t})
   except sqlite3.IntegrityError: return self.json(409,{'error':'duplicate'})
  if p=='/api/auth/login' and m=='POST':
   r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password'))).fetchone()
   if not r:return self.json(401,{'error':'invalid'})
   t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();return self.json(200,{'user':userobj(r),'token':t})
  if p=='/api/auth/me': return self.json(200,{'user':userobj(u)})
  if p=='/api/users/me' and m=='PATCH':
   allowed=['display_name','timezone','avatar_url','status_text','status_emoji']; vals=[b[k] for k in allowed if k in b]
   if 'timezone' in b and not isinstance(b['timezone'],str): return self.json(400,{})
   if vals:
    sets=','.join(k+'=?' for k in allowed if k in b); c.execute('UPDATE users SET '+sets+' WHERE id=?',tuple(vals+[u['id']]));c.commit()
   return self.json(200,{'user':userobj(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
  if p=='/api/workspaces' and m=='POST':
   s=b.get('slug','')
   if not re.fullmatch(r'[a-z0-9-]{2,32}',s) or not b.get('name'): return self.json(400,{})
   try:
    c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(s,b['name'],u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(gid,u['id']));c.commit(); w=dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone());return self.json(201,{'workspace':w,'general_channel':chan(c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone())})
   except sqlite3.IntegrityError:return self.json(409,{})
  if p=='/api/workspaces' and m=='GET':
   rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();return self.json(200,{'workspaces':[dict(x) for x in rows]})
  mm=re.fullmatch(r'/api/workspaces/([^/]+)(?:/(.*))?',p)
  if mm:
   slug,sub=mm.group(1),mm.group(2) or ''; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
   if not w:return self.json(404,{})
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); role=role['role'] if role else None
   if sub=='' and m=='PATCH':
    if role not in ('owner','admin'): return self.json(403,{})
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'): return self.json(400,{})
    sets=[]; vals=[]
    for k in ('name','join_mode'):
     if k in b: sets.append(k+'=?'); vals.append(b[k])
    if sets: c.execute('UPDATE workspaces SET '+','.join(sets)+' WHERE id=?',tuple(vals+[w['id']])); c.commit()
    return self.json(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
   if sub=='' and m=='GET':
    cs=[chan(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?) AND (is_private=0 OR id IN (SELECT channel_id FROM cmembers WHERE user_id=?))',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'),u['id']))]
    states=[]
    for rr in c.execute('SELECT channel_id,last_read FROM reads WHERE user_id=? AND channel_id IN (SELECT id FROM channels WHERE workspace_id=?)',(u['id'],w['id'])):
     states.append({'channel_id':rr['channel_id'],'last_read_event_id':rr['last_read'],'unread_count':0,'mention_count':0})
    return self.json(200,{'workspace':dict(w),'channels':cs,'read_state':states})
   if sub=='members' and m=='GET': return self.json(200,{'members':[dict(x) for x in c.execute('SELECT * FROM members WHERE workspace_id=?',(w['id'],))]})
   if sub=='channels' and m=='POST':
    if role not in ('owner','admin','member') or (role=='member' and b.get('is_private')): return self.json(403,{})
    n=b.get('name','')
    if not re.fullmatch(r'[a-z0-9-]{1,32}',n) or not b.get('name') or len(b.get('topic',''))>250: return self.json(400,{})
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone(): return self.json(409,{})
    try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],n,int(bool(b.get('is_private'))),b.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return self.json(201,{'channel':chan(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    except sqlite3.IntegrityError:return self.json(409,{})
   if sub=='invitations' and m=='POST':
    if role not in ('owner','admin'):return self.json(403,{})
    code=secrets.token_urlsafe(12); target=b.get('invited_user_id'); exp=time.time()+b.get('expires_in_seconds',86400); uses=0; maxuses=1 if target else b.get('max_uses',1); c.execute('INSERT INTO invites(code,workspace_id,invited_user_id,expires_at,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],target,exp,maxuses));c.commit();return self.json(201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':maxuses}})
   if sub=='invitations' and m=='GET':
    if role not in ('owner','admin'):return self.json(403,{})
    return self.json(200,{'invitations':[dict(x) for x in c.execute('SELECT * FROM invites WHERE workspace_id=? AND revoked=0 AND uses<max_uses',(w['id'],))]})
   if sub.startswith('invitations/') and m=='DELETE':
    if role not in ('owner','admin'): return self.json(403,{})
    code=sub.split('/',1)[1]; x=c.execute('SELECT * FROM invites WHERE workspace_id=? AND code=?',(w['id'],code)).fetchone()
    if not x: return self.json(404,{})
    c.execute('UPDATE invites SET revoked=1 WHERE code=?',(code,)); c.commit(); return self.json(200,{'deleted':True})
   tm=re.fullmatch(r'members/(\d+)',sub)
   if tm and m=='PATCH':
    if b.get('role') not in ('admin','member','guest'): return self.json(400,{})
    if role not in ('owner','admin') or int(tm.group(1))==u['id']: return self.json(403,{})
    old=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],tm.group(1))).fetchone()
    if not old:return self.json(404,{})
    if role=='admin' and old['role'] in ('owner','admin'):return self.json(403,{})
    c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],tm.group(1)));c.commit();return self.json(200,{'member':{'user_id':int(tm.group(1)),'role':b['role']}})
   if sub=='transfer_ownership' and m=='POST':
    if role!='owner':return self.json(403,{})
    x=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],b.get('new_owner_id'))).fetchone()
    if not x or x['role']!='admin':return self.json(400,{})
    c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']));c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],b['new_owner_id']));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(b['new_owner_id'],w['id']));c.commit();return self.json(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
  im=re.fullmatch(r'/api/invitations/([^/]+)/accept',p)
  if im and m=='POST':
   x=c.execute('SELECT * FROM invites WHERE code=?',(im.group(1),)).fetchone()
   if not x or x['revoked'] or x['uses']>=x['max_uses'] or x['expires_at']<time.time():return self.json(404,{})
   if x['invited_user_id'] and x['invited_user_id']!=u['id']:return self.json(403,{})
   c.execute('INSERT OR REPLACE INTO members VALUES(?,?,?)',(x['workspace_id'],u['id'],'member'));c.execute('UPDATE invites SET uses=uses+1 WHERE code=?',(im.group(1),));c.commit();return self.json(200,{'member':{'user_id':u['id'],'role':'member'}})
  um=re.fullmatch(r'/api/users/(\d+)',p)
  if um and m=='GET':
   x=c.execute('SELECT * FROM users WHERE id=?',(int(um.group(1)),)).fetchone()
   if not x:return self.json(404,{})
   d=userobj(x); d['mention_count']=x['mention_count']; return self.json(200,{'user':d})
  rm=re.fullmatch(r'/api/messages/(\d+)/replies',p)
  if rm and m=='GET':
   pid=int(rm.group(1));
   if not c.execute('SELECT 1 FROM messages WHERE id=?',(pid,)).fetchone(): return self.json(404,{})
   lim=int(q.get('limit',['50'])[0]); cur=q.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.json(400,{})
   args=[pid]; sql='SELECT * FROM messages WHERE parent_id=? AND deleted=0'
   if cur:
    if not cur.isdigit():return self.json(400,{})
    sql+=' AND id>?'; args.append(int(cur))
   sql+=' ORDER BY id ASC LIMIT ?'; args.append(lim+1); rows=c.execute(sql,args).fetchall(); more=len(rows)>lim; rows=rows[:lim]
   return self.json(200,{'replies':[msgobj(c,x) for x in rows],'next_cursor':str(rows[-1]['id']) if more and rows else None})
  pm=re.fullmatch(r'/api/messages/(\d+)/pin',p)
  if pm:
   mid=int(pm.group(1)); x=c.execute('SELECT m.*,ch.workspace_id FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(mid,)).fetchone()
   if not x:return self.json(404,{})
   rr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(x['workspace_id'],u['id'])).fetchone()
   if not rr:return self.json(403,{})
   if m=='POST':
    c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(mid,u['id'],now()));c.commit(); z=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone();return self.json(200,{'pin':dict(z)})
   if m=='DELETE':
    c.execute('DELETE FROM pins WHERE message_id=?',(mid,));c.commit();return self.json(200,{'unpinned':True})
  if p=='/api/search' and m=='GET':
   term=q.get('q',[''])[0]; lim=int(q.get('limit',['50'])[0]); cur=q.get('cursor',[None])[0]; wid=q.get('workspace_id',[None])[0]
   if lim<1 or lim>200:return self.json(400,{})
   rows=c.execute('SELECT m.* FROM messages m JOIN cmembers cm ON cm.channel_id=m.channel_id AND cm.user_id=? JOIN channels sc ON sc.id=m.channel_id WHERE m.deleted=0 AND m.body LIKE ? AND (? IS NULL OR sc.workspace_id=?) ORDER BY m.id DESC',(u['id'],'%'+term+'%',wid,wid)).fetchall()
   if cur: rows=[x for x in rows if x['id']<int(cur)]
   more=len(rows)>lim; rows=rows[:lim];return self.json(200,{'results':[msgobj(c,x) for x in rows],'next_cursor':str(rows[-1]['id']) if more and rows else None})
  self.channels(c,m,p,q,b,u)
 def channels(self,c,m,p,q,b,u):
  im=re.fullmatch(r'/api/channels/(\d+)(?:/(.*))?',p)
  if not im:return self.json(404,{})
  cid=int(im.group(1)); sub=im.group(2) or ''; ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
  if not ch:return self.json(404,{})
  w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); role=r['role'] if r else None
  if sub=='join' and m=='POST':
   if ch['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return self.json(403,{})
   if role=='guest': return self.json(403,{})
   if role=='guest': return self.json(403,{})
   if not role:
    if w['join_mode']!='open':return self.json(403,{})
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
   c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();send_event(cid,'member.joined',{'user_id':u['id']});return self.json(200,{'channel':chan(ch)})
  if sub=='leave' and m=='POST': c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id']));c.commit();send_event(cid,'member.left',{'user_id':u['id']});return self.json(200,{'channel':chan(ch)})
  if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone() and role!='owner':return self.json(403,{})
  if sub=='' and m=='GET':
   if ch['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone() and role!='owner': return self.json(404,{})
   return self.json(200,{'channel':chan(ch)})
  if sub=='members' and m=='GET':return self.json(200,{'members':[userobj(x) for x in c.execute('SELECT u.* FROM users u JOIN cmembers m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
  if sub in ('archive','unarchive') and m=='POST':
   if role!='owner':return self.json(403,{})
   val=sub=='archive';c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid));c.commit();send_event(cid,'channel.archived' if val else 'channel.unarchived',{});return self.json(200,{'channel':chan(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
  if sub=='' and m=='PATCH':
   if len(b.get('topic',''))>250:return self.json(400,{})
   c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),cid));c.commit();send_event(cid,'channel.updated',{});return self.json(200,{'channel':chan(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
  if sub=='messages':
   if m=='GET':
    lim=int(q.get('limit',['50'])[0]);
    if lim<1 or lim>200 or 'offset' in q or 'page' in q:return self.json(400,{})
    cur=q.get('cursor',[None])[0]
    if cur is not None and not cur.isdigit():return self.json(400,{})
    args=[cid]; sql='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'
    if cur: sql+=' AND id<?'; args.append(int(cur))
    sql+=' ORDER BY id DESC LIMIT ?'; args.append(lim+1); rows=c.execute(sql,args).fetchall(); more=len(rows)>lim; rows=rows[:lim]; nc=str(rows[-1]['id']) if more and rows else None
    return self.json(200,{'messages':[msgobj(c,x) for x in rows],'next_cursor':nc})
   if ch['is_archived']:return self.json(423,{})
   text=b.get('body','')
   if not text:return self.json(400,{})
   parent=b.get('parent_id'); mentions=[]; 
   for x in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',text):
    z=c.execute('SELECT id FROM users WHERE username=?',(x,)).fetchone()
    if z and z['id']!=u['id'] and c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,z['id'])).fetchone():mentions.append(z['id'])
   if '@channel' in text:
    mentions += [x['user_id'] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=?',(cid,u['id']))]
   mentions=list(dict.fromkeys(mentions)); body=text
   if text.startswith('/shrug'):body=text[6:].lstrip()+' ¯\\_(ツ)_/¯'
   if text.startswith('/') and not text.startswith('//') and text.split()[0] not in ('/me','/shrug'):return self.json(400,{})
   c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,u['id'],body,parent,now(),json.dumps(mentions)))
   for midx in mentions:
    c.execute('UPDATE users SET mention_count=mention_count+1 WHERE id=?',(midx,))
   mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('UPDATE messages SET edited_at=NULL WHERE id=?',(mid,))
   c.commit()
   r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   o=msgobj(c,r); kind='message.reply' if parent else 'message.created'
   send_event(cid,kind,{'message':o})
   return self.json(201,{'message':o})
  imsg=re.fullmatch(r'messages/(\d+)/replies',sub)
  if imsg and m=='GET':
   pid=int(imsg.group(1)); lim=int(q.get('limit',['50'])[0]); cur=q.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.json(400,{})
   args=[pid]; sql='SELECT * FROM messages WHERE parent_id=? AND deleted=0'
   if cur: sql+=' AND id>?'; args.append(int(cur))
   sql+=' ORDER BY id ASC LIMIT ?'; args.append(lim+1); rows=c.execute(sql,args).fetchall(); more=len(rows)>lim; rows=rows[:lim]
   return self.json(200,{'replies':[msgobj(c,x) for x in rows],'next_cursor':str(rows[-1]['id']) if more and rows else None})
  imsg=re.fullmatch(r'messages/(\d+)/(pin|replies)',sub)
  if imsg and imsg.group(2)=='pin' and m=='POST':
   mid=int(imsg.group(1)); x=c.execute('SELECT id FROM messages WHERE id=? AND channel_id=?',(mid,cid)).fetchone()
   if not x:return self.json(404,{})
   c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],now()));c.commit(); z=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone();return self.json(200,{'pin':dict(z)})
  if imsg and imsg.group(2)=='pin' and m=='DELETE':
   c.execute('DELETE FROM pins WHERE message_id=?',(int(imsg.group(1)),));c.commit();return self.json(200,{'unpinned':True})
  if sub=='pins' and m=='GET':
   lim=int(q.get('limit',['50'])[0]); cur=q.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.json(400,{})
   args=[cid]; sql='SELECT p.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'
   if cur:
    if not cur.isdigit():return self.json(400,{})
    sql+=' AND p.message_id<?'; args.append(int(cur))
   sql+=' ORDER BY p.message_id DESC LIMIT ?'; args.append(lim+1); rows=c.execute(sql,args).fetchall(); more=len(rows)>lim; rows=rows[:lim]
   return self.json(200,{'pins':[{'message':msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(x['message_id'],)).fetchone()),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':str(rows[-1]['message_id']) if more and rows else None})
  if sub=='read':
   x=c.execute('SELECT last_read FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); last=x['last_read'] if x else 0
   if m=='POST': last=max(last,int(b.get('last_read_event_id',0)));c.execute('INSERT OR REPLACE INTO reads VALUES(?,?,?)',(u['id'],cid,last));c.commit()
   mc=0
   for mr in c.execute('SELECT mentions FROM messages WHERE channel_id=? AND deleted=0',(cid,)):
    try:
     if u['id'] in json.loads(mr['mentions'] or '[]'): mc+=1
    except: pass
   return self.json(200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':0,'mention_count':mc}})
  self.json(404,{})
 def websocket(self,q):
  tok=q.get('token',[''])[0]; c=db(); r=c.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone();c.close()
  if not r:return self.send_error(401)
  key=self.headers.get('Sec-WebSocket-Key',''); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
  s=self.connection
  while True:
   h=s.recv(2)
   if not h:break
   n=h[1]&127; masked=h[1]&128
   if n==126:n=struct.unpack('>H',s.recv(2))[0]
   elif n==127:n=struct.unpack('>Q',s.recv(8))[0]
   mask=s.recv(4) if masked else b''; data=s.recv(n)
   if masked:data=bytes(data[i]^mask[i%4] for i in range(n))
   try:o=json.loads(data); cid=int(o.get('channel_id',0))
   except:continue
   if o.get('type')=='subscribe':
    with WSLOCK:CLIENTS.setdefault(cid,[]).append(s)
    c=db(); head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0];c.close();ws_send(s,{'type':'subscribed','channel_id':cid,'head_event_id':head})
   elif o.get('type')=='resume':
    c=db(); rows=c.execute('SELECT event_id,kind,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,int(o.get('since_event_id',0)))).fetchall();head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0];c.close()
    for x in rows:
     z={'type':x['kind'],'event_id':x['event_id'],'channel_id':cid};z.update(json.loads(x['payload']));ws_send(s,z)
    ws_send(s,{'type':'resumed','channel_id':cid,'head_event_id':head})
def event_poller():
 seen=set()
 try:
  c=db(); seen={(x['channel_id'],x['event_id']) for x in c.execute('SELECT channel_id,event_id FROM events')}; c.close()
 except: pass
 while True:
  try:
   c=db(); rows=c.execute('SELECT channel_id,event_id,kind,payload FROM events ORDER BY channel_id,event_id').fetchall(); c.close()
   for x in rows:
    key=(x['channel_id'],x['event_id'])
    if key in seen: continue
    seen.add(key); z={'type':x['kind'],'event_id':x['event_id'],'channel_id':x['channel_id']}; z.update(json.loads(x['payload'])); broadcast(x['channel_id'],z)
   if len(seen)>50000: seen=set(list(seen)[-20000:])
  except: pass
  time.sleep(.03)
if __name__=='__main__':
 init()
 threading.Thread(target=event_poller,daemon=True).start()
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
