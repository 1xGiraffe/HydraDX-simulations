import copy
import string

from ..amm import omnipool_amm as oamm


def initialize_LPs(state_d: dict, init_LPs: list) -> dict:
    agent_d = {}
    for i in range(len(init_LPs)):
        s = [0] * len(state_d['S'])
        s[i] = state_d['S'][i]
        agent_d[init_LPs[i]] = {
            's': s,  # simply assigning each LP all of an asset
            'h': 0  # no HDX shares to start
        }
    return agent_d


def initialize_state(init_d: dict) -> dict:
    state_d = oamm.initialize_pool_state(init_d)
    return state_d


def swap(old_state: dict, old_agents: dict, trade: dict) -> tuple:
    """Translates from user-friendly trade API to internal API

    swap['token_buy'] is the token being bought
    swap['tokens_sell'] is the list of tokens being sold
    swap['token_sell'] is the token being sold
    swap['amount_sell'] is the amount of the token being sold
    """
    assert trade['token_buy'] != trade['token_sell'], "Cannot trade a token for itself"

    i_buy = -1
    i_sell = -1
    if trade['token_buy'] != 'HDX':
        i_buy = old_state['token_list'].index(trade['token_buy'])
    if trade['token_sell'] != 'HDX':
        i_sell = old_state['token_list'].index(trade['token_sell'])

    if trade['token_sell'] == 'HDX':
        delta_Q = trade['amount_sell']
        delta_R = 0
    else:
        delta_Q = 0
        delta_R = trade['amount_sell']

    if i_buy < 0 or i_sell < 0:
        return oamm.swap_hdx(old_state, old_agents, trade['agent_id'], delta_R, delta_Q, max(i_sell, i_buy))
    else:
        return oamm.swap_assets(old_state, old_agents, trade['agent_id'], trade['amount_sell'], i_buy, i_sell)


def price_i(state: dict, i: int) -> float:
    return oamm.price_i(state, i)


def remove_liquidity(old_state: dict, old_agents: dict, transaction: dict) -> tuple:
    assert transaction['token_remove'] in old_state['token_list']
    agent_id = transaction['agent_id']
    shares_burn = transaction['shares_remove']
    i = old_state['token_list'].index(transaction['token_remove'])
    return oamm.remove_risk_liquidity(old_state, old_agents, agent_id, shares_burn, i)


def add_liquidity(old_state: dict, old_agents: dict, transaction: dict) -> tuple:
    assert transaction['token_add'] in old_state['token_list']
    agent_id = transaction['agent_id']
    amount_add = transaction['amount_add']
    i = old_state['token_list'].index(transaction['token_remove'])
    return oamm.add_risk_liquidity(old_state, old_agents, agent_id, amount_add, i)


def value_assets(state: dict, assets: dict) -> float:
    return assets['q'] + sum([assets['r'][i] * price_i(state, i) for i in range(len(state['R']))])


def withdraw_all_liquidity(state: dict, agent_d: dict, agent_id: string) -> tuple:
    n = len(state['R'])
    new_agents = {agent_id: agent_d}
    new_state = copy.deepcopy(state)

    for i in range(n):
        transaction = {
            'token_remove': 'R' + str(i + 1),
            'agent_id': agent_id,
            'shares_remove': -agent_d['s'][i]
        }

        new_state, new_agents = remove_liquidity(new_state, new_agents, transaction)
    return new_state, new_agents


def value_holdings(state: dict, agent_d: dict, agent_id: string) -> float:
    new_state, new_agents = withdraw_all_liquidity(state, agent_d, agent_id)
    return value_assets(new_state, new_agents[agent_id])


def convert_agent(state: dict, agent_dict: dict) -> dict:
    """Return agent dict compatible with this amm"""
    n = len(state['R'])
    tks = state['token_list']
    d = {'q': 0, 's': [0] * n, 'r': [0] * n, 'p': [0] * n}

    # iterate through tokens held by AMM, look for both tokens and shares. Ignore the rest
    if 'HDX' in agent_dict:
        d['q'] = agent_dict['HDX']
    for i in range(n):
        if tks[i] in agent_dict:
            d['r'][i] = agent_dict[tks[i]]
        if 'omni' + tks[i] in agent_dict:
            d['s'][i] = agent_dict['omni' + tks[i]]
            # absent other information, assumes LPs contributed at current prices
            d['p'][i] = price_i(state, i)

    return d


def convert_agents(state: dict, agents_dict: dict) -> dict:
    d = {}
    for agent_id in agents_dict:
        d[agent_id] = convert_agent(state, agents_dict[agent_id])
    return d
