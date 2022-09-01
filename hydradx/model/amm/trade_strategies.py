import math

from .global_state import GlobalState, swap, add_liquidity, external_market_trade
from .amm import AMM
from .basilisk_amm import ConstantProductPoolState
from typing import Callable
import random


class TradeStrategy:
    def __init__(self, strategy_function: Callable[[GlobalState, str], GlobalState], name: str, run_once: bool = False):
        self.function = strategy_function
        self.run_once = run_once
        self.done = False
        self.name = name

    def execute(self, state: GlobalState, agent_id: str) -> GlobalState:
        if self.done:
            return state
        elif self.run_once:
            self.done = True
        return self.function(state, agent_id)


def random_swaps(
    pool_id: str,
    amount: dict[str: float],
    randomize_amount: bool = False
) -> TradeStrategy:
    """
    amount should be a dict in the form of:
    {
        token_name: sell_quantity
    }
    """

    def strategy(state: GlobalState, agent_id: str):
        buy_asset = random.choice(list(amount.keys()))
        sell_asset = random.choice(list(amount.keys()))
        sell_quantity = (
                                amount[sell_asset] * (random.random() if randomize_amount else 1)
                        ) or 1
        if buy_asset == sell_asset:
            return state
        else:
            return swap(
                old_state=state,
                pool_id=pool_id,
                agent_id=agent_id,
                tkn_sell=sell_asset,
                tkn_buy=buy_asset,
                sell_quantity=sell_quantity
            )

    return TradeStrategy(strategy, name=f'random swaps ({list(amount.keys())})')


def steady_swaps(
    pool_id: str,
    usd_amount: float,
    asset_list: list = ()
) -> TradeStrategy:

    def strategy(state: GlobalState, agent_id: str):

        strategy.buy_index = getattr(strategy, 'buy_index', -1)
        strategy.buy_index += 1
        agent = state.agents[agent_id]
        assets = asset_list or list(agent.holdings.keys())
        buy_index = strategy.buy_index % len(assets)
        sell_index = (buy_index + 1) % len(assets)

        buy_asset = assets[buy_index]
        sell_asset = assets[sell_index]
        sell_quantity = usd_amount / state.price(sell_asset)

        return swap(
            old_state=state,
            pool_id=pool_id,
            agent_id=agent_id,
            tkn_sell=sell_asset,
            tkn_buy=buy_asset,
            sell_quantity=sell_quantity
        )

    return TradeStrategy(strategy, name=f'steady swaps (${usd_amount})')


def invest_all(pool_id: str) -> TradeStrategy:

    def strategy(state: GlobalState, agent_id: str):

        # should only do this once
        # strategy.done = getattr(strategy, 'done', False)
        # if strategy.done:
        #     return state
        # strategy.done = True

        agent = state.agents[agent_id]
        for asset in agent.holdings:

            if asset in state.pools[pool_id].asset_list:
                state = add_liquidity(
                    old_state=state,
                    pool_id=pool_id,
                    agent_id=agent_id,
                    quantity=state.agents[agent_id].holdings[asset],
                    tkn_add=asset
                )

        return state

    return TradeStrategy(strategy, name=f'invest all ({pool_id})', run_once=True)


def constant_product_arbitrage(pool_id: str) -> TradeStrategy:

    def strategy(state: GlobalState, agent_id: str):

        pool: ConstantProductPoolState = state.pools[pool_id]
        if not(isinstance(pool, ConstantProductPoolState)):
            raise TypeError(f'{pool_id} is not compatible with constant product arbitrage.')

        x = pool.asset_list[0]
        y = pool.asset_list[1]

        # why does this eliminate all negative profits?? todo: find out
        # if state.price(pool.asset_list[0]) > state.price(pool.asset_list[1]):
        #     x = pool.asset_list[1]
        #     y = pool.asset_list[0]

        p_ratio = state.price(x) / state.price(y)
        # VVV this would be correct if there is no fee VVV
        agent_delta_y = pool.liquidity[y] - math.sqrt(pool.liquidity[x] * pool.liquidity[y] * p_ratio)

        agent = state.agents[agent_id]

        def price_after_trade(buy_amount=0, sell_amount=0):
            if buy_amount:
                sell_amount = -(pool.liquidity[x] - pool.invariant
                                / (pool.liquidity[y] - buy_amount)) \
                                * (1 + pool.trade_fee(x, y, buy_amount))
                return (pool.liquidity[y] - buy_amount) / (pool.liquidity[x] + sell_amount)
            elif sell_amount:
                buy_amount = (pool.liquidity[x] - pool.invariant
                              / (pool.liquidity[y] + sell_amount)) \
                              * (1 - pool.trade_fee(x, y, sell_amount))
                return (pool.liquidity[y] + sell_amount) / (pool.liquidity[x] - buy_amount)

        def find_b(target_price):
            b = agent_delta_y
            previous_change = 1
            p = price_after_trade(b)
            previous_price = p
            diff = p / target_price
            while abs(1 - diff) > 0.000000001:
                progress = (previous_price - p) / (previous_price - target_price) or 2
                old_b = b
                b -= previous_change * (1 - 1 / progress)
                previous_price = p
                previous_change = b - old_b
                p = price_after_trade(b if b >= 0 else 0, -b if b < 0 else 0)
                diff = p / target_price

            return b

        agent_delta_y = find_b(target_price=state.price(x)/state.price(y))

        holdings = agent.holdings['USD']
        agent_delta_x = -agent_delta_y * pool.liquidity[x] / (pool.liquidity[y] - agent_delta_y)
        if agent_delta_y > 0:
            agent_delta_x *= 1 + pool.trade_fee(x, y, abs(agent_delta_y))
        else:
            agent_delta_x *= 1 - pool.trade_fee(x, y, abs(agent_delta_y))

        projected_profit = (
            agent_delta_y * state.price(y)
            + agent_delta_x * state.price(x)
        )

        agent.projected_profit = projected_profit

        if projected_profit <= 0:
            # don't do it
            agent.trade_rejected += 1
            return state
        elif agent_delta_y < 0:
            er = 1

        # buy just enough of non-USD asset
        if agent_delta_y > 0 and x != 'USD' or agent_delta_y < 0 and y != 'USD':
            state = external_market_trade(
                state=state,
                agent_id=agent_id,
                tkn_buy=x if agent_delta_y > 0 else 'USD',
                tkn_sell=y if agent_delta_y < 0 else 'USD',
                sell_quantity=agent_delta_y if agent_delta_y < 0 else 0,
                buy_quantity=-agent_delta_x if agent_delta_y > 0 else 0
            )

        # swap
        new_state = swap(state, pool_id, agent_id, tkn_sell=x, tkn_buy=y, buy_quantity=agent_delta_y)

        # immediately cash out everything for USD
        new_agent = new_state.agents[agent_id]
        for tkn, quantity in new_agent.holdings.items():
            if new_agent.holdings[tkn] > 0 and tkn != 'USD':
                new_state = external_market_trade(state, agent_id, tkn_buy='USD', tkn_sell=tkn, sell_quantity=quantity)

        actual_profit = new_state.agents[agent_id].holdings['USD'] - holdings

        if projected_profit < 0:
            # don't do it
            new_agent.trade_rejected += 1
        #     return state

        if abs(projected_profit - actual_profit) > 0.000000000001 and abs(actual_profit) > 0.000000000001:
            er = 1
        elif actual_profit > 0.000000000001:
            er = 3
        elif actual_profit < -0.000000000001:
            er = 4
        else:
            er = 2

        return new_state

    return TradeStrategy(strategy, name=f'constant product pool arbitrage ({pool_id})')
