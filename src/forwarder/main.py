"""JD-Relay Forwarder — main entry point.

Usage:
    python -m forwarder.main [--config forwarder.conf]
    RELAY_PORT=8000 python -m forwarder.main
"""

import argparse
import asyncio
import logging
import sys
import os

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from forwarder.config import load_config
from forwarder.ws.server import ForwarderServer

logger = logging.getLogger("forwarder")


async def main():
    parser = argparse.ArgumentParser(description="JD-Relay Forwarder")
    parser.add_argument("--config", "-c", default=None, help="Config file path (TOML)")
    parser.add_argument("--host", default=None, help="Bind host")
    parser.add_argument("--port", type=int, default=None, help="Bind port")
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # CLI overrides
    host = args.host or config.host
    port = args.port or config.port

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info(f"JD-Relay Forwarder v2.0.0 starting on {host}:{port}")
    logger.info(f"ECDSA keys: {config.ecdsa_private_key_file} / {config.ecdsa_public_key_file}")

    # Create Forwarder
    fwd = ForwarderServer(
        ecdsa_priv_file=config.ecdsa_private_key_file,
        ecdsa_pub_file=config.ecdsa_public_key_file,
    )

    # Start uvicorn
    import uvicorn
    uvicorn_config = uvicorn.Config(
        fwd.app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(uvicorn_config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
