
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, Enum as SQLEnum
from datetime import datetime
import enum

from .session import Base


class OrderStatus(enum.Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class OrderType(enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String, index=True)
    order_type = Column(SQLEnum(OrderType))
    price = Column(Float)
    quantity = Column(Integer)
    status = Column(SQLEnum(OrderStatus))
    kis_response = Column(String)  # KIS API 응답 저장
    realized_pl = Column(Float, default=0.0)  # 실현손익


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    total_assets = Column(Float, default=0.0)
    cash_balance = Column(Float, default=0.0)
    holdings_value = Column(Float, default=0.0)
    realized_pl = Column(Float, default=0.0)
    unrealized_pl = Column(Float, default=0.0)
    daily_return_pct = Column(Float, default=0.0)


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True)
    strategy_name = Column(String)
    parameters = Column(Text, default="{}")  # JSON
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    symbol = Column(String, index=True)
    strategy_name = Column(String)
    signal = Column(String)  # BUY, SELL, HOLD
    decision_reason = Column(String)
    indicator_values = Column(Text, default="{}")  # JSON
    current_price = Column(Float)
    action_taken = Column(String)  # EXECUTED, SKIPPED, FAILED
