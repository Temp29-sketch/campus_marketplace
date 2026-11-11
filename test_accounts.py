"""Signup, hostel, avatar, bank, settings and privacy checks. Run: python test_accounts.py"""
import hashlib
import io
import re
from datetime import timedelta

from PIL import Image
from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import Category, Hostel, HostelBlock, PendingSignup, Product, University, User, VendorApplication, utcnow
from services.hostels import hostel_options, validate_address
from services.images import process_avatar
from services.banks import mask_account

app = create_app(TestConfig)
with app.app_context():
    db.create_all(); seed_defaults()
    uni = University.query.filter_by(short_name="BINGHAM").one()
    food = Category.query.filter_by(slug="food").one()
    boys_old = Hostel.query.filter_by(name="Old Hostel (Boys)").one()
    boys_new = Hostel.query.filter_by(name="New Hostel (Boys)").one()
    girls_old = Hostel.query.filter_by(name="Old Hostel (Girls)").one()
    assert all(h['id'] != girls_old.id for h in hostel_options(uni.id))
    block = HostelBlock.query.filter_by(hostel_id=boys_old.id, name="Abel").one()
    clean, error = validate_address(uni.id, boys_old.id, block.id, "", "12a")
    assert not error and clean['room_number'] == '12A'
    assert validate_address(uni.id, boys_old.id, block.id, "1", "12")[1]
    assert validate_address(uni.id, boys_new.id, "", "4", "A-1")[1] is None
    assert validate_address(uni.id, boys_new.id, "", "5", "A-1")[1]
    assert validate_address(uni.id, boys_new.id, "", "", "A-1")[1]
    assert validate_address(uni.id, boys_new.id, block.id, "1", "A")[1]
    assert validate_address(uni.id, boys_old.id, block.id, "", "bad room!")[1]
    other = University(name="Other Uni", short_name="OTHER", email_domain="other.test"); db.session.add(other); db.session.flush()
    other_hostel = Hostel(university_id=other.id, name="Other Campus Hostel", gender="boys", has_blocks=False, floor_count=2)
    db.session.add(other_hostel); db.session.flush(); other_hostel_id=other_hostel.id
    assert validate_address(uni.id, other_hostel_id, "", "1", "A")[1]
    girls_block = HostelBlock(hostel_id=girls_old.id, name="A Block"); db.session.add(girls_block); db.session.flush()
    assert any(h['id'] == girls_old.id for h in hostel_options(uni.id))
    categories_id, university_id, boys_new_id, boys_old_id, boys_block_id = food.id, uni.id, boys_new.id, boys_old.id, block.id
    admin = User(university=uni, name="Admin", email="admin@test.invalid", role="admin"); admin.set_password("password123")
    db.session.add(admin); db.session.commit(); admin_id=admin.id

client = app.test_client()
def body(response): return response.get_data(as_text=True)
def login(email, password='password123'):
    c=app.test_client(); assert c.post('/login', data={'email':email,'password':password}).status_code == 302; return c
address = {'hostel_id': str(boys_new_id), 'hostel_block_id':'', 'floor':'2', 'room_number':'B-12'}

# Branding and buyer signup.
for path in ('/', '/login', '/register', '/register/vendor'):
    page=body(client.get(path)); assert 'Campus Market' not in page and 'UniHub' in page
assert client.post('/register', data={'name':'Buyer One','email':'buyer@example.test','password':'password123','university_id':university_id,**address}).status_code==302
with app.app_context():
    buyer=User.query.filter_by(email='buyer@example.test').one(); assert buyer.hostel_id==boys_new_id and buyer.floor==2 and buyer.room_number=='B-12'; buyer_id=buyer.id
buyer_client=login('buyer@example.test')
assert app.test_client().post('/register', data={'name':'Invalid','email':'bad@example.test','password':'password123','university_id':university_id,**{**address,'floor':'5'}}).status_code==200

# Vendor password-first: hashed pending, GET is side-effect-free; confirm POST creates the user.
signup=app.test_client()
base={'name':'New Vendor','university_id':str(university_id),'school_email':'new@binghamuni.edu.ng','matric_number':'22/001','password':'password123','confirm_password':'password123',**address}
assert signup.post('/register/vendor', data={**base,'password':'short','confirm_password':'short'}).status_code==200
assert signup.post('/register/vendor', data={**base,'password':'password123','confirm_password':'mismatch'}).status_code==200
# Existing login email and a used matric are blocked.
with app.app_context():
    buyer=db.session.get(User,buyer_id); buyer.mark_school_email_verified('buyer@binghamuni.edu.ng','22/009'); db.session.commit()
assert signup.post('/register/vendor', data={**base,'school_email':'buyer@binghamuni.edu.ng'}).status_code==200
assert signup.post('/register/vendor', data={**base,'school_email':'other@binghamuni.edu.ng','matric_number':'22/009'}).status_code==200
assert signup.post('/register/vendor', data=base).status_code==200
with app.app_context():
    pending=PendingSignup.query.filter_by(school_email=base['school_email']).one(); assert pending.token_hash != pending.school_email and len(pending.token_hash)==64
    assert User.query.filter_by(email=base['school_email']).count()==0
    stored_hash=pending.token_hash
old_link=re.search(r'http[^\s]+/register/vendor/confirm/(\S+)', app.extensions['outbox'][-1]['body']).group(1)
# Re-submission for the same email replaces the pending row and invalidates its old token.
again={**base,'matric_number':'22/002'}
signup.post('/register/vendor', data=again)
with app.app_context():
    pending=PendingSignup.query.filter_by(school_email=base['school_email']).one(); assert pending.token_hash != stored_hash
    assert User.query.filter_by(email=base['school_email']).count()==0
link=re.search(r'http[^\s]+/register/vendor/confirm/(\S+)', app.extensions['outbox'][-1]['body']).group(1)
token_hash=hashlib.sha256(link.encode()).hexdigest()
with app.app_context(): assert token_hash==PendingSignup.query.filter_by(school_email=base['school_email']).one().token_hash
assert signup.get(f'/register/vendor/confirm/{old_link}').status_code==200
assert signup.get(f'/register/vendor/confirm/{link}').status_code==200
with app.app_context(): assert User.query.filter_by(email=base['school_email']).count()==0
assert signup.post(f'/register/vendor/confirm/{link}', data={}).status_code==302
with app.app_context():
    vendor=User.query.filter_by(email=base['school_email']).one(); assert vendor.has_verified_school_email and vendor.room_number=='B-12'; vendor_id=vendor.id
assert signup.get(f'/register/vendor/confirm/{link}').status_code==200
assert body(signup.get(f'/register/vendor/confirm/garbage'))
# Expired rows are rejected with a friendly response.
expired_form={**base,'school_email':'expired@binghamuni.edu.ng','matric_number':'22/008'}
expired_client=app.test_client()
expired_client.post('/register/vendor', data=expired_form)
expired_link=re.search(r'http[^\s]+/register/vendor/confirm/(\S+)', app.extensions['outbox'][-1]['body']).group(1)
with app.app_context():
    exp=PendingSignup.query.filter_by(school_email=expired_form['school_email']).one(); exp.expires_at=utcnow()-timedelta(seconds=1); db.session.commit()
assert 'unavailable' in body(expired_client.get(f'/register/vendor/confirm/{expired_link}')).lower()
print('signup and hostel validation ok')

# Avatar image pipeline exact square dimensions.
def png_upload():
    buf=io.BytesIO(); Image.new('RGB',(420,300),'teal').save(buf,'PNG'); buf.seek(0); return buf
with app.app_context():
    full, thumb=process_avatar(type('Upload',(),{'filename':'face.png','read':png_upload().read})())
    assert Image.open(io.BytesIO(full)).size==(512,512) and Image.open(io.BytesIO(thumb)).size==(128,128)
print('avatar processing ok')

# Application requires valid photo and banking data, then stores square avatar and masked account.
with app.app_context():
    vendor=db.session.get(User,vendor_id); vendor.photo_key=None; db.session.commit()
vendor_client=login('new@binghamuni.edu.ng')
appdata={'business_name':'Fresh Foods','category_id':str(categories_id),'bio':'Fresh meals delivered around campus every day.','phone_number':'08012345678','bank_code':'044','account_number':'1234567890','own_account':'on'}
assert vendor_client.post('/become-vendor/apply', data=appdata).status_code==200
assert vendor_client.post('/become-vendor/apply', data={**appdata,'account_number':'123'},content_type='multipart/form-data').status_code==200
assert vendor_client.post('/become-vendor/apply', data={**appdata,'bank_code':'999'},content_type='multipart/form-data').status_code==200
assert vendor_client.post('/become-vendor/apply', data={**appdata,'profile_photo':(io.BytesIO(b'bad'),'fake.jpg')},content_type='multipart/form-data').status_code==200
r=vendor_client.post('/become-vendor/apply', data={**appdata,'profile_photo':(png_upload(),'face.png')},content_type='multipart/form-data')
assert r.status_code==302
with app.app_context():
    vendor=db.session.get(User,vendor_id); va=vendor.vendor_application; assert vendor.photo_key and va.account_number=='1234567890'; assert mask_account(va.account_number)=='******7890'; key=vendor.photo_key; va_id=va.id
    from services.storage import _folder
    import os
    assert Image.open(os.path.join(_folder(),key)).size==(512,512)
# Model approval blocks incomplete profiles.
with app.app_context():
    va=db.session.get(VendorApplication,va_id); admin=db.session.get(User,admin_id); old=va.user.photo_key; va.user.photo_key=None
    try: va.approve(admin); raise AssertionError('approval accepted missing photo')
    except ValueError: pass
    va.user.photo_key=old; va.bank_code=None
    try: va.approve(admin); raise AssertionError('approval accepted missing bank')
    except ValueError: pass
    va.bank_code='044'; va.approve(admin); db.session.commit()
print('vendor application checks ok')

# Settings profile/address/password/theme/business/bank controls.
assert vendor_client.get('/settings').status_code==200
assert '******7890' in body(vendor_client.get('/settings')) and '1234567890' not in body(vendor_client.get('/settings'))
assert 'name="theme"' in body(vendor_client.get('/settings'))
assert vendor_client.post('/settings/address', data={'hostel_id':str(boys_old_id),'hostel_block_id':str(boys_block_id),'room_number':'B4','floor':''}).status_code==302
assert vendor_client.post('/settings/profile', data={'name':'Renamed Vendor'}).status_code==302
with app.app_context(): assert db.session.get(User,vendor_id).name=='Renamed Vendor'
old_key=key
assert vendor_client.post('/settings/profile', data={'name':'Renamed Vendor','profile_photo':(png_upload(),'replacement.png')},content_type='multipart/form-data').status_code==302
with app.app_context(): new_key=db.session.get(User,vendor_id).photo_key; assert new_key != old_key
import os
from services.storage import _folder
with app.app_context():
    assert not os.path.exists(os.path.join(_folder(),old_key))
    assert not os.path.exists(os.path.join(_folder(),old_key.replace('.webp','_t.webp')))
assert buyer_client.post('/settings/profile', data={'name':'Buyer One','profile_photo':(png_upload(),'buyer.png')},content_type='multipart/form-data').status_code==302
assert buyer_client.post('/settings/profile', data={'name':'Buyer One','remove_photo':'on'}).status_code==302
with app.app_context(): assert db.session.get(User,buyer_id).photo_key is None
assert vendor_client.post('/settings/profile', data={'name':'Renamed Vendor','remove_photo':'on'}).status_code==302
with app.app_context(): assert db.session.get(User,vendor_id).photo_key
assert vendor_client.post('/settings/password', data={'current_password':'wrong','new_password':'newpassword','confirm_password':'newpassword'}).status_code==302
before=len(app.extensions['outbox'])
assert vendor_client.post('/settings/password', data={'current_password':'password123','new_password':'newpassword','confirm_password':'newpassword'}).status_code==302
assert len(app.extensions['outbox'])==before+1 and 'password was changed' in app.extensions['outbox'][-1]['subject'].lower()
assert vendor_client.post('/settings/business', data={'bio':'This is a longer business bio for profile.','phone_number':'08019876543'}).status_code==302
with app.app_context(): va=db.session.get(VendorApplication,va_id); va.account_name='Known Name'; va.paystack_subaccount_code='SUB_1'; db.session.commit()
assert vendor_client.post('/settings/bank', data={'bank_code':'058','account_number':'9876543210','current_password':'wrong'}).status_code==302
before=len(app.extensions['outbox'])
assert vendor_client.post('/settings/bank', data={'bank_code':'058','account_number':'9876543210','current_password':'newpassword'}).status_code==302
assert len(app.extensions['outbox'])==before+1
with app.app_context(): va=db.session.get(VendorApplication,va_id); assert va.account_name is None and va.paystack_subaccount_code is None and va.bank_name=='GTBank'
assert app.test_client().get('/settings').status_code==302
admin_client=login('admin@test.invalid'); assert admin_client.get('/settings').status_code==403
print('settings checks ok')

# Public privacy: buyer address/photo and any bank data do not leak; vendor image is public.
with app.app_context():
    buyer=db.session.get(User,buyer_id); buyer.photo_key='buyer-secret-photo.webp'
    v=db.session.get(User,vendor_id); cat=db.session.get(Category,categories_id)
    product=Product(vendor=v,category=cat,title='Public Rice',description='Good rice',price=500,stock=5,is_available=True)
    db.session.add(product); db.session.commit(); product_id=product.id
anon=app.test_client()
for page in ('/',f'/item/{product_id}',f'/store/{vendor_id}'):
    html=body(anon.get(page))
    assert 'buyer-secret-photo.webp' not in html and 'B-12' not in html and '1234567890' not in html and '9876543210' not in html
assert key not in body(anon.get(f'/store/{vendor_id}')) # use latest key after photo replacement below? vendor key unchanged
assert 'buyer-secret-photo' not in body(anon.get('/'))
assert 'profile photo' in body(anon.get(f'/store/{vendor_id}'))
assert new_key.replace('.webp','_t.webp') in body(anon.get(f'/store/{vendor_id}'))
print('privacy checks ok')
assert all('Campus Market' not in (mail['subject'] + mail['body']) for mail in app.extensions['outbox'])

print('ALL ACCOUNT CHECKS PASSED')
