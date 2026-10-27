import os, re, json, time, uuid, sqlite3, hashlib, base64, socket, struct, threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

DB='/app/huddle.db'
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,timeout=8,check_same_thread=False); c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=8000'); return c
def init():
    c=db()
    c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires INTEGER,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,kind TEXT,payload TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
    ''')
    try: c.execute("ALTER TABLE messages ADD COLUMN mentions TEXT DEFAULT '[]'")
    except sqlite3.OperationalError: pass
    c.commit(); c.close()
init()
def q(sql,args=(),one=False):
    c=db(); r=c.execute(sql,args); z=r.fetchone() if one else r.fetchall(); c.close(); return z
def ex(sql,args=()):
    for _ in range(12):
        c=db()
        try:
            r=c.execute(sql,args); c.commit(); x=r.lastrowid; c.close(); return x
        except sqlite3.OperationalError as e:
            try: c.rollback()
            except Exception: pass
            c.close()
            if 'locked' not in str(e).lower(): raise
            time.sleep(0.08)
        except Exception:
            try: c.rollback()
            except Exception: pass
            c.close()
            raise
    raise sqlite3.OperationalError('database is locked')
def userobj(r):
    return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def auth(h):
    a=h.headers.get('Authorization','')
    if not a.startswith('Bearer '): return None
    r=q('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(a[7:],),True)
    return r
def chan(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def ws(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def member(cid,uid): return bool(q('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,uid),True))
def wsm(wid,uid): return q('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(wid,uid),True)
def role(wid,uid):
    r=wsm(wid,uid); return r['role'] if r else None
def err(h,n,msg):
    b=json.dumps({'error':msg}).encode(); h.send_response(n); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
def event(cid,kind,payload):
    c=db(); c.execute('BEGIN IMMEDIATE'); x=c.execute('SELECT COALESCE(MAX(event_id),0)+1 FROM events WHERE channel_id=?',(cid,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(cid,x,kind,json.dumps(payload),now())); c.commit(); c.close(); return x
def paging(qs):
    try: lim=int(qs.get('limit',['50'])[0])
    except: raise ValueError()
    if lim<1 or lim>200: raise ValueError()
    cur=qs.get('cursor',[None])[0]
    if cur is None: off=0
    else:
        try: off=int(base64.urlsafe_b64decode(cur+'===').decode())
        except: raise ValueError()
    return lim,off
def curstr(n): return base64.urlsafe_b64encode(str(n).encode()).decode().rstrip('=')
def resolved_mentions(body,cid,author):
    c=q('SELECT workspace_id FROM channels WHERE id=?',(cid,),True); out=[]
    if not c:return out
    for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',body):
        if name in ('channel','here'):
            out += [x['user_id'] for x in q('SELECT user_id FROM cmembers WHERE channel_id=?',(cid,)) if x['user_id']!=author]
        else:
            x=q('SELECT id FROM users WHERE username=?',(name,),True)
            if x and x['id']!=author and wsm(c['workspace_id'],x['id']) and member(cid,x['id']): out.append(x['id'])
    return list(dict.fromkeys(out))
def msgobj(r):
    a=q('SELECT * FROM users WHERE id=?',(r['author_id'],),True)
    rs=q('SELECT emoji,user_id FROM reactions WHERE message_id=?',(r['id'],))
    d={}
    for x in rs: d.setdefault(x['emoji'],[]).append(x['user_id'])
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':q('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],),True)['n'],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in d.items()],'mentions':json.loads(r['mentions']) if 'mentions' in r.keys() and r['mentions'] else []}
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def read(self):
        self.bad_json=False
        n=int(self.headers.get('Content-Length','0'))
        try: return json.loads(self.rfile.read(n) or b'{}')
        except Exception: self.bad_json=True; return {}
    def out(self,x,code=200):
        b=json.dumps(x).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p=urlparse(self.path); path=p.path; qs=parse_qs(p.query)
        if path=='/api/health': return self.out({'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
        if path=='/': return self.html()
        if path=='/api/ws': return self.websocket(qs.get('token',[''])[0])
        u=auth(self)
        if path=='/api/auth/me':
            if not u:return err(self,401,'unauthorized')
            return self.out({'user':userobj(u)})
        if path=='/api/workspaces':
            if not u:return err(self,401,'unauthorized')
            return self.out({'workspaces':[ws(x) for x in q('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))]})
        m=re.match(r'^/api/users/(\d+)$',path)
        if m:
            r=q('SELECT * FROM users WHERE id=?',(m.group(1),),True); return self.out({'user':userobj(r)}) if r else err(self,404,'not found')
        if not u:return err(self,401,'unauthorized')
        m=re.match(r'^/api/workspaces/([^/]+)(?:/(.*))?$',path)
        if m:
            slug,tail=m.group(1),m.group(2); w=q('SELECT * FROM workspaces WHERE slug=?',(slug,),True)
            if not w:return err(self,404,'not found')
            if tail=='members': return self.out({'members':[dict(x) for x in q('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
            if tail=='invitations':
                if role(w['id'],u['id']) not in ('owner','admin'):return err(self,403,'forbidden')
                return self.out({'invitations':[dict(x) for x in q('SELECT code,workspace_id,invited_user_id,expires,max_uses,uses FROM invites WHERE workspace_id=? AND revoked=0 AND uses<max_uses',(w['id'],))]})
            if tail=='groups': return self.out({'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in q('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],))]})
            cs=[chan(x) for x in q('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],1 if qs.get('include_archived') else 0)) if not x['is_private'] or member(x['id'],u['id'])]
            states=[]
            for chx in q('SELECT id FROM channels WHERE workspace_id=?',(w['id'],)):
                rr=q('SELECT last_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],chx['id']),True)
                states.append({'channel_id':chx['id'],'last_read_event_id':rr['last_event_id'] if rr else 0,'unread_count':0,'mention_count':0})
            return self.out({'workspace':ws(w),'channels':cs,'read_state':states})
        m=re.match(r'^/api/channels/(\d+)(?:/(.*))?$',path)
        if m:
            cid,tail=int(m.group(1)),m.group(2); c=q('SELECT * FROM channels WHERE id=?',(cid,),True)
            if not c:return err(self,404,'not found')
            if tail=='members': return self.out({'members':[userobj(q('SELECT * FROM users WHERE id=?',(x['user_id'],),True)) for x in q('SELECT user_id FROM cmembers WHERE channel_id=?',(cid,))]})
            if tail=='messages':
                try: lim,off=paging(qs)
                except: return err(self,400,'invalid paging')
                rs=q('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<? ORDER BY id DESC LIMIT ?',(cid,10**18 if off==0 else off,lim+1)) if off else q('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT ?',(cid,lim+1)); more=len(rs)>lim; rs=rs[:lim]
                return self.out({'messages':[msgobj(x) for x in rs],'next_cursor':curstr(rs[-1]['id']) if more and rs else None})
            if tail=='pins':
                if c['is_private'] and not member(cid,u['id']) and role(c['workspace_id'],u['id'])!='owner': return err(self,403,'forbidden')
                try: lim,off=paging(qs)
                except: return err(self,400,'invalid paging')
                ps=q('SELECT * FROM pins WHERE message_id IN (SELECT id FROM messages WHERE channel_id=?) ORDER BY message_id DESC LIMIT ? OFFSET ?',(cid,lim+1,off)); more=len(ps)>lim; ps=ps[:lim]
                return self.out({'pins':[{'message':msgobj(q('SELECT * FROM messages WHERE id=?',(x['message_id'],),True)),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in ps],'next_cursor':curstr(off+lim) if more else None})
            if tail=='read':
                r=q('SELECT * FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid),True); last=r['last_event_id'] if r else 0
                mc=sum(1 for x in q('SELECT mentions FROM messages WHERE channel_id=? AND deleted=0',(cid,)) if x['mentions'] and u['id'] in json.loads(x['mentions']))
                return self.out({'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':0,'mention_count':mc}})
            return self.out({'channel':chan(c)})
        m=re.match(r'^/api/messages/(\d+)(?:/(.*))?$',path)
        if m:
            r=q('SELECT * FROM messages WHERE id=?',(m.group(1),),True)
            if not r:return err(self,404,'not found')
            tail=m.group(2)
            if tail=='replies':
                try: lim,off=paging(qs)
                except: return err(self,400,'invalid paging')
                rs=q('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id LIMIT ? OFFSET ?',(r['id'],lim+1,off)); more=len(rs)>lim; rs=rs[:lim]
                return self.out({'replies':[msgobj(x) for x in rs],'next_cursor':curstr(off+lim) if more else None})
            return self.out({'message':msgobj(r)})
        if path.startswith('/api/search'):
            try: lim,off=paging(qs)
            except: return err(self,400,'invalid paging')
            term=qs.get('q',[''])[0]; wid=qs.get('workspace_id',[None])[0]
            sql='SELECT m.* FROM messages m JOIN channels c ON c.id=m.channel_id JOIN cmembers cm ON cm.channel_id=c.id AND cm.user_id=? WHERE m.body LIKE ? AND m.deleted=0'
            args=[u['id'],f'%{term}%']
            if wid: sql+=' AND c.workspace_id=?'; args.append(wid)
            if off: sql+=' AND m.id<?'; args.append(off)
            sql+=' ORDER BY m.id DESC LIMIT ?'; args.append(lim+1)
            rs=q(sql,args); more=len(rs)>lim; rs=rs[:lim]
            return self.out({'results':[msgobj(x) for x in rs],'next_cursor':curstr(rs[-1]['id']) if more and rs else None})
        return err(self,404,'not found')
    def do_POST(self):
        path=urlparse(self.path).path; b=self.read()
        if self.bad_json:return err(self,400,'malformed json')
        u=auth(self)
        if path=='/api/auth/register':
            un=b.get('username',''); pw=b.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return err(self,400,'invalid')
            try: uid=ex('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,pw,b.get('display_name',un)))
            except: return err(self,409,'duplicate')
            t=uuid.uuid4().hex+uuid.uuid4().hex; ex('INSERT INTO tokens VALUES(?,?)',(t,uid)); return self.out({'user':userobj(q('SELECT * FROM users WHERE id=?',(uid,),True)),'token':t},201)
        if path=='/api/auth/login':
            r=q('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password')),True)
            if not r:return err(self,401,'invalid')
            t=uuid.uuid4().hex+uuid.uuid4().hex; ex('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); return self.out({'user':userobj(r),'token':t})
        if path=='/api/auth/me': return err(self,401,'unauthorized') if not u else self.out({'user':userobj(u)})
        if not u:return err(self,401,'unauthorized')
        if path=='/api/workspaces':
            s=b.get('slug',''); 
            if not re.fullmatch(r'[a-z0-9-]{2,32}',s) or not b.get('name'):return err(self,400,'invalid')
            try: wid=ex('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(s,b['name'],u['id']))
            except:return err(self,409,'duplicate')
            ex('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); cid=ex('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); ex('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']))
            return self.out({'workspace':ws(q('SELECT * FROM workspaces WHERE id=?',(wid,),True)),'general_channel':chan(q('SELECT * FROM channels WHERE id=?',(cid,),True))},201)
        m=re.match(r'^/api/workspaces/([^/]+)/channels$',path)
        if m:
            w=q('SELECT * FROM workspaces WHERE slug=?',(m.group(1),),True)
            if not w:return err(self,404,'not found')
            rr=role(w['id'],u['id'])
            if rr not in ('owner','admin','member') or (b.get('is_private') and rr not in ('owner','admin')):return err(self,403,'forbidden')
            if rr=='guest':return err(self,403,'forbidden')
            if len(b.get('topic',''))>250:return err(self,400,'invalid')
            if not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')):return err(self,400,'invalid')
            if q('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name']),True):return err(self,409,'duplicate')
            cid=ex('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')))
            ex('INSERT INTO cmembers VALUES(?,?)',(cid,u['id'])); return self.out({'channel':chan(q('SELECT * FROM channels WHERE id=?',(cid,),True))},201)
        m=re.match(r'^/api/channels/(\d+)/(join|leave|archive|unarchive|read)$',path)
        if m:
            cid,op=int(m.group(1)),m.group(2); c=q('SELECT * FROM channels WHERE id=?',(cid,),True)
            if not c:return err(self,404,'not found')
            rr=role(c['workspace_id'],u['id'])
            if op=='join':
                if c['is_private'] and not member(cid,u['id']):return err(self,403,'forbidden')
                if rr=='guest':return err(self,403,'forbidden')
                if not rr:
                    w=q('SELECT * FROM workspaces WHERE id=?',(c['workspace_id'],),True)
                    if w['join_mode']!='open' or c['is_private']:return err(self,403,'forbidden')
                    ex('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
                ex('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,u['id'])); event(cid,'member.joined',{'user_id':u['id']})
            elif op=='leave': ex('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id'])); event(cid,'member.left',{'user_id':u['id']})
            elif op in ('archive','unarchive'):
                if rr!='owner':return err(self,403,'forbidden')
                ex('UPDATE channels SET is_archived=? WHERE id=?',(int(op=='archive'),cid)); event(cid,'channel.archived' if op=='archive' else 'channel.unarchived',{'channel_id':cid})
            elif op=='read':
                ex('INSERT INTO reads(user_id,channel_id,last_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_event_id=MAX(reads.last_event_id,excluded.last_event_id)',(u['id'],cid,b.get('last_read_event_id',0)))
            if op!='read': return self.out({'channel':chan(q('SELECT * FROM channels WHERE id=?',(cid,),True))})
            rr=q('SELECT last_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid),True); last=rr['last_event_id'] if rr else 0
            return self.out({'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':0,'mention_count':0}})
        m=re.match(r'^/api/channels/(\d+)/messages$',path)
        if m:
            cid=int(m.group(1)); c=q('SELECT * FROM channels WHERE id=?',(cid,),True)
            if not c:return err(self,404,'not found')
            if c['is_archived']:return err(self,423,'archived')
            if not member(cid,u['id']):return err(self,403,'forbidden')
            body=b.get('body','')
            if not body:return err(self,400,'empty')
            if body.startswith('/') and not body.startswith('//') and body.split()[0] not in ('/me','/shrug'): return err(self,400,'unknown command')
            if body.startswith('/shrug'): body=body[6:].lstrip()+' ¯\\\\_(ツ)_/¯'
            mid=ex('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,u['id'],body,b.get('parent_id'),now(),json.dumps(resolved_mentions(body,cid,u['id']))))
            mo=msgobj(q('SELECT * FROM messages WHERE id=?',(mid,),True)); event(cid,'message.reply' if b.get('parent_id') else 'message.created',mo); return self.out({'message':mo},201)
        m=re.match(r'^/api/messages/(\d+)/(pin|reactions)$',path)
        if m:
            r=q('SELECT * FROM messages WHERE id=?',(m.group(1),),True)
            if not r:return err(self,404,'not found')
            if m.group(2)=='pin': ex('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(r['id'],u['id'],now())); return self.out({'pin':{'message_id':r['id'],'pinned_by':u['id'],'pinned_at':now()}})
            ex('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r['id'],u['id'],b.get('emoji',''))); event(r['channel_id'],'reaction.added',{'message_id':r['id']}); return self.out({'reactions':msgobj(r)['reactions']})
        m=re.match(r'^/api/workspaces/([^/]+)/invitations$',path)
        if m:
            w=q('SELECT * FROM workspaces WHERE slug=?',(m.group(1),),True)
            if not w:return err(self,404,'not found')
            if role(w['id'],u['id']) not in ('owner','admin'):return err(self,403,'forbidden')
            code=uuid.uuid4().hex; target=b.get('invited_user_id'); ex('INSERT INTO invites(code,workspace_id,invited_user_id,expires,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],target,int(time.time())+b.get('expires_in_seconds',86400),b.get('max_uses',1))); return self.out({'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':target}},201)
        m=re.match(r'^/api/invitations/([^/]+)/accept$',path)
        if m:
            i=q('SELECT * FROM invites WHERE code=?',(m.group(1),),True)
            if not i or i['revoked'] or i['uses'] >= (1 if i['invited_user_id'] else i['max_uses']) or i['expires']<time.time():return err(self,404,'invalid')
            if i['invited_user_id'] and i['invited_user_id']!=u['id']:return err(self,403,'forbidden')
            ex('INSERT OR IGNORE INTO members VALUES(?,?,?)',(i['workspace_id'],u['id'],'member')); ex('UPDATE invites SET uses=uses+1 WHERE code=?',(m.group(1),)); return self.out({'accepted':True})
        m=re.match(r'^/api/workspaces/([^/]+)/transfer_ownership$',path)
        if m:
            w=q('SELECT * FROM workspaces WHERE slug=?',(m.group(1),),True)
            if not w:return err(self,404,'not found')
            if role(w['id'],u['id'])!='owner':return err(self,403,'forbidden')
            target=b.get('new_owner_id')
            if role(w['id'],target)!='admin':return err(self,400,'target must be admin')
            ex("UPDATE members SET role=CASE WHEN user_id=? THEN 'owner' WHEN user_id=? THEN 'admin' ELSE role END WHERE workspace_id=?",(target,u['id'],w['id']))
            ex('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
            return self.out({'workspace':ws(q('SELECT * FROM workspaces WHERE id=?',(w['id'],),True))})
        return err(self,404,'not found')
    def do_PATCH(self):
        path=urlparse(self.path).path; b=self.read()
        if self.bad_json:return err(self,400,'malformed json')
        u=auth(self)
        if not u:return err(self,401,'unauthorized')
        m=re.match(r'^/api/workspaces/([^/]+)/members/(\d+)$',path)
        if m:
            w=q('SELECT * FROM workspaces WHERE slug=?',(m.group(1),),True)
            if not w:return err(self,404,'not found')
            rr=role(w['id'],u['id']); target=int(m.group(2)); nr=b.get('role'); tr=role(w['id'],target)
            if not tr:return err(self,404,'not found')
            if nr not in ('owner','admin','member','guest'):return err(self,400,'invalid role')
            if nr=='owner':return err(self,400,'invalid role')
            if target==u['id'] and rr=='owner':return err(self,403,'forbidden')
            if rr not in ('owner','admin') or (rr=='admin' and tr in ('owner','admin')):return err(self,403,'forbidden')
            ex('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(nr,w['id'],target)); return self.out({'member':{'user_id':target,'role':nr}})
        m=re.match(r'^/api/workspaces/([^/]+)$',path)
        if m:
            w=q('SELECT * FROM workspaces WHERE slug=?',(m.group(1),),True)
            if not w:return err(self,404,'not found')
            if role(w['id'],u['id']) not in ('owner','admin'):return err(self,403,'forbidden')
            if b.get('join_mode') is not None and b.get('join_mode') not in ('open','invite_only'):return err(self,400,'invalid join_mode')
            ex('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(b.get('name'),b.get('join_mode'),w['id'])); return self.out({'workspace':ws(q('SELECT * FROM workspaces WHERE id=?',(w['id'],),True))})
        m=re.match(r'^/api/channels/(\d+)$',path)
        if m:
            c=q('SELECT * FROM channels WHERE id=?',(m.group(1),),True)
            if not c:return err(self,404,'not found')
            w=role(c['workspace_id'],u['id'])
            if not member(c['id'],u['id']) and w!='owner':return err(self,403,'forbidden')
            if len(b.get('topic',''))>250:return err(self,400,'invalid')
            ex('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),c['id'])); event(c['id'],'channel.updated',{}); return self.out({'channel':chan(q('SELECT * FROM channels WHERE id=?',(c['id'],),True))})
        m=re.match(r'^/api/messages/(\d+)$',path)
        if m:
            r=q('SELECT * FROM messages WHERE id=?',(m.group(1),),True)
            if not r:return err(self,404,'not found')
            if r['deleted']:return err(self,409,'deleted')
            if r['author_id']!=u['id']:return err(self,403,'forbidden')
            ex('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b.get('body',''),now(),r['id'])); event(r['channel_id'],'message.updated',{'message':msgobj(q('SELECT * FROM messages WHERE id=?',(r['id'],),True))}); return self.out({'message':msgobj(q('SELECT * FROM messages WHERE id=?',(r['id'],),True))})
        return err(self,404,'not found')
    def do_DELETE(self):
        u=auth(self); path=urlparse(self.path).path
        if not u:return err(self,401,'unauthorized')
        m=re.match(r'^/api/workspaces/([^/]+)/invitations/([^/]+)$',path)
        if m:
            w=q('SELECT * FROM workspaces WHERE slug=?',(m.group(1),),True)
            if not w:return err(self,404,'not found')
            if role(w['id'],u['id']) not in ('owner','admin'):return err(self,403,'forbidden')
            ex('UPDATE invites SET revoked=1 WHERE workspace_id=? AND code=?',(w['id'],m.group(2)))
            return self.out({'deleted':True})
        m=re.match(r'^/api/messages/(\d+)/reactions/(.*)$',path)
        if m:
            r=q('SELECT * FROM messages WHERE id=?',(m.group(1),),True); ex('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],unquote(m.group(2)))); event(r['channel_id'],'reaction.removed',{}); return self.out({'reactions':msgobj(r)['reactions']})
        m=re.match(r'^/api/messages/(\d+)/pin$',path)
        if m:
            if not q('SELECT 1 FROM messages WHERE id=?',(m.group(1),),True): return err(self,404,'not found')
            ex('DELETE FROM pins WHERE message_id=?',(m.group(1),)); return self.out({'unpinned':True})
        m=re.match(r'^/api/messages/(\d+)$',path)
        if m:
            r=q('SELECT * FROM messages WHERE id=?',(m.group(1),),True)
            if not r:return err(self,404,'not found')
            ch=q('SELECT * FROM channels WHERE id=?',(r['channel_id'],),True)
            if r['author_id']!=u['id'] and role(ch['workspace_id'],u['id'])!='owner':return err(self,403,'forbidden')
            ex('UPDATE messages SET deleted=1 WHERE id=?',(m.group(1),)); event(r['channel_id'],'message.deleted',{}); return self.out({'deleted':True})
        return err(self,404,'not found')
    def html(self):
        b='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{font:16px Arial;margin:0;color:#222}button{padding:9px;border:0;color:#fff}button[data-button-role=primary]{background:#007a5a}button[data-button-role=danger]{background:#e01e5a}button[data-button-role=secondary]{background:#1264a3}.app{padding:30px}.sidebar{background:#3f0e40;color:white;padding:20px}</style></head><body><div class=app><h1>Huddle</h1><form data-testid=auth-form><input name=username placeholder=username><input name=password type=password><button data-testid=auth-submit data-button-role=primary>Login</button></form><div data-testid=channel-list></div><div data-testid=message-list></div></div></body></html>'''
        self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b.encode())
    def websocket(self,token):
        r=q('SELECT user_id FROM tokens WHERE token=?',(unquote(token),),True)
        if not r:return self.send_error(401)
        key=self.headers.get('Sec-WebSocket-Key',''); import hashlib
        accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
        self.connection.settimeout(.25); cid=None; last=0
        def send(x):
            d=json.dumps(x).encode(); n=len(d); h=bytes([129])+((n if n<126 else 126)).to_bytes(1,'big')
            if n>=126:h+=struct.pack('>H',n)
            try:self.connection.sendall(h+d)
            except: raise
        try:
            while True:
                try:
                    h=self.connection.recv(2)
                    if h:
                        n=h[1]&127; mask=self.connection.recv(4)
                        if n==126:n=struct.unpack('>H',self.connection.recv(2))[0]
                        d=self.connection.recv(n); d=bytes(x^mask[i%4] for i,x in enumerate(d)); z=json.loads(d)
                        if z.get('type') in ('subscribe','resume'):
                            cid=int(z['channel_id']); last=int(z.get('since_event_id',0)); head=q('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,),True)['n']
                            if z['type']=='subscribe': send({'type':'subscribed','channel_id':cid,'head_event_id':head})
                            else:
                                for e in q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)):
                                    x=json.loads(e['payload']); x.update(type=e['kind'],event_id=e['event_id'],channel_id=cid); send(x); last=e['event_id']
                                send({'type':'resumed','channel_id':cid,'head_event_id':head})
                except socket.timeout: pass
                if cid is not None:
                    for e in q('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)):
                        x=json.loads(e['payload']); x.update(type=e['kind'],event_id=e['event_id'],channel_id=cid); send(x); last=e['event_id']
        except: pass
def main():
    port=int(os.environ.get('PORT','8000')); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__': main()
