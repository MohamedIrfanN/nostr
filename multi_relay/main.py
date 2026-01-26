import asyncio
import json
import time
import uuid
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from contextlib import asynccontextmanager

from .utils import get_pubkey_xonly_from_env

# Hardcoded relays for now, similar to original script
RELAYS = [
    "wss://relay.damus.io",
    "wss://relay.snort.social",
    "wss://nos.lol",
]

# Global set to track seen event IDs to avoid duplicates sent to frontend
seen_ids: set[str] = set()

app = FastAPI()

app.mount("/static", StaticFiles(directory="multi_relay/static"), name="static")

templates = Jinja2Templates(directory="multi_relay/templates")

@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

async def read_from_relay(relay_url: str, pubkey_xonly: str, frontend_ws: WebSocket):
    """
    Connects to a single relay, subscribes to events, and forwards them to the frontend.
    """
    filter_obj = {
        # "authors": [pubkey_xonly],
        "kinds": [1],
        "limit": 50,
        "since": int(time.time()) - 60 * 60, # Last hour
    }
    sub_id = str(uuid.uuid4())
    req_msg = ["REQ", sub_id, filter_obj]

    try:
        async with websockets.connect(relay_url, ping_interval=20, ping_timeout=20) as ws:
            # Notify frontend of connection status
            await frontend_ws.send_json({"type": "status", "relay": relay_url, "status": "connected"})
            
            await ws.send(json.dumps(req_msg))
            
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                
                if not msg:
                    continue

                msg_type = msg[0]

                if msg_type == "EVENT":
                    _, got_sub_id, event = msg
                    if got_sub_id != sub_id:
                        continue

                    eid = event.get("id")
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)

                    # Augment event with relay info for the UI
                    event["_relay"] = relay_url
                    
                    # Send to frontend
                    await frontend_ws.send_json({"type": "event", "data": event})

                elif msg_type == "EOSE":
                   # Notify frontend EOSE? Maybe not strictly necessary for a continuous stream but good for debug
                   pass

    except Exception as e:
        # Notify frontend of error/disconnect
        try:
            await frontend_ws.send_json({"type": "status", "relay": relay_url, "status": "error", "message": str(e)})
        except:
            pass # Frontend might be gone

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for the frontend.
    When a user connects, we start background tasks to listen to all relays.
    """
    await websocket.accept()
    
    try:
        pubkey = get_pubkey_xonly_from_env()
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"Environment Error: {str(e)}"})
        await websocket.close()
        return

    # Clear seen_ids on new connection to allow fresh fetch? 
    # Or keep it persistent? Let's keep it persistent for the session but maybe it's better to clear 
    # if we want to re-show events on refresh. For now, let's clear it here so page refresh works as expected.
    seen_ids.clear()
    
    # Send pubkey info to frontend
    await websocket.send_json({"type": "info", "pubkey": pubkey})

    # Start listener tasks for each relay
    tasks = [
        asyncio.create_task(read_from_relay(r, pubkey, websocket))
        for r in RELAYS
    ]

    try:
        # Keep the connection open and wait for tasks
        # We also need to listen for any messages from client if we add interactivity later
        # For now, we just wait.
        await asyncio.gather(*tasks)
    except WebSocketDisconnect:
        print("Frontend disconnected")
    except Exception as e:
        print(f"Error in websocket handler: {e}")
    finally:
        for t in tasks:
            t.cancel()
