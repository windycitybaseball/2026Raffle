"""End-to-end tests against a clean, isolated demo database."""
import importlib
import os
from pathlib import Path

from fastapi.testclient import TestClient


def create_app(tmp_path):
    os.environ.update(DEMO_MODE='1',PUBLIC_PREVIEW='0',DATA_DIR=str(tmp_path),
                      SESSION_SECRET='a-testing-secret-that-is-long')
    import app
    importlib.reload(app)
    return app


def login(client):
    res=client.get('/admin/login');assert res.status_code==200
    import re
    token=re.search(r'name="csrf" value="([^"]+)"',res.text).group(1)
    response=client.post('/admin/login',data={'password':'change-me-in-production','csrf':token},follow_redirects=False)
    assert response.status_code==303


def test_website_checkout_and_draw(tmp_path):
    m=create_app(tmp_path)
    with TestClient(m.app) as client:
        res=client.get('/r/windy-city-team-fundraiser')
        assert res.status_code==200 and '100 tickets' in res.text
        import re
        token=re.search(r'name="csrf" value="([^"]+)"',res.text).group(1)
        with m.database() as db:
            tier=db.execute('SELECT id FROM tiers WHERE quantity=25').fetchone()['id']
        checkout=client.post('/checkout',data={'slug':'windy-city-team-fundraiser',
            'tier_id':tier,'name':'Test Buyer','email':'buyer@example.com','rules_agree':'yes',
            'csrf':token},follow_redirects=False)
        assert checkout.status_code==303
        confirmation=client.get(checkout.headers['location'])
        assert 'Your tickets are ready!' in confirmation.text
        with m.database() as db:
            order=db.execute('SELECT * FROM orders').fetchone()
            assert order['status']=='paid' and order['quantity']==25
            assert db.execute('SELECT COUNT(*) n FROM entries').fetchone()['n']==25
        # Payment confirmation is idempotent and cannot duplicate entries.
        assert m.mark_paid(order['id'],'demo_'+order['id'],None) is False
        login(client)
        admin_page=client.get('/admin/raffle/1')
        assert admin_page.status_code==200 and '25 tickets' in admin_page.text
        token=re.search(r'name="csrf" value="([^"]+)"',admin_page.text).group(1)
        close=client.post('/admin/raffle/1/status',data={'status':'closed','csrf':token},follow_redirects=False)
        assert close.status_code==303
        draw=client.post('/admin/raffle/1/draw',data={'prize_label':'Grand Prize','csrf':token},follow_redirects=False)
        assert draw.status_code==303
        with m.database() as db:
            d=db.execute('SELECT * FROM draws').fetchone()
            assert d['eligible_count']==25 and len(d['pool_sha256'])==64
        download=client.get('/admin/raffle/1/export.csv')
        assert download.status_code==200 and 'buyer@example.com' in download.text
        assert len(download.text.splitlines())==26


def test_admin_protection_and_checkout_validation(tmp_path):
    m=create_app(tmp_path)
    with TestClient(m.app) as client:
        noauth=client.get('/admin/raffle/1/export.csv',follow_redirects=False)
        assert noauth.status_code==303
        public=client.get('/r/windy-city-team-fundraiser')
        import re
        token=re.search(r'name="csrf" value="([^"]+)"',public.text).group(1)
        bad=client.post('/checkout',data={'slug':'windy-city-team-fundraiser',
            'tier_id':999999,'name':'T Buyer','email':'buyer@example.com','rules_agree':'yes','csrf':token})
        assert bad.status_code==400
        badcsrf=client.post('/checkout',data={'slug':'windy-city-team-fundraiser',
            'tier_id':1,'name':'T Buyer','email':'buyer@example.com','rules_agree':'yes','csrf':'bad'})
        assert badcsrf.status_code==403


def test_create_new_campaign_and_custom_prices(tmp_path):
    m=create_app(tmp_path)
    with TestClient(m.app) as client:
        login(client)
        new=client.get('/admin/raffle/new')
        assert 'Create a fundraiser' in new.text
        import re
        token=re.search(r'name="csrf" value="([^"]+)"',new.text).group(1)
        response=client.post('/admin/raffle/save',data={
            'csrf':token,'rid':0,'slug':'new-raffle','title':'New Raffle',
            'subtitle':'', 'organization':'Windy City','description':'Team fundraiser',
            'prize_title':'New Grand Prize','prize_description':'Description',
            'goal_dollars':'20000','closes_at':'2027-11-20T20:00',
            'draw_at':'2027-11-21T19:00', 'rules':'Test rules',
            'price_1':'10','price_5':'45','price_10':'80','price_25':'190',
            'price_50':'350','price_100':'600'
        },follow_redirects=False)
        assert response.status_code == 303
        with m.database() as db:
            raffle=db.execute("SELECT * FROM raffles WHERE slug='new-raffle'").fetchone()
            assert raffle['title']=='New Raffle'
            tiers=db.execute('SELECT quantity,price_cents FROM tiers WHERE raffle_id=? ORDER BY quantity',(raffle['id'],)).fetchall()
            assert [(t['quantity'],t['price_cents']) for t in tiers]==[
                (1,1000),(5,4500),(10,8000),(25,19000),(50,35000),(100,60000)]
        public=client.get('/r/new-raffle')
        assert 'New Grand Prize' in public.text


def test_admin_image_upload(tmp_path):
    m=create_app(tmp_path)
    with TestClient(m.app) as client:
        login(client)
        admin_page=client.get('/admin/raffle/1')
        import re,io
        from PIL import Image
        token=re.search(r'name="csrf" value="([^"]+)"',admin_page.text).group(1)
        buf=io.BytesIO()
        Image.new('RGB',(30,30),'red').save(buf,format='PNG')
        uploaded=client.post('/admin/raffle/1/image',data={'csrf':token},
           files={'photo':('prize.png',buf.getvalue(),'image/png')},follow_redirects=False)
        assert uploaded.status_code==303
        with m.database() as db:
            url=db.execute('SELECT prize_image_url FROM raffles WHERE id=1').fetchone()[0]
            assert url.startswith('/media/raffle_1_')
        image_response=client.get(url)
        assert image_response.status_code==200
        assert image_response.headers['content-type']=='image/webp'


def test_public_preview_blocks_public_order_and_allows_admin_demo(tmp_path):
    m=create_app(tmp_path)
    m.PUBLIC_PREVIEW = True
    with TestClient(m.app) as client:
        public=client.get('/r/windy-city-team-fundraiser')
        assert 'PUBLIC PREVIEW' in public.text
        assert 'disabled' in public.text
        import re
        token=re.search(r'name="csrf" value="([^"]+)"',public.text).group(1)
        with m.database() as db:
            tier=db.execute('SELECT id FROM tiers WHERE quantity=5').fetchone()['id']
        payload=dict(slug='windy-city-team-fundraiser',tier_id=tier,name='Demo Buyer',
                     email='demo@example.test',rules_agree='yes',csrf=token)
        forbidden=client.post('/checkout',data=payload)
        assert forbidden.status_code==403
        with m.database() as db:
            assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0
        login(client)
        admin_preview=client.get('/r/windy-city-team-fundraiser')
        payload['csrf']=re.search(r'name="csrf" value="([^"]+)"',admin_preview.text).group(1)
        allowed=client.post('/checkout',data=payload,follow_redirects=False)
        assert allowed.status_code==303
