#!/usr/bin/env python3

"""
Requests into thousand eyes api where alerts and tests have been set up for Camelot.
The alerts if active will generate a warning and we need to reference the corresponding
test to populate the warning with some more details. Camelot should make an effort to
keep the alerts and tests up to date on the thousandeyes api for our acccount.
"""

SCRIPT_VERSION = "1.0"
# v1.0 - Initial Upload

import argparse
import requests
import json
import sys
import re


def main():
    rc = 0
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", help="config file path", required=True)
    args = parser.parse_args()

    with open(args.config) as json_data:
        cfg = json.load(json_data)

    creds = (cfg["user"], cfg["pswd"])
    alerts = requests.get(cfg["url"] + "alerts.json", auth=creds)
    alerts_json = json.loads(alerts.text)
    alert_testids = set()  # so that we can de-duplicate alerts
    for alert in alerts_json["alert"]:  # iterate all the alerts
        if alert["testId"] not in alert_testids:
            alert_testids.add(alert["testId"])  # not seen before
            test_id = alert["testId"]  # save for tests iteration
            test_name = alert["testName"]  # save for tests iteration
            active = alert["active"]  # save for tests iteration
            rule = alert["ruleExpression"].replace(r"\x{2265}", ">")  # save for tests iteration
            destination = ""
            test_type = ""
            tests = requests.get(cfg["url"] + "tests.json", auth=creds)
            tests_json = json.loads(tests.text)
            for test in tests_json["test"]:
                if test["testId"] == test_id:
                    test_type = test["type"]
                    if "url" not in test:
                        url = ""
                    else:
                        url = test["url"]
                    if "server" not in test:
                        destination = url
                    else:
                        destination = test["server"]
                    break  # because we have matched test with alert
            else:  # no corresponding test found for the alert
                continue  # iterating the alerts loop

            rule = rule.replace(",", ":")
            host = re.sub(r"http[s]*:\/\/", "", destination)
            host = re.sub(r":\d+", "", host)
            host = re.sub(r"\/.*", "", host)

            if (
                re.search("http|page-load", test_type)
                and not re.match("(a|b|vl)cise", host)
                and not re.search("camelotgroup", url)
            ):
                usergroup = "Applications"
            elif re.search("camelotgroup", url):
                usergroup = "CloudInfra"
            else:
                usergroup = "Networks"

            if re.search("Packet Loss", rule) or re.search("[Ll]atency", rule) or re.search("[Ll]oss", rule):
                usergroup = "Networks"

            if active == 1:
                print("ThousandEyes WARN: | " + test_name + ",1,0," + rule + "," + usergroup + ",Major,SOURCE: " + host)
                rc += 1  # increment the number of errors we return
            else:
                print("ThousandEyes OK: | " + test_name + ",1,0," + rule + "," + usergroup + ",Clear,SOURCE: " + host)

    return rc


if __name__ == "__main__":
    rc = main()
    exit(rc)
