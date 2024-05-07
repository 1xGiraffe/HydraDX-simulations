import pytest
from hypothesis import given, strategies as st
from mpmath import mp, mpf

from hydradx.model.amm.agents import Agent
from hydradx.model.amm.global_state import find_partial_liquidation_amount, omnipool_liquidate_cdp, GlobalState, \
    liquidate_against_omnipool, liquidate_against_omnipool_and_settle_otc
from hydradx.model.amm.liquidations import CDP, money_market
from hydradx.model.amm.omnipool_amm import OmnipoolState
from hydradx.model.amm.otc import OTC

mp.dps = 50


# CDP tests

def test_cdp_validate():
    debt_asset = "USDT"
    collateral_asset = "DOT"
    init_debt_amt = 1000
    init_collat_amt = 200
    cdp = CDP(debt_asset, collateral_asset, init_debt_amt, init_collat_amt)
    if not cdp.validate():
        raise
    cdp.debt_amt = -1
    if cdp.validate():
        raise
    cdp.debt_amt = init_debt_amt
    cdp.collateral_amt = -1
    if cdp.validate():
        raise
    cdp.collateral_amt = init_collat_amt
    cdp.debt_asset = collateral_asset
    if cdp.validate():
        raise

    cdp = CDP(debt_asset, collateral_asset, 0, init_collat_amt)
    if not cdp.validate():
        raise
    cdp = CDP(debt_asset, collateral_asset, init_debt_amt, 0)
    if not cdp.validate():  # note that toxic debt does not fail validation
        raise


# money_market tests

def test_get_oracle_price():
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): 10}
    )
    if mm.get_oracle_price("DOT", "USDT") != 10:
        raise
    if mm.get_oracle_price("USDT", "DOT") != 0.1:
        raise
    with pytest.raises(Exception):
        mm.get_oracle_price("DOT", "ETH")


def test_is_liquidatable():
    agent = Agent()
    cdp = CDP("USDT", "DOT", 1000, 200)
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): 10},
        cdps=[(agent, cdp)]
    )
    if mm.is_liquidatable(cdp):
        raise
    cdp.collateral_amt = 100
    if not mm.is_liquidatable(cdp):
        raise


def test_liquidate():
    debt_amt = 1000
    collat_amt = 100
    borrow_agent = Agent()
    cdp = CDP("USDT", "DOT", debt_amt, collat_amt)
    collat_price = 11
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): collat_price},
        cdps=[(borrow_agent, cdp)],
        liquidation_threshold=0.7,
        liquidation_penalty=0.01,
    )
    liquidate_amt = debt_amt / 2
    collat_sold_expected = liquidate_amt / collat_price * 1.01
    agent = Agent(holdings={"USDT": liquidate_amt})
    mm.liquidate(cdp, agent, liquidate_amt)
    if debt_amt - liquidate_amt != cdp.debt_amt:
        raise
    if agent.holdings["USDT"] != 0:
        raise
    if agent.holdings["DOT"] != collat_sold_expected:
        raise
    if cdp.collateral_amt != collat_amt - collat_sold_expected:
        raise
    if not mm.validate():
        raise


def test_liquidate_not_liquidatable():
    debt_amt = 1000
    collat_amt = 500
    borrow_agent = Agent()
    cdp = CDP("USDT", "DOT", debt_amt, collat_amt)
    collat_price = 11
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): collat_price},
        cdps=[(borrow_agent, cdp)],
        liquidation_threshold=0.7,
        liquidation_penalty=0.01,
    )
    liquidate_amt = debt_amt / 2
    agent = Agent(holdings={"USDT": liquidate_amt})
    mm.liquidate(cdp, agent, liquidate_amt)
    if debt_amt != cdp.debt_amt:
        raise
    if agent.holdings["USDT"] != liquidate_amt:
        raise
    if agent.is_holding("DOT"):
        raise
    if cdp.collateral_amt != collat_amt:
        raise
    if not mm.validate():
        raise


def test_liquidate_fails():
    debt_asset = "USDT"
    collateral_asset = "DOT"
    init_debt_amt = 1000
    init_collat_amt = 100
    borrow_agent = Agent()
    cdp = CDP(debt_asset, collateral_asset, init_debt_amt, init_collat_amt)
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): 11},
        cdps=[(borrow_agent, cdp.copy())]
    )
    agent = Agent(holdings={"USDT": 10000, "DOT": 10000})

    with pytest.raises(Exception):  # trying to liquidate more than debt amount, should fail
        mm.liquidate(cdp, agent, init_debt_amt * 2)

    # not enough collateral, should fail
    cdp = CDP(debt_asset, collateral_asset, init_debt_amt, init_collat_amt / 10)
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): 11},
        cdps=[(borrow_agent, cdp.copy())]
    )
    agent = Agent(holdings={"USDT": 10000, "DOT": 10000})

    with pytest.raises(Exception):
        mm.liquidate(cdp, agent, init_debt_amt)


def test_borrow():
    borrow_asset = "USDT"
    collateral_asset = "DOT"
    borrow_amt = 500
    collat_amt = 100
    agent = Agent(holdings={collateral_asset: collat_amt})
    mm = money_market(
        liquidity={borrow_asset: 1000000, collateral_asset: 1000000},
        oracles={("DOT", "USDT"): 11},
        liquidation_threshold=0.7,
    )
    mm.borrow(agent, borrow_asset, collateral_asset, borrow_amt, collat_amt)
    if agent.holdings[borrow_asset] != borrow_amt:
        raise
    if agent.is_holding(collateral_asset):
        raise
    if len(mm.cdps) != 1:
        raise
    cdp = mm.cdps[0][1]
    if cdp.debt_amt != borrow_amt:
        raise
    if cdp.collateral_amt != collat_amt:
        raise
    if cdp.debt_asset != borrow_asset:
        raise
    if cdp.collateral_asset != collateral_asset:
        raise
    if not mm.validate():
        raise

    agent2 = Agent(holdings={collateral_asset: collat_amt})
    mm.borrow(agent2, borrow_asset, collateral_asset, borrow_amt, collat_amt)
    if len(mm.cdps) != 2:
        raise
    if not mm.validate():
        raise


def test_borrow_fails():
    borrow_asset = "USDT"
    collateral_asset = "DOT"
    collat_amt = 100
    agent = Agent(holdings={collateral_asset: collat_amt})
    mm = money_market(
        liquidity={borrow_asset: 1000000, collateral_asset: 1000000},
        oracles={("DOT", "USDT"): 10, ("ETH", "UST"): 3000, ("ETH", "DOT"): 300},
        liquidation_threshold=0.7,
        min_ltv=0.6
    )
    borrow_amt = collat_amt * 10 * 0.65
    with pytest.raises(Exception):  # should fail because LTV is too low
        mm.borrow(agent, borrow_asset, collateral_asset, borrow_amt, collat_amt)
    borrow_amt = collat_amt * 10 * 0.50
    with pytest.raises(Exception):  # should fail because debt asset == collateral asset
        mm.borrow(agent, collateral_asset, collateral_asset, borrow_amt, collat_amt)
    with pytest.raises(Exception):  # should fail because collateral asset is not in agent holdings
        mm.borrow(agent, borrow_asset, "ETH", borrow_amt, collat_amt)
    with pytest.raises(Exception):  # should fail because borrow asset is not in liquidity
        mm.borrow(agent, "ETH", collateral_asset, borrow_amt, collat_amt)
    with pytest.raises(Exception):  # should fail because of missing oracle
        mm.borrow(agent, "BTC", collateral_asset, borrow_amt, collat_amt)


def test_repay():
    agent = Agent(holdings={"USDT": 1000})
    cdp = CDP("USDT", "DOT", 1000, 200)
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): 10},
        liquidation_threshold=0.7,
        cdps=[(agent, cdp)]
    )
    mm.repay(agent, 0)
    if agent.holdings["USDT"] != 0:
        raise
    if mm.borrowed["USDT"] != 0:
        raise
    if not mm.validate():
        raise


def test_repay_fails():
    agent = Agent(holdings={"USDT": 500})
    cdp = CDP("USDT", "DOT", 1000, 200)
    mm = money_market(
        liquidity={"USDT": 1000000, "DOT": 1000000},
        oracles={("DOT", "USDT"): 10},
        liquidation_threshold=0.7,
        cdps=[(agent, cdp)]
    )
    with pytest.raises(Exception):  # should fail because agent does not have enough funds
        mm.repay(agent, 0)


@given(
    st.floats(min_value=4, max_value=10),
    st.floats(min_value=200, max_value=200000)
)
def test_omnipool_liquidate_cdp(collat_ratio: float, collateral_amt: float):
    prices = {'DOT': 7, 'HDX': 0.02, 'USDT': 1, 'WETH': 2500, 'iBTC': 45000}

    assets = {
        'DOT': {'usd price': prices['DOT'], 'weight': mpf(0.40)},
        'HDX': {'usd price': prices['HDX'], 'weight': mpf(0.10)},
        'USDT': {'usd price': prices['USDT'], 'weight': mpf(0.30)},
        'WETH': {'usd price': prices['WETH'], 'weight': mpf(0.10)},
        'iBTC': {'usd price': prices['iBTC'], 'weight': mpf(0.10)}
    }

    lrna_price_usd = 35
    initial_omnipool_tvl = 20000000
    liquidity = {}
    lrna = {}

    for tkn, info in assets.items():
        liquidity[tkn] = initial_omnipool_tvl * info['weight'] / info['usd price']
        lrna[tkn] = initial_omnipool_tvl * info['weight'] / lrna_price_usd

    init_pool = OmnipoolState(
        tokens={
            tkn: {'liquidity': liquidity[tkn], 'LRNA': lrna[tkn]} for tkn in assets
        },
        preferred_stablecoin='USDT',
    )
    omnipool = init_pool.copy()

    debt_amt = collat_ratio * collateral_amt
    init_cdp = CDP('USDT', 'DOT', mpf(debt_amt), mpf(collateral_amt))
    cdp = init_cdp.copy()
    penalty = 0.01
    liquidation_amount = debt_amt
    init_agent = Agent(holdings={"USDT": mpf(0), "DOT": mpf(0)})
    treasury_agent = init_agent.copy()
    omnipool_liquidate_cdp(omnipool, cdp, treasury_agent, liquidation_amount, penalty)

    before_DOT = init_agent.holdings['DOT'] + init_cdp.collateral_amt + init_pool.liquidity['DOT']
    before_USDT = init_agent.holdings['USDT'] - init_cdp.debt_amt + init_pool.liquidity['USDT']
    final_DOT = treasury_agent.holdings['DOT'] + cdp.collateral_amt + omnipool.liquidity['DOT']
    final_USDT = treasury_agent.holdings['USDT'] - cdp.debt_amt + omnipool.liquidity['USDT']
    if before_DOT != pytest.approx(final_DOT, rel=1e-20):  # check that total collateral asset amounts are correct
        raise
    if before_USDT != pytest.approx(final_USDT, rel=1e-20):  # check that total debt asset amounts are correct
        raise

    cdp.validate()

    if treasury_agent.holdings['DOT'] != pytest.approx(
            penalty * (omnipool.liquidity['DOT'] - init_pool.liquidity['DOT']), rel=1e-15):
        raise  # check that penalty amount to treasury is correct
    for tkn in treasury_agent.holdings:
        if tkn != cdp.collateral_asset and treasury_agent.holdings[tkn] != 0:
            raise  # treasury_agent should accrue no other token


def test_omnipool_liquidate_cdp_delta_debt_too_large():
    collat_ratio = 5.0
    collateral_amt = 200
    prices = {'DOT': 7, 'HDX': 0.02, 'USDT': 1, 'WETH': 2500, 'iBTC': 45000}

    assets = {
        'DOT': {'usd price': prices['DOT'], 'weight': mpf(0.40)},
        'HDX': {'usd price': prices['HDX'], 'weight': mpf(0.10)},
        'USDT': {'usd price': prices['USDT'], 'weight': mpf(0.30)},
        'WETH': {'usd price': prices['WETH'], 'weight': mpf(0.10)},
        'iBTC': {'usd price': prices['iBTC'], 'weight': mpf(0.10)}
    }

    lrna_price_usd = 35
    initial_omnipool_tvl = 20000000
    liquidity = {}
    lrna = {}

    for tkn, info in assets.items():
        liquidity[tkn] = initial_omnipool_tvl * info['weight'] / info['usd price']
        lrna[tkn] = initial_omnipool_tvl * info['weight'] / lrna_price_usd

    init_pool = OmnipoolState(
        tokens={
            tkn: {'liquidity': liquidity[tkn], 'LRNA': lrna[tkn]} for tkn in assets
        },
        preferred_stablecoin='USDT',
    )
    omnipool = init_pool.copy()

    debt_amt = collat_ratio * collateral_amt
    init_cdp = CDP('USDT', 'DOT', mpf(debt_amt), mpf(collateral_amt))
    cdp = init_cdp.copy()
    penalty = 0.01
    init_agent = Agent(holdings={"USDT": mpf(0), "DOT": mpf(0)})
    treasury_agent = init_agent.copy()
    with pytest.raises(Exception):  # liquidation should fail because delta_debt > cdp.debt_amt
        omnipool_liquidate_cdp(omnipool, cdp, treasury_agent, cdp.debt_amt * 1.01, penalty)


@given(st.floats(min_value=4, max_value=6))
def test_find_partial_liquidation_amount_full(collat_ratio: float):
    prices = {'DOT': 7, 'HDX': 0.02, 'USDT': 1, 'WETH': 2500, 'iBTC': 45000}

    assets = {
        'DOT': {'usd price': prices['DOT'], 'weight': 0.40},
        'HDX': {'usd price': prices['HDX'], 'weight': 0.10},
        'USDT': {'usd price': prices['USDT'], 'weight': 0.30},
        'WETH': {'usd price': prices['WETH'], 'weight': 0.10},
        'iBTC': {'usd price': prices['iBTC'], 'weight': 0.10}
    }

    lrna_price_usd = 35
    initial_omnipool_tvl = 20000000
    liquidity = {}
    lrna = {}

    for tkn, info in assets.items():
        liquidity[tkn] = initial_omnipool_tvl * info['weight'] / info['usd price']
        lrna[tkn] = initial_omnipool_tvl * info['weight'] / lrna_price_usd

    omnipool = OmnipoolState(
        tokens={
            tkn: {'liquidity': liquidity[tkn], 'LRNA': lrna[tkn]} for tkn in assets
        },
        preferred_stablecoin='USDT',
    )

    collateral_amt = 200
    debt_amt = collat_ratio * collateral_amt
    cdp = CDP('USDT', 'DOT', debt_amt, collateral_amt)
    penalty = 0.01

    liquidation_amount = find_partial_liquidation_amount(omnipool, cdp, penalty)
    if liquidation_amount != debt_amt:
        raise  # liquidation should be full


@given(st.floats(min_value=6, max_value=6.9))
def test_find_partial_liquidation_amount_partial(collat_ratio: float):
    prices = {'DOT': 7, 'HDX': 0.02, 'USDT': 1, 'WETH': 2500, 'iBTC': 45000}

    assets = {
        'DOT': {'usd price': prices['DOT'], 'weight': 0.40},
        'HDX': {'usd price': prices['HDX'], 'weight': 0.10},
        'USDT': {'usd price': prices['USDT'], 'weight': 0.30},
        'WETH': {'usd price': prices['WETH'], 'weight': 0.10},
        'iBTC': {'usd price': prices['iBTC'], 'weight': 0.10}
    }

    lrna_price_usd = 35
    initial_omnipool_tvl = mpf(20000000)
    liquidity = {}
    lrna = {}

    for tkn, info in assets.items():
        liquidity[tkn] = initial_omnipool_tvl * info['weight'] / info['usd price']
        lrna[tkn] = initial_omnipool_tvl * info['weight'] / lrna_price_usd

    omnipool = OmnipoolState(
        tokens={
            tkn: {'liquidity': liquidity[tkn], 'LRNA': lrna[tkn]} for tkn in assets
        },
        preferred_stablecoin='USDT',
    )

    collateral_amt = 200000
    debt_amt = collat_ratio * collateral_amt
    cdp = CDP('USDT', 'DOT', debt_amt, collateral_amt)
    penalty = 0.01

    liquidation_amount = find_partial_liquidation_amount(omnipool, cdp, penalty, 100)
    if liquidation_amount >= cdp.debt_amt:
        raise  # liquidation should be partial
    if liquidation_amount == 0:
        raise  # liquidation should happen

    omnipool_copy = omnipool.copy()
    cdp_copy = cdp.copy()
    treasury_agent = Agent(holdings={"USDT": mpf(0), "DOT": mpf(0)})
    omnipool_liquidate_cdp(omnipool_copy, cdp_copy, treasury_agent, liquidation_amount, penalty)

    collat_penalty = treasury_agent.holdings[cdp.collateral_asset]
    collat_sold = cdp.collateral_amt - cdp_copy.collateral_amt
    if collat_penalty != pytest.approx(penalty * (collat_sold - collat_penalty), rel=1e-15):
        raise

    debt_liquidated = cdp.debt_amt - cdp_copy.debt_amt
    exec_price = debt_liquidated / collat_sold

    if exec_price != pytest.approx(collat_ratio, rel=1e-12):
        raise  # execution price should be equal to collateral ratio


@given(
    st.floats(min_value=4, max_value=10),
    st.floats(min_value=200, max_value=200000),
    st.floats(min_value=0.0, max_value=1.0),
    st.booleans()
)
def test_find_partial_liquidation_amount(collat_ratio: float, collateral_amt: float, min_amt: float, invert: bool):
    prices = {'DOT': 7, 'HDX': 0.02, 'USDT': 1, 'WETH': 2500, 'iBTC': 45000}

    assets = {
        'DOT': {'usd price': prices['DOT'], 'weight': mpf(0.40)},
        'HDX': {'usd price': prices['HDX'], 'weight': mpf(0.10)},
        'USDT': {'usd price': prices['USDT'], 'weight': mpf(0.30)},
        'WETH': {'usd price': prices['WETH'], 'weight': mpf(0.10)},
        'iBTC': {'usd price': prices['iBTC'], 'weight': mpf(0.10)}
    }

    lrna_price_usd = 35
    initial_omnipool_tvl = 20000000
    liquidity = {}
    lrna = {}

    for tkn, info in assets.items():
        liquidity[tkn] = initial_omnipool_tvl * info['weight'] / info['usd price']
        lrna[tkn] = initial_omnipool_tvl * info['weight'] / lrna_price_usd

    omnipool = OmnipoolState(
        tokens={
            tkn: {'liquidity': liquidity[tkn], 'LRNA': lrna[tkn]} for tkn in assets
        },
        preferred_stablecoin='USDT',
    )

    if invert:
        collateral_asset = 'USDT'
        debt_asset = 'DOT'
        collat_ratio = 1 / collat_ratio
    else:
        collateral_asset = 'DOT'
        debt_asset = 'USDT'

    debt_amt = collat_ratio * collateral_amt
    cdp = CDP(debt_asset, collateral_asset, debt_amt, collateral_amt)
    penalty = 0.01

    liquidation_amount = find_partial_liquidation_amount(omnipool, cdp, penalty, 100, min_amt)
    if liquidation_amount > cdp.debt_amt:
        raise
    if liquidation_amount < min_amt and liquidation_amount != 0:
        raise
    if omnipool.buy_spot(cdp.debt_asset, cdp.collateral_asset) > (cdp.collateral_amt * (1 + penalty)) / cdp.debt_amt:
        if liquidation_amount != 0:
            raise

    omnipool_copy = omnipool.copy()
    cdp_copy = cdp.copy()
    treasury_agent = Agent(holdings={debt_asset: mpf(0), collateral_asset: mpf(0)})
    omnipool_liquidate_cdp(omnipool_copy, cdp_copy, treasury_agent, liquidation_amount, penalty)

    collat_penalty = treasury_agent.holdings[cdp.collateral_asset]
    collat_sold = cdp.collateral_amt - cdp_copy.collateral_amt
    if collat_penalty != pytest.approx(penalty * (collat_sold - collat_penalty), rel=1e-15):
        raise

    if liquidation_amount > 0:
        debt_liquidated = cdp.debt_amt - cdp_copy.debt_amt
        exec_price = debt_liquidated / collat_sold

        if cdp_copy.debt_amt > 0 and exec_price != pytest.approx(collat_ratio, rel=1e-12):
            raise  # if liquidation is partial, execution price should be equal to collateral ratio


def test_liquidate_against_omnipool():
    prices = {'DOT': 7, 'HDX': 0.02, 'USDT': 1, 'WETH': 2500, 'iBTC': 45000}

    assets = {
        'DOT': {'usd price': prices['DOT'], 'weight': mpf(0.40)},
        'HDX': {'usd price': prices['HDX'], 'weight': mpf(0.10)},
        'USDT': {'usd price': prices['USDT'], 'weight': mpf(0.30)},
        'WETH': {'usd price': prices['WETH'], 'weight': mpf(0.10)},
        'iBTC': {'usd price': prices['iBTC'], 'weight': mpf(0.10)}
    }

    lrna_price_usd = 35
    initial_omnipool_tvl = 20000000
    liquidity = {}
    lrna = {}

    for tkn, info in assets.items():
        liquidity[tkn] = initial_omnipool_tvl * info['weight'] / info['usd price']
        lrna[tkn] = initial_omnipool_tvl * info['weight'] / lrna_price_usd

    oracles = {('DOT', 'USDT'): prices['DOT']}

    init_pool = OmnipoolState(
        tokens={
            tkn: {'liquidity': liquidity[tkn], 'LRNA': lrna[tkn]} for tkn in assets
        },
        preferred_stablecoin='USDT',
    )

    # should be fully liquidated
    cdp1 = CDP('USDT', 'DOT', 1000, 200)
    # should be partially liquidated
    cdp2 = CDP('USDT', 'DOT', 1000000, 1000000 / 6.5)
    # should be fully liquidated
    cdp3 = CDP('DOT', 'USDT', 100, 1000)
    cdps = [cdp1, cdp2, cdp3]
    agents = {"treasury": Agent(holdings={"USDT": 0, "DOT": 0})}
    init_state = GlobalState(
        agents=agents,
        pools={'omnipool': init_pool},
        cdps=cdps,
        liquidation_penalty=0.02,
        external_oracle=oracles
    )

    transform_fn = liquidate_against_omnipool("omnipool", "treasury")
    transform_fn(init_state)

    if cdp1.debt_amt != 0:
        raise  # should be fully liquidated
    if cdp2.debt_amt == 0 or cdp2.debt_amt == 1000000:
        raise  # should be partially liquidated
    if cdp3.debt_amt != 0:
        raise  # should be fully liquidated
    if len(init_state.cdps) != 3:
        raise


def test_liquidate_against_omnipool_and_settle_otc():
    prices = {'DOT': 7, 'HDX': 0.02, 'USDT': 1, 'WETH': 2500, 'iBTC': 45000}

    assets = {
        'DOT': {'usd price': prices['DOT'], 'weight': mpf(0.40)},
        'HDX': {'usd price': prices['HDX'], 'weight': mpf(0.10)},
        'USDT': {'usd price': prices['USDT'], 'weight': mpf(0.30)},
        'WETH': {'usd price': prices['WETH'], 'weight': mpf(0.10)},
        'iBTC': {'usd price': prices['iBTC'], 'weight': mpf(0.10)}
    }

    lrna_price_usd = 35
    initial_omnipool_tvl = 20000000
    liquidity = {}
    lrna = {}

    for tkn, info in assets.items():
        liquidity[tkn] = initial_omnipool_tvl * info['weight'] / info['usd price']
        lrna[tkn] = initial_omnipool_tvl * info['weight'] / lrna_price_usd

    init_pool = OmnipoolState(
        tokens={
            tkn: {'liquidity': liquidity[tkn], 'LRNA': lrna[tkn]} for tkn in assets
        },
        preferred_stablecoin='USDT',
    )

    # should be fully liquidated
    cdp1 = CDP('USDT', 'DOT', 1000, 200, True)
    # can't be liquidated
    # cdp2 = CDP('USDT', 'DOT', 1000, 200, False)

    # should be executed and removed
    otc1 = OTC('DOT', 'USDT', 120, 12, partially_fillable=True)
    # should not be executed
    otc2 = OTC('DOT', 'USDT', 120, 5, partially_fillable=True)

    cdps = [cdp1]
    otcs = [otc1, otc2]
    agents = {"treasury": Agent(holdings={"USDT": 0, "DOT": 0})}
    init_state = GlobalState(
        agents=agents, pools={'omnipool': init_pool}, cdps=cdps, otcs=otcs, liquidation_penalty=0.02)

    transform_fn = liquidate_against_omnipool_and_settle_otc("omnipool", "treasury")
    transform_fn(init_state)

    if cdp1.debt_amt != 0:
        raise  # should be fully liquidated
    # if cdp2.debt_amt != 1000:
    #     raise  # shouldn't be liquidated at all
    if otc1.sell_amount != 0:
        raise  # should be completed
    if otc2.sell_amount != 120:
        raise  # shouldn't be traded
    if len(init_state.cdps) != 2:
        raise
    if len(init_state.otcs) != 1:
        raise
