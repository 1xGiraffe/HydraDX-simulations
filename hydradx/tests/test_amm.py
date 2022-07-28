import copy
import random

import pytest
from hypothesis import given, strategies as st, settings
from hydradx.tests.test_omnipool_amm import omnipool_config
from hydradx.tests.test_basilisk_amm import constant_product_pool_config
from hydradx.model.amm.basilisk_amm import ConstantProductPoolState
from hydradx.model.amm.global_state import GlobalState, oscillate_prices, fluctuate_prices
from hydradx.model.amm.agents import Agent

from hydradx.model import run
from hydradx.model import plot_utils as pu
from hydradx.model import processing
from hydradx.model.processing import cash_out
from hydradx.model.amm.trade_strategies import random_swaps, steady_swaps, invest_all, constant_product_arbitrage
from hydradx.model.amm.amm import AMM

import sys
import random

sys.path.append('../..')

asset_price_strategy = st.floats(min_value=0.01, max_value=1000)
asset_number_strategy = st.integers(min_value=3, max_value=5)
asset_quantity_strategy = st.floats(min_value=1000, max_value=1000)
fee_strategy = st.floats(min_value=0.0001, max_value=0.1, allow_nan=False, allow_infinity=False)


@st.composite
def assets_config(draw, token_count: int = 0) -> dict:
    token_count = token_count or draw(asset_number_strategy)
    return_dict = {
        'HDX': draw(asset_price_strategy),
        'USD': 1
    }
    return_dict.update({
        f"{'abcdefghijklmnopqrstuvwxyz'[i % 26]}{i // 26}": draw(asset_price_strategy)
        for i in range(token_count - 2)
    })
    return return_dict


@st.composite
def agent_config(
    draw,
    holdings: dict = None,
    asset_list: list = None,
    trade_strategy: any = None
):
    return Agent(
        holdings=holdings or {
            tkn: draw(asset_quantity_strategy)
            for tkn in asset_list
        },
        trade_strategy=trade_strategy
    )


@st.composite
def global_state_config(
        draw,
        asset_dict: dict[str: float] = None,
        pools=None,
        agents=None,
        evolve_function=None
) -> GlobalState:
    market_prices = asset_dict or draw(assets_config())
    asset_list = list(market_prices.keys())
    if not pools:
        pools = {}
        # a Basilisk pool for every asset pair
        for x in range(len(asset_list) - 1):
            for y in range(x + 1, len(asset_list)):
                x_quantity = draw(asset_quantity_strategy)
                pools.update({
                    f'{asset_list[x]}/{asset_list[y]}':
                    draw(constant_product_pool_config(
                        asset_dict={
                            asset_list[x]: x_quantity,
                            asset_list[y]: x_quantity * market_prices[asset_list[x]] / market_prices[asset_list[y]]
                        },
                        trade_fee=draw(fee_strategy)
                    ))
                })
        # and an Omnipool
        usd_price_lrna = 1  # draw(asset_price_strategy)
        market_prices.update({'LRNA': usd_price_lrna})
        liquidity = {tkn: 1000 for tkn in asset_list}
        pools.update({
            'omnipool': draw(omnipool_config(
                asset_dict={
                    tkn: {
                        'liquidity': liquidity[tkn],
                        'LRNA': liquidity[tkn] * market_prices[tkn] / usd_price_lrna
                    }
                    for tkn in asset_list
                },
                lrna_fee=draw(fee_strategy),
                asset_fee=draw(fee_strategy)
            ))
        })

    if not agents:
        agents = {
            f'Agent{_}': draw(agent_config(
                asset_list=asset_list
            ))
            for _ in range(5)
        }

    config = GlobalState(
        pools=pools,
        agents=agents,
        external_market=market_prices,
        evolve_function=evolve_function
    )
    return config


@given(global_state_config())
def test_simulation(initial_state: GlobalState):

    for a, agent in enumerate(initial_state.agents.values()):
        pool: AMM = initial_state.pools[list(initial_state.pools.keys())[a % len(initial_state.pools)]]
        agent.trade_strategy = [
            steady_swaps(pool_id=pool.unique_id, usd_amount=100),
            invest_all(pool_id=pool.unique_id)
        ][a % 2]

    # VVV -this would break the property test- VVV
    # initial_state.evolve_function = fluctuate_prices()

    initial_wealth = initial_state.total_wealth()
    events = run.run(initial_state, time_steps=5, silent=True)
    events = processing.postprocessing(events, optional_params=[
        'pool_val', 'holdings_val', 'impermanent_loss', 'trade_volume'
    ])

    # pu.plot(events, asset='all')
    # pu.plot(events, agent='Agent1', prop=['holdings', 'holdings_val'])

    # property test: is there still the same total wealth held by all pools + agents?
    final_state = events[-1]['state']
    if final_state.total_wealth() != pytest.approx(initial_wealth):
        raise AssertionError('total wealth quantity changed!')


@settings(deadline=500)
@given(global_state_config(
    asset_dict={
        'HDX': 0.08,
        'USD': 1
    },
    agents={
        'LP': Agent(
            holdings={
                'HDX': 0,
                'USD': 0
            },
            trade_strategy=invest_all('HDX/USD')
        ),
        'Trader1': Agent(
            holdings={
                'HDX': 80000,
                'USD': 1000
            },
            trade_strategy=steady_swaps('HDX/USD', 100, asset_list=['USD', 'HDX'])
        ),
        'Trader2': Agent(
            holdings={
                'HDX': 80000,
                'USD': 1000
            },
            trade_strategy=steady_swaps('HDX/USD', 100, asset_list=['HDX', 'USD'])
        )
    }
))
def test_LP(initial_state: GlobalState):
    initial_state.agents['LP'].holdings = {
        tkn: quantity for tkn, quantity in initial_state.pools['HDX/USD'].liquidity.items()
    }

    old_state = initial_state.copy()
    events = run.run(old_state, time_steps=100, silent=True)
    final_state: GlobalState = events[-1]['state']

    # post-process
    events = processing.postprocessing(events, optional_params=['withdraw_val', 'deposit_val'])

    if sum(final_state.agents['LP'].holdings.values()) > 0:
        print('failed, not invested')
        raise AssertionError('Why does this LP not have all its assets in the pool???')
    if final_state.agents['LP'].withdraw_val < cash_out(initial_state, initial_state.agents['LP']):
        print('failed, lost money.')
        raise AssertionError('The LP lost money!')
    # print('test passed.')


def test_arbitrage():

    market_prices = {
        'HDX': 1,
        'BSX': 2
    }
    initial_state = GlobalState(
        pools={
            'USD/BSX': ConstantProductPoolState(
                {
                    'USD': 2000000,
                    'BSX': 1000000
                },
                trade_fee=0.1
            )
        },
        agents={
            'trader': Agent(
                holdings={'USD': 1000, 'BSX': 1000},
                trade_strategy=random_swaps(pool_id='USD/BSX', amount={'USD': 100, 'BSX': 50})
            ),
            'arbitrageur': Agent(
                holdings={'USD': 100000, 'BSX': 100000},
                trade_strategy=constant_product_arbitrage('USD/BSX')
            )
        },
        external_market=market_prices,
        evolve_function=fluctuate_prices(volatility={'BSX': 1})
    )
    events = run.run(initial_state, time_steps=100)
    final_state = events[-1]['state']
    final_pool_state = final_state.pools['USD/BSX']
    if (pytest.approx(final_pool_state.liquidity['USD'] / final_pool_state.liquidity['BSX'])
            != final_state.price('BSX') / final_state.price('USD')):
        raise AssertionError('Price ratio does not match ratio in the pool!')


@given(global_state_config())
def test_construction(initial_state: GlobalState):
    # see whether we can just construct a valid global state
    # print(initial_state)
    pass


if __name__ == "__main__":
    pass
