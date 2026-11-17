import os, time, json, sqlite3, hashlib, dataclasses, logging, imaplib, ssl, base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import format_datetime
from email.policy import SMTP
from pathlib import Path
from urllib.parse import urlsplit

os.umask(0o077)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
VERSION = "0.4.2"

def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def dump(x): return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)
def clean(x): return ' '.join(str(x or '').splitlines())
def first(d,*keys): return next((d[k] for k in keys if d.get(k) is not None and d[k] != ''), None)
def asdict(x): return x if isinstance(x,dict) else dataclasses.asdict(x) if dataclasses.is_dataclass(x) else vars(x)
def db_open(path):
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS snapshots (hash TEXT PRIMARY KEY, chat TEXT NOT NULL, payload TEXT NOT NULL, captured TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS messages (chat TEXT, id TEXT, meta TEXT, payload TEXT, PRIMARY KEY(chat,id))')
    db.execute('CREATE TABLE IF NOT EXISTS chat_state (chat TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, checked REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS attachments (chat TEXT, message TEXT, position INTEGER, metadata TEXT NOT NULL, hash TEXT, PRIMARY KEY(chat,message,position))')
    db.commit()
    return db

def save(db,c,messages):
    cid=str(c['id']); raw=dump({'conversation':c,'messages':messages})
    added=existing=missing=0
    with db:
        db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)',(digest(raw),cid,raw,datetime.now(timezone.utc).isoformat()))
        for m in messages:
            mid=first(m.get('raw') or {},'messageId')
            if mid is None:
                missing+=1
                continue
            args=(cid,str(mid),dump(c),dump(m))
            cur=db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?)',args)
            if cur.rowcount: added+=1
            else:
                existing+=1
                db.execute('UPDATE messages SET meta=?,payload=? WHERE chat=? AND id=?',(args[2],args[3],cid,str(mid)))
    return added,existing,missing

def fingerprint(c):
    raw=c.get('raw') or {}
    latest=raw.get('unichatLatestMessage')
    if not latest or not raw.get('receivedDate'): return None
    return digest(dump([latest,raw.get('receivedDate'),raw.get('textShortTrimmed'),c.get('ad_title'),raw.get('adStatus')]))

class AttachmentHTTPError(Exception):
    def __init__(self,status): self.status=status

def download_attachment(url,folder,api):
    parsed=urlsplit(url)
    host=parsed.hostname or ''
    if parsed.scheme!='https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443) or host not in ('api.kleinanzeigen.de','gateway.kleinanzeigen.de'):
        raise ValueError('Unsupported attachment host')
    folder.mkdir(parents=True,exist_ok=True)
    limit=50*1024*1024
    import tempfile
    tmp=None
    try:
        headers=api._headers(authed=True,gateway=host=='gateway.kleinanzeigen.de')
        headers.update({'Accept':'*/*','Accept-Encoding':'identity'})
        api._throttle()
        with api._s.stream('GET',url,headers=headers,allow_redirects=False,timeout=30) as response:
            api._last=time.time()
            if response.status_code!=200: raise AttachmentHTTPError(response.status_code)
            content_type=response.headers.get('Content-Type','').lower().split(';')[0].strip()
            if content_type in ('text/html','application/json','application/problem+json'): raise ValueError('Unexpected response type')
            expected=response.headers.get('Content-Length')
            if expected is not None and int(expected)>limit: raise ValueError('Attachment too large')
            with tempfile.NamedTemporaryFile(dir=folder,delete=False) as f:
                tmp=Path(f.name); h=hashlib.sha256(); size=0
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk: continue
                    size+=len(chunk)
                    if size>limit: raise ValueError('Attachment too large')
                    h.update(chunk); f.write(chunk)
                if not size: raise ValueError('Empty attachment')
                if expected is not None and int(expected)!=size: raise ValueError('Incomplete attachment')
        key=h.hexdigest(); dest=folder/key
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=key: tmp.replace(dest)
        return key
    finally:
        if tmp is not None: tmp.unlink(missing_ok=True)

def fetch_attachment(item,folder,api):
    for url in dict.fromkeys(v for k in ('gatewayUrl','url','imageURL','imageUrl','downloadUrl') if isinstance((v:=item.get(k)),str) and v):
        try: return download_attachment(url,folder,api)
        except AttachmentHTTPError as e:
            logging.warning('Attachment not downloaded (HTTP %d)',e.status)
            if e.status in (429,500,502,503,504): break
        except Exception as e:
            logging.warning('Attachment not downloaded (%s)',type(e).__name__)
            break
    return None

def archive_attachments(db,cid,messages,folder,api):
    for m in messages:
        raw=m.get('raw') or {}; mid=raw.get('messageId')
        if mid is None: continue
        for i,item in enumerate(raw.get('attachments') or []):
            meta=dump(item)
            old=db.execute('SELECT metadata,hash FROM attachments WHERE chat=? AND message=? AND position=?',(cid,str(mid),i)).fetchone()
            if old and old[0]==meta and old[1] and (folder/old[1]).is_file(): continue
            key=None
            if isinstance(item,dict) and os.getenv('DOWNLOAD_ATTACHMENTS','yes')=='yes':
                key=fetch_attachment(item,folder,api)
                time.sleep(2)
            if key is None and old and old[1] and (folder/old[1]).is_file(): key=old[1]
            with db:
                db.execute('INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?)',(cid,str(mid),i,meta,key))

def backup(db,api=None,folder=Path('/data/attachments')):
    if api is None:
        from kleinanzeigen_api import KleinanzeigenAPI, Authenticator
        auth=Authenticator()
        if not auth.logged_in: raise RuntimeError('Login required')
        api=KleinanzeigenAPI(authenticator=auth)
    count=added=existing=missing=skipped=0
    period=max(3600,int(os.getenv('FULL_SCAN_SECONDS','86400')))
    for c in api.conversations():
        c=asdict(c); cid=str(c['id']); count+=1
        fp=fingerprint(c); now=time.time()
        state=db.execute('SELECT fingerprint,checked FROM chat_state WHERE chat=?',(cid,)).fetchone()
        pending=db.execute('SELECT 1 FROM attachments WHERE chat=? AND hash IS NULL LIMIT 1',(cid,)).fetchone()
        if fp and state and fp==state[0] and 0 <= now-state[1]<period and not (pending and os.getenv('DOWNLOAD_ATTACHMENTS','yes')=='yes'):
            skipped+=1; continue
        try:
            messages=[asdict(m) for m in api.messages(cid)]
            a,e,m=save(db,c,messages)
            archive_attachments(db,cid,messages,folder,api)
            if fp:
                with db: db.execute('INSERT OR REPLACE INTO chat_state VALUES (?,?,?)',(cid,fp,now))
        except Exception:
            logging.error('Failed while processing conversation #%d',count)
            raise
        added+=a; existing+=e; missing+=m
        logging.info('Conversation %d: %d new, %d existing, %d without ID (snapshot only)',count,a,e,m)
        time.sleep(2)
    logging.info('Backup complete: %d conversations, %d skipped, %d new, %d existing, %d without ID',count,skipped,added,existing,missing)
    chats,total=db.execute('SELECT count(DISTINCT chat),count(*) FROM messages').fetchone()
    files,refs,pending=db.execute('SELECT count(DISTINCT hash),count(hash),count(*)-count(hash) FROM attachments').fetchone()
    logging.info('Archive totals: %d conversations, %d messages, %d unique files, %d attachment references, %d pending',chats,total,files,refs,pending)

def date(m):
    value=first(m,'received_at','sent_at','timestamp','created_at','date')
    if not isinstance(value,str): raise ValueError('Unsupported timestamp')
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('Missing timezone')
    return d

def message_id(cid,mid): return '<ka-'+digest(cid+'\0'+mid)+'@archive.invalid>'
def make_mail(cid,mid,c,m,own,root,previous):
    d=date(m); direction=m.get('direction')
    if direction not in ('received','sent'): raise ValueError('Unknown direction')
    role=c.get('role'); raw=c.get('raw') or {}
    uid=raw.get('userIdBuyer') if role=='SELLER' else raw.get('userIdSeller') if role=='BUYER' else None
    local=str(uid) if uid is not None else 'chat-'+digest(cid)[:24]
    if not local.isascii() or not local.replace('-','').isalnum(): local=digest(local)
    peer=Address(clean(c.get('counterparty')),local,'kleinanzeigen.invalid')
    ownaddr=Address(addr_spec=own); e=EmailMessage(policy=SMTP)
    e['From'],e['To']=(peer,ownaddr) if direction=='received' else (ownaddr,peer)
    title=clean(c.get('ad_title')) or 'Kleinanzeigen'
    e['Subject']=title; e['Thread-Topic']=title
    ticks=int((root.astimezone(timezone.utc)-datetime(1601,1,1,tzinfo=timezone.utc)).total_seconds()*10000000)
    index=b'\x01'+(ticks>>24).to_bytes(5,'big')+bytes.fromhex(digest(cid)[:32])
    if previous: index+=b'\x00'+bytes.fromhex(digest(mid)[:8])
    e['Thread-Index']=base64.b64encode(index).decode()
    e['Date']=format_datetime(d); e['Message-ID']=message_id(cid,mid)
    if previous: e['In-Reply-To']=previous; e['References']=previous
    e['X-Kleinanzeigen-Conversation-ID']=clean(cid); e['X-Kleinanzeigen-Message-ID']=clean(mid)
    e['X-Kleinanzeigen-Direction']=direction
    e.set_content(str(m.get('text') or ''))
    return e,d

def export(db,mode):
    # Intentionally locked until historical reconciliation is implemented.
    if mode=='sync': raise RuntimeError('IMAP writes disabled in initial release')
    own=os.environ['OWN_EMAIL']; folder=Path('/data/preview'); folder.mkdir(exist_ok=True)
    for (cid,) in db.execute('SELECT DISTINCT chat FROM messages'):
        rows=[(mid,json.loads(c),json.loads(m)) for mid,c,m in db.execute('SELECT id,meta,payload FROM messages WHERE chat=?',(cid,))]
        try: rows.sort(key=lambda r:date(r[2]))
        except ValueError: logging.warning('Skipping conversation with unsupported timestamp'); continue
        previous=None; root=date(rows[0][2]); title=rows[0][1].get('ad_title')
        for mid,c,m in rows:
            c['ad_title']=title
            try: e,d=make_mail(cid,mid,c,m,own,root,previous)
            except ValueError: logging.warning('Skipping unsupported message'); continue
            (folder/(digest(cid+'\0'+mid)+'.eml')).write_bytes(e.as_bytes()); previous=str(e['Message-ID'])

def main():
    logging.info('Kleinanzeigen Mail Sync v%s', VERSION)
    logging.info('Git commit: %s', os.getenv('BUILD_REVISION', 'unknown'))
    mode=os.getenv('MODE','backup'); interval=max(900,int(os.getenv('INTERVAL_SECONDS','3600')))
    if mode not in ('backup','preview','sync') or mode=='sync': raise SystemExit('Unsupported mode: IMAP writes locked')
    if mode=='backup' and os.getenv('ENABLE_LIVE_API')!='yes': raise SystemExit('Live API disabled; review README')
    logging.info('Mode: %s', mode)
    logging.info('Sync interval: %d seconds', interval)
    logging.info('Full scan interval: %d seconds', max(3600,int(os.getenv('FULL_SCAN_SECONDS','86400'))))
    logging.info('Attachment download: %s', 'enabled' if os.getenv('DOWNLOAD_ATTACHMENTS','yes')=='yes' else 'disabled')
    db=db_open('/data/archive.sqlite3')
    while True:
        try:
            if mode=='backup': backup(db)
            else: export(db,mode); return
        except Exception as e:
            logging.error('Operation failed (%s); stopped to avoid repeated access attempts',type(e).__name__)
            import traceback
            for frame in traceback.extract_tb(e.__traceback__):
                logging.error('Location: %s:%d (%s)',Path(frame.filename).name,frame.lineno,frame.name)
            # Keep container alive without retrying until manually restarted.
            while True: time.sleep(3600)
        time.sleep(interval)
if __name__=='__main__': main()
