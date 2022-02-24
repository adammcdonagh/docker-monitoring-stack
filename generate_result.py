#!/usr/bin/env python3
from asyncio import sleep
import sys
import argparse
import logging
import json
import requests
import threading
import re
import os
from random import randrange
import time


config = dict()
logging.info(f"Reading config file")
with open("agents.json") as json_data:
    config = json.load(json_data)

parser = argparse.ArgumentParser()
parser.add_argument("-c", "--check", help="name of check", required=True)
parser.add_argument("-e", "--entity", help="name of entity", required=True)

args = parser.parse_args()

check = args.check

# From the config, get the check thresholds
if re.match(r"^metrics", check):
    # logging.info(f"{config['checks'][check]}")

    # Get the min/max values and generate a suitable value for the current output
    min = config["checks"][check]["normal"][0]
    max = config["checks"][check]["normal"][1]
    value = randrange(min, max)

    # logging.info(f"returning {value}")
    help_text_name = re.sub(r"\{.*?\}", "", config["checks"][check]["metric-name"])
    print(
        f"""# HELP {help_text_name} Some description
# TYPE {help_text_name} GAUGE
{config['checks'][check]['metric-name']} {value} {int(round(time.time() * 1000))}
"""
    )

else:
    # logging.info("Generating check output")
    print(config["checks"][check]["good-status"])
