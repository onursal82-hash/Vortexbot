import os
import json
import time
import logging
import requests
import ccxt
import secrets
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# --- Secrets ---
API_KEY = os.getenv('OKX_API_KEY')
SECRET_KEY = os.getenv('OKX_SECRET_KEY')
PASSPHRASE = os.getenv('OKX_PASSPHRASE')

# --- Exchange Setup (CCXT) ---
# In a real multi-user app, API keys should be per-user. 
# For this platform, we'll share the exchange connection for market data,
# but simulated trading is isolated per user.
exchange = ccxt.okx({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'password': PASSPHRASE,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# --- Constants ---
STORAGE_FILE = 'bot_storage.json'

# --- Global Data Store ---
# Structure: { "users": { "email": { "password": "...", "bots": {}, "financials": {}, "history": [] } } }
DATA = {
    "users": {}
}

# --- Global Caches (Shared) ---
MARKET_CACHE = {
    "ticker": {},
    "last_updated": 0
}

# --- Persistence Layer ---
def load_data():
    global DATA
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE, 'r') as f:
                loaded = json.load(f)
                # Migration check: if old format (has 'bots' at root), migrate
                if 'bots' in loaded and 'users' not in loaded:
                    logging.info("Migrating legacy data to multi-user format...")
                    DATA['users']['legacy@admin.com'] = {
                        "password": generate_password_hash("admin123"),
                        "bots": loaded.get('bots', {}),
                        "financials": loaded.get('financials', {}),
                        "history": loaded.get('history', [])
                    }
                else:
                    DATA = loaded
            logging.info(f"System Restored: {len(DATA.get('users', {}))} users loaded.")
        except Exception as e:
            logging.error(f"Failed to load storage: {e}")
            DATA = {"users": {}}

def save_data():
    try:
        with open(STORAGE_FILE, 'w') as f:
            json.dump(DATA, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save storage: {e}")

# --- Auth Helper ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user_data():
    email = session.get('user')
    if not email or email not in DATA['users']:
        return None
    return DATA['users'][email]

# --- DCA Engine (The Brain) ---
def calculate_dca_logic(user_email, bot, current_price):
    """
    Executes the DCA logic for a specific bot.
    """
    entry_price = bot['entry_price']
    dca_config = bot.get('dca_config', {})
    user_data = DATA['users'][user_email]
    
    # 1. Update Real-time PnL
    pnl_percent = ((current_price - entry_price) / entry_price) * 100
    bot['pnl'] = round(pnl_percent, 2)
    bot['current_price'] = current_price
    
    # 2. Safety Order Logic
    so_count = bot.get('safety_orders_filled', 0)
    max_so = dca_config.get('max_safety_orders', 5)
    
    base_dev = dca_config.get('price_deviation', 1.5)
    step_scale = dca_config.get('step_scale', 1.5)
    
    required_drop = base_dev * (step_scale ** so_count)
    
    if so_count < max_so and pnl_percent < -required_drop:
        logging.info(f"DCA Trigger: Safety Order {so_count + 1} for {bot['symbol']} (User: {user_email})")
        bot['safety_orders_filled'] += 1
        
        so_base_size = dca_config.get('safety_order', 0)
        vol_scale = dca_config.get('volume_scale', 1.5)
        
        so_volume = so_base_size * (vol_scale ** so_count)
        
        bot['investment'] += so_volume
        user_data['financials']['reserved_capital'] += so_volume
        
        current_coins = (bot['investment'] - so_volume) / entry_price
        new_coins = so_volume / current_price
        bot['entry_price'] = bot['investment'] / (current_coins + new_coins)

        # Log Trade
        user_data['history'].insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "symbol": bot['symbol'],
            "type": f"DCA Buy #{so_count + 1}",
            "price": current_price,
            "pnl": f"{pnl_percent:.2f}%"
        })
        if len(user_data['history']) > 50: user_data['history'].pop()

    # 3. Take Profit Logic
    tp_target = dca_config.get('take_profit', 1.5)
    if pnl_percent >= tp_target:
        # Check Loop Logic
        loop_enabled = dca_config.get('loop_enabled', False)
        
        profit_amount = (bot['investment'] * (pnl_percent / 100))
        user_data['financials']['total_balance'] += profit_amount
        user_data['financials']['net_pnl'] += profit_amount
        user_data['financials']['reserved_capital'] -= bot['investment']
        
        # Log Trade
        user_data['history'].insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "symbol": bot['symbol'],
            "type": "Take Profit",
            "price": current_price,
            "pnl": f"+{pnl_percent:.2f}%"
        })
        
        logging.info(f"TAKE PROFIT: {bot['symbol']} closed with {pnl_percent}% (User: {user_email})")

        if loop_enabled:
            # RESTART CYCLE
            logging.info(f"LOOP TRIGGERED: Restarting {bot['symbol']} for {user_email}")
            base_order = dca_config.get('base_order', bot['investment']) # Try to find original base, else use current (buggy? No, investment grows)
            # Correct logic: investment resets to base_order.
            # We need to know the original base order. It's in dca_config['base_order'].
            
            bot['status'] = 'running'
            bot['investment'] = dca_config.get('base_order', 20.0)
            bot['entry_price'] = current_price # Re-enter at current market price
            bot['safety_orders_filled'] = 0
            bot['start_time'] = datetime.now().isoformat()
            
            # Reserve capital again
            user_data['financials']['reserved_capital'] += bot['investment']
            
            user_data['history'].insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "symbol": bot['symbol'],
                "type": "Loop Restart",
                "price": current_price,
                "pnl": "0.00%"
            })
        else:
            bot['status'] = 'completed'

    # 4. Stop Loss Logic (Simpler for now)
    if dca_config.get('stop_loss_enabled'):
        sl_target = dca_config.get('stop_loss', 5.0)
        if pnl_percent <= -sl_target:
             bot['status'] = 'stopped_loss'
             loss_amount = (bot['investment'] * (pnl_percent / 100))
             user_data['financials']['total_balance'] += loss_amount
             user_data['financials']['net_pnl'] += loss_amount
             user_data['financials']['reserved_capital'] -= bot['investment']
             
             user_data['history'].insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "symbol": bot['symbol'],
                "type": "Stop Loss",
                "price": current_price,
                "pnl": f"{pnl_percent:.2f}%"
            })

# --- Background Scheduler ---
def update_market_data():
    global MARKET_CACHE
    
    # Collect all active symbols from ALL users
    targets = {'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'}
    
    for email, user_data in DATA['users'].items():
        bots = user_data.get('bots', {})
        for b in bots.values():
            if b['status'] == 'running':
                sym = b.get('symbol', '')
                if sym and '-' in sym:
                    targets.add(sym.replace('-', '/'))
    
    # Filter out invalid symbols
    targets = {t for t in targets if '/' in t and len(t) > 4 and '///' not in t}
    
    try:
        if not targets:
            return

        tickers = exchange.fetch_tickers(list(targets))
        
        # Update Cache
        for symbol, ticker in tickers.items():
            dash_sym = symbol.replace('/', '-')
            MARKET_CACHE['ticker'][dash_sym] = {
                'last': ticker['last'],
                'change': ticker.get('percentage', 0.0)
            }
        
        MARKET_CACHE['last_updated'] = time.time()
        
        # Run Logic for ALL Users
        changes = False
        for email, user_data in DATA['users'].items():
            bots = user_data.get('bots', {})
            for b_sym, bot in bots.items():
                if bot['status'] == 'running':
                    # Get price
                    # Handle symbol mismatch safe
                    ticker_key = b_sym.replace('-', '/')
                    if ticker_key in tickers:
                        price = tickers[ticker_key]['last']
                        calculate_dca_logic(email, bot, price)
                        changes = True
        
        # Hard-Core Persistence: Save every cycle if changes or at least every minute (handled by job freq)
        # User requested "every minute". We run every 5s. 
        # We save on changes for safety.
        if changes:
            save_data()
            
    except Exception as e:
        logging.error(f"Sync Error: {e}")

def periodic_save():
    """Explicit save every minute as requested."""
    save_data()
    logging.info("Periodic Persistence Save Complete.")

def keep_awake():
    """Self-ping to keep the instance active on Render free tier."""
    try:
        # Render provides this env var. Fallback to localhost if local.
        base_url = os.getenv('RENDER_EXTERNAL_URL')
        if not base_url:
            base_url = 'http://127.0.0.1:5300'
            
        # Ensure scheme
        if not base_url.startswith('http'):
            base_url = f"https://{base_url}" # Render URLs are https usually
            
        # Ping a lightweight endpoint
        target = f"{base_url}/favicon.ico"
        response = requests.get(target, timeout=10)
        logging.info(f"Health check: Keeping the engine awake... (Ping {target} - Status: {response.status_code})")
    except Exception as e:
        logging.warning(f"Health check ping failed: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(update_market_data, 'interval', seconds=2, max_instances=1) # 2s Refresh
scheduler.add_job(periodic_save, 'interval', minutes=1) # Hard-Core Persistence
scheduler.add_job(keep_awake, 'interval', minutes=5) # Prevent Sleep
scheduler.start()
logging.info("BACKGROUND SERVICE STARTED: Market Data & Bot Logic running independently.")

# --- Routes ---

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/login')
def login_page():
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    email = data.get('email')
    # password = data.get('password') # Ignored for bypass
    
    if not email:
        return jsonify({"status": "error", "message": "Email required"}), 400

    # LOGIN BYPASS: Grant access immediately
    if email not in DATA['users']:
        # Auto-register new user
        DATA['users'][email] = {
            "password": generate_password_hash("bypass_mode"),
            "bots": {},
            "financials": {
                "total_balance": 10000.00,
                "reserved_capital": 0.00,
                "net_pnl": 0.00
            },
            "history": []
        }
        save_data()
        logging.info(f"New User Auto-Registered: {email}")
    
    session['user'] = email
    return jsonify({"status": "success"})

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    
    if email in DATA['users']:
        return jsonify({"status": "error", "message": "User already exists"}), 400
    
    # Create new user
    DATA['users'][email] = {
        "password": generate_password_hash(password),
        "bots": {},
        "financials": {
            "total_balance": 10000.00,
            "reserved_capital": 0.00,
            "net_pnl": 0.00
        },
        "history": []
    }
    save_data()
    session['user'] = email
    return jsonify({"status": "success"})

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login_page'))

@app.route('/api/dashboard')
@login_required
def dashboard_data():
    user = get_current_user_data()
    if not user: return jsonify({}), 401
    
    bots = user.get('bots', {})
    
    # Calculate Unrealized PnL
    unrealized_pnl = sum([b['investment'] * (b['pnl']/100) for b in bots.values() if b['status'] == 'running'])
    
    financials = {
        "total_balance": user['financials']['total_balance'],
        "reserved": user['financials']['reserved_capital'],
        "net_pnl": user['financials']['net_pnl'] + unrealized_pnl
    }
    
    # Mock Ticker fallback
    if not MARKET_CACHE['ticker']:
         MARKET_CACHE['ticker'] = {
            'BTC-USDT': {'last': 50000.0, 'change': 2.5},
            'ETH-USDT': {'last': 3000.0, 'change': 1.2},
            'SOL-USDT': {'last': 100.0, 'change': -0.5},
            'BNB-USDT': {'last': 400.0, 'change': 0.1},
            'XRP-USDT': {'last': 0.5, 'change': 0.0}
        }

    return jsonify({
        "financials": financials,
        "bots": list(bots.values()),
        "ticker": MARKET_CACHE['ticker'],
        "history": user['history'][:20]
    })

@app.route('/api/symbols')
def get_symbols():
    # Public route, cached
    # ... (Reuse existing logic or simplified)
    # For brevity, return hardcoded or fetch. 
    # Let's fetch properly.
    try:
        tickers = exchange.fetch_tickers()
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT') and ticker.get('quoteVolume'):
                usdt_pairs.append({
                    'symbol': symbol.replace('/', '-'),
                    'volume': ticker['quoteVolume'],
                    'last': ticker['last']
                })
        usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)
        return jsonify(usdt_pairs[:200])
    except:
        return jsonify([])

@app.route('/api/start_strategy', methods=['POST'])
@login_required
def start_strategy():
    try:
        user_email = session['user']
        user_data = DATA['users'][user_email]
        bots = user_data['bots']
        
        data = request.get_json()
        symbol = data.get('symbol')
        
        if not symbol:
            return jsonify({"status": "error", "message": "No symbol selected"}), 400
            
        if symbol in bots and bots[symbol]['status'] == 'running':
             return jsonify({"status": "error", "message": "Bot already active"}), 400

        # Fetch Price
        try:
            ticker = exchange.fetch_ticker(symbol.replace('-', '/'))
            price = ticker['last']
        except:
             price = 50000.0 # Fallback

        new_bot = {
            "symbol": symbol,
            "status": "running",
            "entry_price": price,
            "current_price": price,
            "investment": 20.0,
            "pnl": 0.0,
            "dca_config": {
                "base_order": 20.0,
                "safety_order": 40.0,
                "max_safety_orders": 15,
                "volume_scale": 1.05,
                "step_scale": 1.0,
                "price_deviation": 2.0,
                "take_profit": 1.5,
                "stop_action": "close",
                "stop_loss_enabled": False,
                "loop_enabled": True # Default to Loop for Vortex
            },
            "safety_orders_filled": 0,
            "start_time": datetime.now().isoformat()
        }
        
        bots[symbol] = new_bot
        user_data['financials']['reserved_capital'] += 20.0
        save_data()
        
        return jsonify({"status": "success", "message": "Vortex Strategy Activated"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/bot_details/<bot_id>')
@login_required
def bot_details(bot_id):
    user_email = session['user']
    user_data = DATA['users'][user_email]
    bots = user_data.get('bots', {})
    
    # bot_id is the symbol
    if bot_id not in bots:
        return "Bot not found", 404
        
    bot = bots[bot_id]
    
    # Filter logs for this bot
    logs = [l for l in user_data.get('history', []) if l['symbol'] == bot_id]
    
    return render_template('bot_details.html', bot=bot, logs=logs)

@app.route('/api/create_bot', methods=['POST'])
@login_required
def create_bot():
    try:
        user_email = session['user']
        user_data = DATA['users'][user_email]
        bots = user_data['bots']
        
        data = request.json
        symbol = data.get('symbol', '').upper()
        if '/' in symbol: symbol = symbol.replace('/', '-')
        
        base_order = float(data.get('investment', 100))
        dca_config = data.get('dca_config', {})
        
        # Ensure loop_enabled is captured
        # The frontend might not send it yet, we need to update frontend.
        # But if it does, it's in dca_config.
        
        if symbol in bots and bots[symbol]['status'] == 'running':
             return jsonify({"status": "error", "message": "Bot already running"}), 400

        try:
            ticker = exchange.fetch_ticker(symbol.replace('-', '/'))
            price = ticker['last']
        except:
             return jsonify({"status": "error", "message": "Invalid Pair"}), 400

        new_bot = {
            "symbol": symbol,
            "status": "running",
            "entry_price": price,
            "current_price": price,
            "investment": base_order,
            "pnl": 0.0,
            "dca_config": dca_config,
            "safety_orders_filled": 0,
            "start_time": datetime.now().isoformat()
        }
        
        bots[symbol] = new_bot
        user_data['financials']['reserved_capital'] += base_order
        save_data()
        
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stop_bot', methods=['POST'])
@login_required
def stop_bot():
    user_email = session['user']
    user_data = DATA['users'][user_email]
    bots = user_data['bots']
    
    data = request.json
    symbol = data.get('symbol')
    
    if symbol in bots:
        # Panic Sell or Stop?
        # Simplified: Stop just releases capital (assuming sold at break-even or manual handling)
        # For Panic Sell (Close at Market), we need separate logic or reuse this.
        # Let's implement panic logic here if requested, but sticking to stop for now.
        user_data['financials']['reserved_capital'] -= bots[symbol]['investment']
        del bots[symbol]
        save_data()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 404

@app.route('/api/panic_sell', methods=['POST'])
@login_required
def panic_sell():
    user_email = session['user']
    user_data = DATA['users'][user_email]
    bots = user_data['bots']
    
    data = request.json
    symbol = data.get('symbol')
    
    if symbol in bots:
        bot = bots[symbol]
        pnl_percent = bot.get('pnl', 0.0)
        pnl_amount = (bot['investment'] * (pnl_percent / 100))
        
        user_data['financials']['total_balance'] += pnl_amount
        user_data['financials']['net_pnl'] += pnl_amount
        user_data['financials']['reserved_capital'] -= bot['investment']
        
        del bots[symbol]
        save_data()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 404

@app.route('/favicon.ico')
def favicon():
    return '', 204

# --- Initialization ---
load_data()

if __name__ == '__main__':
    print('--- VORTEX PLATFORM SERVER STARTING ON PORT 5300 ---')
    app.run(host='0.0.0.0', port=5300, debug=True, threaded=True, use_reloader=False)
