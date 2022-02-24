#!/usr/bin/env python3
import sys
import json
import logging
import re
import argparse
import base64
import os
import boto3
import time

SEVERITIES = {"minor": 3, "major": "4", "critical": 5, "crit": 5, "clear": 9}


def main() -> int:

    args_parser = argparse.ArgumentParser()
    args_parser.add_argument("-v", "--verbose", help="Enable debug logging", action="store_true")
    args_parser.add_argument("-t", "--test", help="Do not set environment to prod, unless in JSON", action="store_true")
    args_parser.add_argument("--queue-name", help="Name of the SQS queue to post to")
    args_parser.add_argument("--proxy", help="Proxy to communicate with APIs through")
    args = args_parser.parse_args()

    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
    if "verbose" in args:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Enabled debug logging")

    with sys.stdin as stdin:
        input = "".join(stdin.readlines())

        # Parse input as JSON
        json_obj = None
        try:
            json_obj = json.loads(input)
            logging.debug(f"Got JSON: {json.dumps(json_obj)}")
        except Exception as e:
            logging.error(e)
            return 1

        # Look at the JSON object and pull out what we need
        client_id = json_obj["entity"]["metadata"]["name"]

        # We need to handle situations where there might be multiple alert types coming from 1 monitor
        # We should then have multiple alert_message.XXXX lines in the check config
        # Here these are pulled out, ready to be used when parsing the output in the payload
        alert_messages = dict()
        check_interval = 0

        if json_obj["check"]["interval"]:
            check_interval = json_obj["check"]["interval"]
            logging.debug(f"Found alert interval definition of {check_interval} secs")

        if "annotations" in json_obj["check"]["metadata"] and json_obj["check"]["metadata"]["annotations"]:
            for key in json_obj["check"]["metadata"]["annotations"]:

                match1 = re.match(r"alert_message$", key)
                match2 = re.match(r"alert_message\.(.*)?", key)
                if match1:
                    alert_messages["standard"] = json_obj["check"]["metadata"]["annotations"][key]

                    break
                # If there's no full stop in the key then there aren't going to be any subtypes
                elif match2:
                    alert_messages[match2.group(1)] = json_obj["check"]["metadata"]["annotations"][key]

        logging.debug(f"Got the following alert messages: {alert_messages}")

        if not alert_messages:
            logging.debug("No alert messages. Using default format")
            alert_messages[
                "standard"
            ] = "Alert from ::client_id:: ID: ::id:: Threshold: ::threshold:: Current Value: ::current_value:: Additional Message: ::additional_text:: (DEFAULT MESSAGE)"

        # Now parse the output
        # Example output: "FSUsage WARN: / 9.5% usage (2.8 GB/30.0 GB) | /,9.5,4,(2.8 GB/30.0 GB),SysAut,Major\n"
        check_result = json_obj["check"]["output"]
        # Remove Windows newlines
        check_result = re.sub(r"\r", "", check_result).splitlines()
        for line in check_result:

            (
                check_type,
                state,
                id,
                current_value,
                threshold,
                additional_text,
                team,
                severity,
                summary,
                expiry,
                environment,
            ) = (None,) * 11

            logging.debug(f"Check line: {line}")

            match_comment = re.match(r"^#", line)
            match_standard = re.match(
                r"^([^\s]+) (WARN|CRITICAL|CRIT|OK): .*? \| ([^,]+),([^,]+),([^,]*),([^,]*),([^,]+),([^,]+)$", line
            )
            match_standard_custom_source = re.match(
                r"^([^\s]+) (WARN|CRITICAL|CRIT|OK): .*? \| ([^,]+),([^,]+),([^,]*),([^,]*),([^,]+),([^,]+),SOURCE: (.*?)$",
                line,
            )
            match_grafana_alert = re.match(
                r"Grafana Alert:",
                line,
            )

            match_keepalive = re.match(
                r"^No keepalive sent from .*? for (\d+) seconds ",
                line,
            )

            match_timeout = re.match(
                r"^Execution timed out|Unable to TERM.KILL the process",
                line,
            )

            match_keepalive_clear = re.match(
                r"Keepalive last sent from",
                line,
            )

            match_generic_ok = re.match(
                r"^([^\s]+) OK: .*?",
                line,
            )

            match_graphite_metrics = re.match(
                r"^(\w+\.)+\w+ ([^\s]+) \d+",
                line,
            )

            if match_comment:
                # Ignore comments
                continue

            if match_standard:
                check_type = match_standard.group(1)
                state = match_standard.group(2)
                id = match_standard.group(3)
                current_value = match_standard.group(4)
                threshold = match_standard.group(5)
                additional_text = match_standard.group(6)
                team = match_standard.group(7)
                severity = match_standard.group(8)

                logging.debug(
                    f"Mapping values to: Check_type: {check_type}  State: {state}  ID: {id}  Current_value: {current_value}  Threshold: {threshold}  Additional_text: {additional_text}  Team: {team}  Severity: {severity}"
                )

            elif match_standard_custom_source:
                check_type = match_standard.group(1)
                state = match_standard.group(2)
                id = match_standard.group(3)
                current_value = match_standard.group(4)
                threshold = match_standard.group(5)
                additional_text = match_standard.group(6)
                team = match_standard.group(7)
                severity = match_standard.group(8)
                client_id = match_standard.group(9)

            elif match_grafana_alert:
                summary = json_obj["check"]["output"]
                severity = re.sub(r".*\| ", "")
                id = summary

            elif match_keepalive:
                summary = f"Sensu agent offline - No communication for {(match_keepalive.group(1)/60):.1f} mins"
                team = "SysAut"
                severity = "Major"
                id = "Sensu agent offline"
                check_type = "keepalive"
                expiry = 130
            elif match_timeout:
                summary = f"Timeout running - {json_obj['check']['metadata']['name']} - Monitor frequency is: {(check_interval/60):.1f} mins."
                id = json_obj["check"]["metadata"]["name"]
                if json_obj["check"]["occurrences"] < 3:
                    logging.debug("Ignoring timeout until there have been 3 occurrences")
                    continue

                state = "WARN"
                severity = "Minor"
                expiry = check_interval + 15
                team = "SysAut"

            elif match_keepalive_clear:
                summary = "CLEAR - Sensu agent is now online"
                team = "SysAut"
                severity = "Major"
                id = "Sensu agent offline"
                severity = "Clear"
                check_type = "keepalive"

            elif match_generic_ok:
                # If this is just a standard OK that hasn't matched above, there's nothing to clear, so we can ignore this line
                continue
            elif match_graphite_metrics:
                # Ignore lines that look like graphite metrics
                continue
            else:
                # Incase the output is an error from the agent itself.. e.g. the script doesnt exist
                # sh: check-ports.pl: command not found
                summary = f"Invalid Sensu check result - {json_obj['check']['metadata']['name']} - {line}"
                state = "WARN"
                severity = "Major"
                team = "SysAut"
                id = summary
                expiry = check_interval + 60

                # Strip out unique second counts
                id = re.sub(r" \d+ seconds ago", " X seconds ago")

            # Ignore info messages
            if severity == "Info":
                continue

            # Get the appropriate alert_message and populate the tokens
            # Only if the above matched and summary hasn't already been overridden
            if not summary:
                if "standard" in alert_messages and alert_messages["standard"]:
                    summary = alert_messages["standard"]
                else:
                    summary = alert_messages[check_type]

                    # If summary is still blank, something must be wrong with the monitor
                    # Try populating summary with additional text, failing that, just use a generic error message
                    if not summary:
                        if additional_text:
                            summary = f"{check_type} - ::id::: ::additional_text::"
                        else:
                            summary = f"{check_type} - Monitor error. Please investigate configs"

            logging.debug(f"Using the following alert_message - {summary}")
            summary = re.sub("::client_id::", client_id, summary)
            summary = re.sub("::id::", id, summary)
            summary = re.sub("::threshold::", threshold, summary)
            summary = re.sub("::current_value::", current_value, summary)
            summary = re.sub("::additional_text::", additional_text, summary)

            match_metric_errors = re.match(r"(check.*has not run recently|Metric check.*is erroring)", summary)
            # For metric check errors, override the default expiry to something short, so they clear quickly if the problem goes away and we don't get a clear
            if match_metric_errors:
                expiry = check_interval + 60

            # If the alert is clearing, then append this to the start of the summary
            if state == "OK":
                severity = "Clear"
                summary = f"CLEAR - {summary}"

            alert_key = f"{client_id}_{check_type}_{id}"

            # Now send a trap
            severity = SEVERITIES[severity.lower()]
            logging.debug(f"Mapped severity to {severity}")

            # Perform some additional checks for heartbeats and metrics status results
            skip_trap = False
            if severity == 0:
                if check_type != "SensuHB:" and check_type != "MetricsStatus:":
                    logging.debug("Skipping sending trap for already cleared alert")
                    skip_trap = True
                else:
                    # Sensu heartbeat should always be a warning level alert
                    #  so it doesnt clear
                    severity = 2

            if not skip_trap:

                # Client ID needs to be just the node name, without the FQDN on the end
                client_id = re.sub(r"\..*", "", client_id)

                # Create a JSON payload to send to SQS queue
                payload = {
                    "node": client_id,
                    "alertKey": alert_key,
                    "summary": summary,
                    "severity": severity,
                    "team": team,
                    "expiry": expiry,
                    "environment": environment,
                }
                b64_payload = base64.b64encode(json.dumps(payload).encode("UTF-8"))
                logging.debug(json.dumps(payload))
                logging.debug(b64_payload)

                # Send payload to SQS
                proxy = args.proxy
                if proxy:
                    proxy = f"http://{proxy}"
                    os.environ["http_proxy"] = proxy
                    os.environ["HTTP_PROXY"] = proxy
                    os.environ["https_proxy"] = proxy
                    os.environ["HTTPS_PROXY"] = proxy

                sqs = boto3.resource("sqs")
                queue = sqs.get_queue_by_name(QueueName=args.queue_name)
                response = queue.send_message(
                    MessageBody=b64_payload.decode(encoding="UTF-8"),
                    MessageGroupId="sensu-alerts",
                    MessageDeduplicationId=f"{alert_key}{time.time()}",
                )

                print(response.get("MessageId"))
                print(response.get("MD5OfMessageBody"))


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
