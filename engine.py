import json
import os
import time
import logging
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
        pos = self.get_position(symbol)
        pos.entry_price = price
        pos.amount = amount
        pos.total_cost = price * amount
        pos.dca_count = 0
        pos.active = True
        pos.start_time = datetime.now().isoformat()
        logging.info(f"Opened trade for {symbol} at {price}")

    def update_after_dca(self, symbol: str, price: float, amount: float):
        pos = self.get_position(symbol)
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
        if not pos.active or pos.dca_count >= self.max_dca:
            return False
        
        # Check if current drop matches next DCA level
        # dca_levels are e.g., [-2, -4, -6, -8, -10]
        drop_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
        next_level = self.dca_levels[pos.dca_count]
        
        return drop_pct <= next_level

    def should_take_profit(self, pos: Position, current_price: float) -> bool:
        if not pos.active or pos.entry_price == 0:
            return False
        return current_price >= pos.take_profit_price

# --- Trading Engine (Orchestrator) ---
class TradingEngine:
    def __init__(self, config_path: str = "bot_state.json"):
        self.config_path = config_path
        self.pos_manager = PositionManager()
        self.profit_engine = ProfitEngine()
        # Default DCA config
        self.dca_engine = DCAEngine(dca_levels=[-2, -4, -6, -8, -10], take_profit_pct=1.5, max_dca=5)
        self.max_positions = 10
        self.load_state()

    def save_state(self):
        state = {
            "positions": {s: p.to_dict() for s, p in self.pos_manager.positions.items()},
            "profit": self.profit_engine.to_dict(),
            "config": {
                "dca_levels": self.dca_engine.dca_levels,
                "take_profit_pct": self.dca_engine.take_profit_pct,
                "max_dca": self.dca_engine.max_dca,
                "max_positions": self.max_positions
            }
        }
        try:
            with open(self.config_path, 'w') as f:
                json.dump(state, f, indent=4)
            # logging.info(f"State saved to {self.config_path}")
        except Exception as e:
            logging.error(f"Failed to save state: {e}")

    def load_state(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    state = json.load(f)
                
                # Load Positions
                for symbol, pos_data in state.get("positions", {}).items():
                    self.pos_manager.positions[symbol] = Position.from_dict(pos_data)
                
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
                logging.error(f"Failed to load state: {e}")

    def tick(self, current_prices: Dict[str, float]):
        """Main loop iteration"""
        # 1. Update Unrealized Profits
        self.profit_engine.calculate_unrealized_profit(self.pos_manager.positions, current_prices)

        # 2. Process each symbol
        for symbol, price in current_prices.items():
            pos = self.pos_manager.get_position(symbol)

            # Emergency Protection
            if pos.active and pos.entry_price == 0:
                logging.warning(f"Emergency Reset: {symbol} had active status but 0 entry price.")
                pos.reset()
                continue

            # a. Take Profit Check
            if self.dca_engine.should_take_profit(pos, price):
                profit = (price - pos.entry_price) * pos.amount
                self.profit_engine.log_trade(symbol, "TAKE_PROFIT", price, pos.amount, profit)
                self.pos_manager.close_trade(symbol)
                # After closing, we don't immediately open a new trade for the same symbol 
                # unless continuous mode is handled by external logic or here.
                # Let's keep it simple: the next tick will see it as inactive.
                continue

            # b. DCA Check
            if self.dca_engine.should_dca(pos, price):
                # For simulation, we'll assume a fixed DCA amount (e.g., same as initial or scaled)
                # Let's use 2x initial order or something configurable.
                # Requirement says "Initial buy opens position ... execute DCA"
                dca_amount = pos.amount * 1.0 # Simple 1:1 for now
                self.pos_manager.update_after_dca(symbol, price, dca_amount)
                pos.take_profit_price = self.dca_engine.calculate_tp_price(pos.entry_price)
                self.profit_engine.log_trade(symbol, "DCA_BUY", price, dca_amount)
                continue

            # c. Open New Trade if inactive
            if not pos.active:
                if self.profit_engine.stats["open_positions"] < self.max_positions:
                    # Initial buy
                    initial_amount = 100.0 / price # Example $100 base order
                    self.pos_manager.open_trade(symbol, price, initial_amount)
                    pos.take_profit_price = self.dca_engine.calculate_tp_price(pos.entry_price)
                    self.profit_engine.log_trade(symbol, "BUY", price, initial_amount)

        # 3. Save state
        self.save_state()
