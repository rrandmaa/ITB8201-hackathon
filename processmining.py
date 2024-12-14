import requests
from web3 import Web3
from datetime import datetime
import pandas as pd
import pm4py
from eth_abi.abi import decode
import json

# ---------------------------
# Configuration
# ---------------------------
INFURA_PROJECT_ID = ""  # Replace with your Infura Project ID
ETHERSCAN_API_KEY = ""  # Replace with your Etherscan API Key
CONTRACT_ADDRESS = ""   # Replace with your target contract address
LAST_N_BLOCKS = 5       # Number of recent blocks to analyze

RPC_URL = f"https://mainnet.infura.io/v3/{INFURA_PROJECT_ID}"
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# Known event signatures for fallback decoding (e.g., an example event from earlier demonstrations)
# Format: "EventName(args)": ("EventName", ["argType", ...])
KNOWN_EVENTS = {
    "StepExecuted(address,string,uint256)": {
        "name": "StepExecuted",
        "arg_types": ["address", "string", "uint256"],
        "case_id_index": 0,   # Which argument to treat as case_id (user)
        "activity_index": 1,  # Which argument to treat as activity (step)
        "timestamp_index": 2, # Which argument to treat as timestamp
    }
    # Add more known event signatures here if desired
}

# ---------------------------
# Utility Functions
# ---------------------------

def get_contract_abi(address, api_key):
    """Fetch the contract ABI from Etherscan if verified."""
    url = f"https://api.etherscan.io/api"
    params = {
        "module": "contract",
        "action": "getabi",
        "address": address,
        "apikey": api_key
    }
    response = requests.get(url, params=params)
    result = response.json()

    if result["status"] == "1":
        return result["result"]
    else:
        return None

def get_event_signature_hash(signature):
    """Calculate the Keccak hash (topic) of the event signature."""
    return w3.keccak(text=signature).hex()

def fetch_logs(address, from_block, to_block):
    """Fetch logs from the specified block range."""
    # If you know the exact event signature you want, you can filter by topics. 
    # Here we do a broad fetch for demonstration (no topics = fetch all events).
    logs = w3.eth.get_logs({
        "address": address,
        "fromBlock": from_block,
        "toBlock": to_block
    })
    return logs

def decode_log(log, abi_events):
    """Attempt to decode a log using the given ABI event definitions."""
    for abi_event in abi_events:
        # Match event by its signature hash
        event_signature = f"{abi_event['name']}({','.join(i['type'] for i in abi_event['inputs'])})"
        event_hash = get_event_signature_hash(event_signature)
        if log['topics'][0].hex() == event_hash:
            # Decode indexed and non-indexed inputs
            indexed_inputs = [i for i in abi_event['inputs'] if i['indexed']]
            non_indexed_inputs = [i for i in abi_event['inputs'] if not i['indexed']]

            # Decode indexed inputs from topics
            decoded_indexed = []
            topic_index = 1
            for inp in indexed_inputs:
                if inp['type'] == 'address':
                    decoded_indexed.append("0x" + log['topics'][topic_index].hex()[-40:])
                else:
                    decoded_indexed.append(int(log['topics'][topic_index].hex(), 16))
                topic_index += 1

            # Decode non-indexed from data
            data_bytes = log['data'] if isinstance(log['data'], (bytes, bytearray)) else bytes.fromhex(log['data'].replace("0x", ""))
            decoded_non_indexed = []
            if len(non_indexed_inputs) > 0:
                arg_types = [i['type'] for i in non_indexed_inputs]
                decoded_non_indexed = list(decode(arg_types, data_bytes))

            # Combine results
            final_args = decoded_indexed + decoded_non_indexed
            return {
                "event_name": abi_event['name'],
                "args": final_args,
                "arg_names": [i['name'] for i in abi_event['inputs']]
            }
    return None

def get_block_timestamp(block_number):
    block = w3.eth.get_block(block_number)
    return block.timestamp

# ---------------------------
# Main Flow
# ---------------------------

# 1. Get current and start block
current_block = w3.eth.block_number
start_block = max(current_block - LAST_N_BLOCKS, 0)

# 2. Try to fetch ABI from Etherscan
abi_json = get_contract_abi(CONTRACT_ADDRESS, ETHERSCAN_API_KEY)
abi = None
abi_events = []
if abi_json:
    abi = json.loads(abi_json) if isinstance(abi_json, str) else abi_json
    # Extract events
    abi_events = [item for item in abi if item.get('type') == 'event']

# 3. Fetch logs
logs = fetch_logs(CONTRACT_ADDRESS, start_block, 'latest')

# 4. Decode logs into a DataFrame suitable for process mining
# We need columns: case_id, activity, timestamp
data = []

for log in logs:
    decoded = None
    if abi_events:
        decoded = decode_log(log, abi_events)

    if not decoded:
        # Can't decode arguments, just store raw
        # We'll treat the event_name as hex of the topic,
        # and activity as the event name, timestamp from block
        block_ts = get_block_timestamp(log['blockNumber'])
        # With no proper decoding, we have no user or step
        # We'll use the log address as case_id, event hash as activity
        data.append({
            "case_id": log['address'],
            "activity": log['topics'][0].hex(),
            "timestamp": datetime.fromtimestamp(block_ts)
        })
    else:
        # We have event_name and args. We must figure out how to map them.
        # Try to find columns for case_id, activity, and timestamp:
        event_name = decoded["event_name"]
        args = decoded["args"]
        arg_names = decoded["arg_names"]

        # Heuristics:
        # - If 'user' in arg_names, use that as case_id
        # - If 'step' or 'activity' in arg_names, use as activity
        # - If 'timestamp' in arg_names, convert from int to datetime
        # Otherwise fallback to address and block timestamp
        case_id_value = CONTRACT_ADDRESS
        activity_value = event_name
        block_ts = get_block_timestamp(log['blockNumber'])
        ts_value = datetime.fromtimestamp(block_ts)

        if 'user' in arg_names:
            user_index = arg_names.index('user')
            case_id_value = args[user_index]

        if 'step' in arg_names:
            step_index = arg_names.index('step')
            activity_value = args[step_index]

        if 'timestamp' in arg_names:
            timestamp_index = arg_names.index('timestamp')
            # Ensure the timestamp is int
            evt_ts = args[timestamp_index]
            if isinstance(evt_ts, int):
                ts_value = datetime.fromtimestamp(evt_ts)
        
        data.append({
            "case_id": case_id_value,
            "activity": activity_value,
            "timestamp": ts_value
        })

event_log_df = pd.DataFrame(data)

if event_log_df.empty:
    print("No events found for the given contract and range.")
    exit(0)
else:
    print("Event count:", len(event_log_df))

# Prepare the DataFrame for PM4Py
event_log_df = pm4py.format_dataframe(
    event_log_df,
    case_id='case_id',
    activity_key='activity',
    timestamp_key='timestamp'
)

print('Converting logs')
log = pm4py.convert_to_event_log(event_log_df)

print('mining')
# Apply Alpha Miner
net, initial_marking, final_marking = pm4py.discover_petri_net_alpha(log)

# Visualize Petri Net
# pm4py.view_petri_net(net, initial_marking, final_marking)

# Discover and visualize Directly-Follows Graph
dfg, start_activities, end_activities = pm4py.discover_dfg(log)
pm4py.view_dfg(dfg, start_activities, end_activities)

print("Event Log Data (sample):")
print(event_log_df.head())
print("Process mining completed. Check the visualizations for process models.")
