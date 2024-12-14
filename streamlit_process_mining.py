import streamlit as st
import requests
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from datetime import datetime
import pandas as pd
import pm4py
import json
from web3._utils.events import get_event_data

# ---------------------------
# Configuration
# ---------------------------
INFURA_PROJECT_ID = ""      # Replace with your Infura Project ID
ETHERSCAN_API_KEY = ""      # Replace with your Etherscan API Key
POLYGONSCAN_API_KEY = ""    # Replace with your Polygonscan API Key

ETHEREUM_RPC_URL = f"https://mainnet.infura.io/v3/{INFURA_PROJECT_ID}"
POLYGON_RPC_URL = f"https://polygon-mainnet.infura.io/v3/{INFURA_PROJECT_ID}"

ETHERSCAN_URL = "https://api.etherscan.io/api"
POLYGONSCAN_URL = "https://api.polygonscan.com/api"

# ---------------------------
# Utility Functions
# ---------------------------
def get_contract_abi(address, api_url, api_key):
    """Fetch the contract ABI from Etherscan-like explorer if verified."""
    params = {
        "module": "contract",
        "action": "getabi",
        "address": address,
        "apikey": api_key
    }
    response = requests.get(api_url, params=params)
    result = response.json()

    if result["status"] == "1":
        return result["result"]
    else:
        return None

def get_event_signature_hash(signature, w3):
    """Calculate the Keccak hash (topic) of the event signature."""
    return w3.keccak(text=signature).hex()

def fetch_logs(address, from_block, to_block, w3):
    """Fetch logs from the specified block range."""
    logs = w3.eth.get_logs({
        "address": address,
        "fromBlock": from_block,
        "toBlock": to_block
    })
    return logs

def decode_log(log, abi_events, w3):
    """Attempt to decode a log using the given ABI event definitions."""

    for abi_event in abi_events:
        try:
            decoded = get_event_data(w3.codec, abi_event, log)
            
            if decoded:
                event_name = decoded['event']
                event_args = decoded['args']
                return {
                    "event_name": event_name,
                    "args": list(event_args.values()),
                    "arg_names": list(event_args.keys())
                }
        except:
            # If it doesn't match this event, just continue to the next
            pass
    return None

def get_block_timestamp(block_number, w3):
    block = w3.eth.get_block(block_number)
    return block.timestamp

def analyze_contract(chain, contract_hash, num_blocks):
    if chain == "Ethereum":
        RPC_URL = ETHEREUM_RPC_URL
        SCAN_URL = ETHERSCAN_URL
        SCAN_API_KEY = ETHERSCAN_API_KEY
    else:  # Polygon
        RPC_URL = POLYGON_RPC_URL
        SCAN_URL = POLYGONSCAN_URL
        SCAN_API_KEY = POLYGONSCAN_API_KEY

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    current_block = w3.eth.block_number
    start_block = max(current_block - num_blocks, 0)

    abi_json = get_contract_abi(contract_hash, SCAN_URL, SCAN_API_KEY)
    abi = None
    abi_events = []
    if abi_json:
        abi = json.loads(abi_json) if isinstance(abi_json, str) else abi_json
        abi_events = [item for item in abi if item.get('type') == 'event']

    logs = fetch_logs(contract_hash, start_block, 'latest', w3)

    data = []
    for log in logs:
        decoded = decode_log(log, abi_events, w3) if abi_events else None
        block_ts = get_block_timestamp(log['blockNumber'], w3)
        case_id_value = log['address']
        activity_value = log['topics'][0].hex()
        ts_value = datetime.fromtimestamp(block_ts)

        if decoded:
            case_id_value = decoded["args"][0]
            activity_value = decoded["event_name"]

        data.append({
            "case_id": case_id_value,
            "activity": activity_value,
            "timestamp": ts_value
        })

    event_log_df = pd.DataFrame(data)
    if event_log_df.empty:
        raise ValueError("No events found for the given contract and range.")

    event_log_df = pm4py.format_dataframe(event_log_df, case_id='case_id', activity_key='activity', timestamp_key='timestamp')
    log = pm4py.convert_to_event_log(event_log_df)

    dfg, start_activities, end_activities = pm4py.discover_dfg(log)
    img_path = "output_dfg.png"
    pm4py.save_vis_dfg(dfg, start_activities, end_activities, img_path)
    return img_path

# ---------------------------
# Streamlit Application
# ---------------------------
st.title("Ethereum Process Mining")
st.write("Analyze Ethereum smart contract events and visualize process flows.")

chain = st.selectbox("Select Chain", ["Ethereum", "Polygon"])
contract_hash = st.text_input("Enter Contract Address", "0x1eD3d2c916cab00631cce4b08a7F880d4Badae94")
num_blocks = st.number_input("Enter Number of Blocks", min_value=1, max_value=10000000, value=1000, step=1)

if st.button("Analyze"):
    try:
        st.write("Fetching and analyzing data...")
        img_path = analyze_contract(chain, contract_hash, num_blocks)
        st.image(img_path, caption="Mined Process Graph", use_container_width=True)
    except Exception as e:
        st.error(f"Error: {e}")
