import os, time, json, sqlite3, hashlib, dataclasses, logging, imaplib, ssl, base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import format_datetime
from email.policy import SMTP
from pathlib import Path

os.umask(0o077)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def dump(x): return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)
def clean(x): return ' '.join(str(x or '').splitlines())
def first(d,*keys): return next((d[k] for k in keys if d.get(k) is not None and d[k] != ''), None)
def asdict(x): return x if isinstance(x,dict) else dataclasses.asdict(x) if dataclasses.is_dataclass(x) else vars(x)
def db_open(path):
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS snapshots (hash TEXT PRIMARY KEY, chat TEXT NOT NULL, payload TEXT NOT NULL, captured TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS messages (chat TEXT, id TEXT, meta TEXT, payload TEXT, PRIMARY KEY(chat,id))')
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

def backup(db):
    from kleinanzeigen_api import KleinanzeigenAPI, Authenticator
    auth=Authenticator()
    if not auth.logged_in: raise RuntimeError('Login required')
    api=KleinanzeigenAPI(authenticator=auth)
    count=added=existing=missing=0
    for c in api.conversations():
        c=asdict(c)
        try:
            messages=[asdict(m) for m in api.messages(c['id'])]
            a,e,m=save(db,c,messages)
        except Exception:
            logging.error('Failed while processing conversation #%d',count+1)
            raise
        count+=1; added+=a; existing+=e; missing+=m
        logging.info('Conversation %d: %d new, %d existing, %d without ID (snapshot only)',count,a,e,m)
        time.sleep(2)
    logging.info('Backup complete: %d conversations, %d new, %d existing, %d without ID',count,added,existing,missing)

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
    mode=os.getenv('MODE','backup'); interval=max(900,int(os.getenv('INTERVAL_SECONDS','3600')))
    if mode not in ('backup','preview','sync') or mode=='sync': raise SystemExit('Unsupported mode: IMAP writes locked')
    if mode=='backup' and os.getenv('ENABLE_LIVE_API')!='yes': raise SystemExit('Live API disabled; review README')
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
