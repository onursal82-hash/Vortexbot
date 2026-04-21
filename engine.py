import json
import os
import time
import logging
import threading
import shutil
from datetime import datetime
from typing import Dict, List, Optional

# --- Configuration & Logging ---
if not os.path.exists('logs'):
    os.makedirs('logs')

# --- Position Object ---
class Position:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.entry_price = 0.0
        self.amount = 0.0
        self.total_cost = 0.0
        self.dca_count = 0
        self.active = False
        self.take_profit_price = 0.0
        self.start_time = None

    def reset(self):
        self.entry_price = 0.0
        self.amount = 0.0
        self.total_cost = 0.0
        self.dca_count = 0
        self.active = False
        self.take_profit_price = 0.0
        self.start_time = None

    def is_valid(self):
        if not self.active:
            return True
        return self.entry_price > 0 and self.amount > 0 and self.total_cost > 0

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "amount": self.amount,
            "total_cost": self.total_cost,
            "dca_count": self.dca_count,
            "active": self.active,
            "take_profit_price": self.take_profit_price,
            "start_time": self.start_time
        }

    @classmethod
    def from_dict(cls, data: dict):
        pos = cls(data['symbol'])
        pos.entry_price = data.get('entry_price', 0.0)
        pos.amount = data.get('amount', 0.0)
        pos.total_cost = data.get('total_cost', 0.0)
        pos.dca_count = data.get('dca_count', 0)
        pos.active = data.get('active', False)
        pos.take_profit_price = data.get('take_profit_price', 0.0)
        pos.start_time = data.get('start_time')
        return pos

# --- Position Manager ---
class PositionManager:
    def __init__(self):
        self.positions: Dict[str, Position] = {}

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol)
        return self.positions[symbol]

    def open_trade(self, symbol: str, price: float, amount: float):
        if price <= 0 or amount <= 0:
            logging.error(f"Cannot open trade for {symbol} with invalid price ({price}) or amount ({amount})")
            return
            
        pos = self.get_position(symbol)
        pos.entry_price = price
        pos.amount = amount
        pos.total_cost = price * amount
        pos.dca_count = 0
        pos.active = True
        pos.start_time = datetime.now().isoformat()
        logging.info(f"Opened trade for {symbol} at {price}")

    def update_after_dca(self, symbol: str, price: float, amount: float):
        if price <= 0 or amount <= 0:
            logging.error(f"Cannot DCA for {symbol} with invalid price ({price}) or amount ({amount})")
            return
            
        pos = self.get_position(symbol)
        if not pos.active:
            logging.warning(f"Attempted DCA on inactive position {symbol}. Opening new trade instead.")
            self.open_trade(symbol, price, amount)
            return

        new_total_cost = pos.total_cost + (price * amount)
        new_total_amount = pos.amount + amount
        pos.entry_price = new_total_cost / new_total_amount
        pos.amount = new_total_amount
        pos.total_cost = new_total_cost
        pos.dca_count += 1
        logging.info(f"DCA Buy #{pos.dca_count} for {symbol} at {price}. New entry: {pos.entry_price}")

    def close_trade(self, symbol: str):
        pos = self.get_position(symbol)
        logging.info(f"Closing trade for {symbol}")
        pos.reset()

# --- Profit Engine ---
class ProfitEngine:
    def __init__(self):
        self.stats = {
            "realized_profit": 0.0,
            "unrealized_profit": 0.0,
            "total_trades": 0,
            "closed_trades": 0,
            "open_positions": 0,
            "win_rate": 0.0
        }
        self.trade_log: List[dict] = []

    def calculate_unrealized_profit(self, positions: Dict[str, Position], current_prices: Dict[str, float]):
        unrealized = 0.0
        open_count = 0
        for symbol, pos in positions.items():
            if pos.active and symbol in current_prices:
                price = current_prices[symbol]
                if pos.entry_price > 0:
                    profit = (price - pos.entry_price) * pos.amount
                    unrealized += profit
                open_count += 1
        self.stats["unrealized_profit"] = round(unrealized, 4)
        self.stats["open_positions"] = open_count

    def log_trade(self, symbol: str, trade_type: str, price: float, amount: float, profit: float = 0.0):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "type": trade_type,
            "price": price,
            "amount": amount,
            "profit": profit
        }
        self.trade_log.append(entry)
        if trade_type == "TAKE_PROFIT":
            self.stats["realized_profit"] += profit
            self.stats["closed_trades"] += 1
            self.stats["total_trades"] += 1
            # Win rate logic: in this bot, all closed trades are profits since no SL
            self.stats["win_rate"] = 100.0 if self.stats["total_trades"] > 0 else 0.0
        elif trade_type == "BUY":
            # We don't increment total_trades yet, it's an open position
            pass
        elif trade_type == "RESET":
            pass

    def to_dict(self):
        return {
            "stats": self.stats,
            "trade_log": self.trade_log
        }

    def load_dict(self, data: dict):
        self.stats = data.get("stats", self.stats)
        self.trade_log = data.get("trade_log", [])

# --- DCA Engine ---
class DCAEngine:
    def __init__(self, dca_levels: List[float], take_profit_pct: float, max_dca: int = 5):
        self.dca_levels = dca_levels
        self.take_profit_pct = take_profit_pct
        self.max_dca = max_dca

    def calculate_tp_price(self, entry_price: float) -> float:
        return entry_price * (1 + self.take_profit_pct / 100)

    def should_dca(self, pos: Position, current_price: float) -> bool:
        if not pos.active or pos.dca_count >= self.max_dca or pos.entry_price <= 0:
            return False
        
        # Check if current drop matches next DCA level
        drop_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
        # If we exhausted levels, cap it
        next_level_idx = min(pos.dca_count, len(self.dca_levels) - 1)
        next_level = self.dca_levels[next_level_idx]
        
        return drop_pct <= next_level

    def should_take_profit(self, pos: Position, current_price: float) -> bool:
        if not pos.active or pos.entry_price <= 0:
            return False
        return current_price >= pos.take_profit_price

# --- Trading Engine (Orchestrator) ---
class TradingEngine:
    def __init__(self, config_path: str = "bot_state.json"):
        self.config_path = config_path
        self.pos_manager = PositionManager()
        self.profit_engine = ProfitEngine()
        self.dca_engine = DCAEngine(dca_levels=[-2, -4, -6, -8, -10], take_profit_pct=1.5, max_dca=5)
        self.max_positions = 10
        self.last_save_time = None
        self.state_lock = threading.Lock()
        self.load_state()

    def save_state(self):
        with self.state_lock:
            state = {
                "positions": {s: p.to_dict() for s, p in self.pos_manager.positions.items() if p.active},
                "profit": self.profit_engine.to_dict(),
                "config": {
                    "dca_levels": self.dca_engine.dca_levels,
                    "take_profit_pct": self.dca_engine.take_profit_pct,
                    "max_dca": self.dca_engine.max_dca,
                    "max_positions": self.max_positions
                }
            }
            try:
                tmp_file = self.config_path + ".tmp"
                with open(tmp_file, 'w') as f:
                    json.dump(state, f, indent=4)
                shutil.move(tmp_file, self.config_path)
                self.last_save_time = datetime.now().isoformat()
            except Exception as e:
                logging.error(f"Failed to save state: {e}")

    def load_state(self):
        with self.state_lock:
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, 'r') as f:
                        state = json.load(f)
                    
                    # Load Positions - Only restore active and valid ones
                    self.pos_manager.positions.clear()
                    for symbol, pos_data in state.get("positions", {}).items():
                        pos = Position.from_dict(pos_data)
                        if pos.active and pos.is_valid():
                            self.pos_manager.positions[symbol] = pos
                        else:
                            logging.warning(f"Discarded invalid/inactive position for {symbol} during load.")
                    
                    # Load Profit Data
                    self.profit_engine.load_dict(state.get("profit", {}))
                    
                    # Load Config
                    config = state.get("config", {})
                    self.dca_engine.dca_levels = config.get("dca_levels", self.dca_engine.dca_levels)
                    self.dca_engine.take_profit_pct = config.get("take_profit_pct", self.dca_engine.take_profit_pct)
                    self.dca_engine.max_dca = config.get("max_dca", self.dca_engine.max_dca)
                    self.max_positions = config.get("max_positions", self.max_positions)
                    
                    logging.info(f"State loaded from {self.config_path}")
                except Exception as e:
                    logging.error(f"Failed to load state (possible corruption): {e}. Initializing empty state.")
                    self.pos_manager.positions.clear()
                    # Will save a fresh state on next save
            else:
                logging.info(f"No state file found at {self.config_path}. Starting fresh.")

    def delete_bot(self, symbol: str):
        """Properly remove a bot from state and storage."""
        if symbol in self.pos_manager.positions:
            logging.info(f"Deleting bot {symbol} from state.")
            del self.pos_manager.positions[symbol]
            self.save_state()
            return True
        return False

    def reset_all_bots(self):
        """Global reset for debugging and testing."""
        logging.info("Global Reset: Clearing all positions, stats, and logs.")
        self.pos_manager.positions.clear()
        self.profit_engine.stats = {
            "realized_profit": 0.0,
            "unrealized_profit": 0.0,
            "total_trades": 0,
            "closed_trades": 0,
            "open_positions": 0,
            "win_rate": 0.0
        }
        self.profit_engine.trade_log.clear()
        self.profit_engine.log_trade("SYSTEM", "RESET", 0, 0, 0)
        self.save_state()

    def tick(self, current_prices: Dict[str, float]):
        """Main loop iteration"""
        # 1. Update Unrealized Profits
        self.profit_engine.calculate_unrealized_profit(self.pos_manager.positions, current_prices)

        # 2. Process each symbol
        for symbol, price in current_prices.items():
            pos = self.pos_manager.get_position(symbol)

            # Emergency Protection
            if pos.active and not pos.is_valid():
                logging.warning(f"Emergency Reset: {symbol} had active status but invalid state (entry: {pos.entry_price}, amount: {pos.amount}).")
                self.profit_engine.log_trade(symbol, "RESET", price, pos.amount, 0)
                pos.reset()
                continue

            # a. Take Profit Check
            if self.dca_engine.should_take_profit(pos, price):
                profit = (price - pos.entry_price) * pos.amount
                self.profit_engine.log_trade(symbol, "TAKE_PROFIT", price, pos.amount, profit)
                self.pos_manager.close_trade(symbol)
                # After closing, we don't immediately open a new trade for the same symbol 
                # Let's keep it simple: the next tick will see it as inactive.
                continue

            # b. DCA Check
            if self.dca_engine.should_dca(pos, price):
                dca_amount = pos.amount * 1.0 # Simple 1:1 for now
                self.pos_manager.update_after_dca(symbol, price, dca_amount)
                pos.take_profit_price = self.dca_engine.calculate_tp_price(pos.entry_price)
                self.profit_engine.log_trade(symbol, "DCA_BUY", price, dca_amount)
                continue

            # c. Open New Trade if inactive
            # For this specific bot logic, we only open if explicitly requested,
            # or if we have continuous mode. For now, it stays inactive until requested.
            pass

        # 3. Autosave state on every cycle
        self.save_state()
