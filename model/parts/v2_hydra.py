import numpy as np
import pandas as pd
from .v2_hydra_utils import * # original mechanisms
# from .hydra_agent_utils import *
from .v2_hydra_agent import *
from .v2_hydra_mechs import * # newer mechanisms
from .v2_hydra_coeffs import * # new mechanism 28 June 2021
# from .hydra_weights import * # weight to share mechanisms

# Mechanisms
def mechanismHub_pool(params, substep, state_history, prev_state, policy_input):
    """
This mechanismHub returns the approprate 'pool' function to a given policy input:
Conditioned upon the choice of the 'CHANGE LOG' parameter selection of alternative mechanisms is facilitated which allows to test different candidate mechanisms and their effects.

    """
    action = policy_input['action_id']
    if action == 'Ri_Purchase':
        return q_to_r_pool(params, substep, state_history, prev_state, policy_input)  ## KP_TE_AC: rename & re-define accordingly to _reserve_one
    elif action == 'Q_Purchase':
        if params['CHANGE_LOG'] == '7-13-21':
            return r_to_q_pool_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return r_to_q_pool_reserve_one(params, substep, state_history, prev_state, policy_input)
    elif action == 'AddLiquidity':
        return addLiquidity_pool(params, substep, state_history, prev_state, policy_input)
    elif action == 'RemoveLiquidity':
        return removeLiquidity_pool(params, substep, state_history, prev_state, policy_input)
    elif action == 'R_Swap':
        if params['CHANGE_LOG'] == '7-13-21':
            return r_to_r_pool_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return r_to_r_pool_reserve_one(params, substep, state_history, prev_state, policy_input)
    return('pool', prev_state['pool'])
    
def mechanismHub_Q_Hydra(params, substep, state_history, prev_state, policy_input):
    """
This mechanism returns the approprate hydra (Q=inside pool) function to a given policy input:
Conditioned upon the choice of the 'CHANGE LOG' parameter selection of alternative mechanisms is facilitated which allows to test different candidate mechanisms and their effects.
    """
    action = policy_input['action_id']
    if action == 'Ri_Purchase':
        if params['CHANGE_LOG'] == '7-13-21':
            return q_to_r_Qh_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return q_to_r_Qh_reserve_one(params, substep, state_history, prev_state, policy_input)
    elif action == 'Q_Purchase':
        if params['CHANGE_LOG'] == '7-13-21':
            return r_to_q_Qh_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return r_to_q_Qh_reserve_one(params, substep, state_history, prev_state, policy_input)
    elif action == 'AddLiquidity':
        return addLiquidity_Qh(params, substep, state_history, prev_state, policy_input)
    elif action == 'RemoveLiquidity':
        return removeLiquidity_Qh(params, substep, state_history, prev_state, policy_input)
    elif action == 'R_Swap':
        if params['CHANGE_LOG'] == '7-13-21':
            return r_to_r_swap_Qh_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return r_to_r_swap_Qh_reserve_one(params, substep, state_history, prev_state, policy_input)
    return('Q', prev_state['Q'])

def mechanismHub_Sq(params, substep, state_history, prev_state, policy_input): # KP-TE-AC: can be removed completely? - not w/o nchanges in PSUB
    """
This mechanism returns the approprate share function to a given policy input:
Conditioned upon the choice of the 'CHANGE LOG' parameter selection of alternative mechanisms is facilitated which allows to test different candidate mechanisms and their effects.
    """    
    action = policy_input['action_id']
    if action == 'AddLiquidity':
        return addLiquidity_Sq(params, substep, state_history, prev_state, policy_input)
    elif action == 'RemoveLiquidity':
        return removeLiquidity_Sq(params, substep, state_history, prev_state, policy_input)
    return('Sq', prev_state['Sq'])

def H_agenthub(params, substep, state_history, prev_state, policy_input):
    action = policy_input['action_id']
    if action == 'Ri_Purchase':        
        if params['CHANGE_LOG'] == '7-13-21':
            return H_agent_q_to_r_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return H_agent_q_to_r_reserve_one(params, substep, state_history, prev_state, policy_input)
    elif action == 'Q_Purchase':        
        if params['CHANGE_LOG'] == '7-13-21': #no actual in change in H
            return H_agent_r_to_q_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return H_agent_r_to_q_reserve_one(params, substep, state_history, prev_state, policy_input)
    elif action == 'AddLiquidity':
        return H_agent_add_liq(params, substep, state_history, prev_state, policy_input)
    elif action == 'RemoveLiquidity':
        return H_agent_remove_liq(params, substep, state_history, prev_state, policy_input)
    elif action == 'R_Swap':
        if params['CHANGE_LOG'] == '7-13-21':
            return H_agent_r_to_r_swap_reserve_one(params, substep, state_history, prev_state, policy_input)   
        else: #placeholder for alternative mechanism below:
            return H_agent_r_to_r_swap_reserve_one(params, substep, state_history, prev_state, policy_input)
    return('hydra_agents', prev_state['hydra_agents'])
    

def mechanismHub_H_Hydra(params, substep, state_history, prev_state, policy_input):
    """
This mechanism returns the approprate Hydra (H=total supply) function to a given policy input.
Conditioned upon the choice of the 'CHANGE LOG' parameter selection of alternative mechanisms is facilitated which allows to test different candidate mechanisms and their effects.
    """
    action = policy_input['action_id']

    if action == 'Ri_Purchase':
        if params['CHANGE_LOG'] == '7-13-21':
            return q_to_r_H_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return q_to_r_H_reserve_one(params, substep, state_history, prev_state, policy_input)
    elif action == 'Q_Purchase':
        if params['CHANGE_LOG'] == '7-13-21':
            return r_to_q_H_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return r_to_q_H_reserve_one(params, substep, state_history, prev_state, policy_input)
    elif action == 'AddLiquidity':
        return resolve_addLiquidity_H(params, substep, state_history, prev_state, policy_input)
    elif action == 'RemoveLiquidity':
        return resolve_remove_Liquidity_H(params, substep, state_history, prev_state, policy_input)
    elif action == 'R_Swap':
        if params['CHANGE_LOG'] == '7-13-21':
            return r_to_r_swap_H_reserve_one(params, substep, state_history, prev_state, policy_input)
        else: #placeholder for alternative mechanism below:
            return r_to_r_swap_H_reserve_one(params, substep, state_history, prev_state, policy_input)
    return('H', prev_state['H'])

def mechanismHub_Wq(params, substep, state_history, prev_state, policy_input): #KP-TE-AC: can be removed completely? - not w/o nchanges in PSUB
    """
This mechanism returns the approprate share function to a given policy input:
Weight and Share break constraint
Conditioned upon the choice of the 'CHANGE LOG' parameter selection of alternative mechanisms is facilitated which allows to test different candidate mechanisms and their effects.
    """
    action = policy_input['action_id']
    if action == 'Ri_Purchase':
        return q_to_r_Wq(params, substep, state_history, prev_state, policy_input) # "reserve -one version"
    #elif action == 'Q_Purchase':
        #return r_to_q_Wq(params, substep, state_history, prev_state, policy_input)  # "reserve -one version"
    elif action == 'AddLiquidity':
        return addLiquidity_Wq(params, substep, state_history, prev_state, policy_input)
    elif action == 'RemoveLiquidity':
        return removeLiquidity_Wq(params, substep, state_history, prev_state, policy_input)
    #elif action == 'R_Swap':
        #return r_to_r_swap_Wq(params, substep, state_history, prev_state, policy_input) # KP 7-15-21 no Wq in V2
    return('Wq', prev_state['Wq'])

def mechanismHub_Y(params, substep, state_history, prev_state, policy_input):
    """
This mechanism returns the approprate Y update function for liquidity events
    """
    action = policy_input['action_id']
    if action == 'AddLiquidity':
        return addLiquidity_Y(params, substep, state_history, prev_state, policy_input)
    elif action == 'RemoveLiquidity':
        return removeLiquidity_Y(params, substep, state_history, prev_state, policy_input)
    return('Y', prev_state['Y'])

