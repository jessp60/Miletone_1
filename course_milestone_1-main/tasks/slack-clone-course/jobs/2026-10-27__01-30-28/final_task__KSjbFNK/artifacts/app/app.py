import os, re, json, time, uuid, sqlite3, hashlib, base64, socket, struct, threading, datetime, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

DB='/app/data.db'; PORT=int(os.environ.get('PORT','8000')); NODE=(PORT-8000)//1_000 if False else PORT-8000
def db():
 c=sqlite3.connect(DB,timeout=15); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=15000'); return c
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
def init():
 c=db(); c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT,mentions INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,invited_user_id INTEGER,expires REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_event INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER);
CREATE TABLE IF NOT EXISTS group_users(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
'''); c.commit(); c.close()
init()
try:
 c=db(); c.execute("ALTER TABLE messages ADD COLUMN mentions TEXT DEFAULT '[]'"); c.commit(); c.close()
except Exception: pass
def q(sql,args=(),one=False):
 c=db(); r=c.execute(sql,args); x=r.fetchone() if one else r.fetchall(); c.close(); return x
def ex(sql,args=()):
 c=db(); r=c.execute(sql,args); c.commit(); x=r.lastrowid; c.close(); return x
def userobj(r):
 return {k:(r[k] if r[k] is not None else '') for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']} | {'mention_count': r['mentions'] if 'mentions' in r.keys() else 0}
def auth(h):
 t=(h.get('Authorization') or '').replace('Bearer ','',1); r=q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t,),True); return r
def channel(r):
 return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def member(w,u): return q('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u),True)
def accessible(ch,u):
 r=q('SELECT * FROM channels WHERE id=?',(ch,),True)
 if not r:return None,False
 m=member(r['workspace_id'],u); cm=q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch,u),True)
 return r,bool(cm or (m and not r['is_private']))
def emit(ch,kind,payload):
 c=db(); c.execute('BEGIN IMMEDIATE'); n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel_id=?',(ch,)).fetchone()['n']; c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,n,kind,json.dumps(payload))); c.commit(); c.close(); return n
def reactions(mid):
 rs=q('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(mid,)); d={}
 for r in rs:d.setdefault(r['emoji'],[]).append(r['user_id'])
 return [{'emoji':e,'count':len(v),'user_ids':v} for e,v in d.items()]
def msg(r):
 a=q('SELECT * FROM users WHERE id=?',(r['author_id'],),True)
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':q('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],),True)['n'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions(r['id']),'mentions':json.loads(r['mentions']) if 'mentions' in r.keys() and r['mentions'] else []}
def event(ch,kind,payload):
 e=emit(ch,kind,payload); return e
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def send(self,status,obj=None,raw=None,typ='application/json'):
  b=raw if raw is not None else json.dumps(obj).encode(); self.send_response(status); self.send_header('Content-Type',typ); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length',0)); return json.loads(self.rfile.read(n) or b'{}')
 def url(self): return urllib.parse.urlparse(self.path)
 def do_GET(self): self.go('GET')
 def do_POST(self): self.go('POST')
 def do_PATCH(self): self.go('PATCH')
 def do_DELETE(self): self.go('DELETE')
 def go(self,method):
  p=self.url(); path=p.path; a=auth(self.headers); seg=path.strip('/').split('/')
  if path=='/api/health': return self.send(200,{'status':'ok','node_id':NODE})
  if path=='/' : return self.send(200,{'ok':True})
  if path=='/api/ws':
   if not a:
    t=urllib.parse.parse_qs(p.query).get('token',[''])[0]; a=q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t,),True)
   return self.websocket(a)
  if path=='/api/auth/register' and method=='POST':
   d=self.body(); u=d.get('username',''); pw=d.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(pw)<8:return self.send(400,{})
   if q('SELECT id FROM users WHERE username=?',(u,),True):return self.send(409,{})
   uid=ex('INSERT INTO users(username,password,display_name,timezone,avatar_url,status_text,status_emoji) VALUES(?,?,?,?,?,?,?)',(u,pw,d.get('display_name',u),'UTC','','','')); t=uuid.uuid4().hex+uuid.uuid4().hex; ex('INSERT INTO tokens VALUES(?,?)',(t,uid)); return self.send(201,{'user':userobj(q('SELECT * FROM users WHERE id=?',(uid,),True)),'token':t})
  if path=='/api/auth/login' and method=='POST':
   d=self.body(); r=q('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),d.get('password')),True)
   if not r:return self.send(401,{})
   t=uuid.uuid4().hex+uuid.uuid4().hex; ex('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); return self.send(200,{'user':userobj(r),'token':t})
  if path=='/api/auth/me':
   if not a:return self.send(401,{})
   return self.send(200,{'user':userobj(a)})
  if not a:return self.send(401,{})
  uid=a['id']
  if path=='/api/users/me' and method=='PATCH':
   d=self.body(); allowed=['display_name','timezone','avatar_url','status_text','status_emoji']; vals=[d[x] for x in allowed if x in d]
   if 'timezone' in d and not isinstance(d['timezone'],str):return self.send(400,{})
   if vals:
    sets=','.join(x+'=?' for x in allowed if x in d); ex('UPDATE users SET '+sets+' WHERE id=?',vals+[uid])
   return self.send(200,{'user':userobj(q('SELECT * FROM users WHERE id=?',(uid,),True))})
  if len(seg)==3 and seg[:2]==['api','users']:
   r=q('SELECT * FROM users WHERE id=?',(seg[2],),True); return self.send(200,{'user':userobj(r)}) if r else self.send(404,{})
  if path=='/api/workspaces' and method=='GET':
   rs=q('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(uid,)); return self.send(200,{'workspaces':[dict(r) for r in rs]})
  if path=='/api/workspaces' and method=='POST':
   d=self.body(); slug=d.get('slug','')
   if not re.fullmatch(r'[a-z0-9-]{2,32}',slug):return self.send(400,{})
   if q('SELECT id FROM workspaces WHERE slug=?',(slug,),True):return self.send(409,{})
   wid=ex('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,d.get('name',''),uid)); ex('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner')); cid=ex('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); ex('INSERT INTO cmembers VALUES(?,?)',(cid,uid)); return self.send(201,{'workspace':dict(q('SELECT * FROM workspaces WHERE id=?',(wid,),True)),'general_channel':channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
  if len(seg)>=3 and seg[0:2]==['api','workspaces']:
   slug=seg[2]; w=q('SELECT * FROM workspaces WHERE slug=?',(slug,),True)
   if not w:return self.send(404,{})
   wm=member(w['id'],uid)
   if len(seg)==4 and seg[3]=='members' and method=='GET':
    rs=q('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)); return self.send(200,{'members':[dict(x) for x in rs]})
   if len(seg)==3 and method=='GET':
    cs=q('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],urllib.parse.parse_qs(p.query).get('include_archived',['0'])[0] in ('1','true'))); return self.send(200,{'workspace':dict(w),'channels':[channel(x) for x in cs if (not x['is_private'] or q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(x['id'],uid),True))],'read_state':[{'channel_id':x['id'],'last_read_event_id':(q('SELECT last_event FROM reads WHERE user_id=? AND channel_id=?',(uid,x['id']),True) or {'last_event':0})['last_event'],'unread_count':0,'mention_count':0} for x in cs]})
   if len(seg)==3 and method=='PATCH':
    if not wm or wm['role'] not in ('owner','admin'):return self.send(403,{})
    d=self.body()
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return self.send(400,{})
    sets=[]; vals=[]
    for k in ('name','join_mode'):
     if k in d:sets.append(k+'=?'); vals.append(d[k])
    if sets: ex('UPDATE workspaces SET '+','.join(sets)+' WHERE id=?',vals+[w['id']])
    return self.send(200,{'workspace':dict(q('SELECT * FROM workspaces WHERE id=?',(w['id'],),True))})
   if len(seg)==4 and seg[3]=='channels' and method=='POST':
    if not wm or wm['role']=='guest':return self.send(403,{})
    d=self.body(); n=d.get('name','')
    if len(n)>32 or not re.fullmatch(r'[a-z0-9-]+',n):return self.send(400,{})
    if d.get('is_private') and wm['role'] not in ('owner','admin'):return self.send(403,{})
    if len(d.get('topic',''))>250:return self.send(400,{})
    if q('SELECT id FROM channels WHERE workspace_id=? AND name=?',(w['id'],n),True):return self.send(409,{})
    cid=ex('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],n,int(bool(d.get('is_private'))),d.get('topic','')))
    ex('INSERT INTO cmembers VALUES(?,?)',(cid,uid)); return self.send(201,{'channel':channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
   if len(seg)==4 and seg[3]=='invitations':
    if not wm or wm['role'] not in ('owner','admin'):return self.send(403,{})
    if method=='GET':
     rs=q('SELECT * FROM invites WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires IS NULL OR expires>?)',(w['id'],time.time())); return self.send(200,{'invitations':[dict(x) for x in rs]})
    if method=='POST':
     d=self.body(); code=uuid.uuid4().hex[:12]; target=d.get('invited_user_id'); mx=1 if target else d.get('max_uses',1); ex('INSERT INTO invites VALUES(?,?,?,?,?,?,?,?)',(code,w['id'],uid,target,time.time()+d.get('expires_in_seconds',31536000),mx,0,0)); return self.send(201,{'invitation':dict(q('SELECT * FROM invites WHERE code=?',(code,),True))})
  if len(seg)==5 and seg[0:2]==['api','workspaces'] and seg[3]=='invitations' and method=='DELETE':
   w=q('SELECT * FROM workspaces WHERE slug=?',(seg[2],),True); me=member(w['id'],uid) if w else None
   if not w:return self.send(404,{})
   if not me or me['role'] not in ('owner','admin'):return self.send(403,{})
   i=q('SELECT * FROM invites WHERE code=? AND workspace_id=?',(seg[4],w['id']),True)
   if not i:return self.send(404,{})
   ex('UPDATE invites SET revoked=1 WHERE code=?',(seg[4],)); return self.send(200,{'deleted':True})
  if len(seg)==4 and seg[0:2]==['api','workspaces'] and seg[3]=='transfer_ownership' and method=='POST':
   w=q('SELECT * FROM workspaces WHERE slug=?',(seg[2],),True); me=member(w['id'],uid) if w else None
   if not w:return self.send(404,{})
   if not me or me['role']!='owner':return self.send(403,{})
   target=self.body().get('new_owner_id'); tm=member(w['id'],target) if target else None
   if not tm or tm['role']!='admin':return self.send(400,{})
   ex('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],uid))
   ex('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
   ex('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
   return self.send(200,{'workspace':dict(q('SELECT * FROM workspaces WHERE id=?',(w['id'],),True))})
  if len(seg)==5 and seg[0:2]==['api','workspaces'] and seg[3]=='members' and method=='PATCH':
   w=q('SELECT * FROM workspaces WHERE slug=?',(seg[2],),True); me=member(w['id'],uid) if w else None; target=int(seg[4]); old=member(w['id'],target) if w else None; role=self.body().get('role')
   if not w:return self.send(404,{})
   if role not in ('owner','admin','member','guest') or role=='owner':return self.send(400,{})
   if not me or me['role'] not in ('owner','admin') or target==w['owner_id'] or (me['role']=='admin' and old and old['role']=='admin'):return self.send(403,{})
   ex('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target)); return self.send(200,{'member':{'user_id':target,'role':role}})
  if len(seg)>=4 and seg[0:2]==['api','invitations'] and seg[-1]=='accept' and method=='POST':
   i=q('SELECT * FROM invites WHERE code=?',(seg[2],),True)
   if not i or i['revoked'] or i['uses']>=i['max_uses'] or i['expires']<time.time():return self.send(404,{})
   if i['invited_user_id'] and i['invited_user_id']!=uid:return self.send(403,{})
   ex('INSERT OR IGNORE INTO members VALUES(?,?,?)',(i['workspace_id'],uid,'member')); ex('UPDATE invites SET uses=uses+1 WHERE code=?',(seg[2],)); return self.send(200,{'workspace_id':i['workspace_id']})
  if len(seg)==3 and seg[0:2]==['api','channels'] and method=='PATCH':
   cid=int(seg[2]); ch=q('SELECT * FROM channels WHERE id=?',(cid,),True)
   if not ch:return self.send(404,{})
   w=q('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],),True); m=member(w['id'],uid); cm=q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,uid),True)
   if not (cm or (m and m['role']=='owner')):return self.send(403,{})
   d=self.body()
   if len(d.get('topic',''))>250:return self.send(400,{})
   ex('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),cid)); event(cid,'channel.updated',channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))); return self.send(200,{'channel':channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
  if len(seg)>=4 and seg[0:2]==['api','channels']:
   try: cid=int(seg[2])
   except:return self.send(404,{})
   ch=q('SELECT * FROM channels WHERE id=?',(cid,),True)
   if not ch:return self.send(404,{})
   w=q('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],),True); m=member(w['id'],uid); cm=q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,uid),True)
   if seg[3]=='members' and method=='GET':
    rs=q('SELECT u.* FROM users u JOIN cmembers m ON m.user_id=u.id WHERE m.channel_id=?',(cid,)); return self.send(200,{'members':[userobj(x) for x in rs]})
   if seg[3] in ('archive','unarchive') and method=='POST':
    if not m or m['role']!='owner':return self.send(403,{})
    val=1 if seg[3]=='archive' else 0; ex('UPDATE channels SET is_archived=? WHERE id=?',(val,cid)); event(cid,'channel.archived' if val else 'channel.unarchived',channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))); return self.send(200,{'channel':channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
   if seg[3]=='pins' and method=='GET':
    if not (cm or (m and not ch['is_private'])):return self.send(403,{})
    qs=urllib.parse.parse_qs(p.query); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
    if lim<1 or lim>200:return self.send(400,{})
    sql='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'; args=[cid]
    if cur: sql+=' AND p.message_id<?'; args.append(cur)
    sql+=' ORDER BY p.pinned_at DESC LIMIT ?'; args.append(lim+1); rs=q(sql,args); more=len(rs)>lim; rs=rs[:lim]
    return self.send(200,{'pins':[{'message':msg(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':str(rs[-1]['message_id']) if more and rs else None})
   if seg[3]=='read':
    if method=='GET':
     r=q('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(uid,cid),True); mc=sum(1 for z in q('SELECT mentions FROM messages WHERE channel_id=? AND deleted=0',(cid,)) if uid in json.loads(z['mentions'] or '[]')); return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':r['last_event'] if r else 0,'unread_count':0,'mention_count':mc}})
    if method=='POST':
     d=self.body(); old=q('SELECT last_event FROM reads WHERE user_id=? AND channel_id=?',(uid,cid),True); n=max(int(d.get('last_read_event_id',0)),old['last_event'] if old else 0); ex('INSERT INTO reads(user_id,channel_id,last_event) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_event=excluded.last_event',(uid,cid,n)); mc=sum(1 for z in q('SELECT mentions FROM messages WHERE channel_id=? AND deleted=0',(cid,)) if uid in json.loads(z['mentions'] or '[]')); return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':n,'unread_count':0,'mention_count':mc}})
   if method=='PATCH' and seg[3] not in ('messages',):
    if not (cm or (m and m['role']=='owner')):return self.send(403,{})
    d=self.body()
    if len(d.get('topic',''))>250:return self.send(400,{})
    ex('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),cid)); event(cid,'channel.updated',channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))); return self.send(200,{'channel':channel(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
   if seg[3]=='join' and method=='POST':
    if m and m['role']=='guest':return self.send(403,{})
    if ch['is_private'] and not cm:return self.send(403,{})
    if not m:
     if w['join_mode']!='open':return self.send(403,{})
     ex('INSERT INTO members VALUES(?,?,?)',(w['id'],uid,'member'))
    ex('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,uid)); event(cid,'member.joined',{'user_id':uid}); return self.send(200,{'channel':channel(ch)})
   if seg[3]=='leave' and method=='POST':
    ex('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(cid,uid)); event(cid,'member.left',{'user_id':uid}); return self.send(200,{'channel':channel(ch)})
   if seg[3]=='messages':
    if not (cm or (m and not ch['is_private'])):return self.send(403,{})
    if method=='GET':
     qs=urllib.parse.parse_qs(p.query); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
     if lim<1 or lim>200 or 'offset' in qs or 'page' in qs:return self.send(400,{})
     if cur is not None and (not str(cur).isdigit()):return self.send(400,{})
     rows=q('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?',(cid,cur,cur,lim+1)); more=len(rows)>lim; rows=rows[:lim]; return self.send(200,{'messages':[msg(x) for x in rows],'next_cursor':str(rows[-1]['id']) if more and rows else None})
    if method=='POST':
     d=self.body(); body=d.get('body','')
     if not body:return self.send(400,{})
     if ch['is_archived']:return self.send(423,{})
     mentions=[]; 
     for name in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
      u=q('SELECT id FROM users WHERE username=?',(name,),True)
      if u and u['id']!=uid and (member(w['id'],u['id']) or q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id']),True)):mentions.append(u['id'])
     if '@channel' in body:
      mentions += [x['user_id'] for x in q('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=?',(cid,uid))]
     mentions=list(dict.fromkeys(mentions))
     for mu in mentions: ex('UPDATE users SET mentions=mentions+1 WHERE id=?',(mu,))
     mid=ex('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,uid,body,d.get('parent_id'),now(),json.dumps(mentions))); r=q('SELECT * FROM messages WHERE id=?',(mid,),True); kind='message.reply' if d.get('parent_id') else 'message.created'; e=event(cid,kind,msg(r)); return self.send(201,{'message':dict(msg(r),event_id=e)})
  if len(seg)>=3 and seg[0:2]==['api','messages']:
   mid=int(seg[2]); r=q('SELECT * FROM messages WHERE id=?',(mid,),True)
   if not r:return self.send(404,{})
   if len(seg)==4 and seg[3]=='replies' and method=='GET':
    qs=urllib.parse.parse_qs(p.query); lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
    if lim<1 or lim>200:return self.send(400,{})
    if cur is not None and not str(cur).isdigit():return self.send(400,{})
    rows=q('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND (? IS NULL OR id>?) ORDER BY id LIMIT ?',(mid,cur,cur,lim+1)); more=len(rows)>lim; rows=rows[:lim]
    return self.send(200,{'replies':[msg(x) for x in rows],'next_cursor':str(rows[-1]['id']) if more and rows else None})
   if len(seg)==4 and seg[3]=='reactions' and method=='POST':
    em=self.body().get('emoji'); ex('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,uid,em)); event(r['channel_id'],'reaction.added',{'message_id':mid,'emoji':em}); return self.send(200,{'reactions':reactions(mid)})
   if len(seg)==4 and seg[3]=='pin' and method=='POST':
    ex('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,uid,now())); return self.send(200,{'pin':dict(q('SELECT * FROM pins WHERE message_id=?',(mid,),True))})
   if len(seg)==4 and seg[3]=='pin' and method=='DELETE':
    ex('DELETE FROM pins WHERE message_id=?',(mid,)); return self.send(200,{'unpinned':True})
   if method=='PATCH':
    d=self.body(); ex('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d.get('body',''),now(),mid)); return self.send(200,{'message':msg(q('SELECT * FROM messages WHERE id=?',(mid,),True))})
   if method=='DELETE':
    ex('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); return self.send(200,{'deleted':True})
  if path.startswith('/api/search'):
   qs=urllib.parse.parse_qs(p.query); term=qs.get('q',[''])[0]; lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]; wid=qs.get('workspace_id',[None])[0]
   if lim<1 or lim>200 or (cur is not None and not str(cur).isdigit()):return self.send(400,{})
   sql='SELECT m.* FROM messages m JOIN channels c ON c.id=m.channel_id JOIN cmembers z ON z.channel_id=c.id WHERE z.user_id=? AND m.body LIKE ? AND m.deleted=0'; args=[uid,f'%{term}%']
   if wid: sql+=' AND c.workspace_id=?'; args.append(wid)
   if cur is not None: sql+=' AND m.id<?'; args.append(cur)
   sql+=' ORDER BY m.id DESC LIMIT ?'; args.append(lim+1); rs=q(sql,args); more=len(rs)>lim; rs=rs[:lim]
   return self.send(200,{'results':[msg(x) for x in rs],'next_cursor':str(rs[-1]['id']) if more and rs else None})
  self.send(404,{})
 def websocket(self,a):
  if not a:return self.send(401,{})
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.send(400,{})
  self.send_response(101); self.send_header('Upgrade','WebSocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()); self.end_headers()
  sock=self.connection; sock.settimeout(.2); subs={}; last={}
  def frame(x):
   b=json.dumps(x).encode(); n=len(b); h=bytes([0x81,n]) if n<126 else bytes([0x81,126])+struct.pack('>H',n); return h+b
  try:
   while True:
    try:
     h=sock.recv(2)
     if h:
      n=h[1]&127; mask=sock.recv(4); data=sock.recv(n); data=bytes(data[i]^mask[i%4] for i in range(n)); d=json.loads(data); cid=int(d.get('channel_id')); subs[cid]=1
      head=q('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,),True)['n']
      if d['type']=='resume':
       for e in q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,int(d.get('since_event_id',0)))):
        sock.sendall(frame(dict(json.loads(e['payload']),type=e['kind'],event_id=e['event_id'],channel_id=cid)))
       sock.sendall(frame({'type':'resumed','channel_id':cid,'head_event_id':head}))
      else:sock.sendall(frame({'type':'subscribed','channel_id':cid,'head_event_id':head})); last[cid]=head
    except socket.timeout: pass
    for cid in list(subs):
     for e in q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last.get(cid,0))):
      x=json.loads(e['payload']); x.update(type=e['kind'],event_id=e['event_id'],channel_id=cid); sock.sendall(frame(x)); last[cid]=e['event_id']
  except Exception: pass
  return
if __name__=='__main__':
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
