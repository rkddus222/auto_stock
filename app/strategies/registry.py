"""전략 레지스트리: 전략 클래스 등록 및 인스턴스 생성"""

from typing import Type

from app.strategies.base import Strategy
from app.strategies.volatility_breakout import VolatilityBreakout


class StrategyRegistry:
    _strategies: dict[str, Type[Strategy]] = {}

    @classmethod
    def register(cls, name: str, strategy_class: Type[Strategy]) -> None:
        cls._strategies[name] = strategy_class

    @classmethod
    def get_strategy(cls, name: str, parameters: dict | None = None) -> Strategy:
        parameters = parameters or {}
        if name not in cls._strategies:
            raise ValueError(f"Unknown strategy: {name}")
        return cls._strategies[name](**parameters)

    @classmethod
    def list_strategies(cls) -> list[dict]:
        return [
            {
                "name": name,
                "param_schema": strategy_class.get_param_schema(),
            }
            for name, strategy_class in cls._strategies.items()
        ]


def _register_builtin():
    StrategyRegistry.register("volatility_breakout", VolatilityBreakout)


def _register_all():
    _register_builtin()
    import app.strategies.ma_crossover  # noqa: F401
    import app.strategies.rsi  # noqa: F401
    import app.strategies.bollinger  # noqa: F401


_register_all()
