from .global_state import AMM
from .agents import Agent
from mpmath import mpf, mp
mp.dps = 50

# N_COINS = 2  # I think we cannot currently go higher than this
# ann means how concentrated the liquidity is;
# the higher the number, the less the price changes as the pool moves away from balance


class StableSwapPoolState(AMM):
    def __init__(self, tokens: dict, amplification: float, precision: float = 1):
        """
        Tokens should be in the form of:
        {
            token1: quantity,
            token2: quantity
        }
        There should only be two.
        """
        super().__init__()
        self.amplification = amplification
        self.precision = precision
        self.liquidity = dict()
        self.asset_list: list[str] = []

        for token, quantity in tokens.items():
            self.asset_list.append(token)
            self.liquidity[token] = mpf(quantity)

        self.shares = self.calculate_d()

    @property
    def ann(self):
        return self.amplification * len(self.asset_list) ** len(self.asset_list)

    def has_converged(self, v0, v1) -> bool:
        diff = abs(v0 - v1)
        if (v1 <= v0 and diff < self.precision) or (v1 > v0 and diff <= self.precision):
            return True
        return False

    def calculate_d(self, max_iterations=128):
        n_coins = len(self.asset_list)
        xp_sorted = sorted(self.liquidity.values())
        s = sum(xp_sorted)
        if s == 0:
            return 0

        d = s
        for i in range(max_iterations):

            d_p = d
            for x in xp_sorted:
                d_p *= d / (x * n_coins)

            d_prev = d
            d = (self.ann * s + d_p * n_coins) * d / ((self.ann - 1) * d + (n_coins + 1) * d_p) + 2

            if self.has_converged(d_prev, d):
                return d

    def calculate_y(self, reserve, d, max_iterations=128):
        s = reserve
        c = d
        c *= d / (2 * reserve)
        c *= d / (self.ann * len(self.liquidity.keys()))

        b = s + d / self.ann
        y = d
        for i in range(max_iterations):
            y_prev = y
            y = (y ** 2 + c) / (2 * y + b - d) + 2
            if self.has_converged(y_prev, y):
                return y

    # Calculate new amount of reserve OUT given amount to be added to the pool
    def calculate_y_given_in(
        self,
        amount: float,
        tkn_in: str,
    ) -> float:
        new_reserve_in = self.liquidity[tkn_in] + amount
        d = self.calculate_d()
        return self.calculate_y(new_reserve_in, d)

    # Calculate new amount of reserve IN given amount to be withdrawn from the pool
    def calculate_y_given_out(
            self,
            amount: float,
            tkn_out: str
    ) -> float:
        new_reserve_out = self.liquidity[tkn_out] - amount
        d = self.calculate_d()
        return self.calculate_y(new_reserve_out, d)

    def spot_price(self):
        x, y = self.liquidity.values()
        d = self.calculate_d()
        return (x / y) * (self.ann * x * y ** 2 + d ** 3) / (self.ann * x ** 2 * y + d ** 3)

    def execute_swap(
        self,
        old_agent: Agent,
        tkn_sell: str,
        tkn_buy: str,
        buy_quantity: float = 0,
        sell_quantity: float = 0
    ):
        if buy_quantity:
            sell_quantity = self.calculate_y_given_out(buy_quantity, tkn_buy)
        elif sell_quantity:
            buy_quantity = self.calculate_y_given_in(sell_quantity, tkn_sell)

        if old_agent.holdings[tkn_sell] - sell_quantity < 0:
            return self.fail_transaction('Agent has insufficient funds.')
        elif self.liquidity[tkn_buy] <= buy_quantity:
            return self.fail_transaction('Pool has insufficient liquidity.')

        agent = old_agent
        agent.holdings[tkn_buy] += buy_quantity
        agent.holdings[tkn_sell] -= sell_quantity
        self.liquidity[tkn_buy] -= buy_quantity
        self.liquidity[tkn_sell] += sell_quantity

        return self, agent


def swap(
    old_state: StableSwapPoolState,
    old_agent: Agent,
    tkn_sell: str,
    tkn_buy: str,
    buy_quantity: float = 0,
    sell_quantity: float = 0
):
    return old_state.copy().execute_swap(old_agent.copy(), tkn_sell, tkn_buy, buy_quantity, sell_quantity)


def add_liquidity(
    old_state: StableSwapPoolState,
    old_agent: Agent,
    quantity: float,
    tkn_add: str
):
    initial_d = old_state.calculate_d()
    new_state = old_state.copy()
    new_agent = old_agent.copy()

    for token in old_state.asset_list:
        delta_r = quantity * old_state.liquidity[token] / old_state.liquidity[tkn_add]
        new_agent.holdings[token] -= delta_r
        new_state.liquidity[token] += delta_r

    updated_d = new_state.calculate_d()

    if updated_d < initial_d:
        return None

    if old_state.shares == 0:
        new_agent.shares[new_state.unique_id] = updated_d
        new_state.shares = updated_d

    else:
        d_diff = updated_d - initial_d
        share_amount = old_state.shares * d_diff / initial_d
        new_state.shares += share_amount
        new_agent.shares[new_state.unique_id] += share_amount

    return new_state, new_agent


def remove_liquidity(
    old_state: StableSwapPoolState,
    old_agent: Agent,
    quantity: float,
    tkn_remove: str
):
    if quantity > old_agent.shares:
        raise ValueError('Agent tried to remove more shares than it owns.')

    share_fraction = quantity / old_state.shares
    new_state = old_state.copy()
    new_agent = old_agent.copy()
    new_state.shares -= quantity
    new_agent.shares -= quantity
    for tkn in new_state.asset_list:
        new_agent.holdings[tkn] += old_state.liquidity[tkn] * share_fraction
        new_state.liquidity[tkn] -= old_state.liquidity[tkn] * share_fraction
    return new_state, new_agent


StableSwapPoolState.add_liquidity = staticmethod(add_liquidity)
StableSwapPoolState.remove_liquidity = staticmethod(remove_liquidity)
StableSwapPoolState.swap = staticmethod(swap)

# def calculate_asset_b_required(reserve_a, reserve_b, delta_a):
#     updated_reserve_a = reserve_a + delta_a
#     updated_reserve_b = updated_reserve_a * reserve_b / reserve_a


#
#
# reserves = [1000000000, 100000000]
# ann = 4 * 10
# d = calculate_d(reserves, ann)
# print(f'spot price at {reserves}: {spot_price(reserves, d, ann)}')
#
# # test that calculate_d and calculate_y are consistent
# ann = 400
# reserve_a = 100000000
# reserve_b = 200000000
# d = calculate_d([reserve_a, reserve_b], ann)
# y = calculate_y(reserve_b, d, ann)
#
# # fix value, i.e. fix p_x^y * x + y
# D = 200000000
# x_step_size = 500000
# x_min = 10000000
# x_max = 200000000
# liq_depth = {}

