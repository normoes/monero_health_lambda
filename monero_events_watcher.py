"""

monero_events_watcher

author: Norman Moeschter-Schenck
email: norman.moeschter@gmail.com

This tool is used as a AWS Lambda function.
It uses the module monero_health to get Monero daemon status information.
In case of an error/invalid Monero daemon status a mattermost webhook is triggered
"""


import logging
import os
from collections import defaultdict
import json

from monero_health import (
    daemon_combined_status_check,
    DAEMON_STATUS_OK,
    DAEMON_STATUS_ERROR,
    DAEMON_STATUS_UNKNOWN,
    HEALTH_KEY,
    LAST_BLOCK_KEY,
    DAEMON_KEY,
)

from eventhooks import MattermostWebHook


logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
logging.getLogger("Event").setLevel(logging.INFO)
logging.getLogger("DaemonHealth").setLevel(logging.WARNING)

DEBUG = False

MONERO_DAEMON = "monero"
MONERO_DAEMON_STATUS_REALM = f"{MONERO_DAEMON}_status"
MONERO_DAEMON_REALMS = {
    MONERO_DAEMON_STATUS_REALM: MONERO_DAEMON_STATUS_REALM,
}

HEALTH_ENDPOINT = HEALTH_KEY
LAST_BLOCK_ENDPOINT = LAST_BLOCK_KEY
DAEMON_ENDPOINT = DAEMON_KEY

# read securely stored environment variables set in AWS Lambda
# Use different variables locally
if "SERVERTYPE" in os.environ and os.environ["SERVERTYPE"] == "AWS Lambda":
    import boto3
    from base64 import b64decode

    ENCRYPTED = os.environ["MATTERMOST_MONERO_URL"]
    MATTERMOST_MONERO_URL = bytes.decode(
        boto3.client("kms").decrypt(CiphertextBlob=b64decode(ENCRYPTED))["Plaintext"]
    )
    ENCRYPTED = os.environ["MATTERMOST_MONERO_TOKEN"]
    MATTERMOST_MONERO_TOKEN = bytes.decode(
        boto3.client("kms").decrypt(CiphertextBlob=b64decode(ENCRYPTED))["Plaintext"]
    )
else:
    log.setLevel(logging.DEBUG)
    logging.getLogger("Event").setLevel(logging.DEBUG)
    logging.getLogger("DaemonHealth").setLevel(logging.DEBUG)
    MATTERMOST_MONERO_URL = ""
    MATTERMOST_MONERO_TOKEN = ""


class MoneroDaemonWatcher():
    def __init__(self, url=None, port=None, events=None, debug=False):
        self.debug = debug
        self.url = url
        self.port = port
        self.events = events

    def check_daemon(self):
        status = DAEMON_STATUS_UNKNOWN
        host = "---"
        block_hash = "---"
        block_timestamp = "---"
        response = defaultdict(dict)
        errors = {}

        response["daemon"] = f"{self.url}:{self.port}"
        result = daemon_combined_status_check(url=self.url, port=self.port)
        if result:
            for endpoint in (LAST_BLOCK_ENDPOINT, DAEMON_ENDPOINT):
                # Get possible errors.
                if endpoint in result:
                    result_ = result[endpoint]
                    if "error" in result_:
                        errors[endpoint] = result_["error"]
                    if endpoint == LAST_BLOCK_ENDPOINT:
                        block_hash = result[endpoint].get("hash", block_hash)
                        block_timestamp = result[endpoint].get("block_timestamp", block_timestamp)

            # Get combined 'status'.
            if "status" in result:
                status = result.get("status", status)
            if "host" in result:
                host = result.get("host", host)

            response.update(result)

        last_block_details = {"hash": block_hash, "block_timestamp": block_timestamp}
        data = {"status": status, "host": host}
        data.update(last_block_details)
        if status in (DAEMON_STATUS_ERROR, DAEMON_STATUS_UNKNOWN) or errors:
            data.update({"errors": errors})
            data_str = json.dumps(data)
            log.error(data_str)
            self.trigger(
                data=data_str,
                realm=MONERO_DAEMON_STATUS_REALM,
                debug=self.debug,
            )
        else:
            log.info(json.dumps(data))

        return response

    def trigger(self, data=None, realm=None, debug=False):
        if not self.events:
            log.warn("No events found.")
            return
        for event in self.events:
            if event:
                event.trigger(data=data, realm=realm, debug=debug)


def check_daemons(event, context):
    news = []

    monero_daemon_trigger_status = MattermostWebHook(
        name="monero_daemon_status_mattermost",
        host=MATTERMOST_MONERO_URL,
        token=MATTERMOST_MONERO_TOKEN,
        realms=(tuple(MONERO_DAEMON_REALMS.values())),
    )
    daemons = (
        # (
        #     "localhost",
        #     "18081",
        #     (
        #         monero_daemon_trigger_status,
        #     ),
        # ),
        (
            "node.xmr.to",
            "18081",
            (
                monero_daemon_trigger_status,
            ),
        ),
    )
    for daemon, port, events in daemons:
        log.debug(f"Checking: '{daemon}:{port}'.")
        watcher = MoneroDaemonWatcher(url=daemon, port=port, events=events)
        news.append(watcher.check_daemon())

    return news


if __name__ == "__main__":
    news = check_daemons(event=None, context=None)
    # for i, new in enumerate(news):
    #     if len(new) > 0:
    #         for key, value in new.items():
    #             print(f" {key}: {value}")
    #     else:
    #         print("  No news.")
