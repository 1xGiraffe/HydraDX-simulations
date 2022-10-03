from .global_state import AMM
from .agents import Agent
from mpmath import mpf, mp
mp.dps = 50

# N_COINS = 2  # I think we cannot currently go higher than this
# ann means how concentrated the liquidity is;
# the higher the number, the less the price changes as the pool moves away from balance


class StableSwapPoolState(AMM):
    unique_id: str = 'stableswap'

    def __init__(self, tokens: dict, amplification: float, precision: float = 0.0001, trade_fee: float = 0):
        """
        Tokens should be in the form of:
        {
            token1: quantity,
            token2: quantity
        }
        There can be up to five.
        """
        super().__init__()
        if len(tokens.keys()) > 5:
            raise ValueError('Too many tokens (limit 5)')

        self.amplification = amplification
        self.precision = precision
        self.liquidity = dict()
        self.asset_list: list[str] = []
        self.trade_fee = trade_fee

        for token, quantity in tokens.items():
            self.asset_list.append(token)
            self.liquidity[token] = mpf(quantity)

        self.shares = self.calculate_d()
        self.d = self.calculate_d()

    @property
    def ann(self) -> float:
        return self.amplification * len(self.asset_list) ** len(self.asset_list)

    @property
    def n_coins(self) -> int:
        return len(self.asset_list)

    def has_converged(self, v0, v1) -> bool:
        diff = abs(v0 - v1)
        if (v1 <= v0 and diff < self.precision) or (v1 > v0 and diff <= self.precision):
            return True
        return False

    def calculate_d(self, reserves=(), max_iterations=128) -> float:
        reserves = reserves or self.liquidity.values()
        xp_sorted = sorted(reserves)
        s = sum(xp_sorted)
        if s == 0:
            return 0

        d = s
        for i in range(max_iterations):

            d_p = d
            for x in xp_sorted:
                d_p *= d / (x * self.n_coins)

            d_prev = d
            d = (self.ann * s + d_p * self.n_coins) * d / ((self.ann - 1) * d + (self.n_coins + 1) * d_p)

            if self.has_converged(d_prev, d):
                return d

    # def calculate_y(self, reserve, d, max_iterations=128):
    #     s = reserve
    #     c = d
    #     c *= d / 2 / reserve
    #     c *= d / self.ann / self.n_coins
    #
    #     b = s + d / self.ann
    #     y = d
    #     for i in range(max_iterations):
    #         y_prev = y
    #         y = (y ** 2 + c) / (2 * y + b - d)
    #         if self.has_converged(y_prev, y):
    #             return y

    """
    Given a value for D and the balances of all tokens except 1, calculate what the balance of the final token should be
    """
    def calculate_y(self, reserves: list, d: float, max_iterations=128):

        # get all the balances except tkn_out and sort them from low to high
        balances = sorted(reserves)

        s = sum(balances)
        c = d
        for reserve in balances:
            c *= d / reserve / self.n_coins
        c *= d / self.ann / self.n_coins

        b = s + d / self.ann
        y = d

        for _ in range(max_iterations):
            y_prev = y
            y = (y ** 2 + c) / (2 * y + b - d)
            # y.checked_mul(y)?
            # .checked_add(c)?
            # .checked_div(two_hp.checked_mul(y)?.checked_add(b)?.checked_sub(d_hp)?)?
            # .checked_add(two_hp)?;

            if self.has_converged(y_prev, y):
                # [cfg(not(feature = "runtime-benchmarks"))]
                return y

        return y

    @property
    def spot_price(self):
        x, y = self.liquidity.values()
        d = self.d
        return self.price_at_balance([x, y], d)

    def price_at_balance(self, balances: list, d: float):
        x, y = balances
        return (x / y) * (self.ann * x * y ** 2 + d ** 3) / (self.ann * x ** 2 * y + d ** 3)

    def modified_balances(self, delta: dict, omit: list = ()):
        balances = {key: value for key, value in self.liquidity.items()}
        for tkn, value in delta.items():
            balances[tkn] += value
        for tkn in omit:
            balances.pop(tkn)
        return list(balances.values())

    def execute_swap(
        self,
        old_agent: Agent,
        tkn_sell: str,
        tkn_buy: str,
        buy_quantity: float = 0,
        sell_quantity: float = 0
    ):
        d = self.calculate_d()
        if buy_quantity:
            reserves = self.modified_balances(delta={tkn_buy: -buy_quantity}, omit=[tkn_sell])
            sell_quantity = (self.calculate_y(reserves, d) - self.liquidity[tkn_sell]) * (1 + self.trade_fee)
        elif sell_quantity:
            reserves = self.modified_balances(delta={tkn_sell: sell_quantity}, omit=[tkn_buy])
            buy_quantity = (self.liquidity[tkn_buy] - self.calculate_y(reserves, d)) * (1 - self.trade_fee)

        if old_agent.holdings[tkn_sell] < sell_quantity:
            return self.fail_transaction('Agent has insufficient funds.'), old_agent
        elif self.liquidity[tkn_buy] <= buy_quantity:
            return self.fail_transaction('Pool has insufficient liquidity.'), old_agent

        new_agent = old_agent  # .copy()
        new_agent.holdings[tkn_buy] += buy_quantity
        new_agent.holdings[tkn_sell] -= sell_quantity
        self.liquidity[tkn_buy] -= buy_quantity
        self.liquidity[tkn_sell] += sell_quantity

        return self, new_agent

    def __repr__(self):
        return (
            f'Stable Swap Pool\n'
            f'base trade fee: {self.trade_fee}\n'
            f'shares: {self.shares}\n'
            f'amplification constant: {self.amplification}\n'
            f'tokens: (\n'
        ) + ')\n(\n'.join(
            [(
                f'    {token}\n'
                f'    quantity: {self.liquidity[token]}\n'
                f'    weight: {self.liquidity[token] / sum(self.liquidity.values())}\n'
            ) for token in self.asset_list]
        ) + '\n)\n' + (
            f'error message:{self.fail or "none"}'
        )


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
    quantity: float,  # quantity of asset to be added
    tkn_add: str
):
    initial_d = old_state.calculate_d()
    new_state = old_state.copy()
    new_agent = old_agent.copy()

    new_state.liquidity[tkn_add] += quantity
    new_agent.holdings[tkn_add] -= quantity

    updated_d = new_state.calculate_d()

    if updated_d < initial_d:
        return old_state.fail_transaction('invariant decreased for some reason'), old_agent

    if old_state.shares == 0:
        new_agent.shares[new_state.unique_id] = updated_d
        new_state.shares = updated_d

    else:
        d_diff = updated_d - initial_d
        share_amount = old_state.shares * d_diff / initial_d
        new_state.shares += share_amount
        if new_state.unique_id not in new_agent.shares:
            new_agent.shares[new_state.unique_id] = 0
        new_agent.shares[new_state.unique_id] += share_amount

    return new_state, new_agent


def remove_liquidity(
    old_state: StableSwapPoolState,
    old_agent: Agent,
    quantity: float,  # in this case, quantity refers to a number of shares, not quantity of asset
    tkn_remove: str
):
    if quantity > old_agent.shares[old_state.unique_id]:
        raise ValueError('Agent tried to remove more shares than it owns.')

    share_fraction = quantity / old_state.shares
    new_state = old_state.copy()
    new_agent = old_agent.copy()
    new_state.shares -= quantity
    new_agent.shares[old_state.unique_id] -= quantity
    new_state.d = new_state.calculate_d(new_state.liquidity.values()) * (1 - share_fraction)
    delta_tkn = new_state.calculate_y(
        new_state.modified_balances(delta={}, omit=[tkn_remove]),
        new_state.d
    ) - new_state.liquidity[tkn_remove]
    delta_tkn *= (1 - new_state.trade_fee)
    new_state.liquidity[tkn_remove] += delta_tkn
    new_agent.holdings[tkn_remove] -= delta_tkn  # agent is receiving funds, because delta_tkn is a negative number
    return new_state, new_agent


StableSwapPoolState.add_liquidity = staticmethod(add_liquidity)
StableSwapPoolState.remove_liquidity = staticmethod(remove_liquidity)
StableSwapPoolState.swap = staticmethod(swap)