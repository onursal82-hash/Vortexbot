import os
# Last Updated: 2026-03-10
import json
import time
import logging
from logging.handlers import RotatingFileHandler
import requests
import ccxt
import secrets
import threading
import shutil
import traceback
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

# Import the new Trading Engine
from engine import TradingEngine, Position

# --- Configuration & Logging ---
if not os.path.exists('logs'):
    os.makedirs('logs')

log_handler = RotatingFileHandler('logs/app.log', maxBytes=10*1024*1024, backupCount=5)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[log_handler, logging.StreamHandler()])

app = Flask(__name__)
CORS(app, origins=["https://vortex-ui.onrender.com"])
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# --- Secrets ---
API_KEY = os.getenv('OKX_API_KEY', '').strip()
SECRET_KEY = os.getenv('OKX_SECRET_KEY', '').strip()
PASSPHRASE = os.getenv('OKX_PASSPHRASE', '').strip()

# --- Exchange Setup (CCXT) ---
exchange = ccxt.okx({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'password': PASSPHRASE,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# --- Initialize Engine ---
engine = TradingEngine(config_path="bot_state.json")

# --- Global Caches (Shared) ---
MARKET_CACHE = {
    "ticker": {},
    "last_updated": 0
}

# --- Auth Helper ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# --- Background Scheduler ---
def update_market_data():
    global MARKET_CACHE
    
    # Active symbols from engine + some defaults
    targets = {'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'}
    for symbol in engine.pos_manager.positions:
        targets.add(symbol.replace('-', '/'))
    
    # Filter out invalid symbols
    targets = {t for t in targets if '/' in t and len(t) > 4}
    
    if not targets:
        return

    try:
        tickers = exchange.fetch_tickers(list(targets))
        
        current_prices = {}
        # Update Cache
        for symbol, ticker in tickers.items():
            dash_sym = symbol.replace('/', '-')
            MARKET_CACHE['ticker'][dash_sym] = {
                'last': ticker['last'],
                'change': ticker.get('percentage', 0.0)
            }
            current_prices[dash_sym] = ticker['last']
        
        MARKET_CACHE['last_updated'] = time.time()
        
        # Run Trading Engine logic
        engine.tick(current_prices)
        
    except Exception as e:
        logging.error(f"Global Sync Error: {e}")

def keep_awake():
    try:
        base_url = os.getenv('RENDER_EXTERNAL_URL')
        if not base_url:
            base_url = 'http://127.0.0.1:5300'
        if not base_url.startswith('http'):
            base_url = f"https://{base_url}"
        target = f"{base_url}/health"
        requests.get(target, timeout=10)
    except Exception as e:
        logging.warning(f"Health check ping failed: {e}")

# Scheduler Setup
scheduler = BackgroundScheduler()
scheduler.add_job(update_market_data, 'interval', seconds=5, max_instances=1)
scheduler.add_job(keep_awake, 'interval', minutes=10)

if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    try:
        scheduler.start()
        logging.info("BACKGROUND SERVICE STARTED: Market Data & Bot Logic running.")
    except Exception as e:
        logging.warning(f"Scheduler start warning: {e}")

# --- Routes ---

@app.before_request
def log_request_info():
    if request.path.startswith('/api/'):
        # Just to have some payload logged if present
        payload = request.get_json(silent=True) if request.is_json else None
        logging.info(f"API Request: {request.method} {request.path} | Payload: {payload}")

@app.after_request
def log_response_info(response):
    if request.path.startswith('/api/'):
        # For JSON responses, we can log the response status and content snippet
        try:
            if response.is_json:
                res_data = response.get_json()
                # Don't log full history/symbols output as it's too large
                if request.path in ['/api/history', '/api/symbols', '/api/dashboard']:
                    snippet = f"<Data omitted for {request.path}>"
                else:
                    snippet = res_data
                logging.info(f"API Response: {request.method} {request.path} | Status: {response.status_code} | Body: {snippet}")
        except:
            pass
    return response

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/health')
def health_check():
    return jsonify({
        "status": "ok",
        "active_bots": sum(1 for p in engine.pos_manager.positions.values() if p.active),
        "positions_loaded": len(engine.pos_manager.positions),
        "last_save": engine.last_save_time or "Never",
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
    if not email:
        return jsonify({"status": "error", "message": "Email required"}), 400
    session['user'] = email
    return jsonify({"status": "success"})

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login_page'))

@app.route('/api/dashboard')
@login_required
def dashboard_data():
    bots_list = []
    now = datetime.now()
    total_unrealized_pnl = 0.0
    
    for symbol, pos in engine.pos_manager.positions.items():
        if pos.active:
            current_price = MARKET_CACHE['ticker'].get(symbol, {}).get('last', 0.0)
            unrealized_pnl = (current_price - pos.entry_price) * pos.amount if pos.entry_price > 0 else 0.0
            total_unrealized_pnl += unrealized_pnl
            pnl_pct = ((current_price - pos.entry_price) / pos.entry_price * 100) if pos.entry_price > 0 else 0.0
            
            # Calculate Uptime
            uptime = "0h 0m"
            if pos.start_time:
                try:
                    start_dt = datetime.fromisoformat(pos.start_time)
                    delta = now - start_dt
                    hours, remainder = divmod(int(delta.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    uptime = f"{hours}h {minutes}m"
                except:
                    pass

            bots_list.append({
                "symbol": symbol,
                "status": "Active" if pos.active else "Inactive",
                "entry_price": round(pos.entry_price, 4),
                "average_entry": round(pos.entry_price, 4), # For UI compatibility
                "current_price": round(current_price, 4),
                "investment": round(pos.total_cost, 2),
                "pnl": round(pnl_pct, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "dca_count": pos.dca_count,
                "uptime": uptime,
                "start_time": pos.start_time,
                "config": pos.config
            })
    
    # Global Stats
    stats = engine.profit_engine.stats
    
    # Mock Ticker fallback
    if not MARKET_CACHE['ticker']:
         MARKET_CACHE['ticker'] = {
            'BTC-USDT': {'last': 50000.0, 'change': 2.5},
            'ETH-USDT': {'last': 3000.0, 'change': 1.2},
            'SOL-USDT': {'last': 100.0, 'change': -0.5}
        }

    # Map to UI expectations
    financials = {
        "total_realized_profit": round(stats.get("realized_profit", 0.0), 2),
        "total_unrealized_profit": round(total_unrealized_pnl, 2),
        "net_pnl": round(stats.get("realized_profit", 0.0) + total_unrealized_pnl, 2),
        "total_balance": 10000.0 + stats.get("realized_profit", 0.0), # Example starting balance
        "equity": 10000.0 + stats.get("realized_profit", 0.0) + total_unrealized_pnl,
        "total_trades": stats.get("total_trades", 0),
        "win_rate": round(stats.get("win_rate", 0.0), 2),
        "open_positions": stats.get("open_positions", 0)
    }

    return jsonify({
        "financials": financials,
        "bots": bots_list,
        "ticker": MARKET_CACHE['ticker'],
        "history": engine.profit_engine.trade_log[-20:]
    })

@app.route('/api/history')
@login_required
def get_trade_history():
    try:
        history = list(engine.profit_engine.trade_log)
        history.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        formatted = []
        for h in history[:100]:
            pnl_usd = h.get('profit', 0.0)
            price = h.get('price', 0.0)
            amount = h.get('amount', 0.0)
            cost = price * amount if price and amount else 0.0
            pnl_pct = (pnl_usd / cost * 100) if cost > 0 else 0.0
            formatted.append({
                "timestamp": h.get('timestamp', ''),
                "symbol": h.get('symbol', ''),
                "event": h.get('type', ''),
                "price": price,
                "amount": amount,
                "pnl_usd": pnl_usd,
                "pnl_percent": pnl_pct
            })
        return jsonify(formatted)
    except Exception as e:
        logging.error(f"History Error: {e}\n{traceback.format_exc()}")
        return jsonify([])

@app.route('/api/symbols')
def get_symbols():
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

@app.route('/api/create_bot', methods=['POST'])
@login_required
def create_bot():
    try:
        data = request.get_json(silent=True) or {}
        symbol = data.get('symbol')
        if not symbol or symbol == '---':
            return jsonify({"status": "error", "message": "No valid symbol selected."}), 400
            
        symbol = symbol.upper().replace("/", "-").strip()
            
        # 1. Validation: Bot already exists AND is active?
        pos = engine.pos_manager.positions.get(symbol)
        if pos:
             if pos.active and pos.amount > 0 and pos.entry_price > 0:
                 return jsonify({"status": "error", "message": f"Bot for {symbol} already exists in active list."}), 400
             else:
                 # stale ghost bot record
                 del engine.pos_manager.positions[symbol]
                 engine.save_state()

        # Fetch Price
        try:
            ticker = exchange.fetch_ticker(symbol.replace('-', '/'))
            price = ticker['last']
        except Exception as e:
            return jsonify({"status": "error", "message": f"Could not fetch price for {symbol}"}), 400

        # 2. Initial state setup
        try:
            amount_usd = float(data.get('investment') or 100.0)
        except (ValueError, TypeError):
            amount_usd = 100.0
            
        initial_amount = amount_usd / price
        engine.pos_manager.open_trade(symbol, price, initial_amount)
        
        # Reload pos reference to set config
        pos = engine.pos_manager.get_position(symbol)
        
        # Handle custom configs if provided from frontend
        if 'dca_config' in data and isinstance(data['dca_config'], dict):
            for k, v in data['dca_config'].items():
                pos.config[k] = v
                
        pos.take_profit_price = engine.dca_engine.calculate_tp_price(pos)
        engine.profit_engine.log_trade(symbol, "BUY", price, initial_amount)
        
        # 3. Save state immediately
        engine.save_state()
        
        return jsonify({"status": "success", "message": "Vortex Strategy Activated"})
    except Exception as e:
        logging.error(f"Create Bot Error: {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 200 # Returning 200 with status error so UI doesn't crash on 500

@app.route('/api/start_strategy', methods=['POST'])
@login_required
def start_strategy():
    try:
        data = request.get_json(silent=True) or {}
        symbol = data.get('symbol')
        if not symbol or symbol == '---':
            return jsonify({"status": "error", "message": "No valid symbol selected."}), 400
            
        symbol = symbol.upper().replace("/", "-").strip()
            
        # 1. Validation: Bot already exists AND is active?
        pos = engine.pos_manager.positions.get(symbol)
        if pos:
             if pos.active and pos.amount > 0 and pos.entry_price > 0:
                 return jsonify({"status": "error", "message": f"Bot for {symbol} already exists in active list."}), 400
             else:
                 # stale ghost bot record
                 del engine.pos_manager.positions[symbol]
                 engine.save_state()

        # Fetch Price
        try:
            ticker = exchange.fetch_ticker(symbol.replace('-', '/'))
            price = ticker['last']
        except Exception as e:
            return jsonify({"status": "error", "message": f"Could not fetch price for {symbol}"}), 400

        # 2. Initial state setup
        try:
            amount_usd = float(data.get('amount') or 100.0)
        except (ValueError, TypeError):
            amount_usd = 100.0
            
        initial_amount = amount_usd / price
        engine.pos_manager.open_trade(symbol, price, initial_amount)
        
        # Reload pos reference
        pos = engine.pos_manager.get_position(symbol)
        
        # Handle custom configs if provided from frontend
        if 'dca_config' in data and isinstance(data['dca_config'], dict):
            for k, v in data['dca_config'].items():
                pos.config[k] = v
                
        pos.take_profit_price = engine.dca_engine.calculate_tp_price(pos)
        engine.profit_engine.log_trade(symbol, "BUY", price, initial_amount)
        
        # 3. Save state immediately
        engine.save_state()
        
        return jsonify({"status": "success", "message": "Vortex Strategy Activated"})
    except Exception as e:
        logging.error(f"Start Strategy Error: {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 200

@app.route('/api/cleanup_bots', methods=['POST'])
@login_required
def cleanup_bots():
    """Manual cleanup endpoint to remove all ghost/stale bots."""
    try:
        cleaned_count = engine.cleanup_ghost_bots(save=True)
        return jsonify({
            "status": "success", 
            "message": f"Cleaned up {cleaned_count} inactive/stale bots."
        })
    except Exception as e:
        logging.error(f"Cleanup Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/stop_bot', methods=['POST'])
@login_required
def stop_bot():
    data = request.json
    symbol = data.get('symbol')
    if symbol:
        symbol = symbol.upper().replace("/", "-").strip()
        # Use new engine delete_bot function
        if engine.delete_bot(symbol):
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": "Bot not found or already stopped"}), 404
    return jsonify({"status": "error", "message": "Symbol required"}), 400

@app.route('/api/panic_sell', methods=['POST'])
@login_required
def panic_sell():
    data = request.json
    symbol = data.get('symbol')
    if symbol:
        symbol = symbol.upper().replace("/", "-").strip()
        if symbol in engine.pos_manager.positions:
            pos = engine.pos_manager.get_position(symbol)
            current_price = MARKET_CACHE['ticker'].get(symbol, {}).get('last', 0.0)
            
            if pos.active and current_price > 0:
                profit = (current_price - pos.entry_price) * pos.amount
                engine.profit_engine.log_trade(symbol, "PANIC_SELL", current_price, pos.amount, profit)
                
                # Use delete_bot to remove from persistence
                engine.delete_bot(symbol)
                return jsonify({"status": "success"})
                
        return jsonify({"status": "error", "message": "Could not execute panic sell"}), 400
    return jsonify({"status": "error", "message": "Symbol required"}), 400

@app.route('/api/debug_positions', methods=['GET'])
@login_required
def debug_positions():
    """Return raw positions data to help detect ghost bots."""
    raw_positions = {}
    for sym, pos in engine.pos_manager.positions.items():
        raw_positions[sym] = {
            "active": pos.active,
            "amount": pos.amount,
            "entry_price": pos.entry_price
        }
    return jsonify(raw_positions)

@app.route('/api/reset_all', methods=['POST'])
@login_required
def reset_all():
    """Route for the new global reset function."""
    try:
        engine.reset_all_bots()
        return jsonify({"status": "success", "message": "All bots and stats have been reset."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/bot_details/<bot_id>')
@login_required
def bot_details(bot_id):
    # bot_id is the symbol
    bot_id = bot_id.upper().replace("/", "-").strip()
    pos = engine.pos_manager.get_position(bot_id)
    if not pos.active:
        return jsonify({"status": "error", "message": "Bot not found"}), 404
        
    # Return exact configuration format
    bot_data = {
        "symbol": pos.symbol,
        "status": "Active" if pos.active else "Inactive",
        "pnl": round(((MARKET_CACHE['ticker'].get(bot_id, {}).get('last', 0.0) - pos.entry_price) / pos.entry_price * 100) if pos.entry_price > 0 else 0.0, 2),
        "investment": round(pos.total_cost, 2),
        "config": pos.config
    }
    
    return jsonify(bot_data)

if __name__ == '__main__':
    print('--- VORTEX PLATFORM SERVER STARTING ON PORT 5300 ---')
    app.run(host='0.0.0.0', port=5300, debug=True, threaded=True, use_reloader=False)
