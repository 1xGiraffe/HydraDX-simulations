import pandas as pd

def initialize_model(initial_liquidity, initial_tradevolume, initial_fee_assets, initial_fee_HDX):

########## AGENT CONFIGURATION ##########
# key -> token name, value -> token amount owned by agent
# note that token name of 'omniABC' is used for omnipool LP shares of token 'ABC'
# omniHDXABC is HDX shares dedicated to pool of token ABC

    trader = {'HDX': 1000000, 'R1': 1000000, 'R2': 1000000, 'R3': 1000000, 'R4': 1000000, 'R5': 1000000}
    LP1 = {'omniR1': initial_liquidity[0]}
    LP2 = {'omniR2': initial_liquidity[1]}
    LP3 = {'omniR3': initial_liquidity[2]}
    LP4 = {'omniR4': initial_liquidity[3]}
    LP5 = {'omniR5': initial_liquidity[4]}

# key -> agent_id, value -> agent dict
    agent_d = {'Trader': trader, 'LP1': LP1, 'LP2': LP2, 'LP3': LP3, 'LP4': LP4, 'LP5': LP5}

########## ACTION CONFIGURATION ##########

    action_dict = {
        'sell_r2_for_r1': {'token_buy': 'R1', 'token_sell': 'R2', 'amount_sell': 3 * initial_tradevolume, 'action_id': 'Trade',
                           'agent_id': 'Trader'},
        'sell_r1_for_r2': {'token_sell': 'R1', 'token_buy': 'R2', 'amount_sell': initial_tradevolume, 'action_id': 'Trade',
                           'agent_id': 'Trader'},
        'sell_r4_for_r3': {'token_buy': 'R3', 'token_sell': 'R4', 'amount_sell': initial_tradevolume, 'action_id': 'Trade',
                           'agent_id': 'Trader'},
        'sell_r3_for_r4': {'token_sell': 'R3', 'token_buy': 'R4', 'amount_sell': 3 * initial_tradevolume, 'action_id': 'Trade',
                           'agent_id': 'Trader'}
    }

# list of (action, number of repetitions of action), timesteps = sum of repititions of all actions
    trade_count = 1000
    action_ls = [('trade', trade_count)]

# maps action_id to action dict, with some probability to enable randomness
    prob_dict = {
        'trade': {'sell_r2_for_r1': 0.5,
                  'sell_r1_for_r2': 0,
                  'sell_r4_for_r3': 0.25,
                  'sell_r3_for_r4': 0.25}
    }

########## CFMM INITIALIZATION ##########

    initial_values = {
        'token_list': ['R1', 'R2', 'R3', 'R4', 'R5'],
        'R': [initial_liquidity[0], initial_liquidity[1], initial_liquidity[2], initial_liquidity[3], initial_liquidity[4]],
        'P': [2, 2 / 3, 1, 3, 4],
        'fee_assets': initial_fee_assets,
        'fee_HDX': initial_fee_HDX
    }

############################################ SETUP ##########################################################

    config_params = {
        'cfmm_type': "",
        'initial_values': initial_values,
        'agent_d': agent_d,
        'action_ls': action_ls,
        'prob_dict': prob_dict,
        'action_dict': action_dict,
    }
    return config_params
    #return ('config_params', config_params)
    #return trader, LP1, LP2, agent_d, action_dict, trade_count, action_ls, prob_dict, initial_values, config_params
