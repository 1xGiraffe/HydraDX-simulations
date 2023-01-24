import datetime
import os
from csv import DictReader, writer, reader
from zipfile import ZipFile
from dataclasses import dataclass
import requests

from .amm.global_state import GlobalState, withdraw_all_liquidity, AMM
from .amm.agents import Agent

cash_out = GlobalState.cash_out
value_assets = GlobalState.value_assets
impermanent_loss = GlobalState.impermanent_loss
pool_val = GlobalState.pool_val
deposit_val = GlobalState.deposit_val
market_prices = GlobalState.market_prices


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
                pool.pool_val = state.pool_val(pool)
            # if 'usd_price' in optional_params:
            #     pool.usd_price = {tkn: pool.price(tkn) for tkn in pool.asset_list}

        # agents
        for agent in state.agents.values():
            if 'deposit_val' in optional_params:
                # what are this agent's original holdings theoretically worth at current spot prices?
                agent.deposit_val = state.value_assets(
                    state.market_prices(agent.holdings),
                    withdraw_state.agents[agent.unique_id].holdings
                )
            if 'withdraw_val' in optional_params:
                # what are this agent's holdings worth if sold?
                agent.withdraw_val = state.cash_out(agent)
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


def import_binance_prices(
    assets: list[str], start_date: str, days: int, interval: int = 12, return_as_dict: bool = False
) -> dict[str: list[float]]:

    start_date = datetime.datetime.strptime(start_date, "%b %d %Y")
    dates = [datetime.datetime.strftime(start_date + datetime.timedelta(days=i), ("%Y-%m-%d")) for i in range(days)]

    # find the data folder
    while not os.path.exists("./data"):
        os.chdir("..")

    # check that the files are all there, and if not, download them
    for tkn in assets:
        for date in dates:
            file = f"{tkn}BUSD-1s-{date}"
            if os.path.exists(f'./data/{file}.csv'):
                continue
            else:
                print(f'Downloading {file}')
                url = f"https://data.binance.vision/data/spot/daily/klines/{tkn}BUSD/1s/{file}.zip"
                response = requests.get(url)
                with open(f'./data/{file}.zip', 'wb') as f:
                    f.write(response.content)
                with ZipFile(f"./data/{file}.zip", 'r') as zipObj:
                    zipObj.extractall(path='./data')
                os.remove(f"./data/{file}.zip")
                # strip out everything except close price
                with open(f'./data/{file}.csv', 'r') as input_file:
                    rows = input_file.readlines()
                with open(f'./data/{file}.csv', 'w', newline='') as output_file:
                    output_file.write('\n'.join([row.split(',')[1] for row in rows]))

    # now that we have all the files, read them in
    price_data = {tkn: [] for tkn in assets}
    for tkn in assets:
        for date in dates:
            file = f"{tkn}BUSD-1s-{date}"
            with open(f'./data/{file}.csv', 'r') as input_file:
                csvreader = reader(input_file)
                price_data[tkn] += [float(row[0]) for row in csvreader][::interval]

    if not return_as_dict:
        price_data = [
            {tkn: price_data[tkn][i] for tkn in assets} for i in range(len(price_data[assets[0]]))
        ]
    return price_data


def import_monthly_binance_prices(
    assets: list[str], start_month: str, months: int, interval: int = 12, return_as_dict: bool = False
) -> dict[str: list[float]]:

    start_mth, start_year = start_month.split(' ')

    start_date = datetime.datetime.strptime(start_mth + ' 15 ' + start_year, "%b %d %Y")
    dates = [datetime.datetime.strftime(start_date + datetime.timedelta(days=i*30), ("%Y-%m")) for i in range(months)]

    # find the data folder
    while not os.path.exists("./data"):
        os.chdir("..")

    # check that the files are all there, and if not, download them
    for tkn in assets:
        for date in dates:
            file = f"{tkn}BUSD-1s-{date}"
            if os.path.exists(f'./data/{file}.csv'):
                continue
            else:
                print(f'Downloading {file}')
                url = f"https://data.binance.vision/data/spot/monthly/klines/{tkn}BUSD/1s/{file}.zip"
                response = requests.get(url)
                with open(f'./data/{file}.zip', 'wb') as f:
                    f.write(response.content)
                with ZipFile(f"./data/{file}.zip", 'r') as zipObj:
                    zipObj.extractall(path='./data')
                os.remove(f"./data/{file}.zip")
                # strip out everything except close price
                with open(f'./data/{file}.csv', 'r') as input_file:
                    rows = input_file.readlines()
                with open(f'./data/{file}.csv', 'w', newline='') as output_file:
                    output_file.write('\n'.join([row.split(',')[1] for row in rows]))

    # now that we have all the files, read them in
    price_data = {tkn: [] for tkn in assets}
    for tkn in assets:
        for date in dates:
            file = f"{tkn}BUSD-1s-{date}"
            with open(f'./data/{file}.csv', 'r') as input_file:
                csvreader = reader(input_file)
                price_data[tkn] += [float(row[0]) for row in csvreader][::interval]

    if not return_as_dict:
        price_data = [
            {tkn: price_data[tkn][i] for tkn in assets} for i in range(len(price_data[assets[0]]))
        ]
    return price_data


def import_prices(input_path: str, input_filename: str) -> list[PriceTick]:
    price_data = []
    with open(input_path + input_filename, newline='') as input_file:
        fieldnames = ['timestamp', 'price']
        reader = DictReader(input_file, fieldnames=fieldnames)
        next(reader)  # skip header
        for row in reader:
            price_data.append(PriceTick(int(row["timestamp"]), float(row["price"])))

    price_data.sort(key=lambda x: x.timestamp)
    return price_data
