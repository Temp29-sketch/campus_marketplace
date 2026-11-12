"""Task 2 checks. Run with: python test_notifications_admin_banners.py"""
import io
import re

from PIL import Image
from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import AdRequest, Banner, Category, Notification, Product, ProductImage, University, User, VendorApplication, APP_APPROVED
from services.notifications import notify_admins, notify_user
from services.storage import save_image
from blueprints.admin import _safe_banner_link

app = create_app(TestConfig)


def login(email):
    client = app.test_client()
    assert client.post('/login', data={'email': email, 'password': 'password123'}).status_code == 302
    return client


def png():
    data = io.BytesIO(); Image.new('RGB', (800, 500), '#247d72').save(data, 'PNG'); data.seek(0); return data


with app.app_context():
    db.create_all(); seed_defaults()
    uni = University.query.filter_by(short_name='BINGHAM').one()
    other = University(name='Other University', short_name='OTHER', email_domain='other.example'); db.session.add(other); db.session.flush()
    food = Category.query.filter_by(slug='food').one()
    def make_user(university, name, email, role):
        u = User(university=university, name=name, email=email, role=role); u.set_password('password123'); db.session.add(u); db.session.flush(); return u
    admin = make_user(uni, 'Admin', 'admin@one.test', 'admin')
    admin2 = make_user(other, 'Other Admin', 'admin@two.test', 'admin')
    buyer = make_user(uni, 'Buyer', 'buyer@one.test', 'buyer')
    other_buyer = make_user(other, 'Other Buyer', 'buyer@two.test', 'buyer')
    vendor = make_user(uni, 'Vendor Name', 'vendor@one.test', 'vendor')
    vendor.photo_key = 'fixture.webp'
    application = VendorApplication(user=vendor, business_name='Fresh Food', category=food, bio='Good fresh food sold every day.', phone_number='+2348012345678', bank_name='Access Bank', account_name='Vendor Name', account_number='9876543210', status=APP_APPROVED)
    db.session.add(application)
    product = Product(vendor=vendor, category=food, title='Rice', price=1000, description='Rice', is_available=True); db.session.add(product); db.session.flush()
    product_image = save_image(__import__('werkzeug').datastructures.FileStorage(stream=png(), filename='rice.png'))
    db.session.add(ProductImage(product=product, storage_key=product_image, position=0))
    db.session.commit()
    vendor_id, product_id, app_id, uni_id, buyer_id, admin_id, admin2_id = vendor.id, product.id, application.id, uni.id, buyer.id, admin.id, admin2.id

anonymous = app.test_client()
buyer_client = login('buyer@one.test'); admin_client = login('admin@one.test'); other_admin = login('admin@two.test')
other_buyer_client = login('buyer@two.test')
assert anonymous.get('/api/notifications').status_code == 302
with app.app_context():
    notify_user(db.session.get(User, buyer_id), 'order', 'Order update', 'Your order is ready.', '/orders')
    notify_admins(uni_id, 'report', 'New report', 'A listing was reported.', '/admin/reports')
    assert Notification.query.filter_by(user_id=admin_id).count() == 1
    assert Notification.query.filter_by(user_id=admin2_id).count() == 0
api = buyer_client.get('/api/notifications').get_json()
assert api['unread_count'] == 1 and api['items'][0]['title'] == 'Order update'
notif_id = api['items'][0]['id']
assert buyer_client.post(f'/notifications/{notif_id}/read', data={'next': '/orders'}).status_code == 302
assert buyer_client.get('/api/notifications').get_json()['unread_count'] == 0
assert buyer_client.get('/notifications').status_code == 200
assert admin_client.get('/admin').status_code == 200
assert other_admin.get('/admin').status_code == 200

# Scoped vendor details and private bank number.
with app.app_context():
    application.status = 'pending'; db.session.commit()
app_detail = admin_client.get(f'/admin/applications/{app_id}').get_data(as_text=True)
assert '9876543210' in app_detail and 'Copy' in app_detail
assert '9876543210' in admin_client.get(f'/admin/vendors/{vendor_id}').get_data(as_text=True)
assert other_admin.get(f'/admin/applications/{app_id}').status_code == 404
assert anonymous.get('/').get_data(as_text=True).find('9876543210') == -1
assert other_admin.get(f'/admin/vendors/{vendor_id}').status_code == 404

# Dashboard counts include the scoped active vendor and listing.
dashboard = admin_client.get('/admin').get_data(as_text=True)
assert 'Active vendors' in dashboard and 'Active listings' in dashboard

# Banner create/edit/toggle, schedule filtering, safe links and fallback.
assert _safe_banner_link('') is None and _safe_banner_link('   ') is None
assert _safe_banner_link('/store/1') == '/store/1'
assert _safe_banner_link('https://wa.me/2348012345678') == 'https://wa.me/2348012345678'
assert _safe_banner_link('javascript:alert(1)') is False
banner_form_html = admin_client.get('/admin/banners/new').get_data(as_text=True)
assert 'Link (optional)' in banner_form_html and 'https://example.com or /store/12' in banner_form_html
assert 'Use the advertiser\'s website' in banner_form_html and 'Only used when a link is set.' in banner_form_html
assert 'id="link_url" name="link_url" type="text" inputmode="url"' in banner_form_html
assert 'id="link_url" name="link_url" type="url"' not in banner_form_html
form = {'title': 'Campus week', 'subtitle': 'Shop campus offers', 'link_url': 'https://example.test/deal', 'button_text': 'See offers', 'is_active': 'on'}
created = admin_client.post('/admin/banners/new', data={**form, 'image': (png(), 'hero.png')}, content_type='multipart/form-data')
assert created.status_code == 302
with app.app_context():
    banner = Banner.query.one(); banner_id = banner.id; assert banner.university_id == uni.id
    assert banner.fit_mode == 'fill' and banner.focus == 'center'
feed = anonymous.get('/').get_data(as_text=True)
assert 'Campus week' in feed and '>Ad</span>' in feed
assert 'banner-focus-center' in feed and 'banner-backdrop' not in feed
assert 'Campus week' in buyer_client.get('/').get_data(as_text=True)
assert 'Campus week' not in other_buyer_client.get('/').get_data(as_text=True)
assert 'Campus week' in anonymous.get('/').get_data(as_text=True)
fit_saved = admin_client.post(f'/admin/banners/{banner_id}/edit', data={**form, 'fit_mode': 'fit', 'focus': 'bottom', 'image': ''}, content_type='multipart/form-data')
assert fit_saved.status_code == 302
with app.app_context():
    saved = db.session.get(Banner, banner_id); assert saved.fit_mode == 'fit' and saved.focus == 'bottom'
fit_feed = anonymous.get('/').get_data(as_text=True)
assert 'class="banner-image shimmer banner-fit"' in fit_feed and 'class="banner-backdrop"' in fit_feed
assert 'aria-hidden="true" alt="" loading="eager"' in fit_feed and 'banner-focus-bottom' in fit_feed
reset = admin_client.post(f'/admin/banners/{banner_id}/edit', data={**form, 'fit_mode': 'sideways', 'focus': 'diagonal', 'image': ''}, content_type='multipart/form-data')
assert reset.status_code == 302
with app.app_context():
    saved = db.session.get(Banner, banner_id); assert saved.fit_mode == 'fill' and saved.focus == 'center'
for html in (feed, anonymous.get(f'/item/{product_id}').get_data(as_text=True)):
    match = re.search(r'src="([^"]*/media/[^"]+)"', html)
    assert match, 'rendered public page should contain a media URL'
    response = anonymous.get(match.group(1))
    assert response.status_code == 200 and response.mimetype == 'image/webp'
    assert response.headers['Cache-Control'].startswith('public, max-age=')
banner_html = admin_client.get('/admin/banners').get_data(as_text=True)
banner_match = re.search(r'src="([^"]*/media/[^"]+)"', banner_html)
assert banner_match and anonymous.get(banner_match.group(1)).status_code == 200
from blueprints import admin as admin_module
old_save_banner = admin_module.save_banner
admin_module.save_banner = lambda _upload: (_ for _ in ()).throw(OSError("fake storage failure"))
failed_save = admin_client.post('/admin/banners/new', data={**form, 'title': 'Must not save', 'image': (png(), 'broken.png')}, content_type='multipart/form-data')
admin_module.save_banner = old_save_banner
assert failed_save.status_code == 200 and 'could not be stored' in failed_save.get_data(as_text=True).lower()
assert 'Banner saved.' not in failed_save.get_data(as_text=True)
with app.app_context(): assert Banner.query.count() == 1
invalid_link = admin_client.post(f'/admin/banners/{banner_id}/edit', data={**form, 'link_url': 'javascript:alert(1)'}, content_type='multipart/form-data')
assert invalid_link.status_code == 400 and 'Enter an http(s) URL' in invalid_link.get_data(as_text=True)
with app.app_context():
    banner = db.session.get(Banner, banner_id); assert banner.link_url == 'https://example.test/deal'
for valid_link in ('/store/1', 'https://wa.me/2348012345678'):
    saved_link = admin_client.post(f'/admin/banners/{banner_id}/edit', data={**form, 'link_url': valid_link, 'image': ''}, content_type='multipart/form-data')
    assert saved_link.status_code == 302
    with app.app_context(): assert db.session.get(Banner, banner_id).link_url == valid_link
cleared = admin_client.post(f'/admin/banners/{banner_id}/edit', data={**form, 'link_url': '   ', 'image': ''}, content_type='multipart/form-data')
assert cleared.status_code == 302
with app.app_context(): assert db.session.get(Banner, banner_id).link_url is None
no_link_feed = anonymous.get('/').get_data(as_text=True)
assert 'Campus week' in no_link_feed and 'banner-sponsored' not in no_link_feed
banner_region = re.search(r'<article class="banner-slide.*?</article>', no_link_feed, re.S).group(0)
assert '<a class="btn"' not in banner_region and 'Campus week' in banner_region
assert admin_client.get('/admin/banners/new').status_code == 200
with app.app_context():
    banner = db.session.get(Banner, banner_id)
    banner.starts_at = __import__('datetime').datetime(2099, 1, 1); db.session.commit()
assert 'Welcome to UniHub' in anonymous.get('/').get_data(as_text=True)
assert admin_client.post(f'/admin/banners/{banner_id}/toggle').status_code == 302
assert admin_client.post(f'/admin/banners/{banner_id}/delete').status_code == 302
with app.app_context(): assert Banner.query.count() == 0
no_link_created = admin_client.post('/admin/banners/new', data={'title': 'No link banner', 'subtitle': 'Just an announcement', 'is_active': 'on', 'image': (png(), 'no-link.png')}, content_type='multipart/form-data')
assert no_link_created.status_code == 302
with app.app_context():
    no_link_banner = Banner.query.one(); assert no_link_banner.link_url is None and no_link_banner.button_text is None
assert 'No link banner' in anonymous.get('/').get_data(as_text=True)
settings_html = admin_client.get('/settings').get_data(as_text=True)
assert '<div class="password-field">' in settings_html and 'aria-label="Show password"' in settings_html

# Advertiser request intake and campus-scoped admin review.
assert anonymous.get('/advertise').status_code == 302
assert admin_client.get('/advertise').status_code == 403
advertise_form = buyer_client.get('/advertise').get_data(as_text=True)
assert 'Advertise with us' in advertise_form and 'csrf_token' in advertise_form
request_data = {'business_name': 'Campus Prints', 'contact_name': 'Buyer Name', 'phone': '08012345678',
                'email': 'buyer@one.test', 'message': 'I would like to advertise with students.'}
submitted = buyer_client.post('/advertise', data=request_data)
assert submitted.status_code == 302 and '/advertise/thanks' in submitted.headers['Location']
assert 'Thanks, we will contact you' in buyer_client.get('/advertise/thanks').get_data(as_text=True)
with app.app_context():
    ad_request = AdRequest.query.one()
    ad_request_id = ad_request.id
    assert ad_request.phone == '+2348012345678' and ad_request.university_id == uni_id
    assert Notification.query.filter_by(user_id=admin_id, kind='ad_request').count() == 1
assert 'Campus Prints' in admin_client.get('/admin/ad-requests').get_data(as_text=True)
assert other_admin.get(f'/admin/ad-requests/{ad_request_id}').status_code == 404
assert admin_client.post(f'/admin/ad-requests/{ad_request_id}', data={'status': 'accepted', 'admin_note': 'Approved'}).status_code == 302
with app.app_context():
    assert db.session.get(AdRequest, ad_request_id).status == 'accepted'
assert 'Create banner from this request' in admin_client.get(f'/admin/ad-requests/{ad_request_id}').get_data(as_text=True)
assert 'ad_request_id=' in admin_client.get(f'/admin/banners/new?ad_request_id={ad_request_id}').request.path or admin_client.get(f'/admin/banners/new?ad_request_id={ad_request_id}').status_code == 200
assert 'Recommended size: 1600 x 400 px (4:1)' in admin_client.get('/admin/banners/new').get_data(as_text=True)
assert 'Tall photos work best with Fit.' in admin_client.get('/admin/banners/new').get_data(as_text=True)
bad_schedule = admin_client.post('/admin/banners/new', data={'title': 'Invalid dates', 'starts_at': '2026-12-10T10:00', 'ends_at': '2026-12-10T09:00'}, content_type='multipart/form-data')
assert bad_schedule.status_code == 400 and 'End time must be after the start time.' in bad_schedule.get_data(as_text=True)
from services.images import process_banner
with app.app_context():
    resized = Image.open(io.BytesIO(process_banner(__import__('werkzeug').datastructures.FileStorage(stream=io.BytesIO((lambda b: (Image.new('RGB',(2000,1000),'#224466').save(b,'PNG'),b.getvalue())[1])(io.BytesIO())), filename='wide.png'))))
    assert resized.size == (1600, 800)

print('ALL NOTIFICATION, ADMIN AND BANNER CHECKS PASSED')
