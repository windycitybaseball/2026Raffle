"""Windy City Raffle — independent, self-hosted raffle fundraiser starter.

Not affiliated with Raffle Creator. All live paid raffle sales are disabled until
legal eligibility and written payment-provider authorization are configured.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, Form, HTTPException, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('DATA_DIR', str(BASE / 'data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'raffles.db'
UPLOAD_DIR = DATA_DIR / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DEMO_MODE = os.getenv('DEMO_MODE', '1').lower() in ('1', 'yes', 'true')
PUBLIC_PREVIEW = os.getenv('PUBLIC_PREVIEW', '0') == '1'  # Public can view, only logged-in admins can simulate checkout
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'change-me-in-production')
SESSION_SECRET = os.getenv('SESSION_SECRET', 'local-demo-secret-change-before-deploy')
PUBLIC_BASE_URL = (os.getenv('PUBLIC_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or 'http://localhost:8000').rstrip('/')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
ENABLE_LIVE_PAYMENTS = os.getenv('ENABLE_LIVE_PAYMENTS', '0') == '1'
RAFFLE_LEGAL_APPROVED = os.getenv('RAFFLE_LEGAL_APPROVED', '0') == '1'
PROCESSOR_APPROVED = os.getenv('PROCESSOR_APPROVED', '0') == '1'
IS_HTTPS = PUBLIC_BASE_URL.startswith('https://')
LIVE_READY = all((ENABLE_LIVE_PAYMENTS, RAFFLE_LEGAL_APPROVED, PROCESSOR_APPROVED,
                  STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, IS_HTTPS, not DEMO_MODE))
if (not DEMO_MODE or PUBLIC_PREVIEW or os.getenv('RENDER') == 'true') and (
    len(ADMIN_PASSWORD) < 20 or ADMIN_PASSWORD == 'change-me-in-production'
    or len(SESSION_SECRET) < 32 or SESSION_SECRET.startswith('local-demo')
):
    raise RuntimeError('Set a unique strong ADMIN_PASSWORD (20+ characters) and SESSION_SECRET (32+ characters) before public deployment.')
if ENABLE_LIVE_PAYMENTS and not LIVE_READY:
    raise RuntimeError('Live payments require legal and processor approvals, HTTPS, Stripe keys, and DEMO_MODE=0.')

app = FastAPI(title='Windy City Raffles', docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=IS_HTTPS,
                   same_site='lax', max_age=60 * 60 * 8)
app.mount('/static', StaticFiles(directory=str(BASE / 'static')), name='static')
app.mount('/media', StaticFiles(directory=str(UPLOAD_DIR)), name='media')
templates = Jinja2Templates(directory=str(BASE / 'templates'))

SCHEMA = '''
CREATE TABLE IF NOT EXISTS raffles (
 id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
 subtitle TEXT NOT NULL, organization TEXT NOT NULL, description TEXT NOT NULL,
 prize_title TEXT NOT NULL, prize_description TEXT NOT NULL, prize_image_url TEXT NOT NULL DEFAULT '',
 goal_cents INTEGER NOT NULL DEFAULT 2500000, status TEXT NOT NULL DEFAULT 'draft',
 closes_at TEXT NOT NULL, draw_at TEXT NOT NULL, rules TEXT NOT NULL,
 license_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tiers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, raffle_id INTEGER NOT NULL REFERENCES raffles(id),
 quantity INTEGER NOT NULL, price_cents INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(raffle_id, quantity)
);
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, raffle_id INTEGER NOT NULL REFERENCES raffles(id), tier_id INTEGER NOT NULL REFERENCES tiers(id),
 name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', seller TEXT NOT NULL DEFAULT '',
 quantity INTEGER NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 stripe_session_id TEXT UNIQUE, stripe_payment_intent TEXT UNIQUE,
 demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, paid_at TEXT
);
CREATE TABLE IF NOT EXISTS entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, raffle_id INTEGER NOT NULL REFERENCES raffles(id),
 order_id TEXT NOT NULL REFERENCES orders(id), ticket_number INTEGER NOT NULL,
 void INTEGER NOT NULL DEFAULT 0, UNIQUE(raffle_id,ticket_number)
);
CREATE TABLE IF NOT EXISTS draws (
 id INTEGER PRIMARY KEY AUTOINCREMENT, raffle_id INTEGER NOT NULL REFERENCES raffles(id),
 entry_id INTEGER NOT NULL UNIQUE REFERENCES entries(id), prize_label TEXT NOT NULL,
 eligible_count INTEGER NOT NULL, selected_index INTEGER NOT NULL, pool_sha256 TEXT NOT NULL,
 drawn_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_raffle ON entries(raffle_id);
CREATE INDEX IF NOT EXISTS orders_raffle ON orders(raffle_id);
'''

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def money(cents):
    return '${:,.2f}'.format((cents or 0)/100)

templates.env.filters['money'] = money

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn

@contextmanager
def database():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with database() as db:
        db.executescript(SCHEMA)
        if DEMO_MODE and not db.execute('SELECT id FROM raffles').fetchone():
            db.execute('''INSERT INTO raffles(slug,title,subtitle,organization,description,prize_title,
                        prize_description,goal_cents,status,closes_at,draw_at,rules,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                'windy-city-team-fundraiser', 'Windy City Team Raffle',
                'Support our players. Every entry makes a difference.', 'Windy City Baseball & Softball',
                'Support player development, equipment, training, and travel. This is an illustrative demo campaign.',
                'Grand Prize Package', 'Add your real prize photos, descriptions, terms and drawing details in the admin dashboard.',
                2500000, 'active', '2026-12-15T20:00', '2026-12-16T19:00',
                'DEMO ONLY. Example campaign with simulated payments and fictional entries. Not an offer to sell raffle tickets. '
                'Before any real campaign, insert the official rules, eligibility conditions, license information and any required disclosures.', now()))
            rid = db.execute('SELECT id FROM raffles WHERE slug=?', ('windy-city-team-fundraiser',)).fetchone()['id']
            db.executemany('INSERT INTO tiers(raffle_id,quantity,price_cents) VALUES(?,?,?)',
                           [(rid,q,p) for q,p in ((1,1000),(5,4500),(10,8500),(25,20000),(50,35000),(100,50000))])


    with database() as db:
        for row in db.execute('SELECT * FROM raffles').fetchall():
            updates = {}
            for key in ('title', 'subtitle', 'description', 'prize_title', 'prize_description', 'rules', 'license_note', 'closes_at', 'draw_at'):
                value = row[key]
                updated = value.replace('2025', '2026').replace('2027', '2026').replace('Fundraisers', 'Raffles').replace('fundraisers', 'raffles').replace('FUNDRAISERS', 'RAFFLES').replace('Fundraiser', 'Raffle').replace('fundraiser', 'raffle').replace('FUNDRAISER', 'RAFFLE')
                if updated != value:
                    updates[key] = updated
            if updates:
                db.execute('UPDATE raffles SET ' + ', '.join(k + '=?' for k in updates) + ' WHERE id=?', [*updates.values(), row['id']])


    with database() as db:
        db.executemany(
            "UPDATE tiers SET price_cents=? WHERE quantity=? AND raffle_id IN (SELECT id FROM raffles WHERE slug='windy-city-team-fundraiser')",
            [(1000,1),(4500,5),(8500,10),(20000,25),(35000,50),(50000,100)])


    with database() as db:
        db.execute("UPDATE raffles SET description=?, closes_at=?, draw_at=? WHERE slug='windy-city-team-fundraiser'",
                   ("Support your Windy City player and enter for a chance to win great prizes, including $2,500 cash!\n\nTickets start at $10, with discounted bundles available.\n\nEnter your player’s full name at checkout so they receive credit toward our top-seller prizes.\n\nTicket sales close December 19, 2026, at 11:59 p.m. Central Time. Winners will be drawn December 20 on Facebook Live.\n\nHOW THE RAFFLE WORKS\n\nEach ticket is $10, or you can choose from the ticket bundle options. Prizes will include a GRAND PRIZE of $2,500, cash prizes, and gift baskets as well as some special items. On December 20, 2026, raffle tickets will be drawn on Facebook Live and prizes dispersed.\n\nAdditionally, players that sell the most tickets will win separate prizes. Supporting your player will help them win prizes and more!\n\nPlease remember to include the full name ONLY of the Windy City player/family that sold your tickets.", '2026-12-19T23:59', '2026-12-20'))

init_db()

def page(request, template, **ctx):
    return templates.TemplateResponse(request, template, {'request': request, 'demo': DEMO_MODE,
        'live': LIVE_READY, 'public_preview': PUBLIC_PREVIEW, 'money': money, **ctx})

def audit(db, action, details):
    db.execute('INSERT INTO audit(action,details,created_at) VALUES(?,?,?)',
               (action, json.dumps(details, sort_keys=True), now()))

def admin_only(request):
    if not request.session.get('admin'):
        raise HTTPException(303, headers={'Location':'/admin/login'})

def csrf_token(request):
    if 'csrf' not in request.session:
        request.session['csrf'] = secrets.token_urlsafe(24)
    return request.session['csrf']

def verify_csrf(request, submitted):
    stored = request.session.get('csrf', '')
    if not stored or not hmac.compare_digest(stored, submitted):
        raise HTTPException(403, detail='Invalid request token. Refresh and retry.')

def get_raffle(db, slug):
    r = db.execute('SELECT * FROM raffles WHERE slug=?', (slug,)).fetchone()
    if not r: raise HTTPException(404, 'Raffle not found')
    return r

def get_public_data(db, rid):
    tiers = db.execute('SELECT * FROM tiers WHERE raffle_id=? AND active=1 ORDER BY quantity', (rid,)).fetchall()
    stats = db.execute('''SELECT COALESCE(SUM(amount_cents),0) raised, COUNT(*) sales,
         COALESCE(SUM(quantity),0) tickets FROM orders WHERE raffle_id=? AND status='paid' ''', (rid,)).fetchone()
    drawings = db.execute('''SELECT d.*, e.ticket_number, o.name winner FROM draws d
        JOIN entries e ON e.id=d.entry_id JOIN orders o ON o.id=e.order_id
        WHERE d.raffle_id=? ORDER BY d.id DESC''',(rid,)).fetchall()
    return tiers, stats, drawings

@app.middleware('http')
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    resp.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if IS_HTTPS: resp.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return resp

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    with database() as db:
        raffle = db.execute("SELECT slug FROM raffles WHERE status != 'archived' ORDER BY id DESC LIMIT 1").fetchone()
    if raffle:
        return raffle_page(request, raffle['slug'])
    return page(request,'home.html',raffles=[])

@app.get('/r/{slug}', response_class=HTMLResponse)
def raffle_page(request: Request, slug: str):
    with database() as db:
        r = get_raffle(db,slug)
        tiers, stats, draws = get_public_data(db, r['id'])
    return page(request,'raffle.html',r=r,tiers=tiers,stats=stats,draws=draws,csrf=csrf_token(request),
                unavailable=(r['status']!='active' or not (DEMO_MODE or LIVE_READY)
                             or (DEMO_MODE and PUBLIC_PREVIEW and not request.session.get('admin'))))

_rate_limit = {}
def throttle(request):
    ip = request.client.host if request.client else 'unknown'
    bucket = _rate_limit.setdefault(ip, [])
    current = time.time()
    bucket[:] = [ts for ts in bucket if ts > current-60]
    if len(bucket) >= 10: raise HTTPException(429, 'Too many checkout attempts; please try later.')
    bucket.append(current)

@app.post('/checkout')
def checkout(request: Request, slug: str = Form(...), tier_id: int = Form(...),
             name: str = Form(...), email: str = Form(...), phone: str = Form(''),
             seller: str = Form(''), rules_agree: str = Form(''), csrf: str = Form(...)):
    verify_csrf(request, csrf)
    if DEMO_MODE and PUBLIC_PREVIEW and not request.session.get('admin'):
        raise HTTPException(403, 'This public preview does not accept orders. Admins may run private simulated checkouts.')
    throttle(request)
    seller = ' '.join(seller.split())[:120]
    if len(seller.split()) < 2:
        raise HTTPException(400, 'Enter the full first and last name of the Windy City player to credit.')
    name, email = name.strip()[:120], email.strip().lower()[:254]
    if len(name) < 2 or '@' not in email or len(email)<5 or rules_agree != 'yes':
        raise HTTPException(400, 'Enter your name, email and accept the rules.')
    with database() as db:
        r = get_raffle(db, slug)
        tier = db.execute('SELECT * FROM tiers WHERE id=? AND raffle_id=? AND active=1',
                          (tier_id, r['id'])).fetchone()
        if not tier or r['status'] != 'active': raise HTTPException(400,'Entries are unavailable.')
        if not (DEMO_MODE or LIVE_READY): raise HTTPException(403,'Real payment collection is not enabled.')
        if not DEMO_MODE and (r['closes_at'] <= datetime.now().strftime('%Y-%m-%dT%H:%M')):
            raise HTTPException(400,'This raffle has closed.')
        oid = uuid.uuid4().hex
        db.execute('''INSERT INTO orders(id,raffle_id,tier_id,name,email,phone,seller,quantity,amount_cents,
                      demo,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                 (oid,r['id'],tier['id'],name,email,phone[:40],seller[:120],tier['quantity'],tier['price_cents'],
                  1 if DEMO_MODE else 0,now()))
    if DEMO_MODE:
        mark_paid(oid, session_id='demo_'+oid, payment_intent=None)
        return RedirectResponse('/order/'+oid, status_code=303)
    try:
        # All sale terms, product details and amounts originate on the server.
        fields = {
            'mode':'payment',
            'success_url':PUBLIC_BASE_URL+'/order/'+oid,
            'cancel_url':PUBLIC_BASE_URL+'/r/'+slug+'?checkout=cancelled',
            'client_reference_id':oid,
            'customer_email':email,
            'line_items[0][price_data][currency]':'usd',
            'line_items[0][price_data][unit_amount]':str(tier['price_cents']),
            'line_items[0][price_data][product_data][name]': f"{r['title']} — {tier['quantity']} tickets",
            'line_items[0][quantity]':'1',
            'metadata[raffle_id]':str(r['id']),
        }
        resp = requests.post('https://api.stripe.com/v1/checkout/sessions', data=fields,
                             auth=(STRIPE_SECRET_KEY,''), timeout=20)
        resp.raise_for_status()
        session = resp.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(502, 'Checkout is temporarily unavailable; no charge was taken.')
    with database() as db:
        db.execute('UPDATE orders SET stripe_session_id=? WHERE id=?', (session['id'],oid))
    return RedirectResponse(session['url'],status_code=303)

def mark_paid(order_id, session_id, payment_intent):
    """Idempotent ticket allocation; never allocates from a browser redirect."""
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            o = db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
            if not o: raise ValueError('Order does not exist')
            if o['status']=='paid':
                db.commit(); return False
            if o['status'] != 'pending': raise ValueError('Order is not pending')
            if payment_intent:
                same = db.execute('SELECT id FROM orders WHERE stripe_payment_intent=? AND id!=?',
                                  (payment_intent, order_id)).fetchone()
                if same: raise ValueError('Payment reused across orders')
            db.execute('''UPDATE orders SET status='paid', paid_at=?, stripe_session_id=?,
                          stripe_payment_intent=? WHERE id=?''',
                       (now(), session_id, payment_intent,order_id))
            last = db.execute('SELECT COALESCE(MAX(ticket_number),0) n FROM entries WHERE raffle_id=?',
                              (o['raffle_id'],)).fetchone()['n']
            db.executemany('INSERT INTO entries(raffle_id,order_id,ticket_number) VALUES(?,?,?)',
                           [(o['raffle_id'],order_id,i) for i in range(last+1,last+o['quantity']+1)])
            audit(db,'tickets_issued',{'order_id':order_id,'count':o['quantity']})
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise

def refund_payment_intent(payment_intent):
    if not payment_intent:return
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            row = db.execute('SELECT * FROM orders WHERE stripe_payment_intent=?', (payment_intent,)).fetchone()
            if row and row['status']=='paid':
                already_won=db.execute('''SELECT 1 FROM draws d JOIN entries e ON e.id=d.entry_id
                    WHERE e.order_id=? LIMIT 1''', (row['id'],)).fetchone()
                if already_won:
                    audit(db,'refund_winner_alert',{'order_id':row['id']})
                db.execute('UPDATE orders SET status=? WHERE id=?',('refunded',row['id']))
                db.execute('UPDATE entries SET void=1 WHERE order_id=?',(row['id'],))
                audit(db,'order_refunded',{'order_id':row['id']})
            db.commit()
        except Exception:
            db.rollback();raise

def stripe_valid_header(raw, header):
    if not STRIPE_WEBHOOK_SECRET: return False
    bits = {}
    for piece in header.split(','):
        if '=' in piece:
            k,v=piece.split('=',1)
            bits.setdefault(k,[]).append(v)
    try: timestamp=int(bits.get('t',['0'])[0])
    except ValueError:return False
    if abs(time.time()-timestamp)>300:return False
    digest=hmac.new(STRIPE_WEBHOOK_SECRET.encode(),str(timestamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(digest, v) for v in bits.get('v1',[]))

@app.post('/webhook/stripe')
async def stripe_webhook(request: Request):
    if not LIVE_READY: raise HTTPException(403, 'Live payments disabled')
    raw = await request.body()
    if not stripe_valid_header(raw,request.headers.get('stripe-signature','')):
        raise HTTPException(400,'Bad signature')
    event=json.loads(raw)
    kind=event.get('type')
    obj=event.get('data',{}).get('object',{})
    if kind in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
        if obj.get('payment_status')=='paid':
            oid=obj.get('client_reference_id','')
            with database() as db:
                o=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
            if not o:raise HTTPException(400,'Unknown order')
            if o['stripe_session_id'] and o['stripe_session_id']!=obj.get('id'):
                raise HTTPException(400,'Mismatched checkout session')
            if obj.get('amount_total')!=o['amount_cents'] or obj.get('currency')!='usd':
                raise HTTPException(400,'Unexpected charged amount')
            created = mark_paid(oid, obj['id'],obj.get('payment_intent'))
            if created: send_order_email(oid)
    elif kind=='charge.refunded':
        refund_payment_intent(obj.get('payment_intent'))
    return JSONResponse({'received':True})

def send_order_email(order_id):
    smtp_host=os.getenv('SMTP_HOST','')
    if not smtp_host:return
    import smtplib
    from email.message import EmailMessage
    with database() as db:
        row=db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        entry=db.execute('SELECT ticket_number FROM entries WHERE order_id=? ORDER BY ticket_number',
                         (order_id,)).fetchall()
        raff=db.execute('SELECT * FROM raffles WHERE id=?',(row['raffle_id'],)).fetchone()
    msg=EmailMessage()
    msg['To']=row['email'];msg['From']=os.getenv('SMTP_FROM','noreply@example.com')
    msg['Subject']='Your raffle ticket confirmation'
    msg.set_content(f"Thank you, {row['name']}!\n\nCampaign: {raff['title']}\n"
                    f"Order: {order_id}\nTickets: {', '.join(str(x['ticket_number']) for x in entry)}\n"
                    f"Total: {money(row['amount_cents'])}\n\nPlease save this confirmation.")
    try:
        with smtplib.SMTP(smtp_host,int(os.getenv('SMTP_PORT','587')),timeout=10) as s:
            s.starttls()
            if os.getenv('SMTP_USER'):
                s.login(os.getenv('SMTP_USER'),os.getenv('SMTP_PASSWORD',''))
            s.send_message(msg)
    except Exception:
        # Payment remains paid even if the optional email service is down.
        with database() as db:audit(db,'email_failure',{'order_id':order_id})

@app.get('/order/{order_id}', response_class=HTMLResponse)
def order_confirmation(request: Request, order_id: str):
    # An unguessable order UUID is a bearer secret. Never expose buyer info in public raffle pages.
    with database() as db:
        o=db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
        if not o: raise HTTPException(404)
        r=db.execute('SELECT * FROM raffles WHERE id=?',(o['raffle_id'],)).fetchone()
        nums=db.execute('SELECT ticket_number FROM entries WHERE order_id=? ORDER BY ticket_number',
                        (order_id,)).fetchall()
    resp=page(request,'confirmation.html',o=o,r=r,nums=nums)
    resp.headers['Cache-Control']='no-store'
    resp.headers['X-Robots-Tag']='noindex'
    return resp

@app.get('/admin/login',response_class=HTMLResponse)
def login_form(request: Request):
    return page(request,'login.html',csrf=csrf_token(request),error='')

@app.post('/admin/login')
def login(request: Request, password: str = Form(...), csrf: str = Form(...)):
    verify_csrf(request,csrf)
    if not hmac.compare_digest(password.encode(),ADMIN_PASSWORD.encode()):
        return page(request,'login.html',csrf=csrf_token(request),error='Incorrect password.')
    request.session.clear()
    request.session['admin']=True
    request.session['csrf']=secrets.token_urlsafe(24)
    return RedirectResponse('/admin',status_code=303)

@app.post('/admin/logout')
def logout(request: Request,csrf: str = Form(...)):
    admin_only(request);verify_csrf(request,csrf)
    request.session.clear()
    return RedirectResponse('/',status_code=303)

@app.get('/admin',response_class=HTMLResponse)
def dashboard(request: Request):
    admin_only(request)
    with database() as db:
        raffles=db.execute('''SELECT r.*, COALESCE(SUM(CASE WHEN o.status='paid' THEN o.amount_cents ELSE 0 END),0) raised,
                COUNT(CASE WHEN o.status='paid' THEN 1 END) paid_orders
                FROM raffles r LEFT JOIN orders o ON o.raffle_id=r.id GROUP BY r.id ORDER BY r.id DESC''').fetchall()
    return page(request,'dashboard.html',raffles=raffles,csrf=csrf_token(request))

@app.get('/admin/raffle/new',response_class=HTMLResponse)
def new_raffle(request: Request):
    admin_only(request)
    data=dict(slug='',title='',subtitle='',organization='Windy City Baseball & Softball',description='',
              prize_title='',prize_description='',prize_image_url='', goal_cents=2500000,
              status='draft', closes_at='',draw_at='',rules='',license_note='')
    return page(request,'edit.html',r=data,tiers=[],is_new=True,csrf=csrf_token(request),error='')

@app.get('/admin/raffle/{rid}',response_class=HTMLResponse)
def edit_raffle(request: Request,rid:int):
    admin_only(request)
    with database() as db:
        r=db.execute('SELECT * FROM raffles WHERE id=?',(rid,)).fetchone()
        if not r:raise HTTPException(404)
        tiers, stats, draws=get_public_data(db,rid)
        orders=db.execute('''SELECT id,name,email,quantity,amount_cents,status,created_at
            FROM orders WHERE raffle_id=? ORDER BY created_at DESC LIMIT 40''',(rid,)).fetchall()
        entrycount=db.execute('SELECT COUNT(*) n FROM entries WHERE raffle_id=? AND void=0',(rid,)).fetchone()['n']
    return page(request,'edit.html',r=r,tiers=tiers,stats=stats,draws=draws,
                orders=orders,entrycount=entrycount,is_new=False,csrf=csrf_token(request),error='')

DEFAULT_QUANTITIES=[1,5,10,25,50,100]
@app.post('/admin/raffle/save')
def save_raffle(request: Request, csrf:str=Form(...),rid:int=Form(0),slug:str=Form(...),title:str=Form(...),
                subtitle:str=Form(''),organization:str=Form(...),description:str=Form(''),
                prize_title:str=Form(...),prize_description:str=Form(''),prize_image_url:str=Form(''),
                goal_dollars:str=Form('25000'),closes_at:str=Form(...),draw_at:str=Form(...),
                rules:str=Form(''),license_note:str=Form(''),
                price_1:str=Form('10'),price_5:str=Form('40'),price_10:str=Form('75'),
                price_25:str=Form('175'),price_50:str=Form('300'),price_100:str=Form('500')):
    admin_only(request);verify_csrf(request,csrf)
    import re
    from decimal import Decimal,InvalidOperation
    slug=slug.strip().lower()
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',slug):raise HTTPException(400,'Slug may contain letters, digits, and hyphens.')
    try:
        prices = [int(Decimal(val)*100) for val in (price_1,price_5,price_10,price_25,price_50,price_100)]
        goal_cents=int(Decimal(goal_dollars)*100)
    except (InvalidOperation,ValueError):raise HTTPException(400,'Invalid price')
    if any(p<100 or p>10000000 for p in prices):raise HTTPException(400,'Invalid ticket price')
    if goal_cents<0:raise HTTPException(400,'Invalid fundraising goal')
    if not title.strip() or not organization.strip() or not prize_title.strip():raise HTTPException(400,'Missing required fields')
    if prize_image_url and not (prize_image_url.startswith('https://') or prize_image_url.startswith('/media/')):
        raise HTTPException(400,'Prize image URL must start with https://')
    if closes_at>=draw_at:raise HTTPException(400,'Drawing must happen after ticket sales close')
    vals=(slug,title[:150],subtitle[:250],organization[:150],description[:3000],prize_title[:150],
          prize_description[:1500],prize_image_url[:700],goal_cents,closes_at,draw_at,rules[:12000],license_note[:500])
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            if rid:
                old=db.execute('SELECT * FROM raffles WHERE id=?',(rid,)).fetchone()
                if not old:raise HTTPException(404)
                if old['status']=='closed' and db.execute('SELECT 1 FROM draws WHERE raffle_id=?',(rid,)).fetchone():
                    raise HTTPException(409,'Cannot edit a raffle after a winner has been drawn')
                db.execute('''UPDATE raffles SET slug=?,title=?,subtitle=?,organization=?,description=?,prize_title=?,
                  prize_description=?,prize_image_url=?,goal_cents=?,closes_at=?,draw_at=?,rules=?,license_note=?
                  WHERE id=?''', vals+(rid,))
            else:
                cursor=db.execute('''INSERT INTO raffles(slug,title,subtitle,organization,description,prize_title,
                  prize_description,prize_image_url,goal_cents,closes_at,draw_at,rules,license_note,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals+(now(),))
                rid=cursor.lastrowid
            for q,p in zip(DEFAULT_QUANTITIES,prices):
                db.execute('''INSERT INTO tiers(raffle_id,quantity,price_cents) VALUES(?,?,?)
                    ON CONFLICT(raffle_id,quantity) DO UPDATE SET price_cents=excluded.price_cents''',(rid,q,p))
            audit(db,'raffle_saved',{'raffle_id':rid})
            db.commit()
        except Exception:
            db.rollback();raise
    return RedirectResponse('/admin/raffle/'+str(rid),status_code=303)

@app.post('/admin/raffle/{rid}/image')
async def upload_prize_image(request:Request,rid:int,csrf:str=Form(...),photo:UploadFile=File(...)):
    admin_only(request);verify_csrf(request,csrf)
    with database() as db:
        r=db.execute('SELECT id FROM raffles WHERE id=?',(rid,)).fetchone()
        if not r:raise HTTPException(404)
        if db.execute('SELECT 1 FROM draws WHERE raffle_id=?',(rid,)).fetchone():
            raise HTTPException(409,'Cannot edit prize photo after a winner is drawn')
    if photo.content_type not in ('image/png','image/jpeg','image/webp'):
        raise HTTPException(400,'Upload a JPG, PNG, or WEBP image')
    raw=await photo.read(5_000_001)
    if len(raw)>5_000_000:raise HTTPException(400,'Image must be smaller than 5MB')
    try:
        from PIL import Image,UnidentifiedImageError
        Image.MAX_IMAGE_PIXELS = 12_000_000
        im=Image.open(io.BytesIO(raw))
        im.load()
        if im.width * im.height > 12_000_000:raise ValueError('Image too large')
        if im.mode not in ('RGB','RGBA'):im=im.convert('RGB')
        if im.mode == 'RGBA':
            background=Image.new('RGB',im.size,'white');background.paste(im,mask=im.getchannel('A'));im=background
        im.thumbnail((1600,1200))
        name=f'raffle_{rid}_{secrets.token_hex(10)}.webp'
        im.save(UPLOAD_DIR/name,format='WEBP',quality=84,method=6)
    except (OSError,ValueError,UnidentifiedImageError):
        raise HTTPException(400,'Invalid or unsupported image')
    with database() as db:
        db.execute('UPDATE raffles SET prize_image_url=? WHERE id=?',('/media/'+name,rid))
        audit(db,'prize_image_uploaded',{'raffle_id':rid,'image':name})
    return RedirectResponse('/admin/raffle/'+str(rid),status_code=303)

@app.post('/admin/raffle/{rid}/status')
def set_status(request:Request,rid:int, status:str=Form(...),csrf:str=Form(...)):
    admin_only(request);verify_csrf(request,csrf)
    if status not in ('draft','active','closed'):raise HTTPException(400)
    with database() as db:
        r=db.execute('SELECT * FROM raffles WHERE id=?',(rid,)).fetchone()
        if not r:raise HTTPException(404)
        if status=='active' and not (DEMO_MODE or LIVE_READY):
            raise HTTPException(403,'Paid raffles require legal and payment processor approval')
        if db.execute('SELECT 1 FROM draws WHERE raffle_id=?',(rid,)).fetchone() and status!='closed':
            raise HTTPException(409,'Cannot reopen a raffle after drawing')
        db.execute('UPDATE raffles SET status=? WHERE id=?',(status,rid))
        audit(db,'status_changed',{'raffle_id':rid,'status':status})
    return RedirectResponse('/admin/raffle/'+str(rid),status_code=303)

@app.post('/admin/raffle/{rid}/draw')
def draw_winner(request:Request,rid:int,prize_label:str=Form(...),csrf:str=Form(...)):
    admin_only(request);verify_csrf(request,csrf)
    prize_label=prize_label.strip()[:120]
    if not prize_label:raise HTTPException(400,'Enter a prize label')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            r=db.execute('SELECT * FROM raffles WHERE id=?',(rid,)).fetchone()
            if not r or r['status']!='closed':raise HTTPException(409,'Close the raffle before drawing')
            eligible=db.execute('''SELECT e.id,e.ticket_number,o.name FROM entries e
             JOIN orders o ON o.id=e.order_id WHERE e.raffle_id=? AND e.void=0 AND o.status='paid'
             AND e.id NOT IN (SELECT entry_id FROM draws) ORDER BY e.id''',(rid,)).fetchall()
            if not eligible:raise HTTPException(409,'No eligible undrawn entries')
            pool_hash=hashlib.sha256(','.join(str(e['id']) for e in eligible).encode()).hexdigest()
            index=secrets.randbelow(len(eligible))
            winner=eligible[index]
            db.execute('''INSERT INTO draws(raffle_id,entry_id,prize_label,eligible_count,selected_index,pool_sha256,drawn_at)
              VALUES(?,?,?,?,?,?,?)''',(rid,winner['id'],prize_label,len(eligible),index,pool_hash,now()))
            audit(db,'winner_drawn',{'raffle_id':rid,'entry_id':winner['id'],'pool_hash':pool_hash})
            db.commit()
        except Exception:
            db.rollback();raise
    return RedirectResponse('/admin/raffle/'+str(rid)+'#winners',status_code=303)

@app.get('/admin/raffle/{rid}/export.csv')
def export_entries(request:Request,rid:int):
    admin_only(request)
    with database() as db:
        rows=db.execute('''SELECT e.ticket_number,o.name,o.email,o.phone,o.seller,o.amount_cents,
             o.quantity,o.status,e.void,o.id order_id,o.paid_at FROM entries e
             JOIN orders o ON o.id=e.order_id WHERE e.raffle_id=? ORDER BY e.ticket_number''',(rid,)).fetchall()
    buffer=io.StringIO();writer=csv.writer(buffer)
    writer.writerow(['ticket_number','buyer','email','phone','seller','order_total_dollars',
                     'package_size','payment_status','void','order_id','paid_at_utc'])
    for x in rows: writer.writerow([x['ticket_number'],x['name'],x['email'],x['phone'],x['seller'],
                                    f"{x['amount_cents']/100:.2f}",x['quantity'],x['status'],x['void'],x['order_id'],x['paid_at']])
    return Response(buffer.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="raffle-{rid}-entries.csv"', 'Cache-Control':'no-store'})

@app.get('/health')
def health():
    return {'ok':True,'demo':DEMO_MODE,'live_payments_enabled':LIVE_READY}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8000)
