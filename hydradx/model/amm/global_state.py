from .agents import Agent
import copy
import random
from .amm import AMM


class GlobalState:
    def __init__(self, agents: dict[str: Agent], pools: dict[str: AMM], external_market: dict[str: float] = {}):
        # get a list of all assets contained in any member of the state
        self.asset_list = list(set(
            [asset for pool in pools.values() for asset in pool.liquidity.keys()]
            + [asset for agent in agents.values() for asset in agent.asset_list]
            + list(external_market.keys())
        ))
        self.agents = agents
        for agent_name in self.agents:
            self.agents[agent_name].unique_id = agent_name
        self.pools = pools
        for pool_name in self.pools:
            self.pools[pool_name].unique_id = pool_name
        self.external_market = external_market

    def price(self, asset: str):
        return self.external_market[asset] if asset in self.external_market else 0

    def copy(self):
        self_copy = copy.deepcopy(self)
        return self_copy


def fluctuate_prices(state: GlobalState, percent: float, bias: float):
    new_state = state.copy()
    for asset in new_state.external_market:
        new_state.external_market[asset] *= (
                1 / (1 + percent / 100)
                + random.random() * (1 / (1 + percent / 100) + percent / 100)
                + bias
        )
    return new_state


def swap(
    old_state: GlobalState,
    pool_id: str,
    agent_id: str,
    tkn_sell: str,
    tkn_buy: str,
    buy_quantity: float = 0,
    sell_quantity: float = 0
) -> GlobalState:
    new_state = old_state  # .copy()
    new_state.pools[pool_id], new_state.agents[agent_id] = new_state.pools[pool_id].swap(
        old_state=new_state.pools[pool_id],
        old_agent=new_state.agents[agent_id],
        tkn_sell=tkn_sell,
        tkn_buy=tkn_buy,
        buy_quantity=buy_quantity,
        sell_quantity=sell_quantity
    )
    return new_state


def add_liquidity(
    old_state: GlobalState,
    pool_id: str,
    agent_id: str,
    quantity: float,
    tkn_add: str
) -> GlobalState:
    new_state = old_state.copy()
    new_state.pools[pool_id], new_state.agents[agent_id] = new_state.pools[pool_id].add_liquidity(
        old_state=new_state.pools[pool_id],
        old_agent=new_state.agents[agent_id],
        quantity=quantity,
        tkn_add=tkn_add
    )
    return new_state


def remove_liquidity(
        old_state: GlobalState,
        pool_id: str,
        agent_id: str,
        quantity: float,
        tkn_remove: str
) -> GlobalState:
    new_state = old_state.copy()
    new_state.pools[pool_id], new_state.agents[agent_id] = new_state.pools[pool_id].remove_liquidity(
        old_state=new_state.pools[pool_id],
        old_agent=new_state.agents[agent_id],
        quantity=quantity,
        tkn_remove=tkn_remove
    )
    return new_state


def withdraw_all_liquidity(state: GlobalState, agent_id: str) -> GlobalState:
    agent = state.agents[agent_id]
    new_state = state
    for key in agent.shares.keys():
        # shares.keys might just be the pool name, or it might be a tuple (pool, token)
        if isinstance(key, tuple):
            pool_id = key[0]
            tkn = key[1]
        else:
            pool_id = key
            tkn = key
        new_state = remove_liquidity(new_state, pool_id, agent.unique_id, agent.shares[key], tkn_remove=tkn)

    return new_state