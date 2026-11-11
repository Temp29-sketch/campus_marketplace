"""Campus address options. Confirm floor counts and girls' block names before launch."""
from extensions import db
from models import Hostel, HostelBlock, University

# Seed source for the Bingham campus. TODO confirm floor counts; TODO supply Old Hostel (Girls) blocks.
HOSTEL_SEED = [
    {"name": "New Hostel", "gender": "girls", "has_blocks": False, "floor_count": 4},
    {"name": "Old Hostel", "gender": "girls", "has_blocks": True, "blocks": []},
    {"name": "New Hostel", "gender": "boys", "has_blocks": False, "floor_count": 4},
    {"name": "Old Hostel", "gender": "boys", "has_blocks": True, "blocks": ["Abel", "Enoch", "Samson", "Barack", "Emmanuel", "Gideon", "Moses", "Daniel", "Noah"]},
]


def seed_hostels(university_id):
    for order, spec in enumerate(HOSTEL_SEED):
        hostel = Hostel.query.filter_by(university_id=university_id, name=f"{spec['name']} ({spec['gender'].title()})").first()
        if hostel is None:
            hostel = Hostel(university_id=university_id, name=f"{spec['name']} ({spec['gender'].title()})", gender=spec['gender'], has_blocks=spec['has_blocks'], floor_count=spec.get('floor_count'), sort_order=order)
            db.session.add(hostel)
            db.session.flush()
        for index, name in enumerate(spec.get('blocks', [])):
            if not HostelBlock.query.filter_by(hostel_id=hostel.id, name=name).first():
                db.session.add(HostelBlock(hostel_id=hostel.id, name=name, sort_order=index))


def hostel_options(university_id):
    try: university_id = int(university_id)
    except (TypeError, ValueError): return []
    university = db.session.get(University, university_id)
    if university is None or not university.is_active: return []
    rows = Hostel.query.filter_by(university_id=university_id, is_active=True).order_by(Hostel.sort_order, Hostel.name).all()
    options = []
    for hostel in rows:
        blocks = HostelBlock.query.filter_by(hostel_id=hostel.id).order_by(HostelBlock.sort_order, HostelBlock.name).all()
        if hostel.has_blocks and not blocks:
            continue
        options.append({"id": hostel.id, "name": hostel.name, "has_blocks": hostel.has_blocks,
                        "floor_count": hostel.floor_count, "blocks": [{"id": b.id, "name": b.name} for b in blocks]})
    return options


def validate_address(university_id, hostel_id, block_id, floor, room):
    clean = {"hostel_id": None, "hostel_block_id": None, "floor": None, "room_number": None}
    try:
        hostel = db.session.get(Hostel, int(hostel_id))
    except (TypeError, ValueError): hostel = None
    eligible = {row["id"] for row in hostel_options(university_id)}
    if hostel is None or hostel.university_id != int(university_id) or not hostel.is_active or hostel.id not in eligible:
        return clean, "Please choose an available hostel."
    clean["hostel_id"] = hostel.id
    room = (room or "").strip()
    if not (1 <= len(room) <= 10) or not __import__('re').fullmatch(r"[A-Za-z0-9/-]+", room):
        return clean, "Enter a room number using 1-10 letters, numbers, dashes or slashes."
    clean["room_number"] = room.upper()
    if hostel.has_blocks:
        try: block = db.session.get(HostelBlock, int(block_id))
        except (TypeError, ValueError): block = None
        if block is None or block.hostel_id != hostel.id:
            return clean, "Choose a block for this hostel."
        if str(floor or "").strip(): return clean, "Floor is not used for this hostel."
        clean["hostel_block_id"] = block.id
    else:
        if str(block_id or "").strip(): return clean, "This hostel does not use blocks."
        try: floor_num = int(floor)
        except (TypeError, ValueError): floor_num = None
        if floor_num is None or not 1 <= floor_num <= (hostel.floor_count or 0):
            return clean, f"Choose a floor from 1 to {hostel.floor_count or 0}."
        clean["floor"] = floor_num
    return clean, None
