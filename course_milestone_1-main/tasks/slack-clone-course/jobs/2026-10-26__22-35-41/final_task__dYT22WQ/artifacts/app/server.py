import sys,os,json,re,sqlite3,secrets,hashlib,datetime,urllib.parse,threading,base64,struct
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row; return c
def init():
 c=db(); c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,expires INTEGER,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0,target_id INTEGER);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,user_id INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
'''); c.commit(); c.close()
def userobj(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def auth(h):
 x=h.get('Authorization','')
 if not x.startswith('Bearer '): return None
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
def js(h,o,status=200):
 b=json.dumps(o,separators=(',',':')).encode(); h.send_response(status); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def route(self):
  return urllib.parse.urlparse(self.path).path.split('/'),urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
 def need(self):
  u=auth(self.headers)
  if not u: js(self,{'error':'unauthorized'},401); return None
  return u
 def member(self,c,w,u):
  return c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone()
 def send_user(self,uid):
  c=db(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return userobj(r) if r else None
 def do_GET(self):
  p,q=self.route()
  if p[:3]==['','api','health']: js(self,{'status':'ok','node_id':int(self.server.server_port)-8000}); return
  if p[:4]==['','api','auth','me']:
   u=self.need()
   if u: js(self,{'user':userobj(u)})
   return
  u=self.need()
  if not u:return
  c=db()
  try:
   if p[:3]==['','api','workspaces'] and len(p)==3:
    rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
    js(self,{'workspaces':[dict(x) for x in rows]}); return
   if p[:3]==['','api','users'] and len(p)==4:
    r=c.execute('SELECT * FROM users WHERE id=?',(p[3],)).fetchone()
    if not r: js(self,{'error':'not found'},404)
    else: js(self,{'user':userobj(r)})
    return
   if len(p)>=5 and p[1:3]==['api','workspaces']:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w: js(self,{'error':'not found'},404); return
    if p[4]=='members':
     rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall(); js(self,{'members':[dict(x) for x in rows]}); return
    if p[4]=='invitations':
     m=self.member(c,w['id'],u['id'])
     if not m or m['role'] not in ('owner','admin'): js(self,{'error':'forbidden'},403); return
     rows=c.execute('SELECT * FROM invites WHERE workspace_id=? AND revoked=0 AND uses<max_uses',(w['id'],)).fetchall()
     js(self,{'invitations':[dict(x) for x in rows]}); return
    if p[4]=='groups':
     rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall()
     js(self,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in rows]}); return
   if len(p)>=4 and p[1:3]==['api','workspaces']:
    pass
   if p[:3]==['','api','channels'] and len(p)==4:
    ch=c.execute('SELECT * FROM channels WHERE id=?',(p[3],)).fetchone()
    if not ch: js(self,{'error':'not found'},404); return
    if p[4:] and p[4]=='messages': pass
   if len(p)>=5 and p[1]=='api' and p[2]=='channels':
    cid=int(p[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch: js(self,{'error':'not found'},404); return
    if p[4]=='members':
     rows=c.execute('SELECT u.* FROM cmembers m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(cid,)).fetchall(); js(self,{'members':[userobj(x) for x in rows]}); return
    if p[4]=='messages':
     rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(cid,)).fetchall()
     js(self,{'messages':[self.msg(c,x) for x in rows],'next_cursor':None}); return
    if p[4]=='pins':
     rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC',(cid,)).fetchall()
     js(self,{'pins':[{'message':self.msg(c,x),'pinned_by':x['user_id'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':None}); return
    if p[4]=='read':
     r=c.execute('SELECT last_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); n=r['last_event_id'] if r else 0; js(self,{'read_state':{'channel_id':cid,'last_read_event_id':n,'unread_count':0,'mention_count':0}}); return
   if len(p)>=4 and p[1:3]==['api','messages']:
    mid=int(p[3]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if r and len(p)>4 and p[4]=='replies':
     rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall(); js(self,{'replies':[self.msg(c,x) for x in rows],'next_cursor':None}); return
  finally:c.close()
  js(self,{'error':'not found'},404)
 def chobj(self,r):
  d=dict(r)
  for k in ('is_private','is_dm','is_archived'): d[k]=bool(d[k])
  return d
 def msg(self,c,r):
  au=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
  rr=c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
  return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(au),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[]} for x in rr],'mentions':[],'event_id':r['event_id']}
 def do_POST(self):
  p,q=self.route()
  if p[:4]==['','api','auth','register']:
   try:d=self.body(); un=d['username']; pw=d['password']
   except: js(self,{'error':'bad'},400);return
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8: js(self,{'error':'invalid'},400);return
   c=db()
   try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,pw,d.get('display_name',un))); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));c.commit(); js(self,{'user':userobj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':t},201)
   except sqlite3.IntegrityError: js(self,{'error':'duplicate'},409)
   finally:c.close()
   return
  if p[:4]==['','api','auth','login']:
   d=self.body();c=db();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),d.get('password'))).fetchone()
   if not r: js(self,{'error':'unauthorized'},401)
   else:t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();js(self,{'user':userobj(r),'token':t})
   c.close();return
  u=self.need()
  if not u:return
  c=db(); d=self.body()
  try:
   if p[:3]==['','api','workspaces'] and len(p)==3:
    if not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')): js(self,{'error':'invalid'},400);return
    try:c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(gid,u['id']));c.commit();w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();js(self,{'workspace':dict(w),'general_channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone())},201)
    except sqlite3.IntegrityError:js(self,{'error':'duplicate'},409)
    return
   if len(p)>=5 and p[1:3]==['api','workspaces'] and p[4]=='channels':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone();m=self.member(c,w['id'],u['id']) if w else None
    if not w:js(self,{},404);return
    if not m or m['role']=='guest' or (d.get('is_private') and m['role'] not in ('owner','admin')):js(self,{},403);return
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d.get('name'))).fetchone():js(self,{},409);return
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic','')));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();js(self,{'channel':self.chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201);return
   if p[:3]==['','api','workspaces'] and len(p)==5 and p[4]=='invitations':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone(); m=self.member(c,w['id'],u['id']) if w else None
    if not w: js(self,{},404); return
    if not m or m['role'] not in ('owner','admin'): js(self,{},403); return
    code=secrets.token_urlsafe(16); exp=int(datetime.datetime.now().timestamp())+int(d.get('expires_in_seconds',86400))
    target=d.get('invited_user_id')
    c.execute('INSERT INTO invites(code,workspace_id,creator_id,expires,max_uses,target_id) VALUES(?,?,?,?,?,?)',(code,w['id'],u['id'],exp,int(d.get('max_uses',1)),target)); c.commit()
    js(self,{'invitation':dict(c.execute('SELECT * FROM invites WHERE code=?',(code,)).fetchone())},201); return
   if p[:3]==['','api','invitations'] and len(p)==5 and p[4]=='accept':
    inv=c.execute('SELECT * FROM invites WHERE code=?',(p[3],)).fetchone()
    if not inv or inv['revoked'] or inv['uses'] >= (1 if inv['target_id'] is not None else inv['max_uses']) or inv['expires'] < int(datetime.datetime.now().timestamp()): js(self,{},404); return
    if inv['target_id'] is not None and int(inv['target_id'])!=u['id']: js(self,{},403); return
    if not self.member(c,inv['workspace_id'],u['id']): c.execute('INSERT INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'))
    c.execute('UPDATE invites SET uses=uses+1 WHERE code=?',(p[3],)); c.commit(); js(self,{'accepted':True}); return
   if p[:3]==['','api','workspaces'] and len(p)==5 and p[4]=='transfer_ownership':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone(); target=d.get('new_owner_id')
    if not w: js(self,{},404); return
    if u['id']!=w['owner_id']: js(self,{},403); return
    tm=self.member(c,w['id'],target)
    if not tm or tm['role']!='admin': js(self,{},400); return
    c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
    c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
    c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id'])); c.commit(); js(self,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())}); return
   if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='join':
    ch=c.execute('SELECT * FROM channels WHERE id=?',(p[3],)).fetchone()
    if not ch:js(self,{},404);return
    m=self.member(c,ch['workspace_id'],u['id'])
    if ch['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():js(self,{},403);return
    if m and m['role']=='guest':js(self,{},403);return
    if not m:
     w=c.execute('SELECT join_mode FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
     if w['join_mode']!='open':js(self,{},403);return
     c.execute('INSERT INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
    c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(ch['id'],u['id']));c.commit();js(self,{'channel':self.chobj(ch)});return
   if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='messages':
    cid=int(p[3]);ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();m=c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
    if not ch:js(self,{},404);return
    if not m:js(self,{},403);return
    if ch['is_archived']:js(self,{},423);return
    body=d.get('body','')
    if not body:js(self,{},400);return
    eid=(c.execute('SELECT coalesce(max(event_id),0)+1 n FROM messages WHERE channel_id=?',(cid,)).fetchone()['n'])
    c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,event_id) VALUES(?,?,?,?,?,?)',(cid,u['id'],body,d.get('parent_id'),now(),eid));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit();js(self,{'message':self.msg(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())},201);return
  finally:c.close()
  js(self,{},404)
 def do_DELETE(self):
  p,q=self.route(); u=self.need()
  if not u:return
  c=db()
  try:
   if len(p)==6 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone(); m=self.member(c,w['id'],u['id']) if w else None
    if not w: js(self,{},404); return
    if not m or m['role'] not in ('owner','admin'): js(self,{},403); return
    c.execute('UPDATE invites SET revoked=1 WHERE code=? AND workspace_id=?',(p[5],w['id'])); c.commit(); js(self,{'deleted':True}); return
  finally:c.close()
  js(self,{},404)
 def do_PATCH(self):
  p,q=self.route();u=self.need()
  if not u:return
  c=db();d=self.body()
  try:
   if p[:3]==['','api','users'] and len(p)==4:
    vals={k:d[k] for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in d}; 
    if vals:c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),u['id']));c.commit()
    js(self,{'user':userobj(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())});return
   if len(p)>=6 and p[1:3]==['api','workspaces'] and p[4]=='members':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone(); me=self.member(c,w['id'],u['id']); role=d.get('role')
    if role not in ('owner','admin','member','guest') or role=='owner':js(self,{},400);return
    target=self.member(c,w['id'],p[5])
    if not me or me['role'] not in ('owner','admin') or not target or p[5]==str(w['owner_id']) or (me['role']=='admin' and target['role']=='admin'):js(self,{},403);return
    c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],p[5]));c.commit();js(self,{'member':{'user_id':int(p[5]),'role':role}});return
   if len(p)==4 and p[1:3]==['api','workspaces']:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone();m=self.member(c,w['id'],u['id'])
    if not m or m['role'] not in ('owner','admin'):js(self,{},403);return
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):js(self,{},400);return
    c.execute('UPDATE workspaces SET '+','.join(k+'=?' for k in d if k in ('name','join_mode'))+' WHERE id=?',(*(d[k] for k in d if k in ('name','join_mode')),w['id']));c.commit();js(self,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())});return
  finally:c.close()
  js(self,{},404)

# Component B compatibility extensions
_old_get=H.do_GET
_old_post=H.do_POST
_old_patch=H.do_PATCH
_old_delete=getattr(H,'do_DELETE',None)

def _b_get(self):
 p,q=self.route()
 u=auth(self.headers)
 if u:
  c=db()
  try:
   if len(p)==4 and p[1:3]==['api','workspaces']:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w: js(self,{},404); return
    m=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
    if not m: js(self,{},403); return
    chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall()
    rs=[]
    for ch in chans:
     if ch['is_private'] and not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(): continue
     rs.append(self.chobj(ch))
    js(self,{'workspace':dict(w),'channels':rs,'read_state':[]}); return
   if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='messages':
    cid=int(p[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch: js(self,{},404); return
    if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(): js(self,{},403); return
    rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(cid,)).fetchall()
    js(self,{'messages':[self.msg(c,x) for x in rows],'next_cursor':None}); return
   if len(p)==5 and p[1:3]==['api','messages'] and p[4]=='replies':
    mid=int(p[3]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r: js(self,{},404); return
    rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall()
    js(self,{'replies':[self.msg(c,x) for x in rows],'next_cursor':None}); return
   if len(p)==3 and p[1:3]==['api','search']:
    term=q.get('q',[''])[0]; rows=c.execute("SELECT * FROM messages WHERE deleted=0 AND body LIKE ? ORDER BY id DESC LIMIT 200",('%'+term+'%',)).fetchall()
    js(self,{'results':[self.msg(c,x) for x in rows],'next_cursor':None}); return
  finally: c.close()
 _old_get(self)

def _b_post(self):
 p,q=self.route()
 if p[:3]==['','api','messages'] and len(p)==5 and p[4] in ('pin','reactions'):
  u=self.need()
  if not u:return
  c=db(); mid=int(p[3]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not r: js(self,{},404); c.close(); return
  if p[4]=='pin':
   c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],now())); c.commit()
   js(self,{'pin':{'message_id':mid,'pinned_by':u['id'],'pinned_at':c.execute('SELECT pinned_at FROM pins WHERE message_id=?',(mid,)).fetchone()['pinned_at']}}); c.close(); return
  d=self.body(); emoji=d.get('emoji','')
  if not emoji: js(self,{},400); c.close(); return
  c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji)); c.commit()
  rr=c.execute('SELECT emoji,count(*) n FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall()
  js(self,{'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[]} for x in rr]}); c.close(); return
 if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='read':
  u=self.need()
  if not u:return
  c=db(); d=self.body(); cid=int(p[3]); val=int(d.get('last_read_event_id',0))
  old=c.execute('SELECT last_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
  val=max(val,old['last_event_id'] if old else 0)
  c.execute('INSERT OR REPLACE INTO reads VALUES(?,?,?)',(u['id'],cid,val)); c.commit()
  js(self,{'read_state':{'channel_id':cid,'last_read_event_id':val,'unread_count':0,'mention_count':0}}); c.close(); return
 _old_post(self)

def _b_patch(self):
 p,q=self.route()
 if p[:3]==['','api','messages'] and len(p)==4:
  u=self.need()
  if not u:return
  c=db(); d=self.body(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[3],)).fetchone()
  if not r: js(self,{},404); c.close(); return
  if r['author_id']!=u['id']: js(self,{},403); c.close(); return
  if not d.get('body'): js(self,{},400); c.close(); return
  c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],now(),p[3])); c.commit()
  js(self,{'message':self.msg(c,c.execute('SELECT * FROM messages WHERE id=?',(p[3],)).fetchone())}); c.close(); return
 if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='settings':
  pass
 _old_patch(self)

def _b_delete(self):
 p,q=self.route(); u=auth(self.headers)
 if u and len(p)==5 and p[1:3]==['api','messages'] and p[4]=='pin':
  c=db(); c.execute('DELETE FROM pins WHERE message_id=?',(p[3],)); c.commit(); c.close(); js(self,{'unpinned':True}); return
 if u and len(p)==6 and p[1:3]==['api','messages'] and p[4]=='reactions':
  c=db(); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(p[3],u['id'],urllib.parse.unquote(p[5]))); c.commit(); c.close(); js(self,{'reactions':[]}); return
 if u and len(p)==4 and p[1:3]==['api','messages']:
  c=db(); r=c.execute('SELECT * FROM messages WHERE id=?',(p[3],)).fetchone()
  if not r: js(self,{},404); c.close(); return
  if r['author_id']!=u['id']: js(self,{},403); c.close(); return
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(p[3],)); c.commit(); c.close(); js(self,{'deleted':True}); return
 if _old_delete: _old_delete(self)
 else: js(self,{},404)

H.do_GET=_b_get
H.do_POST=_b_post
H.do_PATCH=_b_patch
H.do_DELETE=_b_delete

UI='''<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>
*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#1d1c1d;background:#fff}.app{display:flex;height:100vh}.side{width:250px;background:#3f0e40;color:white;padding:16px}.main{flex:1;display:flex;flex-direction:column}.messages{flex:1;padding:24px;overflow:auto}.thread{width:330px;border-left:1px solid #ddd;padding:16px}.channel-entry{display:block;color:#ddd;padding:8px;border:0;background:none;text-align:left;width:100%;cursor:pointer}.channel-entry:before{content:"# "}.top{height:64px;border-bottom:1px solid #ddd;padding:16px;font-size:18px}.row{padding:10px;border-bottom:1px solid #eee}.avatar{display:inline-block;width:30px;height:30px;border-radius:50%;background:#1264a3;color:#fff;text-align:center;padding-top:7px;margin-right:8px}.compose{display:flex;padding:16px;gap:8px}.compose input{flex:1;padding:12px}button{border:0;border-radius:4px;padding:9px 14px;cursor:pointer}.primary{background:#007a5a;color:#fff}.danger{background:#e01e5a;color:#fff}.secondary{background:#1264a3;color:#fff}.modal{position:fixed;inset:0;background:#0008;display:none;align-items:center;justify-content:center}.card{background:white;padding:24px;border-radius:8px;min-width:340px}input,select{padding:9px;margin:5px 0;width:100%}.hidden{display:none}.reaction-chip{display:inline-block;background:#eee;border-radius:12px;padding:3px 8px;margin:3px}
</style></head><body><div id="auth-modal" class="modal" style="display:flex"><div class="card"><h2>Huddle</h2><form id="auth-form" data-testid="auth-form"><input name="username" placeholder="Username" required><input name="password" type="password" placeholder="Password" required><button id="auth-submit" data-testid="auth-submit" data-button-role="primary" class="primary">Sign in</button></form><button id="auth-toggle" data-testid="auth-toggle" class="secondary" data-button-role="secondary">Create account</button><div id="auth-error"></div></div></div>
<div class="app"><aside class="side"><h2 id="workspace-header" data-testid="workspace-header">Huddle</h2><div data-testid="current-user" id="current-user"></div><div id="channel-list" data-testid="channel-list"></div><button id="new-channel-btn" data-testid="new-channel-btn" data-button-role="secondary" class="secondary">New channel</button><button id="logout-btn" data-testid="logout-btn" data-button-role="danger" class="danger">Logout</button><div id="dms-list" data-testid="dms-list"></div></aside><main class="main"><header class="top"><span id="channel-title" data-testid="channel-title"># general</span><small id="channel-topic" data-testid="channel-topic"></small></header><section id="message-list" data-testid="message-list" class="messages"><div id="empty-state">No messages yet — start the conversation.</div></section><form class="compose"><input id="message-input" data-testid="message-input" name="message" placeholder="Message"><button id="send-btn" data-testid="send-btn" data-button-role="primary" class="primary">Send</button></form></main><aside id="thread-panel" data-testid="thread-panel" class="thread hidden"><button id="close-thread" data-testid="close-thread" data-button-role="secondary" class="secondary">Close</button><h3>Thread</h3><div id="thread-messages"></div><form><input id="thread-input" data-testid="thread-input"><button id="thread-send" data-testid="thread-send" data-button-role="primary" class="primary">Reply</button></form></aside></div>
<div id="create-channel-modal" class="modal"><div class="card"><form id="create-channel-form" data-testid="create-channel-form"><input name="name"><label><input type="checkbox" id="create-channel-private" data-testid="create-channel-private"> Private</label><button id="create-channel-submit" data-testid="create-channel-submit" data-button-role="primary" class="primary">Create</button><button type="button" id="create-channel-cancel" data-testid="create-channel-cancel" data-button-role="secondary" class="secondary">Cancel</button></form></div></div>
<div id="channel-settings-modal" class="modal"><div class="card"><h3 id="channel-settings-title" data-testid="channel-settings-title">Channel settings</h3><button id="channel-settings-close" data-testid="channel-settings-close" data-button-role="secondary" class="secondary">Close</button><input id="channel-topic-input" data-testid="channel-topic-input"><button id="channel-topic-submit" data-testid="channel-topic-submit" data-button-role="primary" class="primary">Save topic</button><button id="archive-channel-btn" data-testid="archive-channel-btn" data-button-role="danger" class="danger">Archive</button><button id="unarchive-channel-btn" data-testid="unarchive-channel-btn" data-button-role="secondary" class="secondary">Unarchive</button><div id="channel-members-list" data-testid="channel-members-list"></div></div></div>
<div id="workspace-settings-modal" class="modal"><div class="card"><button id="workspace-settings-close" data-testid="workspace-settings-close" data-button-role="secondary" class="secondary">Close</button><input id="workspace-name-input" data-testid="workspace-name-input"><select id="workspace-join-mode" data-testid="workspace-join-mode"><option>open</option><option>invite_only</option></select><button id="workspace-general-submit" data-testid="workspace-general-submit" data-button-role="primary" class="primary">Save</button><div id="settings-pane-members" data-testid="settings-pane-members"></div><div id="settings-pane-invitations" data-testid="settings-pane-invitations"></div><div id="settings-pane-general" data-testid="settings-pane-general"></div></div></div>
<script>
const $=x=>document.querySelector(x), token=()=>localStorage.getItem('huddle.token');let channel=null,ws=null;
async function api(path,opt={}){opt.headers={...(opt.headers||{}),'Content-Type':'application/json',...(token()?{Authorization:'Bearer '+token()}: {})};let r=await fetch('/api'+path,opt);let j=await r.json().catch(()=>({}));if(!r.ok)throw j;return j}
async function load(){try{let m=await api('/auth/me');$('#auth-modal').style.display='none';$('#current-user').textContent=m.user.display_name||m.user.username;let x=await api('/workspaces');if(x.workspaces[0]){let d=await api('/workspaces/'+x.workspaces[0].slug);$('#workspace-header').textContent=d.workspace.name;$('#channel-list').innerHTML=d.channels.map(c=>'<button class="channel-entry" data-testid="channel-entry" data-channel-id="'+c.id+'" data-channel-name="'+c.name+'">'+c.name+'</button>').join('');document.querySelectorAll('.channel-entry').forEach(e=>e.onclick=()=>select(e.dataset.channelId,e.dataset.channelName));if(d.channels[0])select(d.channels[0].id,d.channels[0].name)}}catch(e){}}
async function select(id,name){channel=id;$('#channel-title').textContent='# '+name;let x=await api('/channels/'+id+'/messages');$('#message-list').innerHTML=x.messages.length?x.messages.map(m=>'<div class="row" data-testid="message"><span class="avatar">'+(m.author.display_name||m.author.username)[0]+'</span><b>'+m.author.username+'</b> <small>'+new Date(m.created_at).toLocaleString()+'</small><div data-testid="message-body">'+m.body+(m.edited_at?' <em>(edited)</em>':'')+'</div><button data-testid="open-thread-btn">Replies</button><button data-testid="reaction-button">😊</button></div>').join(''):'<div id="empty-state">No messages yet — start the conversation.</div>'}
$('#auth-form').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);try{let j=await api('/auth/login',{method:'POST',body:JSON.stringify({username:f.get('username'),password:f.get('password')})});localStorage.setItem('huddle.token',j.token);load()}catch(x){$('#auth-error').textContent='Invalid username or password'}}
$('.compose').onsubmit=async e=>{e.preventDefault();let i=$('#message-input');if(!i.value.trim())return;await api('/channels/'+channel+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';select(channel,$('#channel-title').textContent.slice(2))}
$('#logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');location.reload()};load();
</script></body></html>'''
_prev_b_get=H.do_GET
def _ui_get(self):
 if self.path=='/' or self.path.split('?')[0] in ('/index.html','/app'):
  b=UI.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
 _prev_b_get(self)
H.do_GET=_ui_get
