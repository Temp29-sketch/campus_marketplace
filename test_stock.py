"""Stock, settings menu, and header button checks. Run: python test_stock.py"""
from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import Category, Product, University, User, VendorApplication

app = create_app(TestConfig)
with app.app_context():
    db.create_all(); seed_defaults()
    uni = University.query.filter_by(short_name="BINGHAM").one()
    cat = Category.query.filter_by(slug="food").one()
    admin = User(university=uni, name="Admin", email="admin@x.test", role="admin"); admin.set_password("password123")
    buyer = User(university=uni, name="Buyer", email="buyer@x.test"); buyer.set_password("password123")
    vendor = User(university=uni, name="Vendor", email="vendor@x.test", role="vendor"); vendor.set_password("password123")
    va = VendorApplication(user=vendor, business_name="Vendor Shop", category=cat, bio="Good products here.", phone_number="+2348012345678", status="approved")
    db.session.add_all([admin,buyer,vendor,va]); db.session.commit()

def login(email):
    c=app.test_client(); assert c.post('/login', data={'email':email,'password':'password123'}).status_code==302; return c
def body(r): return r.get_data(as_text=True)

def listing(**kwargs):
    return {'title':'Test item','category_id':str(cat_id),'price':'2500','description':'Item','is_service':'on',**kwargs}
with app.app_context(): cat_id=Category.query.filter_by(slug='food').one().id
admin_c=login('admin@x.test'); buyer_c=login('buyer@x.test'); vendor_c=login('vendor@x.test')

# Settings permissions and menu content / avatar.
assert '/settings' in body(buyer_c.get('/')) and 'B' in body(buyer_c.get('/'))
assert buyer_c.get('/settings').status_code==200 and 'Profile settings' in body(buyer_c.get('/settings'))
assert vendor_c.get('/settings').status_code==200
assert admin_c.get('/settings').status_code==403
admin_menu=body(admin_c.get('/'))
assert 'Log out' in admin_menu and 'Profile settings' not in admin_menu
assert 'Applications' in admin_menu and 'Reports' in admin_menu and 'class="btn"' in admin_menu
assert 'class="btn"' in body(vendor_c.get('/')) and 'My listings' in body(vendor_c.get('/'))
assert 'class="btn"' in body(buyer_c.get('/')) and 'Become a vendor' in body(buyer_c.get('/'))
print('settings and header checks ok')

# Product stock required, optional for services, invalid rejected.
r=vendor_c.post('/vendor/products/new', data=listing(is_service=None))
assert r.status_code==200 and b'Stock quantity is required' in r.data
for bad in ('-1','1.5','abc','100001'):
    r=vendor_c.post('/vendor/products/new', data=listing(stock=bad))
    assert r.status_code==200, bad
r=vendor_c.post('/vendor/products/new', data=listing(stock=''))
assert r.status_code==302
with app.app_context():
    service=Product.query.filter_by(title='Test item').one(); assert service.stock is None
print('stock form validation ok')

# Save sold-out item, feed omission, then raising stock restores availability.
r=vendor_c.post('/vendor/products/new', data=listing(title='Zero item', is_service='on', stock='0'))
assert r.status_code==302
with app.app_context():
    zero=Product.query.filter_by(title='Zero item').one(); assert zero.stock==0 and not zero.is_available; pid=zero.id
assert 'Zero item' not in body(buyer_c.get('/'))
r=vendor_c.post(f'/vendor/products/{pid}/toggle')
assert r.status_code==302 and 'Add stock first' in body(vendor_c.get('/vendor/products'))
# Services can be edited to tracked stock; use current checkbox plus increase.
r=vendor_c.post(f'/vendor/products/{pid}/edit', data=listing(title='Zero item', is_service='on', stock='3', is_available='on'))
assert r.status_code==302
with app.app_context():
    zero=db.session.get(Product,pid); assert zero.stock==3 and zero.is_available
# Stock decrement is conditional and atomically disables at zero.
with app.app_context():
    zero=db.session.get(Product,pid)
    assert zero.decrement_stock() is True and zero.stock==2
    zero.stock=1; zero.is_available=True; db.session.commit()
    assert zero.decrement_stock() is True and zero.stock==0 and not zero.is_available
    assert zero.decrement_stock() is False and zero.stock==0
    assert zero.decrement_stock(2) is False
print('stock save and decrement ok')

# Low-stock card thresholds 5 and 6.
with app.app_context():
    v=db.session.query(User).filter_by(email='vendor@x.test').one(); c=db.session.get(Category,cat_id)
    p5=Product(vendor=v,category=c,title='Five left',description='x',price=1,stock=5,is_available=True)
    p6=Product(vendor=v,category=c,title='Six left',description='x',price=1,stock=6,is_available=True)
    db.session.add_all([p5,p6]); db.session.commit(); id5,id6=p5.id,p6.id
feed=body(buyer_c.get('/'))
assert 'Only 5 left' in feed and 'Only 6 left' not in feed
assert 'Only 5 left' in body(buyer_c.get(f'/item/{id5}')) and '6 in stock' in body(buyer_c.get(f'/item/{id6}'))
assert '5 in stock' in body(vendor_c.get('/vendor/products'))
print('stock display ok')

print('ALL STOCK CHECKS PASSED')
