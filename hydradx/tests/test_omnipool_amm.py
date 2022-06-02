import copy
import pytest
import random
from hypothesis import given, strategies as st, assume

from hydradx.model.amm import omnipool_amm as oamm

asset_price_strategy = st.floats(min_value=0.01, max_value=1000)
asset_number_strategy = st.integers(min_value=3, max_value=5)
asset_quantity_strategy = st.floats(min_value=10000, max_value=10000000)
fee_strategy = st.floats(min_value=0.0001, max_value=0.1, allow_nan=False, allow_infinity=False)


@st.composite
def assets_config(draw, token_count: int = 0) -> dict:
    token_count = token_count or draw(asset_number_strategy)
    lrna_price_usd = draw(asset_price_strategy)
    return_dict = {
        'HDX': {
            'liquidity': draw(asset_quantity_strategy),
            'LRNA': draw(asset_quantity_strategy)
        },
        'USD': {
            'liquidity': draw(asset_quantity_strategy),
            'LRNA_price': 1 / lrna_price_usd
        }
    }
    return_dict.update({
        ''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(3)): {
            'liquidity': draw(asset_quantity_strategy),
            'LRNA': draw(asset_quantity_strategy)
        } for _ in range(token_count - 2)
    })
    return return_dict


@st.composite
def omnipool_config(
        draw,
        asset_dict=None,
        token_count=0,
        lrna_fee=None,
        asset_fee=None,
        tvl_cap_usd=0
) -> oamm.OmnipoolState:
    asset_dict = asset_dict or draw(assets_config(token_count))
    return oamm.OmnipoolState(
        tokens=asset_dict,
        tvl_cap=tvl_cap_usd or float('inf'),
        asset_fee=draw(st.floats(min_value=0, max_value=0.1)) if asset_fee is None else asset_fee,
        lrna_fee=draw(st.floats(min_value=0, max_value=0.1)) if lrna_fee is None else lrna_fee
    )


def test_swap_lrna_delta_Qi_respects_invariant(d: oamm.OmnipoolState, delta_ri: float, i: str):
    assume(i in d.asset_list)
    assume(d.liquidity[i] > delta_ri > -d.liquidity[i])
    d2 = copy.deepcopy(d)
    delta_Qi = oamm.swap_lrna_delta_Qi(d, delta_ri, i)
    d2.liquidity[i] += delta_ri
    d2.lrna[i] += delta_Qi

    # Test basics
    for j in d2.asset_list:
        assert d2.liquidity[j] > 0
        assert d2.lrna[j] > 0
    assert not (delta_ri > 0 and delta_Qi > 0)
    assert not (delta_ri < 0 and delta_Qi < 0)

    # Test that the pool invariant is respected
    assert oamm.asset_invariant(d2, i) == pytest.approx(oamm.asset_invariant(d, i))


def test_swap_lrna_delta_Ri_respects_invariant(d: oamm.OmnipoolState, delta_qi: float, i: str):
    assume(i in d.asset_list)
    assume(d.lrna[i] > delta_qi > -d.lrna[i])
    d2 = copy.deepcopy(d)
    delta_Ri = oamm.swap_lrna_delta_Ri(d, delta_qi, i)
    d2.lrna[i] += delta_qi
    d2.liquidity[i] += delta_Ri

    # Test basics
    for j in d.asset_list:
        assert d2.liquidity[j] > 0
        assert d2.lrna[j] > 0
    assert not (delta_Ri > 0 and delta_qi > 0)
    assert not (delta_Ri < 0 and delta_qi < 0)

    # Test that the pool invariant is respected
    assert oamm.asset_invariant(d2, i) == pytest.approx(oamm.asset_invariant(d, i))


# Combining these two tests because the valid input space is the same
@given(omnipool_config(asset_fee=0, lrna_fee=0), asset_quantity_strategy)
def test_swap_lrna_delta_TKN_respects_invariant(d, delta_tkn):
    test_swap_lrna_delta_Qi_respects_invariant(d, delta_tkn, 'HDX')
    test_swap_lrna_delta_Ri_respects_invariant(d, delta_tkn, 'HDX')


# Tests over input space of a list of token quantities

@given(omnipool_config(asset_fee=0, lrna_fee=0))
def test_weights(initial_state: oamm.OmnipoolState):
    old_state = initial_state
    for i in old_state.asset_list:
        assert oamm.weight_i(old_state, i) >= 0
    assert sum([oamm.weight_i(old_state, i) for i in old_state.asset_list]) == pytest.approx(1.0)

    i = old_state.asset_list[-1]
    # check enforcement of per-asset weight limit
    old_agents = {
        'LP': {
            'r': {i: 1000000},
            's': {i: 0},
            'p': {i: old_state.liquidity[i] / old_state.lrna[i]}
        }
    }
    lp_id = 'LP'

    i = old_state.asset_list[-1]
    old_state.weight_cap[i] = old_state.tvl[i] / old_state.tvl_total * 1.00001

    # calculate what should be the maximum allowable liquidity provision
    max_amount = ((old_state.weight_cap[i] / (1 - old_state.weight_cap[i])
                  * old_state.lrna_total - old_state.lrna[i] / (1 - old_state.weight_cap[i]))
                  / oamm.price_i(old_state, i))

    # make sure agent has enough funds
    old_agents[lp_id]['r'][i] = max_amount * 2

    # try one just above and just below the maximum allowable amount
    illegal_state, illegal_agents = oamm.add_risk_liquidity(old_state, old_agents, lp_id, max_amount * 1.0001, i)
    if illegal_state.liquidity[i] != pytest.approx(old_state.liquidity[i]):
        raise AssertionError(f'illegal transaction passed against weight limit in {i}')
    legal_state, legal_agents = oamm.add_risk_liquidity(old_state, old_agents, lp_id, max_amount * 1.9999, i)
    if legal_state.liquidity[i] == old_state.liquidity[i]:
        raise AssertionError(f'legal transaction failed against weight limit in {i}')


@given(omnipool_config())
def test_QR_strat(market_state: oamm.OmnipoolState):
    for i in market_state.asset_list:
        assert oamm.price_i(market_state, i) > 0


@given(omnipool_config(token_count=3, lrna_fee=0, asset_fee=0))
def test_add_risk_liquidity(initial_state: oamm.OmnipoolState):
    old_state = initial_state
    LP_id = 'LP'
    i = old_state.asset_list[2]

    old_agents = {
        LP_id: {
            'r': {i: 1000},
            's': {i: 0},
            'p': {i: old_state.liquidity[i] / old_state.lrna[i]}
        }
    }
    delta_R = 1000

    new_state, new_agents = oamm.add_risk_liquidity(old_state, old_agents, LP_id, delta_R, i)
    for j in initial_state.asset_list:
        assert oamm.price_i(old_state, j) == pytest.approx(oamm.price_i(new_state, j))
    if old_state.liquidity[i] / old_state.shares[i] != pytest.approx(new_state.liquidity[i] / new_state.shares[i]):
        raise AssertionError(f'Price change in {i}'
                             f'({old_state.liquidity[i] / old_state.shares[i]}) -->'
                             f'({pytest.approx(new_state.liquidity[i] / new_state.shares[i])})'
                             )

    if old_state.lrna_imbalance / old_state.lrna_total != \
            pytest.approx(new_state.lrna_imbalance / new_state.lrna_total):
        raise AssertionError(f'LRNA imbalance did not remain constant.')

    # check enforcement of agent's spending limit
    new_state, new_agents = oamm.add_risk_liquidity(old_state, old_agents, LP_id, old_agents[LP_id]['r'][i] + 1, i)
    assert new_state, new_agents == (old_state, old_agents)
    new_state, new_agents = oamm.add_risk_liquidity(old_state, old_agents, LP_id, old_agents[LP_id]['r'][i] - 1, i)
    assert new_state, new_agents != (old_state, old_agents)

    # check enforcement of overall TVL cap
    TVL = sum([
        old_state.lrna[i] * old_state.liquidity[old_state.stablecoin] / old_state.lrna[old_state.stablecoin]
        for i in old_state.asset_list
    ])
    assert TVL == old_state.tvl_total
    old_state.tvl_cap = TVL
    new_state, new_agents = oamm.add_risk_liquidity(old_state, old_agents, LP_id, delta_r=1, i=i)
    assert new_state, new_agents == (old_state, old_agents)


@given(omnipool_config(token_count=3))
def test_remove_risk_liquidity(initial_state: oamm.OmnipoolState):
    old_state = initial_state

    LP_id = 'LP'
    p_init = 1
    old_agents = {
        LP_id: {
            'r': {token: 0 for token in initial_state.asset_list},
            's': {token: 1000 for token in initial_state.asset_list},
            'p': {token: p_init for token in initial_state.asset_list},
            'q': 0
        }
    }
    delta_S = -1000
    i = initial_state.asset_list[2]

    new_state, new_agents = oamm.remove_risk_liquidity(old_state, old_agents, LP_id, delta_S, i)
    for j in new_state.asset_list:
        assert oamm.price_i(old_state, j) == pytest.approx(oamm.price_i(new_state, j))
    if old_state.liquidity[i] / old_state.shares[i] != pytest.approx(new_state.liquidity[i] / new_state.shares[i]):
        raise AssertionError('')
    delta_r = new_agents[LP_id]['r'][i] - old_agents[LP_id]['r'][i]
    delta_q = new_agents[LP_id]['q'] - old_agents[LP_id]['q']
    if delta_q <= 0 and delta_q != pytest.approx(0):
        raise AssertionError('Delta Q < 0')
    if delta_r <= 0 and delta_r != pytest.approx(0):
        raise AssertionError('Delta R < 0')

    piq = oamm.price_i(old_state, i)
    val_withdrawn = piq * delta_r + delta_q
    assert -2 * piq / (piq + p_init) * delta_S / old_state.shares[i] * piq * old_state.liquidity[
        i] == pytest.approx(val_withdrawn)


@given(omnipool_config(token_count=3), fee_strategy)
def test_swap_lrna(initial_state, fee):
    # This example fails. (Also fails in original.) TODO: figure out why
    # initial_state = oamm.OmnipoolState(
    #     tokens={token: {'liquidity': 1.0, 'LRNA': 0.0001} for token in ['HDX', 'USD', 'MYN']}
    # )
    old_state = initial_state
    trader_id = 'trader'
    LP_id = 'lp'
    old_agents = {
        trader_id: {
            'r': {token: 1000 for token in initial_state.asset_list},
            'q': 1000,
            's': {token: 0 for token in initial_state.asset_list}
        },
        LP_id: {
            'r': {token: 0 for token in initial_state.asset_list},
            'q': 0,
            's': {token: 900 for token in initial_state.asset_list}
        }
    }
    delta_Ra = 1000
    delta_Qa = -1000
    i = initial_state.asset_list[2]

    # Test with trader selling asset i
    feeless_state, feeless_agents = oamm.swap_lrna(old_state, old_agents, trader_id, delta_Ra, 0, i, 0, 0)
    if oamm.asset_invariant(feeless_state, i) != pytest.approx(oamm.asset_invariant(old_state, i)):
        raise

    # Test with trader selling LRNA
    new_state, new_agents = oamm.swap_lrna(old_state, old_agents, trader_id, 0, delta_Qa, i, fee, fee)
    feeless_state, feeless_agents = oamm.swap_lrna(old_state, old_agents, trader_id, 0, delta_Qa, i, 0, 0)
    if oamm.asset_invariant(feeless_state, i) != pytest.approx(oamm.asset_invariant(old_state, i)):
        raise
    for j in old_state.asset_list:
        if min(new_state.liquidity[j] - feeless_state.liquidity[j], 0) != pytest.approx(0):
            raise
    if min(oamm.asset_invariant(new_state, i) / oamm.asset_invariant(old_state, i), 1) != pytest.approx(1):
        raise

    if old_state.lrna[i] / old_state.liquidity[i] != \
            pytest.approx((new_state.lrna[i] + new_state.lrna_imbalance) / new_state.liquidity[i]):
        raise AssertionError(f'Impermanent loss calculation incorrect. '
                             f'{old_state.lrna[i] / old_state.liquidity[i]} != '
                             f'{(new_state.lrna[i] + new_state.lrna_imbalance) / new_state.liquidity[i]}')
    else:
        pass


@given(omnipool_config(token_count=3), fee_strategy, fee_strategy, st.integers(min_value=1, max_value=2))
def test_swap_assets(initial_state: oamm.OmnipoolState, fee_lrna, fee_assets, i):
    i_buy = initial_state.asset_list[i]
    old_state = initial_state

    trader_id = 'trader'
    LP_id = 'lp'

    old_agents = {
        trader_id: {
            'r': {token: 10000 for token in initial_state.asset_list},
            'q': 10000,
            's': {token: 10000 for token in initial_state.asset_list}
        },
        LP_id: {
            'r': {token: 0 for token in initial_state.asset_list},
            'q': 0,
            's': {token: 900 for token in initial_state.asset_list}
        }
    }
    delta_R = 1000
    sellable_tokens = len(old_state.asset_list) - 1
    i_sell = old_state.asset_list[i % sellable_tokens + 1]

    # Test with trader selling asset i, no LRNA fee... price should match feeless
    new_state, new_agents = \
        oamm.swap_assets(old_state, old_agents, trader_id, 'sell', delta_R, i_buy, i_sell, fee_assets, fee_lrna)
    asset_fee_only_state, asset_fee_only_agents = \
        oamm.swap_assets(old_state, old_agents, trader_id, 'sell', delta_R, i_buy, i_sell, fee_assets, 0)
    feeless_state, feeless_agents = \
        oamm.swap_assets(old_state, old_agents, trader_id, 'sell', delta_R, i_buy, i_sell, 0, 0)
    for j in old_state.asset_list:
        # assets in pools only go up compared to asset_fee_only_state
        if min(asset_fee_only_state.liquidity[j] - feeless_state.liquidity[j], 0) != pytest.approx(0):
            raise AssertionError("asset in pool {j} is lesser when compared with no-fee case")
        # asset in pool goes up from asset_fee_only_state -> new_state (i.e. introduction of LRNA fee)
        if min(new_state.liquidity[j] - asset_fee_only_state.liquidity[j], 0) != pytest.approx(0):
            raise AssertionError("asset in pool {j} is lesser when LRNA fee is added vs only asset fee")
        # invariant does not decrease
        if min(oamm.asset_invariant(new_state, j) / oamm.asset_invariant(old_state, j), 1) != pytest.approx(1):
            raise AssertionError("invariant ratio less than zero")
        # total quantity of R_i remains unchanged
        if (old_state.liquidity[j] + old_agents[trader_id]['r'][j]
                != pytest.approx(new_state.liquidity[j] + new_agents[trader_id]['r'][j])):
            raise AssertionError("total quantity of R[{j}] changed")

    # test that no LRNA is lost
    delta_Qi = new_state.lrna[i_sell] - old_state.lrna[i_sell]
    delta_Qj = new_state.lrna[i_buy] - old_state.lrna[i_buy]
    delta_Qh = new_state.lrna['HDX'] - old_state.lrna['HDX']
    delta_L = new_state.lrna_imbalance - old_state.lrna_imbalance
    if delta_L + delta_Qj + delta_Qi + delta_Qh != pytest.approx(0, abs=1e10):
        raise AssertionError('Some LRNA was lost along the way.')

    delta_out_new = new_agents[trader_id]['r'][i_buy] - old_agents[trader_id]['r'][i_buy]

    # Test with trader buying asset i, no LRNA fee... price should match feeless
    buy_state, buy_agents = oamm.swap_assets(
        old_state, old_agents, trader_id, 'buy', -delta_out_new, i_buy, i_sell, fee_assets, fee_lrna
    )

    for j in old_state.asset_list:
        assert buy_state.liquidity[j] == pytest.approx(new_state.liquidity[j])
        assert buy_state.lrna[j] == pytest.approx(new_state.lrna[j])
        assert old_state.liquidity[j] + old_agents[trader_id]['r'][j] == \
               pytest.approx(buy_state.liquidity[j] + buy_agents[trader_id]['r'][j])
        assert buy_agents[trader_id]['r'][j] == pytest.approx(new_agents[trader_id]['r'][j])
        assert buy_agents[trader_id]['q'] == pytest.approx(new_agents[trader_id]['q'])


# Want to make sure this does not change pij, only changes piq proportionally
# Also should make sure things stay reasonably bounded
# Requires state with H, T, Q, burn_rate
rate_strat = st.floats(min_value=1e-4, max_value=.99, allow_nan=False, allow_infinity=False)

if __name__ == '__main__':
    test_swap_lrna_delta_TKN_respects_invariant()
    test_swap_lrna()
    test_weights()
    test_QR_strat()
    test_add_risk_liquidity()
    test_remove_risk_liquidity()
    test_swap_assets()


def test_simulation():
    # Dependencies
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt

    # Experiments
    from hydradx.model import run
    from hydradx.model import processing
    # from model.plot_utils import *
    from hydradx.model import plot_utils as pu
    from hydradx.model import init_utils

    ########## AGENT CONFIGURATION ##########
    # key -> token name, value -> token amount owned by agent
    # note that token name of 'omniABC' is used for omnipool LP shares of token 'ABC'
    LP1 = LP1 = {'omniR1': 500000}
    LP2 = {'omniR2': 1500000}
    trader = {'LRNA': 1000000, 'R1': 1000000, 'R2': 1000000}

    # key -> agent_id, value -> agent dict
    agent_d = {'Trader': trader, 'LP1': LP1, 'LP2': LP2}
    # agent_d = {'Trader': trader, 'LP1': LP1}

    ########## ACTION CONFIGURATION ##########

    action_dict = {
        'sell_lrna_for_r1': {'token_buy': 'R1', 'token_sell': 'LRNA', 'amount_sell': 2000, 'action_id': 'Trade',
                             'agent_id': 'Trader'},
        'sell_r1_for_lrna': {'token_sell': 'R1', 'token_buy': 'LRNA', 'amount_sell': 1000, 'action_id': 'Trade',
                             'agent_id': 'Trader'}
    }

    # list of (action, number of repititions of action), timesteps = sum of repititions of all actions
    trade_count = 5000
    action_ls = [('trade', trade_count)]

    # maps action_id to action dict, with some probability to enable randomness
    prob_dict = {
        'trade': {'sell_lrna_for_r1': 0.5,
                  'sell_r1_for_lrna': 0.5}
    }

    ########## CFMM INITIALIZATION ##########

    initial_state = oamm.OmnipoolState(
        tokens={
            'HDX': {'liquidity': 1000000, 'LRNA_price': 1},
            'USD': {'liquidity': 1000000, 'LRNA_price': 1},
            'R1': {'liquidity': 500000, 'LRNA_price': 2},
            'R2': {'liquidity': 1500000, 'LRNA_price': 2 / 3},
        },
        lrna_fee=0,
        asset_fee=0
    )

    config_params = {
        # 'amm': amm,
        'cfmm_type': "",
        'initial_state': initial_state,
        'agent_d': agent_d,
        'action_ls': action_ls,
        'prob_dict': prob_dict,
        'action_dict': action_dict,
    }

    config_dict, state = init_utils.get_configuration(config_params)

    pd.options.mode.chained_assignment = None  # default='warn'
    pd.options.display.float_format = '{:.2f}'.format

    run.config(config_dict, state)
    events = run.run()

    rdf, agent_df = processing.postprocessing(events, optional_params=['pool_val', 'hold_val', 'withdraw_val'])


def testThreeSum():
    nums = [-1, 0, 1, 2, -1, -4, 1, 0, 0, -1, -2, -4, -3, -5, -4, -1]
    triplets = []
    nums = sorted(nums)
    for i in range(len(nums) - 2):
        if i > 0 and nums[i] == nums[i - 1]:
            continue
        i2 = i + 1
        i3 = len(nums) - 1
        while i3 > i2:
            sum = nums[i] + nums[i2] + nums[i3]
            if sum > 0:
                i3 -= 1
            elif sum < 0:
                i2 += 1
            else:
                triplets.append([nums[i], nums[i2], nums[i3]])
                last = nums[i3]
                while nums[i3] == last and i3 > 0:
                    i3 -= 1
    return triplets

