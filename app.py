from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
import bcrypt
import jwt
import datetime
from functools import wraps
import os
import random

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'shoppal-secret-key-2026'

# ---------- Serve static images ----------
@app.route('/static/images/<path:filename>')
def serve_image(filename):
    return send_from_directory('static/images', filename)

# ---------- MongoDB ----------
try:
    client = MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=5000)
    db = client['shoppal']
    users_collection = db['users']
    products_collection = db['products']
    favorites_collection = db['favorites']
    print("✅ Connected to MongoDB")
except Exception as e:
    print(f"❌ MongoDB error: {e}")
    users_collection = None
    products_collection = None
    favorites_collection = None

# ---------- Helper: AI Advice ----------
def get_ai_advice(product):
    discount = product.get('discount', 0)
    rating = product.get('rating', 0)
    if discount >= 40:
        return "Best time to buy! 🔥"
    elif discount >= 30 and rating >= 4.0:
        return "Great deal! Buy now 👍"
    elif discount >= 25 and rating >= 4.5:
        return "Good value, recommended 🛒"
    elif discount >= 20:
        return "Wait for price drop ⏳"
    else:
        return "Wait for drop 💡"

# ---------- Token Required Decorator ----------
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'success': False, 'error': 'Token missing'}), 401
        try:
            if token.startswith('Bearer '):
                token = token.split(' ')[1]
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            if users_collection is None:
                return jsonify({'success': False, 'error': 'MongoDB not available'}), 500
            current_user = users_collection.find_one({'email': data['email']})
            if not current_user:
                return jsonify({'success': False, 'error': 'User not found'}), 401
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid token'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

# ==================== PRODUCT SEARCH + RECOMMENDATIONS ====================
@app.route('/api/search', methods=['POST'])
def search():
    try:
        data = request.get_json() or {}
        query = data.get('query', '').strip()
        category = data.get('category', 'all')

        filter_query = {}
        if category != 'all':
            filter_query['category'] = category
        if query:
            filter_query['$or'] = [
                {'name': {'$regex': query, '$options': 'i'}},
                {'brand': {'$regex': query, '$options': 'i'}}
            ]

        products = list(products_collection.find(filter_query).limit(50))
        for p in products:
            p['_id'] = str(p['_id'])
            p['ml_insights'] = {
                'price_prediction': {
                    'advice': get_ai_advice(p)
                }
            }

        recommended = []
        if products and query:
            categories = set(p.get('category') for p in products if p.get('category'))
            if categories:
                shown_names = set(p['name'] for p in products)
                rec_filter = {
                    'category': {'$in': list(categories)},
                    'name': {'$nin': list(shown_names)}
                }
                recommended = list(products_collection.find(rec_filter).limit(10))
                for r in recommended:
                    r['_id'] = str(r['_id'])
                    r['ml_insights'] = {
                        'price_prediction': {
                            'advice': get_ai_advice(r)
                        }
                    }

        return jsonify({
            'success': True,
            'products': products,
            'count': len(products),
            'recommendations': recommended
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'products': [], 'recommendations': []}), 500

# ==================== AUTH ====================
@app.route('/api/signup', methods=['POST'])
def signup():
    if users_collection is None:
        return jsonify({'success': False, 'error': 'MongoDB not available'}), 500
    try:
        data = request.get_json()
        name, email, password = data.get('name'), data.get('email'), data.get('password')
        if not all([name, email, password]):
            return jsonify({'success': False, 'error': 'All fields required'}), 400
        if users_collection.find_one({'email': email}):
            return jsonify({'success': False, 'error': 'User already exists'}), 409
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        user = {
            'name': name,
            'email': email,
            'password': hashed,
            'created_at': datetime.datetime.utcnow(),
            'phone': '',
            'dob': '',
            'gender': '',
            'addresses': [],
            'purchase_history': [],
            'recent_views': [],
            'skin_profile': {'skin_type': '', 'concerns': [], 'allergies': []}
        }
        users_collection.insert_one(user)
        return jsonify({'success': True, 'message': 'Account created'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    if users_collection is None:
        return jsonify({'success': False, 'error': 'MongoDB not available'}), 500
    try:
        data = request.get_json()
        email, password = data.get('email'), data.get('password')
        user = users_collection.find_one({'email': email})
        if not user or not bcrypt.checkpw(password.encode('utf-8'), user['password']):
            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
        token = jwt.encode(
            {'email': email, 'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)},
            app.config['SECRET_KEY'],
            algorithm='HS256'
        )
        return jsonify({
            'success': True,
            'token': token,
            'user': {
                'name': user['name'],
                'email': email,
                'phone': user.get('phone', ''),
                'dob': user.get('dob', ''),
                'gender': user.get('gender', '')
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== PROFILE ====================
@app.route('/api/profile', methods=['GET'])
@token_required
def get_profile(current_user):
    try:
        fav_count = favorites_collection.count_documents({'email': current_user['email']}) if favorites_collection is not None else 0
        user_data = {
            'name': current_user['name'],
            'email': current_user['email'],
            'phone': current_user.get('phone', ''),
            'dob': current_user.get('dob', ''),
            'gender': current_user.get('gender', ''),
            'favorites_count': fav_count,
            'purchase_history': current_user.get('purchase_history', []),
            'recent_views': current_user.get('recent_views', [])
        }
        return jsonify({'success': True, 'user': user_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/update-profile', methods=['PUT'])
@token_required
def update_profile(current_user):
    try:
        data = request.get_json()
        allowed = ['name', 'phone', 'dob', 'gender']
        update_data = {k: v for k, v in data.items() if k in allowed}
        if update_data:
            users_collection.update_one({'email': current_user['email']}, {'$set': update_data})
        return jsonify({'success': True, 'message': 'Profile updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== ADDRESSES ====================
@app.route('/api/addresses', methods=['GET'])
@token_required
def get_addresses(current_user):
    addresses = current_user.get('addresses', [])
    return jsonify({'addresses': addresses})

@app.route('/api/addresses', methods=['POST'])
@token_required
def add_address(current_user):
    try:
        data = request.get_json()
        if not data.get('name') or not data.get('street') or not data.get('city'):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        data['id'] = str(datetime.datetime.utcnow().timestamp())
        addresses = current_user.get('addresses', [])
        addresses.append(data)
        users_collection.update_one({'email': current_user['email']}, {'$set': {'addresses': addresses}})
        return jsonify({'success': True, 'message': 'Address added'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/addresses/<address_id>', methods=['DELETE'])
@token_required
def delete_address(current_user, address_id):
    try:
        addresses = current_user.get('addresses', [])
        addresses = [a for a in addresses if a.get('id') != address_id]
        users_collection.update_one({'email': current_user['email']}, {'$set': {'addresses': addresses}})
        return jsonify({'success': True, 'message': 'Address deleted'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== SKIN PROFILE ====================
@app.route('/api/skin-profile', methods=['GET'])
@token_required
def get_skin_profile(current_user):
    skin = current_user.get('skin_profile', {})
    return jsonify({'skin_profile': skin})

@app.route('/api/skin-profile', methods=['PUT'])
@token_required
def update_skin_profile(current_user):
    try:
        data = request.get_json()
        users_collection.update_one({'email': current_user['email']}, {'$set': {'skin_profile': data}})
        return jsonify({'success': True, 'message': 'Skin profile updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== FAVORITES (FIXED) ====================
# All favorites are now stored in the 'products' field of the favorites collection.
# Both write and read operations use 'products'.

@app.route('/api/add-to-favorites', methods=['POST'])
@token_required
def add_to_favorites(current_user):
    if favorites_collection is None:
        return jsonify({'success': False, 'error': 'MongoDB not available'}), 500
    try:
        data = request.get_json()
        product = data.get('product')
        if not product:
            return jsonify({'success': False, 'error': 'Product name required'}), 400

        # Use 'products' field consistently
        favorites_collection.update_one(
            {'email': current_user['email']},
            {'$addToSet': {'products': product}},
            upsert=True
        )
        return jsonify({'success': True, 'message': 'Added to favorites ❤️'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites', methods=['GET'])
@token_required
def get_favorites(current_user):
    if favorites_collection is None:
        return jsonify({'success': False, 'favorites': []}), 200
    fav_doc = favorites_collection.find_one({'email': current_user['email']})
    # Read from 'products' field
    return jsonify({
        'success': True,
        'favorites': fav_doc.get('products', []) if fav_doc else []
    })

@app.route('/api/remove-from-favorites', methods=['POST'])
@token_required
def remove_from_favorites(current_user):
    if favorites_collection is None:
        return jsonify({'success': False, 'error': 'MongoDB not available'}), 500
    try:
        data = request.get_json()
        product = data.get('product')
        if product:
            # Remove from 'products' field
            favorites_collection.update_one(
                {'email': current_user['email']},
                {'$pull': {'products': product}}
            )
        return jsonify({'success': True, 'message': 'Removed from favorites'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== HEALTH ====================
@app.route('/api/health', methods=['GET'])
def health():
    count = products_collection.count_documents({}) if products_collection is not None else 0
    return jsonify({'status': 'healthy', 'products': count})

# ==================== START SERVER ====================
if __name__ == '__main__':
    os.makedirs('static/images', exist_ok=True)
    print("\n🚀 Shoppal Backend Started")
    print("📍 http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)