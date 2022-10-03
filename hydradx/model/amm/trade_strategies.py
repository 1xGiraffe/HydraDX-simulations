import math

from .global_state import GlobalState, swap, add_liquidity, external_market_trade, withdraw_all_liquidity
from .agents import Agent
from .basilisk_amm import ConstantProductPoolState
from .omnipool_amm import OmnipoolState
from .stableswap_amm import StableSwapPoolState
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

    def __add__(self, other):
        assert isinstance(other, TradeStrategy)

        def combo_function(state, agent_id) -> GlobalState:
            new_state = self.execute(state, agent_id)
            return other.execute(new_state, agent_id)

        return TradeStrategy(combo_function, name='\n'.join([self.name, other.name]))


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

        return state.execute_swap(
            pool_id=pool_id,
            agent_id=agent_id,
            tkn_sell=sell_asset,
            tkn_buy=buy_asset,
            sell_quantity=sell_quantity
        )

    return TradeStrategy(strategy, name=f'steady swaps (${usd_amount})')


def invest_all(pool_id: str) -> TradeStrategy:

    def strategy(state: GlobalState, agent_id: str):

        agent: Agent = state.agents[agent_id]
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


def withdraw_all(when: int) -> TradeStrategy:

    def strategy(state: GlobalState, agent_id: str):
        if state.time_step == when:
            return withdraw_all_liquidity(state, agent_id)
        else:
            return state

    return TradeStrategy(strategy, name=f'withdraw all at time step {when}')


def sell_all(pool_id: str, sell_asset: str, buy_asset: str):

    def strategy(state: GlobalState, agent_id: str) -> GlobalState:
        agent = state.agents[agent_id]
        if not agent.holdings[sell_asset]:
            return state
        return state.execute_swap(pool_id, agent_id, sell_asset, buy_asset, sell_quantity=agent.holdings[sell_asset])

    return TradeStrategy(strategy, name=f'sell all {sell_asset} for {buy_asset}')


# iterative arbitrage method
def find_agent_delta_y(
    target_price: float,
    price_after_trade: Callable,
    starting_bid: float = 1,
    precision: float = 0.000000001
):
    b = starting_bid
    previous_change = 1
    p = price_after_trade(b)
    previous_price = p
    diff = p / target_price
    while abs(1 - diff) > precision:
        progress = (previous_price - p) / (previous_price - target_price) or 2
        old_b = b
        b -= previous_change * (1 - 1 / progress)
        previous_price = p
        previous_change = b - old_b
        p = price_after_trade(buy_amount=b if b >= 0 else 0, sell_amount=-b if b < 0 else 0)
        diff = p / target_price

    return b


def constant_product_arbitrage(pool_id: str, minimum_profit: float = 0, direct_calc: bool = True) -> TradeStrategy:

    def strategy(state: GlobalState, agent_id: str):

        pool: ConstantProductPoolState = state.pools[pool_id]
        if not(isinstance(pool, ConstantProductPoolState)):
            raise TypeError(f'{pool_id} is not compatible with constant product arbitrage.')

        x = pool.asset_list[0]
        y = pool.asset_list[1]

        if pool.fee_function or not direct_calc:
            agent_delta_y = recursive_calculation(state, x, y)
        else:
            agent_delta_y = direct_calculation(state, x, y)

        agent_delta_x = -agent_delta_y * pool.liquidity[x] / (pool.liquidity[y] - agent_delta_y)
        if agent_delta_y > 0:
            agent_delta_x *= 1 + pool.trade_fee(x, y, abs(agent_delta_y))
        else:
            agent_delta_x *= 1 - pool.trade_fee(x, y, abs(agent_delta_y))

        projected_profit = (
            agent_delta_y * state.price(y)
            + agent_delta_x * state.price(x)
        )

        # in case we want to graph this later
        # agent = state.agents[agent_id]
        # agent.projected_profit = projected_profit

        if projected_profit <= minimum_profit:
            # don't do it
            # agent.trade_rejected += 1
            return state

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
        new_state = state.execute_swap(pool_id, agent_id, tkn_sell=x, tkn_buy=y, buy_quantity=agent_delta_y)

        # immediately cash out everything for USD
        new_agent = state.agents[agent_id]
        for tkn, quantity in new_agent.holdings.items():
            if new_agent.holdings[tkn] > 0 and tkn != 'USD':
                new_state = external_market_trade(
                    state, agent_id, tkn_buy='USD', tkn_sell=tkn, sell_quantity=quantity
                )

        return new_state

    def direct_calculation(state: GlobalState, tkn_sell: str, tkn_buy: str):

        pool = state.pools[pool_id]
        p = state.price(tkn_buy) / state.price(tkn_sell)
        x = pool.liquidity[tkn_sell]
        y = pool.liquidity[tkn_buy]
        f = pool.base_fee
        if p < x/y * (1 - f):
            # agent can profit by selling y to AMM
            b = 2 * y - (f / p) * x * (1 - f)
            c = y ** 2 - x * y / p * (1 - f)
            t = math.sqrt(b ** 2 - 4 * c)
            if -b < t:
                dY = (-b + t) / 2
            else:
                dY = (-b - t) / 2
            return -dY
        elif p > x/y * (1 + f):
            # agent can profit by selling x to AMM
            b = 2 * y + (f / p) * x * (1 + f)
            c = y ** 2 - x * y / p * (1 + f)
            t = math.sqrt(b ** 2 - 4 * c)
            if -b < t:
                dY = (-b + t) / 2
            else:
                dY = (-b - t) / 2
            return -dY
        else:
            return 0

    def recursive_calculation(state: GlobalState, tkn_sell: str, tkn_buy: str):
        # an alternate way to calculate optimal trade
        # should be equivalent with a flat percentage fee, but also works with more complicated fee structures
        pool = state.pools[pool_id]
        x = pool.liquidity[tkn_sell]
        y = pool.liquidity[tkn_buy]
        # VVV this would be correct if there is no fee VVV
        agent_delta_y = (y - math.sqrt(x * y * state.price(tkn_sell) / state.price(tkn_buy)))

        def price_after_trade(buy_amount=0, sell_amount=0):
            if buy_amount:
                sell_amount = -(x - pool.invariant / (y - buy_amount)) \
                              * (1 + pool.trade_fee(tkn_sell, tkn_buy, buy_amount))
                price = (y - buy_amount) / (x + sell_amount)

            elif sell_amount:
                buy_amount = (x - pool.invariant / (y + sell_amount)) \
                             * (1 - pool.trade_fee(tkn_sell, tkn_buy, sell_amount))
                price = (y + sell_amount) / (x - buy_amount)

            else:
                raise ValueError('Must specify either buy_amount or sell_amount')

            if agent_delta_y < 0:
                price /= (1 - pool.trade_fee(x, y, buy_amount))
            else:
                price /= (1 + pool.trade_fee(x, y, buy_amount))

            return price

        target_price = state.price(tkn_sell) / state.price(tkn_buy)
        return find_agent_delta_y(target_price, price_after_trade, agent_delta_y)

    return TradeStrategy(strategy, name=f'constant product pool arbitrage ({pool_id})')


def omnipool_arbitrage(pool_id: str):

    def strategy(state: GlobalState, agent_id: str) -> GlobalState:

        omnipool: OmnipoolState = state.pools[pool_id]
        if not isinstance(omnipool, OmnipoolState):
            raise AssertionError()
        sell_index = state.time_step % len(omnipool.asset_list)
        buy_index = (sell_index + 1) % len(omnipool.asset_list)
        sell_asset = omnipool.asset_list[sell_index]
        buy_asset = omnipool.asset_list[buy_index]
        target_price = state.external_market[sell_asset] / state.external_market[buy_asset]
        # pool_ratio = omnipool.lrna_price[sell_asset] / omnipool.lrna_price[buy_asset]

        def price_after_trade(buy_amount: float, sell_amount: float):
            sell_amount = sell_amount or buy_amount
            Qi = omnipool.lrna[sell_asset]
            Ri = omnipool.liquidity[sell_asset]
            Qj = omnipool.lrna[buy_asset]
            Rj = omnipool.liquidity[buy_asset]
            x = sell_amount
            delta_q = Qi * (-x / (Ri + x))
            return ((Qi + delta_q) / (Ri + x)) / ((Qj - delta_q) / (Rj + Rj * (delta_q / (Qj - delta_q))))

        delta_y = find_agent_delta_y(target_price, price_after_trade)
        return state.execute_swap(pool_id, agent_id, sell_asset, buy_asset, sell_quantity=delta_y)

    return TradeStrategy(strategy, name='omnipool arbitrage')


def stableswap_arbitrage(pool_id: str, minimum_profit: float = 1):

    def strategy(state: GlobalState, agent_id: str) -> GlobalState:

        stable_pool: StableSwapPoolState = state.pools[pool_id]
        if not isinstance(stable_pool, StableSwapPoolState):
            raise AssertionError()
        sorted_assets = sorted(list(
            stable_pool.liquidity.keys()), key=lambda k: state.external_market[k] / stable_pool.liquidity.get(k)
        )
        buy_asset = sorted_assets[0]
        sell_asset = sorted_assets[-1]
        target_price = state.external_market[buy_asset] / state.external_market[sell_asset]

        # target_price *= (1 + stable_pool.trade_fee)
        d = stable_pool.calculate_d()

        def price_after_trade(buy_amount: float = 0, sell_amount: float = 0):
            buy_amount = buy_amount or sell_amount
            balance_out = stable_pool.liquidity[buy_asset] - buy_amount
            balance_in = stable_pool.calculate_y(
                stable_pool.modified_balances(delta={buy_asset: -buy_amount}, omit=[sell_asset]), d
            )
            return stable_pool.price_at_balance([balance_in, balance_out], d)

        delta_y = find_agent_delta_y(target_price, price_after_trade, precision=0.0001)
        delta_x = (
            stable_pool.liquidity[sell_asset]
            - stable_pool.calculate_y(stable_pool.modified_balances(delta={buy_asset: -delta_y}, omit=[sell_asset]), d)
        ) * (1 + stable_pool.trade_fee)

        projected_profit = (
            delta_y * state.price(buy_asset)
            + delta_x * state.price(sell_asset)
        )

        # in case we want to graph this later
        # agent = state.agents[agent_id]
        # agent.projected_profit = projected_profit

        if projected_profit <= minimum_profit:
            # don't do it
            # agent.trade_rejected += 1
            return state

        # old_state = state.copy()
        new_state = state.execute_swap(pool_id, agent_id, sell_asset, buy_asset, buy_quantity=delta_y)
        # actual_profit = (
        #     new_state.agents[agent_id].holdings[buy_asset] * new_state.price(buy_asset)
        #     + new_state.agents[agent_id].holdings[sell_asset] * new_state.price(sell_asset)
        # ) - (
        #     old_state.agents[agent_id].holdings[buy_asset] * old_state.price(buy_asset)
        #     + old_state.agents[agent_id].holdings[sell_asset] * old_state.price(sell_asset)
        # )
        # print(f'profit difference (actual-projected) = {actual_profit - projected_profit}')
        return new_state

    return TradeStrategy(strategy, name='stableswap arbitrage')


def toxic_asset_attack(pool_id: str, asset_name: str, trade_size: float) -> TradeStrategy:

    def strategy(state: GlobalState, agent_id: str) -> GlobalState:

        omnipool: OmnipoolState = state.pools[pool_id]
        current_price = omnipool.lrna_price[asset_name]
        if current_price <= 0:
            return state
        usd_price = omnipool.lrna_price[omnipool.stablecoin] / current_price
        if usd_price <= 0:
            return state
        quantity = (
            (omnipool.lrna_total - omnipool.lrna[asset_name])
            * omnipool.weight_cap[asset_name] / (1 - omnipool.weight_cap[asset_name])
            - omnipool.lrna[asset_name]
        ) / current_price - 0.001  # because rounding errors

        state = add_liquidity(
            state, pool_id, agent_id,
            quantity=quantity,
            tkn_add=asset_name
        )
        sell_quantity = trade_size * usd_price
        if state.pools[pool_id].liquidity[asset_name] + sell_quantity > 10 ** 12:
            # go right up to the maximum
            sell_quantity = max(10 ** 12 - state.pools[pool_id].liquidity[asset_name] - 0.0000001, 0)
            if sell_quantity == 0:
                # pool is maxed
                return state
        state = swap(
            state, pool_id, agent_id,
            tkn_sell=asset_name,
            tkn_buy='USD',
            sell_quantity=sell_quantity
        )
        return state

    return TradeStrategy(strategy, name=f'toxic asset attack (asset={asset_name}, trade_size={trade_size})')
