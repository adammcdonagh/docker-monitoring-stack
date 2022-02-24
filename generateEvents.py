#!/usr/bin/env python3
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


def local_agent():
    os.system(
        (
            "./sensu-agent start --backend-url ws://localhost:8081 --agent-managed-entity "
            "--subscriptions proxyagent --log-level warn --keepalive-interval 5 --keepalive-timeout 10 "
            "--cache-dir /tmp"
        )
    )


def get_check_result(check):
    # From the config, get the check thresholds
    if re.match(r"^metrics", check):
        # logging.info(f"{config['checks'][check]}")

        # Get the min/max values and generate a suitable value for the current output
        min = config["checks"][check]["normal"][0]
        max = config["checks"][check]["normal"][1]
        value = randrange(min, max)

        # logging.info(f"returning {value}")
        help_text_name = re.sub(r"\{.*?\}", "", config["checks"][check]["metric-name"])
        return (
            f"# HELP {help_text_name} Some description"
            f"# TYPE {help_text_name} GAUGE"
            f"{config['checks'][check]['metric-name']} {value} {int(round(time.time() * 1000))}"
        )

    else:
        # logging.info("Generating check output")
        return config["checks"][check]["good-status"]


def thread_function(entity):
    entity_name = entity["name"]
    logging.info(f"Thread for {entity_name} started")

    token = None
    response = requests.get(
        f"{config['backend']['url']}/auth", auth=(config["backend"]["username"], config["backend"]["password"])
    )
    if response:
        token = json.loads(response.content)["access_token"]
        logging.info(token)

    # Get the entity as is from Sensu
    response = requests.get(
        f"{config['backend']['url']}/api/core/v2/namespaces/default/entities/{entity['name']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    entity_object = json.loads(response.content)

    while True:

        token = None
        response = requests.get(
            f"{config['backend']['url']}/auth", auth=(config["backend"]["username"], config["backend"]["password"])
        )
        if response:
            token = json.loads(response.content)["access_token"]
            logging.info(token)

        # Loop through each check and insert an appropriate result for this entitiy
        for check in entity["checks"]:
            check_result = get_check_result(check)
            logging.info(f"Got {check_result}")

            pipelines = []

            check_definition = dict()
            check_definition["interval"] = 10
            check_definition["status"] = 0
            check_definition["state"] = "passing"
            check_definition["publish"] = True
            check_definition["metadata"] = dict()
            check_definition["metadata"]["name"] = check
            check_definition["output"] = check_result
            check_definition["executed"] = int(time.time())
            check_definition["issued"] = int(time.time())

            if re.match(r"^metrics", check):
                check_definition["output_metric_format"] = "prometheus_text"
                check_definition["output_metric_tags"] = []
                check_definition["output_metric_tags"].append({"name": "entity", "value": "{{ .name }}"})
                check_definition["output_metric_tags"].append({"name": "namespace", "value": "{{ .namespace }}"})
                check_definition["output_metric_tags"].append({"name": "os", "value": "{{ .os }}"})
                check_definition["output_metric_tags"].append({"name": "platform", "value": "{{ .system.platform  }}"})
                check_definition["output_metric_tags"].append({"name": "zone", "value": "{{ .labels.zone }}"})
                check_definition["output_metric_tags"].append(
                    {"name": "service", "value": "{{ .labels.service_type }}"}
                )

                check_definition["metrics_handlers"] = ["metrics-storage"]

            else:
                # check_object["handlers"] = ["event-storage"]
                check_definition["status"] = 1
                check_definition["state"] = "failing"
                # check_object["status"] = 0
                # check_object["state"] = "passing"

                pipelines.append({"type": "pipeline", "api_version": "core/v2", "name": "sensu_checks_to_sumo"})

            # Post the check result
            check_result = dict()
            check_result["entity"] = entity_object
            check_result["check"] = check_definition
            if re.match(r"^check", check):
                check_result["pipelines"] = pipelines

            logging.info(f"Posting: {json.dumps(check_result)}")
            response = requests.post(
                f"{config['backend']['url']}/api/core/v2/namespaces/default/events/{entity_name}/{check}",
                json=check_result,
                headers={"Authorization": f"Bearer {token}"},
            )
            logging.info(f"Got {response.status_code} - {response.content}")
        time.sleep(10)


logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)


# This script will generate a number of events in sensu for some fake agents
parser = argparse.ArgumentParser()
parser.add_argument("-c", "--config", help="config file to read agent list from", required=True)

args = parser.parse_args()

# Load config file
config = dict()
logging.info(f"Reading config file {args.config}")
with open(args.config) as json_data:

    config = json.load(json_data)

# Login to Sensu and get a token
token = None
response = requests.get(
    f"{config['backend']['url']}/auth", auth=(config["backend"]["username"], config["backend"]["password"])
)
if response:
    token = json.loads(response.content)["access_token"]
    logging.info(token)
else:
    sys.exit(1)

# Create/update check definitions

for check_name in config["checks"]:

    check = dict()

    check_definition = dict()

    check_definition["interval"] = 10
    check_definition["publish"] = False

    if re.match(r"^metrics", check_name):
        check_definition["output_metric_format"] = "prometheus_text"
        check_definition["output_metric_tags"] = []
        check_definition["output_metric_tags"].append({"name": "entity", "value": "{{ .name }}"})
        check_definition["output_metric_tags"].append({"name": "namespace", "value": "{{ .namespace }}"})
        # check_definition["output_metric_tags"].append({"name": "os", "value": "{{ .os }}"})
        # check_definition["output_metric_tags"].append({"name": "platform", "value": "{{ .system.platform  }}"})
        check_definition["output_metric_tags"].append({"name": "zone", "value": "{{ .labels.zone }}"})
        check_definition["output_metric_tags"].append({"name": "service", "value": "{{ .labels.service_type }}"})
        check_definition["pipelines"] = [
            {"api_version": "core/v2", "type": "Pipeline", "name": "sensu_metrics_to_sumo"}
        ]

    else:
        check_definition["pipelines"] = [{"api_version": "core/v2", "type": "Pipeline", "name": "sensu_checks_to_sumo"}]

    check_definition["metadata"] = dict()
    check_definition["metadata"]["name"] = check_name
    check_definition["metadata"]["namespace"] = "default"

    # Delete existing check
    response = requests.delete(
        f"{config['backend']['url']}/api/core/v2/namespaces/default/checks/{check_name}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # # Create check
    # response = requests.post(
    #     f"{config['backend']['url']}/api/core/v2/namespaces/default/checks",
    #     json=check_definition,
    #     headers={"Authorization": f"Bearer {token}"},
    # )
    # logging.info(f"{response.status_code}")


threads = []


# Create/update proxy entities
for entity in config["agents"]:

    entity_definition = dict()
    entity_definition["entity_class"] = "proxy"
    entity_definition["subscriptions"] = entity["subscriptions"]
    entity_definition["metadata"] = dict()
    entity_definition["metadata"]["name"] = entity["name"]
    entity_definition["metadata"]["namespace"] = "default"
    entity_definition["metadata"]["labels"] = entity["labels"]

    # Delete existing entity
    response = requests.delete(
        f"{config['backend']['url']}/api/core/v2/namespaces/default/entities/{entity['name']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = requests.post(
        f"{config['backend']['url']}/api/core/v2/namespaces/default/entities",
        json=entity_definition,
        headers={"Authorization": f"Bearer {token}"},
    )

# Create the checks
for check_name in config["checks"]:
    logging.info(f"Creating check {check_name}")
    check = config["checks"][check_name]
    check_definition = dict()
    check_definition["interval"] = 10
    check_definition["command"] = f"python3 generate_result.py -c {check_name} -e {{{{ .name }}}}"
    check_definition["publish"] = True
    check_definition["metadata"] = dict()
    check_definition["metadata"]["namespace"] = "default"
    check_definition["metadata"]["name"] = check_name
    check_definition["subscriptions"] = ["proxyagent"]
    check_definition["proxy_requests"] = dict()
    check_definition["proxy_requests"]["entity_attributes"] = dict()
    check_definition["proxy_requests"]["entity_attributes"] = [
        'entity.entity_class="proxy"',
        f"( entity.subscriptions.indexOf('{check['subscriptions'][0]}') >= 0)",
    ]

    if re.match(r"^metrics", check_name):
        check_definition["output_metric_format"] = "prometheus_text"
        check_definition["output_metric_tags"] = []
        check_definition["output_metric_tags"].append({"name": "entity", "value": "{{ .name }}"})
        check_definition["output_metric_tags"].append({"name": "namespace", "value": "{{ .namespace }}"})
        # check_definition["output_metric_tags"].append({"name": "os", "value": "{{ .os }}"})
        # check_definition["output_metric_tags"].append({"name": "platform", "value": "{{ .system.platform  }}"})
        check_definition["output_metric_tags"].append({"name": "zone", "value": "{{ .labels.zone }}"})
        check_definition["output_metric_tags"].append({"name": "service", "value": "{{ .labels.service_type }}"})
        check_definition["pipelines"] = [
            {"api_version": "core/v2", "type": "Pipeline", "name": "sensu_metrics_to_sumo"}
        ]
    else:
        check_definition["pipelines"] = dict()
        check_definition["pipelines"] = [{"api_version": "core/v2", "type": "Pipeline", "name": "sensu_checks_to_sumo"}]

    response = requests.delete(
        f"{config['backend']['url']}/api/core/v2/namespaces/default/checks/{check_name}",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = requests.post(
        f"{config['backend']['url']}/api/core/v2/namespaces/default/checks",
        json=check_definition,
        headers={"Authorization": f"Bearer {token}"},
    )
    logging.info(f"Got {response.status_code} - {response.content}")


#     # Spawn a thread per entity to handle posting dummy check results
#     thread = threading.Thread(target=thread_function, args=(entity,))
#     thread.start()
#     threads.append(thread)
# Start the sensu agent locally

local_agent_thread = threading.Thread(target=local_agent)
local_agent_thread.start()
threads.append(local_agent_thread)
