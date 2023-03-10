from csv import DictReader, writer, reader
from dataclasses import dataclass
import requests
import json
from zipfile import ZipFile
import datetime
import os

from .amm.global_state import GlobalState, withdraw_all_liquidity, AMM, value_assets
# from .amm.agents import Agent

cash_out = GlobalState.cash_out
impermanent_loss = GlobalState.impermanent_loss
pool_val = GlobalState.pool_val
deposit_val = GlobalState.deposit_val

TOKEN_METADATA = [
    ('HDX', 12),
    ('LRNA', 12),
    ('DAI', 18),
    ('WBTC', 8),
    ('WETH', 18),
    ('DOT', 10),
    ('APE', 18),
    ('USDC', 6),
    ('PHA', 12),
]
LRNA_I = 1


def postprocessing(events: list, optional_params: list[str] = ()) -> list:
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
    initial_state: GlobalState = events[0]
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

    # a little pre-processing
    if 'deposit_val' in optional_params:
        # move the agents' liquidity deposits back into holdings, as something to compare against later
        for agent_id in initial_state.agents:
            # do it this convoluted way because we're pretending each agent withdrew their assets alone,
            # isolated from any effects of the other agents withdrawing *their* assets
            withdraw_state.agents[agent_id] = withdraw_all_liquidity(initial_state.copy(), agent_id).agents[agent_id]

    for step in events:
        state: GlobalState = step

        for pool in state.pools.values():
            if 'pool_val' in optional_params:
                pool.pool_val = state.pool_val(pool)

        # agents
        for agent in state.agents.values():
            if 'deposit_val' in optional_params:
                # what are this agent's original holdings theoretically worth at current spot prices?
                agent.deposit_val = value_assets(
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
                    previous_agent = events[state.time_step - 1].agents[agent.unique_id]
                    agent.trade_volume += (
                        sum([
                            abs(previous_agent.holdings[tkn] - agent.holdings[tkn]) * state.price(tkn)
                            for tkn in agent.holdings])
                    )

    return events


def import_binance_prices(
        assets: list[str], start_date: str, days: int, interval: int = 12, return_as_dict: bool = False
) -> dict[str: list[float]]:
    start_date = datetime.datetime.strptime(start_date, "%b %d %Y")
    dates = [datetime.datetime.strftime(start_date + datetime.timedelta(days=i), ("%Y-%m-%d")) for i in range(days)]

    # find the data folder
    while not os.path.exists("./data"):
        cwd = os.getcwd()
        os.chdir("..")
        if cwd == os.getcwd():
            raise FileNotFoundError("Could not find the data folder")

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
        data_length = min([len(price_data[tkn]) for tkn in assets])
        price_data = [
            {tkn: price_data[tkn][i] for tkn in assets} for i in range(data_length)
        ]
    return price_data


def import_monthly_binance_prices(
        assets: list[str], start_month: str, months: int, interval: int = 12, return_as_dict: bool = False
) -> dict[str: list[float]]:
    start_mth, start_year = start_month.split(' ')

    start_date = datetime.datetime.strptime(start_mth + ' 15 ' + start_year, "%b %d %Y")
    dates = [datetime.datetime.strftime(start_date + datetime.timedelta(days=i * 30), ("%Y-%m")) for i in range(months)]

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


# def import_prices(input_path: str, input_filename: str) -> list[PriceTick]:
#     price_data = []
#     with open(input_path + input_filename, newline='') as input_file:
#         fieldnames = ['timestamp', 'price']
#         reader = DictReader(input_file, fieldnames=fieldnames)
#         next(reader)  # skip header
#         for row in reader:
#             price_data.append(PriceTick(int(row["timestamp"]), float(row["price"])))
#
#     price_data.sort(key=lambda x: x.timestamp)
#     return price_data


def read_csvs_to_list_of_dicts(paths: list[str]) -> list:
    dicts = []
    for path in paths:
        with open(path) as csv_file:
            reader = DictReader(csv_file, delimiter=',')
            dicts.extend(list(reader))
    return dicts


def import_trades(paths: list[str]) -> list[dict]:
    rows = read_csvs_to_list_of_dicts(paths)

    token_names = [token[0] for token in TOKEN_METADATA]
    token_decimals = [token[1] for token in TOKEN_METADATA]

    trades = []
    for row in rows:
        name = row['call_name'].split('.')[1]
        if row['asset_out'] == '':
            raise
        out_i = int(row['asset_out'])
        in_i = int(row['asset_in'])
        limit_decimals = token_decimals[out_i] if name == 'sell' else token_decimals[in_i]
        trade_dict = {
            'id': row['extrinsic_id'],
            'block_no': int(row['extrinsic_id'].split('-')[0]),
            'tx_no': int(row['extrinsic_id'].split('-')[1]),
            'type': name,
            'success': bool(row['call_success']),
            'who': row['who'],
            'fee': int(row['fee']),
            'tip': int(row['tip']),
            'asset_in': token_names[in_i],
            'asset_out': token_names[out_i],
            'amount_in': int(row['amount_in']) / 10 ** token_decimals[in_i],
            'amount_out': int(row['amount_out']) / 10 ** token_decimals[out_i],
            'limit_amount': int(row['limit_amount']) / 10 ** limit_decimals,
        }
        trades.append(trade_dict)
    return trades


def import_add_liq(paths: list[str]) -> list[dict]:
    rows = read_csvs_to_list_of_dicts(paths)

    token_names = [token[0] for token in TOKEN_METADATA]
    token_decimals = [token[1] for token in TOKEN_METADATA]

    txs = []
    for row in rows:
        tx_dict = {
            'id': row['extrinsic_id'],
            'block_no': int(row['extrinsic_id'].split('-')[0]),
            'tx_no': int(row['extrinsic_id'].split('-')[1]),
            'type': row['call_name'].split('.')[1],
            'success': bool(row['call_success']),
            'who': row['who'],
            'fee': int(row['fee']),
            'tip': int(row['tip']),
            'asset': token_names[int(row['asset'])],
            'amount': int(row['amount']) / 10 ** token_decimals[int(row['asset'])],
            'price': int(row['price']) / 10 ** (18 + token_decimals[LRNA_I] - token_decimals[int(row['asset'])]),
            'shares': int(row['shares']) / 10 ** token_decimals[int(row['asset'])],
            'position_id': row['position_id'],
        }
        txs.append(tx_dict)
    return txs


def import_remove_liq(paths: list[str]) -> list[dict]:
    rows = read_csvs_to_list_of_dicts(paths)

    token_names = [token[0] for token in TOKEN_METADATA]
    token_decimals = [token[1] for token in TOKEN_METADATA]

    txs = []
    for row in rows:
        tx_dict = {
            'id': row['extrinsic_id'],
            'block_no': int(row['extrinsic_id'].split('-')[0]),
            'tx_no': int(row['extrinsic_id'].split('-')[1]),
            'type': row['call_name'].split('.')[1],
            'success': bool(row['call_success']),
            'who': row['who'],
            'fee': int(row['fee']),
            'tip': int(row['tip']),
            'asset': token_names[int(row['asset'])],
            'amount': int(row['amount']) / 10 ** token_decimals[int(row['asset'])],
            'lrna_amount': int(row['lrna_amount']) / 10 ** token_decimals[LRNA_I] if row['lrna_amount'] != '' else 0,
            'shares': int(row['shares']) / 10 ** token_decimals[int(row['asset'])],
            'position_id': row['position_id'],
        }
        txs.append(tx_dict)
    return txs


def import_tx_data():
    trade_paths = ['input/trades.csv']
    liq_add_paths = ['input/add_liquidity.csv']
    liq_rem_paths = ['input/remove_liquidity.csv']

    trades = import_trades(trade_paths)
    liq_adds = import_add_liq(liq_add_paths)
    liq_rems = import_remove_liq(liq_rem_paths)

    txs = trades + liq_adds + liq_rems
    txs.sort(key=lambda x: (x['block_no'], x['tx_no']))

    return txs
