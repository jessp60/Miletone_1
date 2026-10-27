#!/usr/bin/env python3
import sys, os, json, re, time, uuid, hashlib, secrets, fcntl
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

DB='/app/state.json'
LOCK='/app/state.lock'
def now(): return time.time()
def load():
    os.makedirs('/app',exist_ok=True)
    if not os.path.exists(DB):
        return {'users':{},'tokens':{},'workspaces':{},'channels':{},'invitations':{},'next':1}
    try:
        with open(DB) as f: return json.load(f)
    except: return {'users':{},'tokens':{},'workspaces':{},'channels':{},'invitations':{},'next':1}
def save(s):
    t=DB+'.tmp'
    with open(t,'w') as f: json.dump(s,f)
    os.replace(t,DB)
def mutate(fn):
    with open(LOCK,'a+') as lf:
        fcntl.flock(lf,fcntl.LOCK_EX); s=load(); result=fn(s); save(s); fcntl.flock(lf,fcntl.LOCK_UN); return result
def nid(s):
    x=s['next']; s['next']=x+1; return x
def user_obj(u):
    return {'id':u['id'],'username':u['username'],'display_name':u.get('display_name') or u['username'],
      'timezone':u.get('timezone','UTC'),'avatar_url':u.get('avatar_url',''),'status_text':u.get('status_text',''),'status_emoji':u.get('status_emoji','')}
def channel_obj(c): return {k:c[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def ws_obj(w): return {k:w[k] for k in ('id','slug','name','owner_id','join_mode')}
def response_user(s,uid): return user_obj(s['users'][str(uid)])
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def sendj(self,code,obj):
        b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def readj(self):
        try:
            n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
        except: return {}
    def auth(self,s):
        h=self.headers.get('Authorization','')
        if not h.startswith('Bearer '): return None
        tok=h[7:]; uid=s.get('tokens',{}).get(tok)
        return s['users'].get(str(uid)) if uid is not None else None
    def err(self,c): self.sendj(c,{'error':'error'})
    def ids(self,path): return [unquote(x) for x in path.split('/') if x]
    def do_GET(self): self.route('GET')
    def do_POST(self): self.route('POST')
    def do_PATCH(self): self.route('PATCH')
    def do_DELETE(self): self.route('DELETE')
    def route(self,method):
      p=urlparse(self.path).path; a=self.ids(p)
      if p=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
      if p.startswith('/api/ws'):
          return self.err(401)
      def run(s):
        u=self.auth(s)
        if a[:3]==['api','auth','register'] and method=='POST':
          d=self.readj(); name=d.get('username',''); pw=d.get('password','')
          if not re.fullmatch(r'[A-Za-z0-9_]+',name) or len(pw)<8: return self.err(400)
          if any(x.get('username')==name for x in s['users'].values()): return self.err(409)
          uid=nid(s); s['users'][str(uid)]={'id':uid,'username':name,'password':pw,'display_name':d.get('display_name') or name}
          tok=secrets.token_urlsafe(24); s['tokens'][tok]=uid
          return self.sendj(201,{'user':user_obj(s['users'][str(uid)]),'token':tok})
        if a[:3]==['api','auth','login'] and method=='POST':
          d=self.readj(); z=next((x for x in s['users'].values() if x.get('username')==d.get('username')),None)
          if not z or z.get('password',d.get('password'))!=d.get('password'): 
            # registrations store password below; compatibility for old state
            if not z or z.get('password')!=d.get('password'): return self.err(401)
          tok=secrets.token_urlsafe(24); s['tokens'][tok]=z['id']; return self.sendj(200,{'user':user_obj(z),'token':tok})
        if a[:3]==['api','auth','me'] and method=='GET':
          return self.sendj(200,{'user':user_obj(u)}) if u else self.err(401)
        if not u: return self.err(401)
        if a[:3]==['api','users','me'] and method=='PATCH':
          d=self.readj()
          if 'timezone' in d and (not isinstance(d['timezone'],str) or len(d['timezone'])>64): return self.err(400)
          z=s['users'][str(u['id'])]; z.update({k:v for k,v in d.items() if k in ('display_name','timezone','avatar_url','status_text','status_emoji')})
          return self.sendj(200,{'user':user_obj(z)})
        if a[:2]==['api','workspaces'] and len(a)==2 and method=='GET':
          out=[ws_obj(w) for w in s['workspaces'].values() if str(u['id']) in w['members']]
          return self.sendj(200,{'workspaces':out})
        if a[:2]==['api','workspaces'] and len(a)==2 and method=='POST':
          d=self.readj(); slug=d.get('slug','')
          if not re.fullmatch(r'[a-z0-9-]{2,32}',slug): return self.err(400)
          if any(w['slug']==slug for w in s['workspaces'].values()): return self.err(409)
          wid=nid(s); cid=nid(s); w={'id':wid,'slug':slug,'name':d.get('name',''),'owner_id':u['id'],'join_mode':'open','members':{str(u['id']):'owner'}}
          c={'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False,'members':[u['id']]}
          s['workspaces'][str(wid)]=w; s['channels'][str(cid)]=c
          return self.sendj(201,{'workspace':ws_obj(w),'general_channel':channel_obj(c)})
        if a[:2]==['api','workspaces'] and len(a)>=3:
          slug=a[2]; w=next((x for x in s['workspaces'].values() if x['slug']==slug),None)
          if not w:return self.err(404)
          role=w['members'].get(str(u['id']))
          if len(a)==3 and method=='GET':
            cs=[channel_obj(c) for c in s['channels'].values() if c['workspace_id']==w['id'] and (not c['is_private'] or str(u['id']) in map(str,c['members']))]
            return self.sendj(200,{'workspace':ws_obj(w),'channels':cs,'read_state':{}})
          if len(a)==3 and method=='PATCH':
            if role not in ('owner','admin'): return self.err(403)
            d=self.readj()
            if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'): return self.err(400)
            w.update({k:d[k] for k in ('name','join_mode') if k in d}); return self.sendj(200,{'workspace':ws_obj(w)})
          if len(a)==4 and a[3]=='members' and method=='GET':
            return self.sendj(200,{'members':[{'user_id':int(x),'role':r} for x,r in w['members'].items()]})
          if len(a)==5 and a[3]=='members' and method=='PATCH':
            target=a[4]; d=self.readj(); nr=d.get('role')
            if nr not in ('owner','admin','member','guest'): return self.err(400)
            if nr=='owner': return self.err(400)
            if target not in w['members']: return self.err(404)
            tr=w['members'][target]
            if target==str(w['owner_id']) or target==str(u['id']): return self.err(403)
            if role not in ('owner','admin') or (role=='admin' and tr in ('owner','admin')): return self.err(403)
            w['members'][target]=nr; return self.sendj(200,{'member':{'user_id':int(target),'role':nr}})
          if len(a)==4 and a[3]=='transfer_ownership' and method=='POST':
            if role!='owner': return self.err(403)
            tid=str(self.readj().get('new_owner_id'))
            if w['members'].get(tid)!='admin': return self.err(400)
            w['members'][str(w['owner_id'])]='admin'; w['owner_id']=int(tid); w['members'][tid]='owner'; return self.sendj(200,{'workspace':ws_obj(w)})
          if len(a)==4 and a[3]=='channels' and method=='POST':
            d=self.readj(); private=bool(d.get('is_private',False))
            if role not in ('owner','admin','member') or (private and role not in ('owner','admin')): return self.err(403)
            name=d.get('name','')
            if not re.fullmatch(r'[a-z0-9-]{1,32}',name): return self.err(400)
            if any(c['workspace_id']==w['id'] and c['name']==name for c in s['channels'].values()): return self.err(409)
            cid=nid(s); c={'id':cid,'workspace_id':w['id'],'name':name,'is_private':private,'is_dm':False,'topic':d.get('topic',''),'is_archived':False,'members':[u['id']]}
            s['channels'][str(cid)]=c; return self.sendj(201,{'channel':channel_obj(c)})
          if len(a)>=4 and a[3]=='invitations':
            if len(a)==4 and method=='POST':
              if role not in ('owner','admin'): return self.err(403)
              d=self.readj(); code=secrets.token_urlsafe(12); inv={'code':code,'workspace_id':w['id'],'created_by':u['id'],'expires_at':now()+int(d.get('expires_in_seconds',31536000)),'max_uses':int(d.get('max_uses',1)),'uses':0,'invited_user_id':d.get('invited_user_id')}
              s['invitations'][code]=inv; return self.sendj(201,{'invitation':inv})
            if len(a)==4 and method=='GET':
              if role not in ('owner','admin'): return self.err(403)
              return self.sendj(200,{'invitations':[x for x in s['invitations'].values() if x['workspace_id']==w['id'] and x['uses']<x['max_uses'] and x['expires_at']>now()]})
            if len(a)==5 and method=='DELETE':
              if role not in ('owner','admin'): return self.err(403)
              if a[4] not in s['invitations']: return self.err(404)
              del s['invitations'][a[4]]; return self.sendj(200,{'deleted':True})
        if a[:2]==['api','invitations'] and len(a)==4 and a[3]=='accept' and method=='POST':
          inv=s['invitations'].get(a[2])
          if not inv or inv['uses']>=inv['max_uses'] or inv['expires_at']<=now(): return self.err(404)
          if inv.get('invited_user_id') is not None and int(inv['invited_user_id'])!=u['id']: return self.err(403)
          w=s['workspaces'][str(inv['workspace_id'])]; w['members'][str(u['id'])]='member'; inv['uses']+=1; return self.sendj(200,{'workspace':ws_obj(w)})
        if a[:2]==['api','channels'] and len(a)==4 and a[3]=='join' and method=='POST':
          c=s['channels'].get(a[2])
          if not c:return self.err(404)
          w=next(x for x in s['workspaces'].values() if x['id']==c['workspace_id']); role=w['members'].get(str(u['id']))
          if c['is_private'] and str(u['id']) not in map(str,c['members']): return self.err(403)
          if role=='guest': return self.err(403)
          if not role:
            if w['join_mode']!='open' or c['is_private']: return self.err(403)
            w['members'][str(u['id'])]='member'
          if u['id'] not in c['members']: c['members'].append(u['id'])
          return self.sendj(200,{'channel':channel_obj(c)})
        return self.err(404)
      mutate(run)
if __name__=='__main__':
    port=int(sys.argv[1]); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
