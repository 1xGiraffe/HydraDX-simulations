import copy
import string
from .agents import Agent
from .global_state import AMM


class OmnipoolState(AMM):
    unique_id: str = 'omnipool'

    def __init__(self,
                 tokens: dict[str: dict],
                 tvl_cap: float = float('inf'),
                 preferred_stablecoin: str = "USD",
                 asset_fee: float = 0,
                 lrna_fee: float = 0
                 ):
        """
        tokens should be a dict in the form of [str: dict]
        the nested dict needs the following parameters:
        {
          'liquidity': float  # starting risk asset liquidity in the pool
          (
          'LRNA': float  # starting LRNA on the other side of the pool
          or
          'LRNA_price': float  # price of the asset denominated in LRNA
          )

          optional:
          'weight_cap': float  # maximum fraction of TVL that may be held in this pool
        }
        """

        super().__init__()

        if 'HDX' not in tokens:
            raise ValueError('HDX not included in tokens.')
        if preferred_stablecoin not in tokens:
            raise ValueError(f'{preferred_stablecoin} is preferred stablecoin, but not included in tokens.')

        self.asset_list: list[str] = []
        self.liquidity = {}
        self.lrna = {}
        self.shares = {}
        self.protocol_shares = {}
        self.weight_cap = {}
        for token, pool in tokens.items():
            assert pool['liquidity'], f'token {token} missing required parameter: liquidity'
            self.asset_list.append(token)
            self.liquidity[token] = (pool['liquidity'])
            self.shares[token] = (pool['liquidity'])
            self.protocol_shares[token] = (pool['liquidity'])
            self.weight_cap[token] = (pool['weight_cap'] if 'weight_cap' in pool else 1)
            if 'LRNA' in pool:
                self.lrna[token] = (pool['LRNA'])
            elif 'LRNA_price' in pool:
                self.lrna[token] = pool['liquidity'] / pool['LRNA_price']
            else:
                raise ValueError("token {name} missing required parameter: ('LRNA' or 'LRNA_price)")

        self.asset_fee = asset_fee
        self.lrna_fee = lrna_fee
        self.lrna_imbalance = 0  # AKA "L"
        self.tvl_cap = tvl_cap
        self.stablecoin = preferred_stablecoin
        self.fail = ''

    def price(self, i: str):
        """
        price of an asset in USD, according to current market conditions in the omnipool
        """
        return self.lrna[i] / self.liquidity[i] / self.lrna[self.stablecoin] * self.liquidity[self.stablecoin]

    @property
    def lrna_price(self) -> dict[str: float]:
        """
        price of asset i in LRNA
        """
        return {i: self.lrna[i] / self.liquidity[i] for i in self.asset_list}

    @property
    def lrna_total(self):
        return sum(self.lrna.values())

    @property
    def tvl_total(self):
        # base this just on the LRNA/USD exchange rate in the pool
        return self.liquidity[self.stablecoin] * self.lrna_total / self.lrna[self.stablecoin]

    def copy(self):
        copy_state = copy.deepcopy(self)
        copy_state.fail = ''
        return copy_state

    def __repr__(self):
        return (
            f'Omnipool\n'
            f'tvl cap: {self.tvl_cap}\n'
            f'lrna fee: {self.lrna_fee}\n'
            f'asset fee: {self.asset_fee}\n'
            f'asset pools: (\n'
        ) + ')\n(\n'.join(
            [(
                f'    {token}\n'
                f'    asset quantity: {self.liquidity[token]}\n'
                f'    lrna quantity: {self.lrna[token]}\n'
                f'    USD price: {self.price(token)}\n'
                f'    tvl: {self.lrna[token] * self.liquidity[self.stablecoin] / self.lrna[self.stablecoin]}\n'
                f'    weight: {self.lrna[token]}/{self.lrna_total} ({self.lrna[token] / self.lrna_total})\n'
                f'    weight cap: {self.weight_cap[token]}\n'
                f'    total shares: {self.shares[token]}\n'
                f'    protocol shares: {self.protocol_shares[token]}\n'
            ) for token in self.asset_list]
        ) + '\n)'


def asset_invariant(state: OmnipoolState, i: str) -> float:
    """Invariant for specific asset"""
    return state.liquidity[i] * state.lrna[i]


def swap_lrna_delta_Qi(state: OmnipoolState, delta_ri: float, i: str) -> float:
    return state.lrna[i] * (- delta_ri / (state.liquidity[i] + delta_ri))


def swap_lrna_delta_Ri(state: OmnipoolState, delta_qi: float, i: str) -> float:
    return state.liquidity[i] * (- delta_qi / (state.lrna[i] + delta_qi))


def weight_i(state: OmnipoolState, i: str) -> float:
    return state.lrna[i] / state.lrna_total


def lrna_price(state: OmnipoolState, i: str, fee: float = 0) -> float:
    """Price of i denominated in LRNA"""
    if state.liquidity[i] == 0:
        return 0
    else:
        return (state.lrna[i] / state.liquidity[i]) * (1 - fee)


def swap_lrna(
        old_state: OmnipoolState,
        old_agent: Agent,
        delta_ra: float = 0,
        delta_qa: float = 0,
        tkn: str = ''
) -> tuple[OmnipoolState, Agent]:
    """Compute new state after LRNA swap"""

    new_state = old_state.copy()
    new_agent = old_agent.copy()

    if delta_qa < 0:
        delta_Q = -delta_qa
        delta_R = old_state.liquidity[tkn] * -delta_Q / (delta_Q + old_state.lrna[tkn]) * (1 - old_state.asset_fee)
        delta_ra = -delta_R
    elif delta_ra > 0:
        delta_R = -delta_ra
        delta_Q = old_state.lrna[tkn] * -delta_R / (old_state.liquidity[tkn] * (1 - old_state.asset_fee) + delta_R)
        delta_qa = -delta_Q
    else:
        return old_state.fail_transaction('Buying LRNA not implemented.'), old_agent

    if delta_qa + old_agent.holdings['LRNA'] < 0:
        return old_state.fail_transaction("agent doesn't have enough lrna"), old_agent
    elif delta_ra + old_agent.holdings[tkn] < 0:
        return old_state.fail_transaction(f"agent doesn't have enough {tkn} holdings"), old_agent
    elif delta_R + old_state.liquidity[tkn] <= 0:
        return old_state.fail_transaction('insufficient assets in pool'), old_agent
    elif delta_Q + old_state.lrna[tkn] <= 0:
        return old_state.fail_transaction('insufficient lrna in pool'), old_agent

    new_agent.holdings['LRNA'] += delta_qa
    new_agent.holdings[tkn] += delta_ra
    new_state.lrna[tkn] += delta_Q
    new_state.liquidity[tkn] += delta_R
    new_state.lrna_imbalance = (
        new_state.lrna_total * new_state.liquidity[tkn] / new_state.lrna[tkn]
        * old_state.lrna[tkn] / old_state.liquidity[tkn]
        * (1 + old_state.lrna_imbalance / old_state.lrna_total) - new_state.lrna_total
    )

    return new_state, new_agent


def swap_assets_direct(
        old_state: OmnipoolState,
        old_agent: Agent,
        delta_token: float,
        tkn_buy: str,
        tkn_sell: str
) -> tuple[OmnipoolState, Agent]:
    i = tkn_sell
    j = tkn_buy
    delta_Ri = delta_token
    if delta_Ri <= 0:
        return old_state.fail_transaction('sell amount must be greater than zero'), old_agent

    delta_Qi = old_state.lrna[i] * -delta_Ri / (old_state.liquidity[i] + delta_Ri)
    delta_Qj = -delta_Qi * (1 - old_state.lrna_fee)
    delta_Rj = old_state.liquidity[j] * -delta_Qj / (old_state.lrna[j] + delta_Qj) * (1 - old_state.asset_fee)
    delta_L = min(-delta_Qi * old_state.lrna_fee, -old_state.lrna_imbalance)
    delta_QH = -old_state.lrna_fee * delta_Qi - delta_L

    new_state = old_state.copy()
    new_state.lrna[i] += delta_Qi
    new_state.lrna[j] += delta_Qj
    new_state.liquidity[i] += delta_Ri
    new_state.liquidity[j] += delta_Rj
    new_state.lrna['HDX'] += delta_QH
    new_state.lrna_imbalance += delta_L

    new_agent = old_agent.copy()
    new_agent.holdings[i] -= delta_Ri
    new_agent.holdings[j] -= delta_Rj

    if new_state.liquidity[i] > 10 ** 12:
        return old_state.fail_transaction('Asset liquidity cannot exceed 10 ^ 12.'), old_agent

    if new_agent.holdings[i] < 0 or new_agent.holdings[j] < 0:
        return (
            old_state.fail_transaction(f"Agent doesn't have enough {i if new_agent.holdings[i] < 0 else j}"),
            old_agent
        )

    return new_state, new_agent


def swap(
        old_state: OmnipoolState,
        old_agent: Agent,
        tkn_buy: str,
        tkn_sell: str,
        buy_quantity: float = 0,
        sell_quantity: float = 0
) -> tuple[OmnipoolState, Agent]:

    if tkn_sell == 'LRNA' or tkn_buy == 'LRNA':

        if tkn_sell == 'LRNA':
            delta_qa = sell_quantity or -buy_quantity
            delta_ra = buy_quantity or -sell_quantity
            tkn = tkn_buy

        else:  # tkn_buy == 'LRNA'
            delta_qa = sell_quantity or -buy_quantity
            delta_ra = buy_quantity or -sell_quantity
            tkn = tkn_sell

        new_state, new_agents = swap_lrna(
            old_state=old_state,
            old_agent=old_agent,
            delta_ra=delta_ra,
            delta_qa=delta_qa,
            tkn=tkn
        )

        return new_state, new_agents

    elif sell_quantity != 0:

        new_state, new_agents = swap_assets_direct(
            old_state=old_state,
            old_agent=old_agent,
            delta_token=sell_quantity,
            tkn_buy=tkn_buy,
            tkn_sell=tkn_sell,
        )

        return new_state, new_agents

    elif buy_quantity != 0:
        # back into correct delta_Ri, then execute sell
        delta_Qj = old_state.lrna[tkn_buy] * buy_quantity / (
                old_state.liquidity[tkn_buy] * (1 - old_state.asset_fee) - buy_quantity)
        delta_Qi = -delta_Qj / (1 - old_state.lrna_fee)
        delta_Ri = -old_state.liquidity[tkn_sell] * delta_Qi / (old_state.lrna[tkn_sell] + delta_Qi)
        return swap(
            old_state=old_state,
            old_agent=old_agent,
            tkn_buy=tkn_buy,
            tkn_sell=tkn_sell,
            sell_quantity=delta_Ri
        )

    else:
        raise


def add_liquidity(
        old_state: OmnipoolState,
        old_agent: Agent = None,
        quantity: float = 0,
        tkn_add: str = ''
) -> tuple[OmnipoolState, Agent]:
    """Compute new state after liquidity addition"""

    # assert quantity > 0, f"delta_R must be positive: {quantity}"
    assert tkn_add in old_state.asset_list, f"invalid value for i: {tkn_add}"

    new_state = old_state.copy()
    new_agent = old_agent.copy()

    # Token amounts update
    new_state.liquidity[tkn_add] += quantity

    if old_agent:
        new_agent.holdings[tkn_add] -= quantity
        if new_agent.holdings[tkn_add] < 0:
            # print('Transaction rejected because agent has insufficient funds.')
            # print(f'agent {LP_id}, asset {new_state["token_list"][i]}, amount {delta_R}')
            return old_state.fail_transaction(), old_agent

    # Share update
    if new_state.shares[tkn_add]:
        new_state.shares[tkn_add] *= new_state.liquidity[tkn_add] / old_state.liquidity[tkn_add]
    else:
        new_state.shares[tkn_add] = new_state.liquidity[tkn_add]

    if old_agent:
        # shares go to provisioning agent
        if not (new_state.unique_id, tkn_add) in new_agent.shares:
            new_agent.shares[(new_state.unique_id, tkn_add)] = 0
        new_agent.shares[(new_state.unique_id, tkn_add)] += new_state.shares[tkn_add] - old_state.shares[tkn_add]
    else:
        # shares go to protocol
        new_state.protocol_shares[tkn_add] += new_state.shares[tkn_add] - old_state.shares[tkn_add]

    # LRNA add (mint)
    delta_Q = lrna_price(old_state, tkn_add) * quantity
    new_state.lrna[tkn_add] += delta_Q

    # L update: LRNA fees to be burned before they will start to accumulate again
    delta_L = (
        quantity * old_state.lrna[tkn_add] / old_state.liquidity[tkn_add]
        * old_state.lrna_imbalance / old_state.lrna_total
    )
    new_state.lrna_imbalance += delta_L

    if new_state.lrna[tkn_add] / new_state.lrna_total > new_state.weight_cap[tkn_add]:
        return old_state.fail_transaction(
            'Transaction rejected because it would exceed the weight cap in pool[{i}].'
        ), old_agent

    if new_state.tvl_total > new_state.tvl_cap:

        return old_state.fail_transaction('Transaction rejected because it would exceed the TVL cap.'), old_agent

    if new_state.liquidity[tkn_add] > 10 ** 12:

        return old_state.fail_transaction('Asset liquidity cannot exceed 10 ^ 12.'), old_agent

    # set price at which liquidity was added
    if old_agent:
        new_agent.share_prices[(new_state.unique_id, tkn_add)] = new_state.lrna_price[tkn_add]

    return new_state, new_agent


def remove_liquidity(
        old_state: OmnipoolState,
        old_agent: Agent,
        quantity: float,
        tkn_remove: str
) -> tuple[OmnipoolState, Agent]:
    """Compute new state after liquidity removal"""
    quantity = -abs(quantity)
    assert quantity <= 0, f"delta_S cannot be positive: {quantity}"
    assert tkn_remove in old_state.asset_list, f"invalid token name: {tkn_remove}"

    new_state = old_state.copy()
    new_agent = old_agent.copy()

    if quantity == 0:
        return new_state, new_agent

    # determine if they should get some LRNA back as well as the asset they invested
    piq = old_state.lrna_price[tkn_remove]
    p0 = new_agent.share_prices[(new_state.unique_id, tkn_remove)]
    mult = (piq - p0) / (piq + p0)

    # Share update
    delta_B = max(mult * quantity, 0)
    new_state.protocol_shares[tkn_remove] += delta_B
    new_state.shares[tkn_remove] += quantity + delta_B
    new_agent.shares[(new_state.unique_id, tkn_remove)] += quantity

    # Token amounts update
    delta_R = old_state.liquidity[tkn_remove] * max((quantity + delta_B) / old_state.shares[tkn_remove], -1)
    new_state.liquidity[tkn_remove] += delta_R
    new_agent.holdings[tkn_remove] -= delta_R
    if piq >= p0:  # prevents rounding errors
        if 'LRNA' not in new_agent.holdings:
            new_agent.holdings['LRNA'] = 0
        new_agent.holdings['LRNA'] -= piq * (
            2 * piq / (piq + p0) * quantity / old_state.shares[tkn_remove]
            * old_state.liquidity[tkn_remove] - delta_R
        )

    # LRNA burn
    delta_Q = lrna_price(old_state, tkn_remove) * delta_R
    new_state.lrna[tkn_remove] += delta_Q

    # L update: LRNA fees to be burned before they will start to accumulate again
    delta_L = (
        delta_R * old_state.lrna[tkn_remove] / old_state.liquidity[tkn_remove]
        * old_state.lrna_imbalance / old_state.lrna_total
    )
    new_state.lrna_imbalance += delta_L

    return new_state, new_agent


OmnipoolState.swap = staticmethod(swap)
OmnipoolState.add_liquidity = staticmethod(add_liquidity)
OmnipoolState.remove_liquidity = staticmethod(remove_liquidity)
