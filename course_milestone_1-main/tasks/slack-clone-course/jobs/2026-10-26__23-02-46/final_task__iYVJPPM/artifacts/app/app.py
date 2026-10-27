import json,os,re,time,uuid,hashlib,base64,struct,fcntl,threading,select
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
DB='/dev/shm/huddle_state.json'; LOCK='/dev/shm/huddle_state.lock'
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def load():
 if not os.path.exists(DB) or os.path.getsize(DB)==0:
  d={'users':{},'tokens':{},'workspaces':{},'channels':{},'messages':{},'pins':{},'events':{},'reads':{},'next':1}
  with open(DB,'w') as f: json.dump(d,f)
  return d
 with open(DB) as f: return json.load(f)
def save(d):
 tmp=DB+'.tmp.'+str(os.getpid())
 with open(tmp,'w') as f:
  json.dump(d,f,separators=(',',':')); f.flush()
 os.replace(tmp,DB)
def nid(d):
 x=d['next']; d['next']+=1; return x
def userobj(u):
 return {k:u.get(k,'') for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def chobj(c): return {k:c.get(k) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def msgobj(d,m):
 u=d['users'].get(str(m['author_id']),{})
 rs={}
 for x in d.get('reactions',{}).values():
  if x['message_id']==m['id']: rs.setdefault(x['emoji'],[]).append(x['user_id'])
 return {'id':m['id'],'channel_id':m['channel_id'],'author_id':m['author_id'],'author':userobj(u),'body':m['body'],'parent_id':m.get('parent_id'),'reply_count':sum(1 for z in d['messages'].values() if z.get('parent_id')==m['id'] and not z.get('deleted')),'created_at':m['created_at'],'edited_at':m.get('edited_at'),'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in rs.items()],'mentions':m.get('mentions',[])}
def event(d,cid,typ,extra={}):
 k=str(cid); d['events'].setdefault(k,[]); eid=len(d['events'][k])+1
 x={'type':typ,'event_id':eid,'channel_id':cid}; x.update(extra); d['events'][k].append(x); return x
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 write_lock=threading.Lock()
 def log_message(self,*a): pass
 def sendj(self,code,o):
  b=json.dumps(o).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def auth(self,d):
  t=self.headers.get('Authorization','').replace('Bearer ',''); return d['users'].get(str(d['tokens'].get(t)))
 def path(self): return urlparse(self.path)
 def do_GET(self):
  p=urlparse(self.path); q=parse_qs(p.query); d=load()
  if p.path=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(os.getenv('NODE','0'))})
  if p.path.startswith('/api/ws'): return self.ws(d,q)
  u=self.auth(d)
  if p.path=='/api/auth/me': return self.sendj(200,{'user':userobj(u)}) if u else self.sendj(401,{'error':'unauthorized'})
  if not u: return self.sendj(401,{'error':'unauthorized'})
  m=re.match(r'/api/users/(\d+)$',p.path)
  if m: return self.sendj(200,{'user':userobj(d['users'][m.group(1)])}) if m.group(1) in d['users'] else self.sendj(404,{})
  if p.path=='/api/workspaces': return self.sendj(200,{'workspaces':[w for w in d['workspaces'].values() if str(u['id']) in w['members']]})
  m=re.match(r'/api/workspaces/([^/]+)$',p.path)
  if m:
   w=next((x for x in d['workspaces'].values() if x['slug']==m.group(1)),None)
   if not w:return self.sendj(404,{})
   cs=[chobj(c) for c in d['channels'].values() if c['workspace_id']==w['id'] and (not c['is_private'] or str(u['id']) in [str(x) for x in c['members']]) and (not c['is_archived'] or q.get('include_archived',['0'])[0] in ('1','true'))]
   rs={str(c['id']):self.readstate(d,u['id'],c['id']) for c in d['channels'].values() if c['workspace_id']==w['id']}
   return self.sendj(200,{'workspace':w,'channels':cs,'read_state':rs})
  m=re.match(r'/api/workspaces/([^/]+)/members$',p.path)
  if m:
   w=next((x for x in d['workspaces'].values() if x['slug']==m.group(1)),None); return self.sendj(200,{'members':[{'user_id':int(x),'role':r} for x,r in w['members'].items()]})
  m=re.match(r'/api/channels/(\d+)/members$',p.path)
  if m:
   c=d['channels'].get(m.group(1)); return self.sendj(200,{'members':[userobj(d['users'][str(x)]) for x in c['members']]})
  m=re.match(r'/api/channels/(\d+)/messages$',p.path)
  if m:
   c=int(m.group(1)); lim=int(q.get('limit',['50'])[0]); cur=q.get('cursor',[None])[0]
   if lim<1 or lim>200:return self.sendj(400,{})
   arr=[x for x in d['messages'].values() if x['channel_id']==c and not x.get('deleted') and not x.get('parent_id')]; arr.sort(key=lambda x:x['id'],reverse=True)
   start=0; boundary=arr[0]['id'] if arr else 0
   if cur:
    try:
     z=json.loads(base64.urlsafe_b64decode(cur.encode()).decode()); boundary=int(z['b']); start=int(z['o'])
    except:return self.sendj(400,{})
   arr=[x for x in arr if x['id']<=boundary]
   out=arr[start:start+lim]
   nc=None
   if start+lim<len(arr):
    nc=base64.urlsafe_b64encode(json.dumps({'b':boundary,'o':start+lim},separators=(',',':')).encode()).decode()
   return self.sendj(200,{'messages':[msgobj(d,x) for x in out],'next_cursor':nc})
  m=re.match(r'/api/messages/(\d+)/replies$',p.path)
  if m:
   a=[x for x in d['messages'].values() if x.get('parent_id')==int(m.group(1)) and not x.get('deleted')]; a.sort(key=lambda x:x['id'])
   return self.sendj(200,{'replies':[msgobj(d,x) for x in a],'next_cursor':None})
  if p.path=='/api/search':
   text=q.get('q',[''])[0].lower(); wid=q.get('workspace_id',[None])[0]; allowed=[]
   for c in d['channels'].values():
    w=d['workspaces'][str(c['workspace_id'])]
    if str(u['id']) in [str(x) for x in c['members']] and (not wid or str(w['id'])==wid): allowed.append(c['id'])
   a=[x for x in d['messages'].values() if x['channel_id'] in allowed and text in x['body'].lower() and not x.get('deleted')]; a.sort(key=lambda x:x['id'],reverse=True)
   return self.sendj(200,{'results':[msgobj(d,x) for x in a],'next_cursor':None})
  m=re.match(r'/api/channels/(\d+)/pins$',p.path)
  if m:
   c=d['channels'].get(m.group(1))
   if not c:return self.sendj(404,{})
   if c.get('is_private') and u['id'] not in [int(x) for x in c.get('members',[])]:
    return self.sendj(403,{})
   a=[x for x in d['pins'].values() if x['channel_id']==int(m.group(1))]; a.sort(key=lambda x:x['id'],reverse=True)
   return self.sendj(200,{'pins':[{'message':msgobj(d,d['messages'][str(x['message_id'])]),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in a],'next_cursor':None})
  m=re.match(r'/api/channels/(\d+)/read$',p.path)
  if m:return self.sendj(200,{'read_state':self.readstate(d,u['id'],int(m.group(1)))})
  return self.sendj(404,{})
 def readstate(self,d,uid,cid):
  x=d['reads'].get(f'{uid}:{cid}',0); ev=d['events'].get(str(cid),[])
  return {'channel_id':cid,'last_read_event_id':x,'unread_count':max(0,len(ev)-x),'mention_count':sum(1 for m in d['messages'].values() if m['channel_id']==cid and uid in m.get('mentions',[]) and m['id']>x)}
 def do_POST_locked(self):
  p=urlparse(self.path); d=load()
  if p.path=='/api/auth/register':
   b=self.body(); un=b.get('username',''); pw=b.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.sendj(400,{})
   if any(x['username']==un for x in d['users'].values()):return self.sendj(409,{})
   i=nid(d); u={'id':i,'username':un,'display_name':b.get('display_name',un),'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''}; d['users'][str(i)]=u
   t=uuid.uuid4().hex+uuid.uuid4().hex; d['tokens'][t]=i; save(d); return self.sendj(201,{'user':userobj(u),'token':t})
  if p.path=='/api/auth/login':
   b=self.body(); u=next((x for x in d['users'].values() if x['username']==b.get('username')),None)
   if not u:return self.sendj(401,{})
   t=uuid.uuid4().hex+uuid.uuid4().hex; d['tokens'][t]=u['id']; save(d); return self.sendj(200,{'user':userobj(u),'token':t})
  u=self.auth(d)
  if not u:return self.sendj(401,{})
  b=self.body()
  if p.path=='/api/workspaces':
   if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.sendj(400,{})
   if any(w['slug']==b['slug'] for w in d['workspaces'].values()):return self.sendj(409,{})
   wi=nid(d); w={'id':wi,'slug':b['slug'],'name':b.get('name',''),'owner_id':u['id'],'join_mode':'open','members':{str(u['id']):'owner'}}; d['workspaces'][str(wi)]=w
   ci=nid(d); d['channels'][str(ci)]={'id':ci,'workspace_id':wi,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False,'members':[u['id']]}; save(d); return self.sendj(201,{'workspace':w,'general_channel':chobj(d['channels'][str(ci)])})
  m=re.match(r'/api/workspaces/([^/]+)/channels$',p.path)
  if m:
   w=next((x for x in d['workspaces'].values() if x['slug']==m.group(1)),None); n=b.get('name','')
   if not w or str(u['id']) not in w['members']:return self.sendj(403,{})
   if not re.fullmatch(r'[a-z0-9-]{1,32}',n) or len(b.get('topic',''))>250:return self.sendj(400,{})
   if any(c['workspace_id']==w['id'] and c['name']==n for c in d['channels'].values()):return self.sendj(409,{})
   if b.get('is_private') and w['members'].get(str(u['id'])) not in ('owner','admin'):return self.sendj(403,{})
   i=nid(d); c={'id':i,'workspace_id':w['id'],'name':n,'is_private':bool(b.get('is_private')),'is_dm':False,'topic':b.get('topic',''),'is_archived':False,'members':[u['id']]}; d['channels'][str(i)]=c; save(d); return self.sendj(201,{'channel':chobj(c)})
  m=re.match(r'/api/channels/(\d+)/(join|leave|archive|unarchive)$',p.path)
  if m:
   c=d['channels'].get(m.group(1)); w=d['workspaces'][str(c['workspace_id'])]; op=m.group(2)
   if op=='join':
    if str(u['id']) not in w['members']: w['members'][str(u['id'])]='member'
    if u['id'] not in c['members']: c['members'].append(u['id']); event(d,c['id'],'member.joined',{'user_id':u['id']})
   elif op=='leave':
    c['members']=[x for x in c['members'] if x!=u['id']]; event(d,c['id'],'member.left',{'user_id':u['id']})
   else:
    if w['owner_id']!=u['id']:return self.sendj(403,{})
    c['is_archived']=op=='archive'; event(d,c['id'],'channel.'+('archived' if op=='archive' else 'unarchived'))
   save(d); return self.sendj(200,{'channel':chobj(c)})
  m=re.match(r'/api/channels/(\d+)/messages$',p.path)
  if m:
   c=d['channels'].get(m.group(1)); body=b.get('body','')
   if c['is_archived']:return self.sendj(423,{})
   if not body:return self.sendj(400,{})
   mentions=[]; text=body
   for x in d['users'].values():
    if x['id']!=u['id'] and re.search(r'(?<![\w@])@'+re.escape(x['username'])+r'\b',text) and str(x['id']) in d['workspaces'][str(c['workspace_id'])]['members'] and x['id'] in [int(y) for y in c['members']]:mentions.append(x['id'])
   if '@channel' in text: mentions += [x for x in c['members'] if x!=u['id']]
   mentions=list(dict.fromkeys(mentions)); i=nid(d); m={'id':i,'channel_id':c['id'],'author_id':u['id'],'body':body,'parent_id':b.get('parent_id'),'created_at':now(),'mentions':mentions}; d['messages'][str(i)]=m; ev=event(d,c['id'],'message.reply' if m.get('parent_id') else 'message.created',{'message':msgobj(d,m)}); save(d); return self.sendj(201,{'message':msgobj(d,m)})
  m=re.match(r'/api/messages/(\d+)/(pin|reactions/[^/]+)$',p.path)
  if m:
   mi=int(m.group(1))
   if m.group(2)=='pin':
    d['pins'][str(mi)]={'id':mi,'message_id':mi,'channel_id':d['messages'][str(mi)]['channel_id'],'pinned_by':u['id'],'pinned_at':now()}; save(d); return self.sendj(200,{'pin':d['pins'][str(mi)]})
  m=re.match(r'/api/channels/(\d+)/read$',p.path)
  if m:
   k=f'{u["id"]}:{m.group(1)}'; d['reads'][k]=max(d['reads'].get(k,0),int(b.get('last_read_event_id',0))); save(d); return self.sendj(200,{'read_state':self.readstate(d,u['id'],int(m.group(1)))})
  return self.sendj(404,{})
 def do_POST(self):
  with H.write_lock:
   with open(LOCK,'a+') as lf:
    fcntl.flock(lf,fcntl.LOCK_EX)
    try:
     return self.do_POST_locked()
    finally:
     fcntl.flock(lf,fcntl.LOCK_UN)

 def do_PATCH(self):
  p=urlparse(self.path); d=load(); u=self.auth(d)
  if not u:return self.sendj(401,{})
  b=self.body()
  m=re.match(r'/api/channels/(\d+)$',p.path)
  if m:
   c=d['channels'].get(m.group(1)); w=d['workspaces'][str(c['workspace_id'])]
   if u['id'] not in c['members'] and w['owner_id']!=u['id']:return self.sendj(403,{})
   if len(b.get('topic',''))>250:return self.sendj(400,{})
   c['topic']=b.get('topic',c['topic']); event(d,c['id'],'channel.updated',{'channel':chobj(c)}); save(d); return self.sendj(200,{'channel':chobj(c)})
  return self.sendj(404,{})
 def do_DELETE(self):
  p=urlparse(self.path); d=load(); u=self.auth(d)
  if not u:return self.sendj(401,{})
  m=re.match(r'/api/messages/(\d+)/pin$',p.path)
  if m:d['pins'].pop(m.group(1),None); save(d); return self.sendj(200,{'unpinned':True})
  return self.sendj(404,{})
 def ws(self,d,q):
  t=q.get('token',[''])[0]; uid=d['tokens'].get(t)
  if not uid:
   self.send_response(401); self.end_headers(); return
  key=self.headers.get('Sec-WebSocket-Key','')
  acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',acc); self.end_headers()
  def frame(x):
   b=json.dumps(x,separators=(',',':')).encode(); n=len(b)
   if n<126: h=bytes([129,n])
   elif n<65536: h=bytes([129,126])+struct.pack('>H',n)
   else: h=bytes([129,127])+struct.pack('>Q',n)
   self.wfile.write(h+b); self.wfile.flush()
  self.connection.setblocking(False); subscribed={}; sent={}
  while True:
   try:
    readable,_,_=select.select([self.connection],[],[],0.15)
    if readable:
     h=self.connection.recv(2)
     if not h: break
     n=h[1]&127
     if n==126: n=struct.unpack('>H',self.connection.recv(2))[0]
     elif n==127: n=struct.unpack('>Q',self.connection.recv(8))[0]
     mask=self.connection.recv(4); data=self.connection.recv(n)
     while len(data)<n:
      more=self.connection.recv(n-len(data))
      if not more: break
      data+=more
     data=bytes(data[i]^mask[i%4] for i in range(len(data)))
     z=json.loads(data); cid=int(z.get('channel_id'))
     dd=load(); evs=dd.get('events',{}).get(str(cid),[])
     since=int(z.get('since_event_id',0))
     if z.get('type')=='subscribe':
      subscribed[cid]=True; sent[cid]=0
      frame({'type':'subscribed','channel_id':cid,'head_event_id':len(evs)})
     elif z.get('type')=='resume':
      subscribed[cid]=True; sent[cid]=since
      if since < max(0,len(evs)-1000): frame({'type':'resume.gap','channel_id':cid,'earliest_event_id':max(1,len(evs)-999)})
      for e in evs[since:]: frame(e)
      sent[cid]=len(evs); frame({'type':'resumed','channel_id':cid,'head_event_id':len(evs)})
    for cid in list(subscribed):
     dd=load(); evs=dd.get('events',{}).get(str(cid),[]); pos=sent.get(cid,0)
     for e in evs[pos:]: frame(e)
     sent[cid]=len(evs)
   except (BrokenPipeError,ConnectionResetError,ValueError, OSError, json.JSONDecodeError):
    break

def main():
 port=int(os.getenv('PORT','8000')); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__':main()
