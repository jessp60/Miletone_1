import os,sys,json,re,sqlite3,secrets,hashlib,base64,threading,time,urllib.parse,datetime,struct,socket
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True)
NODE=int(os.environ.get('NODE_ID','0')); PORT=8000+NODE
lock=threading.RLock()
def db():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=3000'); return c
def init():
 c=db(); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires INTEGER,max_uses INTEGER,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,UNIQUE(channel_id,event_id));
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_event INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
''');
 try: c.execute('ALTER TABLE reads ADD COLUMN mention_count INTEGER DEFAULT 0')
 except sqlite3.OperationalError: pass
 c.commit(); c.close()
init()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def js(x): return json.dumps(x,separators=(',',':'))
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(h):
 m=re.search(r'Bearer\s+(.+)',h or '')
 if not m:return None
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(m.group(1),)).fetchone(); c.close(); return r
def out(o): return o
def err(h,n,msg):
 b=js({'error':msg}).encode(); h.send_response(n); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.send_header('Connection','close'); h.end_headers(); h.wfile.write(b)
def channel(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def members(c,wid): return [dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(wid,))]
def permitted(c,uid,ch):
 r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
 if not r:return None,None
 m=c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone()
 return r,bool(m)
def add_event(c,ch,kind,payload):
 n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?',(ch,)).fetchone()[0]
 c.execute('INSERT INTO events(channel_id,event_id,kind,payload) VALUES(?,?,?,?)',(ch,n,kind,js(payload))); return n
def msgobj(c,r):
 u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 reacts=[]
 for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
  reacts.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]})
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reacts,'mentions':[]}
def cursorval(q,total=0):
 try: lim=int(q.get('limit',['50'])[0])
 except: return 'bad',None
 if lim<1 or lim>200: return 'bad',None
 cur=q.get('cursor',[None])[0]
 if cur:
  try:
   i=int(base64.urlsafe_b64decode(cur+'=='))
   if i<0: return 'bad',None
  except: return 'bad',None
 else:i=0
 return lim,i
def page(rows,lim,i):
 if i:
  pos=next((n for n,x in enumerate(rows) if (x.get('id') if isinstance(x,dict) else None)==i or (isinstance(x,dict) and isinstance(x.get('message'),dict) and x['message'].get('id')==i)),None)
  if pos is not None: i=pos+1
 total=len(rows); out=rows[i:i+lim]
 if not out:return out,None
 marker=out[-1].get('id') if isinstance(out[-1],dict) else None
 if marker is None and isinstance(out[-1],dict) and isinstance(out[-1].get('message'),dict): marker=out[-1]['message'].get('id')
 nc=base64.urlsafe_b64encode(str(marker).encode()).decode().rstrip('=') if i+lim<total and marker is not None else None
 return out,nc
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def read(self):
  n=int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n) or '{}')
 def send(self,n,o=None,raw=None,ctype='application/json'):
  b=raw if raw is not None else js(o).encode(); self.send_response(n); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_GET(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query)
  if path=='/api/health':return self.send(200,{'status':'ok','node_id':NODE})
  if path.startswith('/api/ws'): return self.websocket(q.get('token',[''])[0])
  u=auth(self.headers.get('Authorization')); c=db()
  if path=='/api/auth/me':
   if not u:return err(self,401,'auth')
   return self.send(200,{'user':user(u)})
  if path.startswith('/api/users/'):
   try:r=c.execute('SELECT * FROM users WHERE id=?',(int(path.split('/')[-1]),)).fetchone()
   except:r=None
   return self.send(200,{'user':user(r)}) if r else err(self,404,'not found')
  if path=='/api/workspaces':
   if not u:return err(self,401,'auth')
   rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
   return self.send(200,{'workspaces':[dict(x) for x in rs]})
  if not u:return err(self,401,'auth')
  m=re.match(r'/api/workspaces/([^/]+)(.*)',path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return err(self,404,'workspace')
   tail=m.group(2)
   if tail=='/members':return self.send(200,{'members':members(c,w['id'])})
   if tail=='/invitations':
    if not self.role(c,u['id'],w['id']) in ('owner','admin'):return err(self,403,'forbidden')
    rs=c.execute('SELECT * FROM invites WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires=0 OR expires>?)',(w['id'],int(time.time()))).fetchall()
    return self.send(200,{'invitations':[dict(x) for x in rs]})
   if tail=='/groups': return self.send(200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],))]})
   if tail=='':
    inc=q.get('include_archived',['0'])[0] in ('1','true'); rs=c.execute('SELECT * FROM channels WHERE workspace_id=? '+('' if inc else 'AND is_archived=0'),(w['id'],)).fetchall(); mid=c.execute('SELECT channel_id,last_event FROM reads WHERE user_id=?',(u['id'],)).fetchall()
    return self.send(200,{'workspace':dict(w),'channels':[channel(x) for x in rs if not x['is_private'] or c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone()],'read_state':[{'channel_id':x['channel_id'],'last_read_event_id':x['last_event'],'unread_count':0,'mention_count':0} for x in mid]})
  m=re.match(r'/api/channels/(\d+)/members$',path)
  if m:
   cid=int(m[1]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not ch:return err(self,404,'channel')
   if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return err(self,403,'forbidden')
   rs=[user(x) for x in c.execute('SELECT u.* FROM users u JOIN cmembers cm ON cm.user_id=u.id WHERE cm.channel_id=?',(cid,))]
   return self.send(200,{'members':rs})
  if re.match(r'/api/channels/\d+/messages',path):
   cid=int(path.split('/')[3]); ch,ok=permitted(c,u['id'],cid)
   if not ch or not ok:return err(self,403,'forbidden')
   rs=[msgobj(c,x) for x in c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(cid,))]
   lim,i=cursorval(q); 
   if lim=='bad':return err(self,400,'cursor')
   rr,nc=page(rs,lim,i); return self.send(200,{'messages':rr,'next_cursor':nc})
  if re.match(r'/api/messages/\d+/replies',path):
   mid=int(path.split('/')[3]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   if not r:return err(self,404,'message')
   rs=[msgobj(c,x) for x in c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,))]; lim,i=cursorval(q)
   if lim=='bad':return err(self,400,'cursor')
   rr,nc=page(rs,lim,i); return self.send(200,{'replies':rr,'next_cursor':nc})
  if re.match(r'/api/channels/\d+/pins',path):
   cid=int(path.split('/')[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not ch:return err(self,404,'channel')
   if ch['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return err(self,403,'forbidden')
   rs=[]
   for pp in c.execute('SELECT message_id,pinned_by,pinned_at FROM pins WHERE message_id IN (SELECT id FROM messages WHERE channel_id=?) ORDER BY pinned_at DESC',(cid,)):
    mm=c.execute('SELECT * FROM messages WHERE id=?',(pp['message_id'],)).fetchone()
    rs.append({'message':msgobj(c,mm),'pinned_by':pp['pinned_by'],'pinned_at':pp['pinned_at']})
   lim,i=cursorval(q); rr,nc=page(rs,lim,i); return self.send(200,{'pins':rr,'next_cursor':nc})
  if path=='/api/search':
   term=q.get('q',[''])[0].lower(); wid=int(q['workspace_id'][0]) if q.get('workspace_id') else None
   sql='SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN cmembers cm ON cm.channel_id=ch.id AND cm.user_id=? WHERE m.deleted=0 AND lower(m.body) LIKE ?'; args=[u['id'],'%'+term+'%']
   if wid is not None: sql+=' AND ch.workspace_id=?'; args.append(wid)
   sql+=' ORDER BY m.id DESC'; rs=[msgobj(c,x) for x in c.execute(sql,args)]; lim,i=cursorval(q)
   if lim=='bad':return err(self,400,'cursor')
   rr,nc=page(rs,lim,i); return self.send(200,{'results':rr,'next_cursor':nc})
  if re.match(r'/api/channels/\d+/read',path):
   cid=int(path.split('/')[3]); r=c.execute('SELECT last_event,mention_count FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); last=r[0] if r else 0
   head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
   return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':max(0,head-last),'mention_count':(r[1] if r and len(r)>1 else 0)}})
  return err(self,404,'not found')
 def role(self,c,uid,wid): 
  r=c.execute('SELECT role FROM members WHERE user_id=? AND workspace_id=?',(uid,wid)).fetchone(); return r[0] if r else None
 def do_POST(self):
  p=urllib.parse.urlparse(self.path); path=p.path; b=self.read(); u=auth(self.headers.get('Authorization')); c=db()
  if path=='/api/auth/register':
   if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return err(self,400,'invalid')
   try:cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],b['password'],b.get('display_name',b['username']))); uid=cur.lastrowid; t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); return self.send(201,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':t})
   except sqlite3.IntegrityError:return err(self,409,'duplicate')
  if path=='/api/auth/login':
   r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password'))).fetchone()
   if not r:return err(self,401,'invalid')
   t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();return self.send(200,{'user':user(r),'token':t})
  if path.startswith('/api/invitations/') and path.endswith('/accept'):
   if not u:return err(self,401,'auth')
   code=path.split('/')[3]; x=c.execute('SELECT * FROM invites WHERE code=?',(code,)).fetchone()
   if not x or x['revoked'] or x['uses']>=x['max_uses'] or (x['expires'] and x['expires']<time.time()):return err(self,404,'invite')
   if x['invited_user_id'] and x['invited_user_id']!=u['id']:return err(self,403,'target')
   if x['invited_user_id'] and x['uses']>0:return err(self,404,'invite')
   c.execute('INSERT OR REPLACE INTO members VALUES(?,?,?)',(x['workspace_id'],u['id'],'member')); c.execute('UPDATE invites SET uses=uses+1 WHERE code=?',(code,));c.commit();return self.send(200,{'accepted':True})
  if not u:return err(self,401,'auth')
  m=re.match(r'/api/workspaces/([^/]+)/transfer_ownership$',path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return err(self,404,'workspace')
   if self.role(c,u['id'],w['id'])!='owner':return err(self,403,'owner')
   target=int(b.get('new_owner_id',0))
   if self.role(c,target,w['id'])!='admin':return err(self,400,'target')
   c.execute("UPDATE members SET role='admin' WHERE workspace_id=? AND user_id=?",(w['id'],u['id']))
   c.execute("UPDATE members SET role='owner' WHERE workspace_id=? AND user_id=?",(w['id'],target))
   c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id'])); c.commit()
   return self.send(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
  if path=='/api/workspaces':
   if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return err(self,400,'slug')
   try:cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id'])); wid=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));gid=cur.lastrowid;c.execute('INSERT INTO cmembers VALUES(?,?)',(gid,u['id']));c.commit();return self.send(201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone())})
   except sqlite3.IntegrityError:return err(self,409,'duplicate')
  m=re.match(r'/api/workspaces/([^/]+)(.*)',path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return err(self,404,'workspace')
   tail=m.group(2); role=self.role(c,u['id'],w['id'])
   if tail=='/channels':
    if role not in ('owner','admin','member') or (b.get('is_private') and role not in ('owner','admin')):return err(self,403,'forbidden')
    name=b.get('name','')
    if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or len(b.get('topic',''))>250:return err(self,400,'invalid')
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():return err(self,409,'duplicate')
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(b.get('is_private'))),b.get('topic','')));cid=cur.lastrowid;c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit(); chrow=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); return self.send(201,{'channel':channel(chrow)})
   if tail=='/invitations':
    if role not in ('owner','admin'):return err(self,403,'forbidden')
    x=b.get('expires_in_seconds',0); code=secrets.token_urlsafe(12); inv=b.get('invited_user_id'); c.execute('INSERT INTO invites VALUES(?,?,?,?,?,?,?)',(code,w['id'],inv,int(time.time()+x) if x else 0,b.get('max_uses',1),0,0));c.commit();return self.send(201,{'invitation':dict(c.execute('SELECT * FROM invites WHERE code=?',(code,)).fetchone())})
   if tail=='/groups':
    cur=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],b['handle'],b.get('name',''),u['id'],js(b.get('user_ids',[]))));c.commit();return self.send(201,{'group':{'id':cur.lastrowid,'handle':b['handle'],'name':b.get('name',''),'member_user_ids':b.get('user_ids',[])}})
  m=re.match(r'/api/channels/(\d+)/(join|leave|archive|unarchive|read)',path)
  if m:
   cid=int(m.group(1));ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
   if not ch:return err(self,404,'channel')
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); role=self.role(c,u['id'],w['id'])
   if m.group(2)=='join':
    if ch['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return err(self,403,'private')
    if role=='guest':return err(self,403,'guest')
    if not role and w['join_mode']=='invite_only':return err(self,403,'invite')
    if not role:c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
    c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,u['id'])); add_event(c,cid,'member.joined',{'user_id':u['id']}); c.commit();return self.send(200,{'channel':channel(ch)})
   if m.group(2)=='read':
    old=c.execute('SELECT last_event FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); val=max(old[0] if old else 0,int(b.get('last_read_event_id',0))); c.execute('INSERT OR REPLACE INTO reads(user_id,channel_id,last_event,mention_count) VALUES(?,?,?,COALESCE((SELECT mention_count FROM reads WHERE user_id=? AND channel_id=?),0))',(u['id'],cid,val,u['id'],cid)); c.commit(); return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':val,'unread_count':0,'mention_count':0}})
   if m.group(2)=='leave':c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])); add_event(c,cid,'member.left',{'user_id':u['id']}); c.commit();return self.send(200,{'channel':channel(ch)})
   if m.group(2) not in ('archive','unarchive'): return err(self,400,'invalid')
   if role!='owner':return err(self,403,'owner')
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if m.group(2)=='archive' else 0,cid)); add_event(c,cid,'channel.archived' if m.group(2)=='archive' else 'channel.unarchived',{}); c.commit();return self.send(200,{'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if m.group(2)=='read':
    old=c.execute('SELECT last_event FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); val=max(old[0] if old else 0,int(b.get('last_read_event_id',0)));c.execute('INSERT OR REPLACE INTO reads(user_id,channel_id,last_event,mention_count) VALUES(?,?,?,COALESCE((SELECT mention_count FROM reads WHERE user_id=? AND channel_id=?),0))',(u['id'],cid,val,u['id'],cid));c.commit();return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':val,'unread_count':0,'mention_count':0}})
  if re.match(r'/api/channels/\d+/messages',path):
   cid=int(path.split('/')[3]);ch,ok=permitted(c,u['id'],cid)
   if not ch or not ok:return err(self,403,'member')
   if ch['is_archived']:return err(self,423,'archived')
   body=b.get('body','')
   if not body:return err(self,400,'empty')
   if body.startswith('/') and not body.startswith('//') and not body.startswith('/me '):return err(self,400,'command')
   if body.startswith('/shrug'):body=body[6:].strip()+' ¯\\_(ツ)_/¯'
   t=now();cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,b.get('parent_id'),t));mid=cur.lastrowid;r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();mo=msgobj(c,r)
   ids=[]
   for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',body):
    if name=='channel': ids += [x[0] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id<>?',(cid,u['id']))]
    else:
     x=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
     if x and x[0]!=u['id'] and c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,x[0])).fetchone(): ids.append(x[0])
   mo['mentions']=list(dict.fromkeys(ids))
   for midu in mo['mentions']:
    c.execute('INSERT OR IGNORE INTO reads(user_id,channel_id,last_event,mention_count) VALUES(?,?,0,0)',(midu,cid)); c.execute('UPDATE reads SET mention_count=mention_count+1 WHERE user_id=? AND channel_id=?',(midu,cid))
   ev=add_event(c,cid,'message.reply' if b.get('parent_id') else 'message.created',mo);c.commit();return self.send(201,{'message':mo})
  if re.match(r'/api/messages/\d+/pin',path):
   mid=int(path.split('/')[3]);r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
   if not r:return err(self,404,'message')
   c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],now()));c.commit();return self.send(200,{'pin':{'message_id':mid,'pinned_by':u['id'],'pinned_at':now()}})
  return err(self,404,'not found')
 def do_PATCH(self):
  p=self.path.split('?')[0]; b=self.read();u=auth(self.headers.get('Authorization'));c=db()
  if not u:return err(self,401,'auth')
  m=re.match(r'/api/users/me',p)
  if m:
   vals={k:b[k] for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in b}; c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',list(vals.values())+[u['id']]);c.commit();return self.send(200,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
  m=re.match(r'/api/workspaces/([^/]+)$',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m[1],)).fetchone()
   if not w:return err(self,404,'workspace')
   role=self.role(c,u['id'],w['id'])
   if role not in ('owner','admin'):return err(self,403,'forbidden')
   if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return err(self,400,'join_mode')
   fields=[]; vals=[]
   for k in ('name','join_mode'):
    if k in b: fields.append(k+'=?'); vals.append(b[k])
   if fields:
    c.execute('UPDATE workspaces SET '+','.join(fields)+' WHERE id=?',vals+[w['id']]); c.commit()
   return self.send(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
  m=re.match(r'/api/workspaces/([^/]+)/members/(\d+)',p)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m[1],)).fetchone(); role=self.role(c,u['id'],w['id']); target=int(m[2])
   if b.get('role') not in ('owner','admin','member','guest'):return err(self,400,'role')
   if b.get('role')=='owner':return err(self,400,'role')
   if role not in ('owner','admin'):return err(self,403,'forbidden')
   tr=self.role(c,target,w['id'])
   if role=='admin' and tr in ('owner','admin'):return err(self,403,'forbidden')
   if target==w['owner_id']:return err(self,403,'owner')
   c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],target));c.commit();return self.send(200,{'member':{'user_id':target,'role':b['role']}})
  m=re.match(r'/api/channels/(\d+)',p)
  if m:
   cid=int(m[1]);ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
   if self.role(c,u['id'],w['id']) not in ('owner','admin') and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return err(self,403,'forbidden')
   if len(b.get('topic',''))>250:return err(self,400,'topic')
   c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),cid)); add_event(c,cid,'channel.updated',{'topic':b.get('topic','')}); c.commit(); chrow=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); return self.send(200,{'channel':channel(chrow)})
  return err(self,404,'not found')
 def do_DELETE(self):
  u=auth(self.headers.get('Authorization'));c=db()
  if not u:return err(self,401,'auth')
  m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',self.path)
  if m:
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m[1],)).fetchone()
   if not w:return err(self,404,'workspace')
   if self.role(c,u['id'],w['id']) not in ('owner','admin'):return err(self,403,'forbidden')
   c.execute('UPDATE invites SET revoked=1 WHERE workspace_id=? AND code=?',(w['id'],m[2])); c.commit()
   return self.send(200,{'deleted':True})
  m=re.match(r'/api/messages/(\d+)/pin',self.path)
  if m:c.execute('DELETE FROM pins WHERE message_id=?',(int(m[1]),));c.commit();return self.send(200,{'unpinned':True})
  return err(self,404,'not found')
 def websocket(self,token):
  c=db();r=c.execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone()
  if not r:self.send_response(401);self.end_headers();return
  key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  def recv():
   h=self.rfile.read(2)
   if len(h)<2:return None
   ln=h[1]&127
   if ln==126:ln=struct.unpack('>H',self.rfile.read(2))[0]
   elif ln==127:ln=struct.unpack('>Q',self.rfile.read(8))[0]
   mask=self.rfile.read(4);data=self.rfile.read(ln);return json.loads(bytes(data[i]^mask[i%4] for i in range(ln)).decode())
  def sendf(x):
   b=js(x).encode(); n=len(b)
   if n<126: h=bytes([129,n])
   elif n<65536: h=bytes([129,126])+struct.pack('>H',n)
   else: h=bytes([129,127])+struct.pack('>Q',n)
   self.wfile.write(h+b);self.wfile.flush()
  self.connection.settimeout(0.01); sub=None; last=0
  import select
  while True:
   try:
    ready,_,_=select.select([self.connection],[],[],0.05)
    if ready:
     x=recv()
     if x:
      sub=x.get('channel_id'); last=x.get('since_event_id',0)
      c=db()
      if x['type']=='subscribe':
       head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(sub,)).fetchone()[0]
       sendf({'type':'subscribed','channel_id':sub,'head_event_id':head})
      elif x['type']=='resume':
       for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(sub,last)):
        sendf({'type':e['kind'],'event_id':e['event_id'],'channel_id':sub,**json.loads(e['payload'])})
       head=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(sub,)).fetchone()[0]
       sendf({'type':'resumed','channel_id':sub,'head_event_id':head}); last=head
    if sub:
     c=db()
     events=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(sub,last)).fetchall()
     for e in events:
      sendf({'type':e['kind'],'event_id':e['event_id'],'channel_id':sub,**json.loads(e['payload'])}); last=e['event_id']
   except (socket.timeout,BlockingIOError): pass
   except Exception: return

if __name__=='__main__':
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
