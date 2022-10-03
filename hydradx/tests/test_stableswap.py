import pytest

from hydradx.model.amm import stableswap_amm as stableSwap
from hydradx.model.amm.stableswap_amm import StableSwapPoolState
from hydradx.model.amm.agents import Agent
from hydradx.model.amm.trade_strategies import random_swaps, stableswap_arbitrage
from hydradx.model.amm.global_state import GlobalState
from hydradx.model import run
from hypothesis import given, strategies as st, assume

asset_price_strategy = st.floats(min_value=0.01, max_value=1000)
asset_quantity_strategy = st.floats(min_value=1000, max_value=1000000)
fee_strategy = st.floats(min_value=0, max_value=0.1, allow_nan=False)
trade_quantity_strategy = st.floats(min_value=-1000, max_value=1000)
amplification_strategy = st.floats(min_value=1, max_value=10000)


@st.composite
def assets_config(draw, token_count) -> dict:
    return_dict = {
        f"{'abcdefghijklmnopqrstuvwxyz'[i % 26]}{i // 26}": draw(asset_quantity_strategy)
        for i in range(token_count)
    }
    return return_dict


@st.composite
def stableswap_config(
        draw,
        asset_dict=None,
        token_count: int = 2,
        trade_fee: float = None,
        amplification: float = None
) -> stableSwap.StableSwapPoolState:
    asset_dict = asset_dict or draw(assets_config(token_count))
    test_state = StableSwapPoolState(
        tokens=asset_dict,
        amplification=draw(amplification_strategy) if amplification is None else amplification,
        trade_fee=draw(st.floats(min_value=0, max_value=0.1)) if trade_fee is None else trade_fee
    )
    return test_state


def testSwapInvariant():
    initial_state = GlobalState(
        pools={
            'R1/R2': StableSwapPoolState(
                tokens={
                    'R1': 100000,
                    'R2': 100000
                },
                amplification=100,
                trade_fee=0
            )
        },
        agents={
            'trader': Agent(
                holdings={'R1': 1000, 'R2': 1000},
                trade_strategy=random_swaps(pool_id='R1/R2', amount={'R1': 200, 'R2': 1000}, randomize_amount=True)
            )
        },
        external_market={'R1': 1, 'R2': 1}
    )

    pool_events = []
    agent_events = []
    new_pool = initial_state.pools['R1/R2'].copy()
    new_agent = initial_state.agents['trader'].copy()
    d = new_pool.calculate_d()
    for n in range(10):
        if n % 2 == 0:
            new_pool, new_agent = new_pool.execute_swap(
                new_agent, 'R1', 'R2', 100, 0
            )
        else:
            new_pool, new_agent = new_pool.execute_swap(
                new_agent, 'R2', 'R1', 0, 100
            )
        new_d = new_pool.calculate_d()
        if new_d != pytest.approx(d):
            raise AssertionError('Invariant has varied.')
        pool_events.append(new_pool.copy())
        agent_events.append(new_agent.copy())


@given(stableswap_config(asset_dict={'R1': 1000000, 'R2': 1000000}, trade_fee=0))
def test_arbitrage(stable_pool):
    initial_state = GlobalState(
        pools={
            'R1/R2': stable_pool
        },
        agents={
            'Trader': Agent(
                holdings={'R1': 1000000, 'R2': 1000000},
                trade_strategy=random_swaps(pool_id='R1/R2', amount={'R1': 10000, 'R2': 10000}, randomize_amount=True)
            ),
            'Arbitrageur': Agent(
                holdings={'R1': 1000000, 'R2': 1000000},
                trade_strategy=stableswap_arbitrage(pool_id='R1/R2', minimum_profit=0)
            )
        },
        external_market={
            'R1': 1,
            'R2': 1
        },
        # evolve_function = fluctuate_prices(volatility={'R1': 1, 'R2': 1}, trend = {'R1': 1, 'R1': 1})
    )
    events = run.run(initial_state, time_steps=10, silent=True)
    # print(events[0]['state'].pools['R1/R2'].spot_price, events[-1]['state'].pools['R1/R2'].spot_price)
    if (
        events[0]['state'].pools['R1/R2'].spot_price
        != pytest.approx(events[-1]['state'].pools['R1/R2'].spot_price, rel=1e-2)
    ):
        raise AssertionError("Arbitrageur didn't keep the price stable.")
    if (
        events[0]['state'].agents['Arbitrageur'].holdings['R1']
        + events[0]['state'].agents['Arbitrageur'].holdings['R2']
        > events[-1]['state'].agents['Arbitrageur'].holdings['R1']
        + events[-1]['state'].agents['Arbitrageur'].holdings['R2']
    ):
        raise AssertionError("Arbitrageur didn't make money.")


@given(stableswap_config(trade_fee=0))
def test_add_remove_liquidity(initial_state: StableSwapPoolState):
    lp_tkn = initial_state.asset_list[0]
    lp = Agent(
        holdings={lp_tkn: 10000}
    )
    add_liquidity_state, add_liquidity_agent = stableSwap.add_liquidity(
        initial_state, old_agent=lp, quantity=10000, tkn_add=lp_tkn
    )
    remove_liquidity_state, remove_liquidity_agent = add_liquidity_state.remove_liquidity(
        add_liquidity_state,
        add_liquidity_agent,
        quantity=add_liquidity_agent.shares[initial_state.unique_id],
        tkn_remove=lp_tkn
    )
    if remove_liquidity_agent.holdings[lp_tkn] != pytest.approx(lp.holdings[lp_tkn]):
        raise AssertionError('LP did not get the same balance back when withdrawing liquidity.')
