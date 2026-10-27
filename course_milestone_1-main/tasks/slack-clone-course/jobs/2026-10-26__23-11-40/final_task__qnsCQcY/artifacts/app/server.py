import os,sys,json,re,sqlite3,hashlib,secrets,threading,time,base64,struct
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
DB='/app/data.db'; PORT=int(sys.argv[1]); NODE=PORT-8000
lock=threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=10,check_same_thread=False); c.row_factory=sqlite3.Row; return c
def init():
 os.makedirs('/app',exist_ok=True); c=db()
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT,mention_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,UNIQUE(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,UNIQUE(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,UNIQUE(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,user_id INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS groupsx(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,users TEXT,UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires INTEGER,max_uses INTEGER,uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read INTEGER DEFAULT 0,UNIQUE(user_id,channel_id));''')
 c.commit(); c.close()
init()
def q(sql,args=(),one=False):
 c=db(); r=c.execute(sql,args); out=r.fetchone() if one else r.fetchall(); c.commit(); c.close(); return out
def userobj(r):
 return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']} | ({'mention_count':r['mention_count']} if 'mention_count' in r.keys() else {})
def chan(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def msg(r):
 u=q('select * from users where id=?',(r['author_id'],),True)
 reps=q('select count(*) n from messages where parent_id=? and deleted=0',(r['id'],),True)['n']
 rx=q('select emoji,count(*) n from reactions where message_id=? group by emoji',(r['id'],))
 reactions=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[z['user_id'] for z in q('select user_id from reactions where message_id=? and emoji=?',(r['id'],x['emoji']))]} for x in rx]
 mentions=[]
 for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',r['body']):
  if name=='channel':
   ids=[x['user_id'] for x in q('select user_id from cmembers where channel_id=?',(r['channel_id'],)) if x['user_id']!=r['author_id']]
  else:
   z=q('select id from users where username=?',(name,),True); ids=[z['id']] if z and z['id']!=r['author_id'] and q('select 1 from cmembers where channel_id=? and user_id=?',(r['channel_id'],z['id']),True) else []
  for i in ids:
   if i not in mentions: mentions.append(i)
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions,'mentions':mentions}

def body(h):
 n=int(h.headers.get('Content-Length','0'))
 return json.loads(h.rfile.read(n) or b'{}')
def auth(h):
 a=h.headers.get('Authorization','')
 if not a.startswith('Bearer '): return None
 return q('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(a[7:],),True)

class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def out(self,status,obj,ctype='application/json'):
  b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b))); self.send_header('Connection','close'); self.end_headers(); self.wfile.write(b)
 def path(self): return urlparse(self.path)
 def do_GET(self):
  p=urlparse(self.path); path=p.path; qs=parse_qs(p.query)
  if path=='/api/health': return self.out(200,{'status':'ok','node_id':NODE})
  if path=='/': return self.out(200,{'status':'ok'})
  if path.startswith('/api/ws'): return self.ws()
  u=auth(self)
  if path=='/api/auth/me':
   return self.out(200,{'user':userobj(u)}) if u else self.out(401,{'error':'unauthorized'})
  if path.startswith('/api/users/') and u:
   try: x=q('select * from users where id=?',(int(path.split('/')[-1]),),True); return self.out(200,{'user':userobj(x)}) if x else self.out(404,{})
   except: pass
  if not u: return self.out(401,{})
  try:
   if path=='/api/workspaces':
    ws=q('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],)); return self.out(200,{'workspaces':[dict(x) for x in ws]})
   if path.startswith('/api/workspaces/') and path.endswith('/invitations'):
    slug=path.split('/')[3]; w=q('select * from workspaces where slug=?',(slug,),True)
    mr=q('select role from members where workspace_id=? and user_id=?',(w['id'],u['id']),True) if w else None
    if not mr or mr['role'] not in ('owner','admin'): return self.out(403,{})
    return self.out(200,{'invitations':[dict(x) for x in q('select * from invites where workspace_id=? and uses<max_uses',(w['id'],))]})
   if path.startswith('/api/workspaces/') and path.endswith('/groups'):
    slug=path.split('/')[3]; w=q('select * from workspaces where slug=?',(slug,),True)
    if not w or not q('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id']),True): return self.out(403,{})
    return self.out(200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['users'])} for x in q('select * from groupsx where workspace_id=?',(w['id'],))]})
   if path.startswith('/api/workspaces/'):
    slug=path.split('/')[3]; w=q('select * from workspaces where slug=?',(slug,),True)
    if not w:return self.out(404,{})
    if path.endswith('/members'): return self.out(200,{'members':[dict(x) for x in q('select user_id,role from members where workspace_id=?',(w['id'],))]})
    if path.endswith('/groups'): return self.out(200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['users'])} for x in q('select * from groupsx where workspace_id=?',(w['id'],))]})
    cs=q('select * from channels where workspace_id=? and (is_archived=0 or ?)',(w['id'],1 if qs.get('include_archived') else 0))
    cs=[chan(x) for x in cs if not x['is_private'] or q('select 1 from cmembers where channel_id=? and user_id=?',(x['id'],u['id']),True) or w['owner_id']==u['id']]
    return self.out(200,{'workspace':dict(w),'channels':cs,'read_state':[dict(x) for x in q('select channel_id,last_read as last_read_event_id from reads where user_id=?',(u['id'],))]})
   if path.startswith('/api/channels/'):
    cid=int(path.split('/')[3]); c=q('select * from channels where id=?',(cid,),True)
    if not c:return self.out(404,{})
    if path.endswith('/members'): return self.out(200,{'members':[userobj(q('select * from users where id=?',(x['user_id'],),True)) for x in q('select user_id from cmembers where channel_id=?',(cid,))]})
    if path.endswith('/messages'):
     lim=int(qs.get('limit',['50'])[0]); cur=qs.get('cursor',[None])[0]
     if lim<1 or lim>200:return self.out(400,{})
     if 'offset' in qs or 'page' in qs:return self.out(400,{})
     if cur:
      try: boundary=int(base64.urlsafe_b64decode(cur+'==').decode())
      except:return self.out(400,{})
      rows=q('select * from messages where channel_id=? and deleted=0 and parent_id is null and id<? order by id desc limit ?',(cid,boundary,lim+1))
     else:
      rows=q('select * from messages where channel_id=? and deleted=0 and parent_id is null order by id desc limit ?',(cid,lim+1))
     more=len(rows)>lim; rows=rows[:lim]
     nxt=base64.urlsafe_b64encode(str(rows[-1]['id']).encode()).decode().rstrip('=') if more and rows else None
     return self.out(200,{'messages':[msg(x) for x in rows],'next_cursor':nxt})
    if path.endswith('/pins'):
     ps=q('select p.*,m.* from pins p join messages m on m.id=p.message_id where m.channel_id=? order by p.pinned_at desc',(cid,))
     if c['is_private'] and not q('select 1 from cmembers where channel_id=? and user_id=?',(cid,u['id']),True): return self.out(403,{})
     return self.out(200,{'pins':[{'message':msg(x),'pinned_by':x['user_id'],'pinned_at':x['pinned_at']} for x in ps],'next_cursor':None})
    if path.endswith('/read'):
     rr=q('select * from reads where user_id=? and channel_id=?',(u['id'],cid),True); last=rr['last_read'] if rr else 0; head=q('select coalesce(max(event_id),0) n from events where channel_id=?',(cid,),True)['n']; mc=sum(1 for x in q('select * from messages where channel_id=? and deleted=0',(cid,)) if u['id'] in msg(x)['mentions']); return self.out(200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':max(0,head-last),'mention_count':mc}})
   if path.startswith('/api/messages/') and path.endswith('/replies'):
    mid=int(path.split('/')[3]); rows=q('select * from messages where parent_id=? and deleted=0 order by id',(mid,)); return self.out(200,{'replies':[msg(x) for x in rows],'next_cursor':None})
   if path=='/api/search':
    term=qs.get('q',[''])[0]; rows=q('select m.* from messages m join channels c on c.id=m.channel_id join members wm on wm.workspace_id=c.workspace_id and wm.user_id=? left join cmembers cm on cm.channel_id=c.id and cm.user_id=? where m.deleted=0 and m.body like ? and (c.is_private=0 or cm.user_id is not null)',(u['id'],u['id'],'%'+term+'%')); wid=qs.get('workspace_id',[None])[0]; rows=[x for x in rows if wid is None or str(x['channel_id']) in [str(z['id']) for z in q('select id from channels where workspace_id=?',(wid,))]]; return self.out(200,{'results':[msg(x) for x in rows[::-1]],'next_cursor':None})
  except Exception as e: return self.out(400,{'error':str(e)})
  return self.out(404,{})
 def do_POST(self):
  path=self.path.split('?')[0]; d=body(self); u=auth(self)
  if path=='/api/auth/register':
   if not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or len(d.get('password',''))<8:return self.out(400,{})
   try:
    q('insert into users(username,password,display_name,timezone,avatar_url,status_text,status_emoji) values(?,?,?,?,?,?,?)',(d['username'],d['password'],d.get('display_name') or d['username'],'UTC','','',''))
   except:return self.out(409,{})
   x=q('select * from users where username=?',(d['username'],),True); t=secrets.token_urlsafe(24); q('insert into tokens values(?,?)',(t,x['id'])); return self.out(201,{'user':userobj(x),'token':t})
  if path=='/api/auth/login':
   x=q('select * from users where username=? and password=?',(d.get('username'),d.get('password')),True)
   if not x:return self.out(401,{})
   t=secrets.token_urlsafe(24);q('insert into tokens values(?,?)',(t,x['id']));return self.out(200,{'user':userobj(x),'token':t})
  if not u:return self.out(401,{})
  # Component A: workspace roles, invitations, and groups
  if path.startswith('/api/invitations/') and path.endswith('/accept'):
   code=path.split('/')[3]; inv=q('select * from invites where code=?',(code,),True)
   if not inv or (inv['expires'] and inv['expires']<int(time.time())) or inv['uses']>=inv['max_uses'] or (inv['invited_user_id'] and inv['invited_user_id']!=u['id']): return self.out(404,{})
   q('update invites set uses=uses+1 where code=?',(code,)); q('insert or ignore into members values(?,?,?)',(inv['workspace_id'],u['id'],'member')); return self.out(200,{'workspace':dict(q('select * from workspaces where id=?',(inv['workspace_id'],),True))})
  if path.startswith('/api/workspaces/'):
   parts=path.split('/'); slug=parts[3]; w=q('select * from workspaces where slug=?',(slug,),True)
   if not w:return self.out(404,{})
   mr=q('select role from members where workspace_id=? and user_id=?',(w['id'],u['id']),True); role=mr['role'] if mr else None
   if path.endswith('/invitations'):
    if role not in ('owner','admin'): return self.out(403,{})
    code=secrets.token_urlsafe(12); target=d.get('invited_user_id')
    if not target and d.get('invited_username'):
     z=q('select id from users where username=?',(d['invited_username'],),True); target=z['id'] if z else None
    exp=int(time.time())+int(d.get('expires_in_seconds',604800)); mx=int(d.get('max_uses',1))
    q('insert into invites values(?,?,?,?,?,0)',(code,w['id'],target,exp,mx))
    return self.out(201,{'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target,'expires_at':datetime.fromtimestamp(exp,timezone.utc).isoformat().replace('+00:00','Z'),'max_uses':mx,'uses':0}})
   if path.endswith('/groups'):
    if not role:return self.out(403,{})
    h=d.get('handle','').lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',h):return self.out(400,{})
    try:q('insert into groupsx(workspace_id,handle,name,creator_id,users) values(?,?,?,?,?)',(w['id'],h,d.get('name',h),u['id'],json.dumps(d.get('user_ids',[]))))
    except:return self.out(409,{})
    g=q('select * from groupsx where workspace_id=? and handle=?',(w['id'],h),True);return self.out(201,{'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['users'])}})
   if path.endswith('/transfer_ownership'):
    if w['owner_id']!=u['id']:return self.out(403,{})
    target=int(d.get('new_owner_id',0)); tm=q('select role from members where workspace_id=? and user_id=?',(w['id'],target),True)
    if not tm or tm['role']!='admin':return self.out(400,{})
    q('update members set role=? where workspace_id=? and user_id=?',('admin',w['id'],u['id']));q('update members set role=? where workspace_id=? and user_id=?',('owner',w['id'],target));q('update workspaces set owner_id=? where id=?',(target,w['id']));return self.out(200,{'workspace':dict(q('select * from workspaces where id=?',(w['id'],),True))})
   if len(parts)>=6 and parts[4]=='members':
    if role not in ('owner','admin'):return self.out(403,{})
    target=int(parts[5]); nr=d.get('role','')
    if nr not in ('admin','member','guest'):return self.out(400,{})
    old=q('select role from members where workspace_id=? and user_id=?',(w['id'],target),True)
    if not old:return self.out(404,{})
    if w['owner_id']!=u['id'] and (old['role'] in ('owner','admin') or nr=='admin'):return self.out(403,{})
    q('update members set role=? where workspace_id=? and user_id=?',(nr,w['id'],target));return self.out(200,{'member':{'user_id':target,'role':nr,'workspace_id':w['id']}})
  try:
   if path=='/api/workspaces':
    if not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')):return self.out(400,{})
    try:q('insert into workspaces(slug,name,owner_id) values(?,?,?)',(d['slug'],d.get('name',d['slug']),u['id']))
    except:return self.out(409,{})
    w=q('select * from workspaces where slug=?',(d['slug'],),True);q('insert into members values(?,?,?)',(w['id'],u['id'],'owner'));q('insert into channels(workspace_id,name) values(?,?)',(w['id'],'general'));c=q('select * from channels where workspace_id=?',(w['id'],),True);q('insert into cmembers values(?,?)',(c['id'],u['id']));return self.out(201,{'workspace':dict(w),'general_channel':chan(c)})
   if path.startswith('/api/workspaces/') and path.endswith('/channels'):
    slug=path.split('/')[3];w=q('select * from workspaces where slug=?',(slug,),True)
    if len(d.get('topic',''))>250 or not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('name','')):return self.out(400,{})
    if q('select 1 from channels where workspace_id=? and name=?',(w['id'],d['name']),True):return self.out(409,{})
    private=1 if d.get('is_private') else 0
    q('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],d['name'],private,d.get('topic','')));c=q('select * from channels where workspace_id=? and name=?',(w['id'],d['name']),True);q('insert into cmembers values(?,?)',(c['id'],u['id']));return self.out(201,{'channel':chan(c)})
   if path.startswith('/api/channels/'):
    cid=int(path.split('/')[3]);c=q('select * from channels where id=?',(cid,),True)
    if not c:return self.out(404,{})
    if path.endswith('/join'):
     w=q('select * from workspaces where id=?',(c['workspace_id'],),True)
     if c['is_private']: return self.out(403,{})
     existing=q('select role from members where workspace_id=? and user_id=?',(w['id'],u['id']),True)
     if existing and existing['role']=='guest': return self.out(403,{})
     if w['join_mode']=='invite_only' and not existing: return self.out(403,{})
     if not q('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id']),True):q('insert or ignore into members values(?,?,?)',(w['id'],u['id'],'member'))
     q('insert or ignore into cmembers values(?,?)',(cid,u['id'])); eid=q('select coalesce(max(event_id),0)+1 n from events where channel_id=?',(cid,),True)['n']; q('insert into events values(?,?,?,?,?)',(cid,eid,'member.joined',json.dumps({'user_id':u['id']}),now())); return self.out(200,{'channel':chan(c)})
    if path.endswith('/leave'):q('delete from cmembers where channel_id=? and user_id=?',(cid,u['id'])); eid=q('select coalesce(max(event_id),0)+1 n from events where channel_id=?',(cid,),True)['n']; q('insert into events values(?,?,?,?,?)',(cid,eid,'member.left',json.dumps({'user_id':u['id']}),now())); return self.out(200,{'channel':chan(c)})
    if path.endswith('/archive') or path.endswith('/unarchive'):
     w=q('select * from workspaces where id=?',(c['workspace_id'],),True)
     if w['owner_id']!=u['id']:return self.out(403,{})
     val=0 if path.endswith('unarchive') else 1;q('update channels set is_archived=? where id=?',(val,cid)); eid=q('select coalesce(max(event_id),0)+1 n from events where channel_id=?',(cid,),True)['n']; kind='channel.unarchived' if val==0 else 'channel.archived'; q('insert into events values(?,?,?,?,?)',(cid,eid,kind,json.dumps({}),now())); c=q('select * from channels where id=?',(cid,),True);return self.out(200,{'channel':chan(c)})
    if path.endswith('/messages'):
     if c['is_archived']:return self.out(423,{})
     if not d.get('body'):return self.out(400,{})
     mentions=[]; bodytxt=d['body']
     for x in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',bodytxt):
      z=q('select id from users where username=?',(x,),True)
      if z and z['id']!=u['id'] and q('select 1 from cmembers where channel_id=? and user_id=?',(cid,z['id']),True):mentions.append(z['id'])
     q('insert into messages(channel_id,author_id,body,parent_id,created_at) values(?,?,?,?,?)',(cid,u['id'],bodytxt,d.get('parent_id'),now()));r=q('select * from messages where channel_id=? and author_id=? and created_at=(select max(created_at) from messages where channel_id=? and author_id=?)',(cid,u['id'],cid,u['id']),True); [q('update users set mention_count=mention_count+1 where id=?',(x,)) for x in msg(r)['mentions']]; eid=q('select coalesce(max(event_id),0)+1 n from events where channel_id=?',(cid,),True)['n'];kind='message.reply' if d.get('parent_id') else 'message.created';q('insert into events values(?,?,?,?,?)',(cid,eid,kind,json.dumps({'message':dict(r)}),now()));return self.out(201,{'message':msg(r)})
    if path.endswith('/read'):
     eid=int(d.get('last_read_event_id',0));r=q('select * from reads where user_id=? and channel_id=?',(u['id'],cid),True)
     if r: q('update reads set last_read=max(last_read,?) where user_id=? and channel_id=?',(eid,u['id'],cid))
     else:q('insert into reads values(?,?,?)',(u['id'],cid,eid))
     return self.do_GET()
   if path.startswith('/api/messages/') and path.endswith('/pin'):
    mid=int(path.split('/')[3]);q('insert or ignore into pins values(?,?,?)',(mid,u['id'],now()));p=q('select * from pins where message_id=?',(mid,),True);return self.out(200,{'pin':{'message_id':mid,'pinned_by':p['user_id'],'pinned_at':p['pinned_at']}})
  except Exception as e:return self.out(400,{'error':str(e)})
  return self.out(404,{})
 def do_PATCH(self):
  u=auth(self);d=body(self)
  if not u:return self.out(401,{})
  path=self.path.split('?')[0]
  try:
   if path=='/api/users/me':
    sets=[];args=[]
    for k in ['display_name','timezone','avatar_url','status_text','status_emoji']:
     if k in d:sets.append(k+'=?');args.append(d[k])
    if sets:q('update users set '+','.join(sets)+' where id=?',args+[u['id']])
    return self.out(200,{'user':userobj(q('select * from users where id=?',(u['id'],),True))})
   if path.startswith('/api/workspaces/') and '/members/' in path:
    parts=path.split('/'); slug=parts[3]; w=q('select * from workspaces where slug=?',(slug,),True)
    if not w:return self.out(404,{})
    mr=q('select role from members where workspace_id=? and user_id=?',(w['id'],u['id']),True)
    if not mr or mr['role'] not in ('owner','admin'):return self.out(403,{})
    target=int(parts[5]); nr=d.get('role',''); old=q('select role from members where workspace_id=? and user_id=?',(w['id'],target),True)
    if nr not in ('admin','member','guest') or not old:return self.out(400 if nr not in ('admin','member','guest') else 404,{})
    if w['owner_id']!=u['id'] and old['role'] in ('owner','admin'):return self.out(403,{})
    q('update members set role=? where workspace_id=? and user_id=?',(nr,w['id'],target));return self.out(200,{'member':{'user_id':target,'role':nr,'workspace_id':w['id']}})
   if path.startswith('/api/workspaces/') and '/members/' not in path:
    slug=path.split('/')[3];w=q('select * from workspaces where slug=?',(slug,),True);mr=q('select role from members where workspace_id=? and user_id=?',(w['id'],u['id']),True)
    if not mr or mr['role'] not in ('owner','admin'):return self.out(403,{})
    if 'name' in d:q('update workspaces set name=? where id=?',(d['name'],w['id']))
    if 'join_mode' in d and d['join_mode'] in ('open','invite_only'):q('update workspaces set join_mode=? where id=?',(d['join_mode'],w['id']))
    return self.out(200,{'workspace':dict(q('select * from workspaces where id=?',(w['id'],),True))})
   if path.startswith('/api/channels/'):
    cid=int(path.split('/')[3]);c=q('select * from channels where id=?',(cid,),True)
    if len(d.get('topic',''))>250:return self.out(400,{})
    w=q('select * from workspaces where id=?',(c['workspace_id'],),True)
    if not q('select 1 from cmembers where channel_id=? and user_id=?',(cid,u['id']),True) and w['owner_id']!=u['id']:return self.out(403,{})
    q('update channels set topic=? where id=?',(d.get('topic',''),cid)); eid=q('select coalesce(max(event_id),0)+1 n from events where channel_id=?',(cid,),True)['n']; q('insert into events values(?,?,?,?,?)',(cid,eid,'channel.updated',json.dumps({}),now())); return self.out(200,{'channel':chan(q('select * from channels where id=?',(cid,),True))})
   if path.startswith('/api/messages/'):
    mid=int(path.split('/')[3]);r=q('select * from messages where id=?',(mid,),True)
    if not r:return self.out(404,{})
    if r['author_id']!=u['id']:return self.out(403,{})
    q('update messages set body=?,edited_at=? where id=?',(d.get('body',''),now(),mid));return self.out(200,{'message':msg(q('select * from messages where id=?',(mid,),True))})
  except:return self.out(400,{})
  return self.out(404,{})
 def do_DELETE(self):
  u=auth(self);p=self.path.split('?')[0]
  if not u:return self.out(401,{})
  if p.startswith('/api/messages/') and p.endswith('/pin'):
   q('delete from pins where message_id=?',(int(p.split('/')[3]),));return self.out(200,{'unpinned':True})
  if p.startswith('/api/messages/'):
   q('update messages set deleted=1 where id=?',(int(p.split('/')[3]),));return self.out(200,{'deleted':True})
  return self.out(404,{})
 def ws(self):
  qs=parse_qs(urlparse(self.path).query); t=qs.get('token',[''])[0];u=q('select user_id from tokens where token=?',(t,),True)
  if not u:return self.close()
  key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  self.connection.settimeout(1); ch=None; last=0
  def frame(x):
   b=json.dumps(x).encode(); n=len(b); h=b'\x81'+(bytes([n]) if n<126 else (b'\x7e'+struct.pack('!H',n) if n<65536 else b'\x7f'+struct.pack('!Q',n)));self.connection.sendall(h+b)
  try:
   while True:
    try:
     h=self.connection.recv(2)
     if not h:break
     n=h[1]&127; mask=self.connection.recv(4); data=self.connection.recv(n); data=bytes(data[i]^mask[i%4] for i in range(n)); z=json.loads(data); 
     if z.get('type') in ('subscribe','resume'):
      ch=int(z['channel_id']); last=int(z.get('since_event_id',0))
      head=q('select coalesce(max(event_id),0) n from events where channel_id=?',(ch,),True)['n']
      if z['type']=='resume':
       for e in q('select * from events where channel_id=? and event_id>? order by event_id',(ch,last)):
        pay=json.loads(e['payload']); frame({'type':e['kind'],'event_id':e['event_id'],'channel_id':ch,**pay}); last=e['event_id']
       frame({'type':'resumed','channel_id':ch,'head_event_id':head})
      else:
       frame({'type':'subscribed','channel_id':ch,'head_event_id':head})
    except socket.timeout: pass
    if ch:
     es=q('select * from events where channel_id=? and event_id>? order by event_id',(ch,last))
     for e in es:
      pay=json.loads(e['payload']);frame({'type':e['kind'],'event_id':e['event_id'],'channel_id':ch,**pay});last=e['event_id']
  except: pass
 def close(self):
  self.send_response(401);self.end_headers()
import socket
ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
