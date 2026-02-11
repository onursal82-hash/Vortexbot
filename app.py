import os
import json
import time
import logging
from logging.handlers import RotatingFileHandler
import requests
import ccxt
import secrets
import threading
import shutil
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

# --- Configuration & Logging ---
if not os.path.exists('logs'):
    os.makedirs('logs')

# Add RotatingFileHandler
log_handler = RotatingFileHandler('logs/app.log', maxBytes=10*1024*1024, backupCount=5)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[log_handler, logging.StreamHandler()])

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# --- Secrets ---
API_KEY = os.getenv('OKX_API_KEY', '').strip()
SECRET_KEY = os.getenv('OKX_SECRET_KEY', '').strip()
PASSPHRASE = os.getenv('OKX_PASSPHRASE', '').strip()
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'your@email.com')

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
HISTORY_FILE = 'bot_history.json'
DATA_LOCK = threading.Lock()
HISTORY_LOCK = threading.Lock()
GLOBAL_USER_ID = 'global_shared_workspace'

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
    
    # Ensure Global Workspace Exists
    if GLOBAL_USER_ID not in DATA['users']:
        DATA['users'][GLOBAL_USER_ID] = {
            "password": generate_password_hash("global_secret"),
            "bots": {},
            "financials": {
                "total_balance": 10000.00,
                "reserved_capital": 0.00,
                "net_pnl": 0.00
            },
            "history": []
        }

def save_data():
    with DATA_LOCK:
        try:
            tmp_file = STORAGE_FILE + ".tmp"
            # Write to tmp first (atomic write pattern)
            with open(tmp_file, 'w') as f:
                json.dump(DATA, f, indent=4)
            
            # Rename to actual file
            shutil.move(tmp_file, STORAGE_FILE)
        except Exception as e:
            logging.error(f"Failed to save storage: {e}")

def backup_data():
    """Daily backup of the database."""
    try:
        if os.path.exists(STORAGE_FILE):
            backup_file = 'bot_storage_backup.json'
            shutil.copy(STORAGE_FILE, backup_file)
            logging.info("Daily Backup Created.")
    except Exception as e:
        logging.error(f"Backup failed: {e}")

def save_trade_history(entry):
    """
    Appends a trade event to the persistent history file safely.
    """
    with HISTORY_LOCK:
        try:
            history = []
            if os.path.exists(HISTORY_FILE):
                try:
                    with open(HISTORY_FILE, 'r') as f:
                        history = json.load(f)
                except json.JSONDecodeError:
                    history = []
            
            history.append(entry)
            
            # Keep only last 1000 entries to prevent infinite growth
            if len(history) > 1000:
                history = history[-1000:]
                
            with open(HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=4)
                
        except Exception as e:
            logging.error(f"Failed to save trade history: {e}")

# --- Auth Helper ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user_data():
    """
    MODIFIED: Returns the GLOBAL shared user data regardless of who is logged in.
    This enables 'global visibility' across devices.
    """
    if 'user' not in session:
        return None
    # Instead of session['user'], we return the global workspace
    return DATA['users'].get(GLOBAL_USER_ID)

# --- DCA Engine (The Brain) ---
def calculate_dca_logic(user_email, bot, current_price):
    """
    Executes the DCA logic for a specific bot.
    """
    try:
        entry_price = bot['entry_price']
        dca_config = bot.get('dca_config', {})
        user_data = DATA['users'][user_email]
        
        # 1. Update Real-time PnL
        pnl_percent = ((current_price - entry_price) / entry_price) * 100
        bot['pnl'] = round(pnl_percent, 2)
        bot['current_price'] = current_price
        
        # 2. Safety Order Logic
        so_count = bot.get('safety_orders_filled', 0)
        max_so = dca_config.get('max_safety_orders', 15)
        
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
            
            save_trade_history({
                "symbol": bot['symbol'],
                "timestamp": datetime.now().isoformat(),
                "event": f"DCA Buy #{so_count + 1}",
                "pnl_percent": pnl_percent,
                "pnl_usd": 0.0
            })

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
            
            save_trade_history({
                "symbol": bot['symbol'],
                "timestamp": datetime.now().isoformat(),
                "event": "Take Profit",
                "pnl_percent": pnl_percent,
                "pnl_usd": profit_amount
            })
            
            logging.info(f"TAKE PROFIT: {bot['symbol']} closed with {pnl_percent}% (User: {user_email})")

            if loop_enabled:
                # RESTART CYCLE
                logging.info(f"LOOP TRIGGERED: Restarting {bot['symbol']} for {user_email}")
                
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
                 
                 save_trade_history({
                    "symbol": bot['symbol'],
                    "timestamp": datetime.now().isoformat(),
                    "event": "Stop Loss",
                    "pnl_percent": pnl_percent,
                    "pnl_usd": loss_amount
                })
                 logging.info(f"STOP LOSS: {bot['symbol']} closed with {pnl_percent}% (User: {user_email})")

    except Exception as e:
        logging.error(f"Error in DCA Logic for {bot.get('symbol', 'UNKNOWN')}: {e}")

# --- Background Scheduler ---
def update_market_data():
    global MARKET_CACHE
    
    # Collect all active symbols from ALL users
    targets = {'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'}
    
    try:
        for email, user_data in DATA['users'].items():
            bots = user_data.get('bots', {})
            for b in bots.values():
                if b['status'] == 'running':
                    sym = b.get('symbol', '')
                    if sym and '-' in sym:
                        targets.add(sym.replace('-', '/'))
        
        # Filter out invalid symbols
        targets = {t for t in targets if '/' in t and len(t) > 4 and '///' not in t}
        
        if not targets:
            return

        tickers = {}
        try:
            tickers = exchange.fetch_tickers(list(targets))
            
            # Update Cache
            for symbol, ticker in tickers.items():
                dash_sym = symbol.replace('/', '-')
                MARKET_CACHE['ticker'][dash_sym] = {
                    'last': ticker['last'],
                    'change': ticker.get('percentage', 0.0)
                }
            MARKET_CACHE['last_updated'] = time.time()
        
        except Exception as e:
            logging.error(f"Exchange Fetch Error: {e}. Using cached values if available.")
            # Fallback to cache is automatic if we use MARKET_CACHE below
        
        # Run Logic for ALL Users
        changes = False
        for email, user_data in DATA['users'].items():
            bots = user_data.get('bots', {})
            for b_sym, bot in bots.items():
                if bot['status'] == 'running':
                    # Get price from cache (fetched or old)
                    ticker_key = b_sym
                    # Check both formats just in case
                    if ticker_key not in MARKET_CACHE['ticker']:
                        ticker_key = b_sym.replace('-', '/')
                        
                    if ticker_key in MARKET_CACHE['ticker']:
                        price = MARKET_CACHE['ticker'][ticker_key]['last']
                        calculate_dca_logic(email, bot, price)
                        changes = True
                    else:
                        # Fallback check against fresh tickers if available but not in cache?
                        # (Unlikely if cache update worked)
                        pass
        
        if changes:
            save_data()
            
    except Exception as e:
        logging.error(f"Global Sync Error: {e}")

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
        target = f"{base_url}/health"
        response = requests.get(target, timeout=10)
        logging.info(f"Health check: Keeping the engine awake... (Ping {target} - Status: {response.status_code})")
    except Exception as e:
        logging.warning(f"Health check ping failed: {e}")

# Scheduler Setup
scheduler = BackgroundScheduler()
# Prevent multiple instances: only add jobs if we are confident (or just let APScheduler handle it via max_instances)
# We use max_instances=1 to ensure no overlap of the SAME job.
scheduler.add_job(update_market_data, 'interval', seconds=5, max_instances=1) # Increased to 5s
scheduler.add_job(periodic_save, 'interval', minutes=1)
scheduler.add_job(backup_data, 'interval', hours=24) # Daily backup
scheduler.add_job(keep_awake, 'interval', minutes=10) # 10 mins

# Check if we should start scheduler (avoid double start in reloader)
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    try:
        scheduler.start()
        logging.info("BACKGROUND SERVICE STARTED: Market Data & Bot Logic running.")
    except Exception as e:
        logging.warning(f"Scheduler start warning: {e}")

# --- Routes ---

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/health')
def health_check():
    """Health check endpoint for keep-awake and monitoring."""
    active_bots = 0
    try:
        for user_data in DATA['users'].values():
            bots = user_data.get('bots', {})
            active_bots += len([b for b in bots.values() if b['status'] == 'running'])
    except:
        pass
        
    return jsonify({
        "status": "ok",
        "active_bots": active_bots,
        "time": datetime.now().isoformat()
    })

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
        # Auto-register new user (still needed for valid session)
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
    # MODIFIED: Get global data instead of session user data
    user = DATA['users'].get(GLOBAL_USER_ID)
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

@app.route('/api/history')
@login_required
def get_trade_history():
    with HISTORY_LOCK:
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    history = json.load(f)
                    # Sort by timestamp descending
                    history.sort(key=lambda x: x['timestamp'], reverse=True)
                    return jsonify(history[:100])
        except Exception as e:
            logging.error(f"Error reading history: {e}")
            
    return jsonify([])

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
        # MODIFIED: Use Global Workspace
        user_data = DATA['users'][GLOBAL_USER_ID]
        bots = user_data['bots']
        
        try:
            data = request.get_json()
        except:
            return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

        symbol = data.get('symbol')
        
        if not symbol or symbol == '---':
            return jsonify({"status": "error", "message": "No valid symbol selected. Please select a pair first."}), 400
            
        if symbol in bots and bots[symbol]['status'] == 'running':
             return jsonify({"status": "error", "message": f"Bot for {symbol} is already active"}), 400

        # Fetch Price with retry
        price = 0.0
        try:
            ticker = exchange.fetch_ticker(symbol.replace('-', '/'))
            price = ticker['last']
        except Exception as e:
             logging.error(f"Price fetch failed for {symbol}: {e}")
             # Fallback to cache
             cached_ticker = MARKET_CACHE['ticker'].get(symbol)
             if cached_ticker:
                 price = cached_ticker['last']
             else:
                 price = 50000.0 # Ultimate fallback for simulation

        # Set DCA Config defaults
        dca_config = {
            "base_order": float(data.get('amount', 20.0)),
            "safety_order": 40.0,
            "max_safety_orders": 15,
            "volume_scale": 1.05,
            "step_scale": 1.0,
            "price_deviation": 2.0,
            "take_profit": 1.5,
            "stop_action": "close",
            "stop_loss_enabled": False,
            "loop_enabled": True # Default to Loop for Vortex
        }
        
        new_bot = {
            "symbol": symbol,
            "status": "running",
            "entry_price": price,
            "current_price": price,
            "investment": float(data.get('amount', 20.0)), # Allow frontend to override
            "pnl": 0.0,
            "dca_config": dca_config,
            "safety_orders_filled": 0,
            "start_time": datetime.now().isoformat()
        }
        
        bots[symbol] = new_bot
        user_data['financials']['reserved_capital'] += new_bot['investment']
        save_data()
        
        return jsonify({"status": "success", "message": "Vortex Strategy Activated"})
    except Exception as e:
        logging.error(f"Start Strategy Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/bot_details/<bot_id>')
@login_required
def bot_details(bot_id):
    # MODIFIED: Use Global Workspace
    user_data = DATA['users'][GLOBAL_USER_ID]
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
        # MODIFIED: Use Global Workspace
        user_data = DATA['users'][GLOBAL_USER_ID]
        bots = user_data['bots']
        
        data = request.json
        symbol = data.get('symbol', '').upper()
        if '/' in symbol: symbol = symbol.replace('/', '-')
        
        base_order = float(data.get('investment', 100))
        dca_config = data.get('dca_config', {})
        
        # Apply defaults to DCA config
        dca_config.setdefault('base_order', base_order)
        dca_config.setdefault('safety_order', 40.0)
        dca_config.setdefault('max_safety_orders', 15)
        dca_config.setdefault('volume_scale', 1.05)
        dca_config.setdefault('step_scale', 1.0)
        dca_config.setdefault('price_deviation', 2.0)
        dca_config.setdefault('take_profit', 1.5)
        
        if symbol in bots and bots[symbol]['status'] == 'running':
             return jsonify({"status": "error", "message": "Bot already running"}), 400

        try:
            ticker = exchange.fetch_ticker(symbol.replace('-', '/'))
            price = ticker['last']
        except:
             # Fallback to cache
             cached_ticker = MARKET_CACHE['ticker'].get(symbol)
             if cached_ticker:
                 price = cached_ticker['last']
             else:
                 return jsonify({"status": "error", "message": "Invalid Pair (Fetch Failed)"}), 400

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
    # MODIFIED: Use Global Workspace
    user_data = DATA['users'][GLOBAL_USER_ID]
    bots = user_data['bots']
    
    data = request.json
    symbol = data.get('symbol')
    
    if symbol in bots:
        # Panic Sell or Stop?
        # Simplified: Stop just releases capital (assuming sold at break-even or manual handling)
        user_data['financials']['reserved_capital'] -= bots[symbol]['investment']
        del bots[symbol]
        save_data()
        logging.info(f"Bot Stopped: {symbol} for Global Workspace")
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 404

@app.route('/api/panic_sell', methods=['POST'])
@login_required
def panic_sell():
    # MODIFIED: Use Global Workspace
    user_data = DATA['users'][GLOBAL_USER_ID]
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
        
        save_trade_history({
            "symbol": bot['symbol'],
            "timestamp": datetime.now().isoformat(),
            "event": "Panic Sell",
            "pnl_percent": pnl_percent,
            "pnl_usd": pnl_amount
        })

        del bots[symbol]
        save_data()
        logging.info(f"Panic Sell: {symbol} for Global Workspace")
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
