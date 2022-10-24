import pytest
import functools
from hydradx.model.amm import stableswap_amm as stableswap
from hydradx.model.amm.stableswap_amm import StableSwapPoolState
from hydradx.model.amm.agents import Agent
from hydradx.model.amm.trade_strategies import random_swaps, stableswap_arbitrage
from hydradx.model.amm.global_state import GlobalState, fluctuate_prices
from hydradx.model import run
from hypothesis import given, strategies as st, assume

asset_price_strategy = st.floats(min_value=0.01, max_value=1000)
asset_quantity_strategy = st.floats(min_value=1000, max_value=1000000)
fee_strategy = st.floats(min_value=0, max_value=0.1, allow_nan=False)
trade_quantity_strategy = st.floats(min_value=-1000, max_value=1000)
amplification_strategy = st.floats(min_value=1, max_value=10000)
asset_number_strategy = st.integers(min_value=2, max_value=5)


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
        token_count: int = None,
        trade_fee: float = None,
        amplification: float = None
) -> stableswap.StableSwapPoolState:
    token_count = token_count or draw(asset_number_strategy)
    asset_dict = asset_dict or draw(assets_config(token_count))
    test_state = StableSwapPoolState(
        tokens=asset_dict,
        amplification=draw(amplification_strategy) if amplification is None else amplification,
        trade_fee=draw(st.floats(min_value=0, max_value=0.1)) if trade_fee is None else trade_fee
    )
    return test_state


@given(stableswap_config(trade_fee=0))
def test_swap_invariant(initial_pool: StableSwapPoolState):
    # print(f'testing with {len(initial_pool.asset_list)} assets')
    initial_state = GlobalState(
        pools={
            'stableswap': initial_pool
        },
        agents={
            'trader': Agent(
                holdings={tkn: 100000 for tkn in initial_pool.asset_list},
                trade_strategy=random_swaps(
                    pool_id='stableswap',
                    amount={tkn: 1000 for tkn in initial_pool.asset_list},
                    randomize_amount=True
                )
            )
        }
    )

    new_state = initial_state.copy()
    d = new_state.pools['stableswap'].calculate_d()
    for n in range(10):
        new_state = new_state.agents['trader'].trade_strategy.execute(new_state, agent_id='trader')
        new_d = new_state.pools['stableswap'].calculate_d()
        if new_d != pytest.approx(d):
            raise AssertionError('Invariant has varied.')
    if initial_state.total_wealth() != pytest.approx(new_state.total_wealth()):
        raise AssertionError('Some assets were lost along the way.')


@given(stableswap_config(trade_fee=0))
def test_round_trip_dy(initial_pool: StableSwapPoolState):
    d = initial_pool.calculate_d()
    asset_a = initial_pool.asset_list[0]
    other_reserves = [initial_pool.liquidity[a] for a in list(filter(lambda k: k != asset_a, initial_pool.asset_list))]
    y = initial_pool.calculate_y(reserves=other_reserves, d=d)
    if y != pytest.approx(initial_pool.liquidity[asset_a]) or y < initial_pool.liquidity[asset_a]:
        raise AssertionError('Round-trip calculation incorrect.')


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
                trade_strategy=stableswap_arbitrage(pool_id='R1/R2', minimum_profit=0, precision=0.000001)
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
        != pytest.approx(events[-1]['state'].pools['R1/R2'].spot_price)
    ):
        raise AssertionError(f"Arbitrageur didn't keep the price stable."
                             f"({events[0]['state'].pools['R1/R2'].spot_price})"
                             f"{events[-1]['state'].pools['R1/R2'].spot_price}")
    if (
        events[0]['state'].agents['Arbitrageur'].holdings['R1']
        + events[0]['state'].agents['Arbitrageur'].holdings['R2']
        > events[-1]['state'].agents['Arbitrageur'].holdings['R1']
        + events[-1]['state'].agents['Arbitrageur'].holdings['R2']
    ):
        raise AssertionError("Arbitrageur didn't make money.")


def stable_swap_equation(d: float, a: float, n: int, reserves: list):
    # this is the equation that should remain true at all times within a stableswap pool
    side1 = a * n ** n * sum(reserves) + d
    side2 = a * n ** n * d + d ** (n + 1) / (n ** n * functools.reduce(lambda i, j: i * j, reserves))
    return side1 == pytest.approx(side2)


@given(stableswap_config(trade_fee=0))
def test_add_remove_liquidity(initial_pool: StableSwapPoolState):
    # print(f'testing with {len(initial_state.asset_list)} assets')
    lp_tkn = initial_pool.asset_list[0]
    lp = Agent(
        holdings={lp_tkn: 10000}
    )

    add_liquidity_state, add_liquidity_agent = stableswap.add_liquidity(
        initial_pool, old_agent=lp, quantity=10000, tkn_add=lp_tkn
    )
    if not stable_swap_equation(
        add_liquidity_state.calculate_d(),
        add_liquidity_state.amplification,
        add_liquidity_state.n_coins,
        add_liquidity_state.liquidity.values()
    ):
        raise AssertionError('Stableswap equation does not hold after add liquidity operation.')

    remove_liquidity_state, remove_liquidity_agent = add_liquidity_state.remove_liquidity(
        add_liquidity_state,
        add_liquidity_agent,
        quantity=add_liquidity_agent.shares[initial_pool.unique_id],
        tkn_remove=lp_tkn
    )
    if not stable_swap_equation(
        remove_liquidity_state.calculate_d(),
        remove_liquidity_state.amplification,
        remove_liquidity_state.n_coins,
        remove_liquidity_state.liquidity.values()
    ):
        raise AssertionError('Stableswap equation does not hold after add liquidity operation.')
    if remove_liquidity_agent.holdings[lp_tkn] != pytest.approx(lp.holdings[lp_tkn]):
        raise AssertionError('LP did not get the same balance back when withdrawing liquidity.')
