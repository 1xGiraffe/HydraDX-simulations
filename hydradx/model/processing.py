from csv import DictReader, writer
from dataclasses import dataclass

from .amm.global_state import GlobalState, withdraw_all_liquidity, AMM
from .amm.agents import Agent


# functions for calculating extra parameters we may want to track
def value_assets(prices: dict, assets: dict) -> float:
    """
    return the value of the agent's assets if they were sold at current spot prices
    """
    return sum([
        assets[i] * prices[i] if i in prices else 0
        for i in assets.keys()
    ])


def market_prices(state: GlobalState, shares: dict) -> dict:
    """
    return the market price of each asset in state.asset_list, as well as the price of each asset in shares
    """
    prices = {tkn: state.price(tkn) for tkn in state.asset_list}
    for share_id in shares:
        # if shares are for a specific asset in a specific pool, get prices according to that pool
        if isinstance(share_id, tuple):
            pool_id = share_id[0]
            tkn_id = share_id[1]
            prices[share_id] = state.pools[pool_id].usd_price(tkn_id)

    return prices


def cash_out(state: GlobalState, agent: Agent) -> float:
    """
    return the value of the agent's holdings if they withdraw all liquidity
    and then sell at current spot prices
    """
    new_agent = withdraw_all_liquidity(state, agent.unique_id).agents[agent.unique_id]
    prices = market_prices(state, agent.holdings)
    return value_assets(prices, new_agent.holdings)


def pool_val(state: GlobalState, pool: AMM):
    """ get the total value of all liquidity in the pool. """
    total = 0
    for asset in pool.asset_list:
        total += pool.liquidity[asset] * state.price(asset)
    return total


def impermanent_loss(state: GlobalState, agent_id: str) -> float:
    return value_assets(  # deposit_val
        market_prices(state, state.agents[agent_id].initial_holdings),
        state.agents[agent_id].initial_holdings
    ) / cash_out(  # withdraw_val
        state, state.agents[agent_id]
    ) - 1


def postprocessing(events: list[dict], optional_params: list[str] = ()) -> list[dict]:
    """
    Definition:
    Compute more abstract metrics from the simulation

    Optional parameters:
    'withdraw_val': tracks the actual value of each agent's assets if they were withdrawn from the pool at each step
    'deposit_val': tracks the theoretical value of each agent's original assets at each step's current spot prices,
        if they had been held outside the pool from the beginning
    'holdings_val': the total value of the agent's outside holdings
    'pool_val': tracks the value of all assets held in the pool
    'impermanent_loss': computes loss for LPs due to price movements in either direction
    """
    # save initial state
    initial_state: GlobalState = events[0]['state']
    withdraw_state: GlobalState = initial_state.copy()

    optional_params = set(optional_params)
    if 'impermanent_loss' in optional_params:
        optional_params.add('deposit_val')
        optional_params.add('withdraw_val')

    agent_params = {
        'deposit_val',
        'withdraw_val',
        'holdings_val',
        'impermanent_loss',
        'token_count',
        'trade_volume'
    }
    exchange_params = {
        'pool_val',
        # 'usd_price'
    }
    unrecognized_params = optional_params.difference(agent_params | exchange_params)
    if unrecognized_params:
        raise ValueError(f'Unrecognized parameter {unrecognized_params}')

    # print(f'processing {optional_params}')
    #
    # a little pre-processing
    if 'deposit_val' in optional_params:
        # move the agents' liquidity deposits back into holdings, as something to compare against later
        for agent_id in initial_state.agents:
            # do it this convoluted way because we're pretending each agent withdrew their assets alone,
            # isolated from any effects of the other agents withdrawing *their* assets
            withdraw_state.agents[agent_id] = withdraw_all_liquidity(initial_state.copy(), agent_id).agents[agent_id]

    for step in events:
        state: GlobalState = step['state']

        for pool in state.pools.values():
            if 'pool_val' in optional_params:
                pool.pool_val = pool_val(state, pool)
            # if 'usd_price' in optional_params:
            #     pool.usd_price = {tkn: pool.price(tkn) for tkn in pool.asset_list}

        # agents
        for agent in state.agents.values():
            if 'deposit_val' in optional_params:
                # what are this agent's original holdings theoretically worth at current spot prices?
                agent.deposit_val = value_assets(
                    market_prices(state, agent.holdings),
                    withdraw_state.agents[agent.unique_id].holdings
                )
            if 'withdraw_val' in optional_params:
                # what are this agent's holdings worth if sold?
                agent.withdraw_val = cash_out(state, agent)
            if 'holdings_val' in optional_params:
                agent.holdings_val = sum([quantity * state.price(asset) for asset, quantity in agent.holdings.items()])
            if 'impermanent_loss' in optional_params:
                agent.impermanent_loss = agent.withdraw_val / agent.deposit_val - 1
            if 'token_count' in optional_params:
                agent.token_count = sum(agent.holdings.values())
            if 'trade_volume' in optional_params:
                agent.trade_volume = 0
                if state.time_step > 0:
                    previous_agent = events[state.time_step - 1]['state'].agents[agent.unique_id]
                    agent.trade_volume += (
                        sum([
                            abs(previous_agent.holdings[tkn] - agent.holdings[tkn]) * state.price(tkn)
                            for tkn in agent.holdings])
                    )

    return events


@dataclass
class PriceTick:
    timestamp: int
    price: float


def write_price_data(price_data: list[PriceTick], output_filename: str) -> None:
    with open(output_filename, 'w', newline='') as output_file:
        fieldnames = ['timestamp', 'price']
        csvwriter = writer(output_file)
        csvwriter.writerow(fieldnames)
        for row in price_data:
            csvwriter.writerow([row.timestamp, row.price])


def import_price_data(input_filename: str) -> list[PriceTick]:
    price_data = []
    with open(input_filename, newline='') as input_file:
        reader = DictReader(input_file)
        for row in reader:
            price_data.append(PriceTick(int(row["timestamp"]), float(row["price"])))
    return price_data


def import_binance_prices(input_path: str, input_filenames: list[str]) -> list[PriceTick]:
    price_data = []
    for input_filename in input_filenames:
        with open(input_path + input_filename, newline='') as input_file:
            fieldnames = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume',
                          'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
            reader = DictReader(input_file, fieldnames=fieldnames)
            # reader = DictReader(input_file)
            for row in reader:
                price_data.append(PriceTick(int(row["timestamp"]), float(row["open"])))

    price_data.sort(key=lambda x: x.timestamp)
    return price_data
