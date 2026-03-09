#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2017, Thierry Sallé (@seuf)
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

DOCUMENTATION = """
---
module: grafana_alert_rules
author:
  - Thierry Sallé (@seuf)
version_added: "1.0.0"
short_description: Manage Grafana Alert Rules
description:
  - Create, update, delete, export Grafana Alert Rules via API.
options:
  org_id:
    description:
      - The Grafana organization ID where the alert rule will be imported / exported / deleted.
      - Not used when I(grafana_api_key) is set, because the grafana_api_key only belongs to one organization.
      - Mutually exclusive with C(org_name).
    default: 1
    type: int
  org_name:
    description:
      - The Grafana organization name where the alert rule will be imported / exported / deleted.
      - Not used when I(grafana_api_key) is set, because the grafana_api_key only belongs to one organization.
      - Mutually exclusive with C(org_id).
    type: str
  folder:
    description:
      - UID of the folder where the alert rule will be created or imported.
      - Required if C(parent_folder) is set.
    default: General
    version_added: "1.0.0"
    type: str
  parent_folder:
    description:
      - UID of the parent folder used to scope the search for the specified C(folder).
      - Available with subfolder feature of Grafana 11.
    version_added: "2.2.0"
    type: str
  state:
    description:
      - State of the alert rule.
    choices: [ absent, export, present ]
    default: present
    type: str
  uid:
    version_added: "1.0.0"
    description:
      - Used to identify the alert rule when C(state) is C(export) or C(absent).
      - When C(state) is C(present), this can be used to set the UID during alert rule creation.
    type: str
  path:
    description:
      - The path to the json file containing the Grafana alert rule to import or export.
      - A http URL is also accepted (since 2.10).
      - Required if C(state) is C(export) or C(present).
    aliases: [ alert_rule_url ]
    type: str
  overwrite:
    description:
      - Override existing alert rule when state is present.
    type: bool
    default: false
  title:
    description:
      - Title of the alert rule.
      - Used when searching for alert rules.
    type: str
extends_documentation_fragment:
- community.grafana.basic_auth
- community.grafana.api_key
"""

EXAMPLES = """
- name: Import Grafana alert rule from file
  community.grafana.grafana_alert_rules:
    url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    state: present
    overwrite: true
    path: /path/to/alert_rules/cpu_alert.json

- name: Import Grafana alert rule from URL
  community.grafana.grafana_alert_rules:
    url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    folder: alerts
    alert_rule_url: https://example.com/alert_rules/cpu_alert.json

- name: Import Grafana alert rule in a subfolder
  community.grafana.grafana_alert_rules:
    url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    parent_folder: alerts
    folder: system
    path: /path/to/alert_rules/cpu_alert.json

- name: Export alert rule
  community.grafana.grafana_alert_rules:
    url: http://grafana.company.com
    url_username: "admin"
    url_password: "{{ grafana_password }}"
    org_id: 1
    state: export
    uid: "cpu_alert_001"
    path: "/path/to/export/cpu_alert.json"

- name: Delete alert rule
  community.grafana.grafana_alert_rules:
    url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    state: absent
    uid: "cpu_alert_001"
"""

RETURN = """
---
uid:
  description: uid of the created / deleted / exported alert rule.
  returned: success
  type: str
  sample: cpu_alert_001
"""

import json
import copy
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils._text import to_native
from ansible.module_utils._text import to_text
from ansible_collections.community.grafana.plugins.module_utils.base import (
    grafana_argument_spec,
    clean_url
)

__metaclass__ = type


class GrafanaAPIException(Exception):
    pass


class GrafanaMalformedJson(Exception):
    pass


class GrafanaExportException(Exception):
    pass


class GrafanaDeleteException(Exception):
    pass


def parse_grafana_version(version_str):
    """
    Parse Grafana version string and return a dict with version components.

    Args:
        version_str: Version string like "9.5.3" or "11.0.0"

    Returns:
        dict: {"major": 9, "minor": 5, "patch": 3}

    Examples:
        >>> parse_grafana_version("9.5.3")
        {"major": 9, "minor": 5, "patch": 3}
        >>> parse_grafana_version("11.0.0")
        {"major": 11, "minor": 0, "patch": 0}
    """
    try:
        # Remove any pre-release or build metadata (e.g., "9.5.3-beta1" -> "9.5.3")
        version_str = version_str.split('-')[0].split('+')[0]

        parts = version_str.split('.')
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0

        return {
            "major": major,
            "minor": minor,
            "patch": patch
        }
    except (ValueError, AttributeError, IndexError) as e:
        raise GrafanaAPIException("Unable to parse Grafana version '%s': %s" % (version_str, str(e)))


def grafana_organization_id_by_name(module, url, org_name, headers):
    r, info = fetch_url(
        module, "%s/api/user/orgs" % url, headers=headers, method="GET"
    )
    if info["status"] != 200:
        raise GrafanaAPIException("Unable to retrieve users organizations: %s" % info)
    organizations = json.loads(to_text(r.read()))
    for org in organizations:
        if org["name"] == org_name:
            return org["orgId"]

    raise GrafanaAPIException(
        "Current user isn't member of organization: %s" % org_name
    )


def grafana_switch_organization(module, url, org_id, headers):
    r, info = fetch_url(
        module,
        "%s/api/user/using/%s" % (url, org_id),
        headers=headers,
        method="POST",
        )
    if info["status"] != 200:
        raise GrafanaAPIException(
            "Unable to switch to organization %s : %s" % (org_id, info)
        )


def grafana_headers(module, data):
    headers = {"content-type": "application/json; charset=utf8"}
    if "grafana_api_key" in data and data["grafana_api_key"]:
        headers["Authorization"] = "Bearer %s" % data["grafana_api_key"]
    else:
        module.params["force_basic_auth"] = True
        if module.params["org_name"]:
            org_name = module.params["org_name"]
            data["org_id"] = grafana_organization_id_by_name(
                module, data["url"], org_name, headers
            )
        grafana_switch_organization(module, data["url"], data["org_id"], headers)

    return headers


def get_grafana_version(module, url, headers):
    r, info = fetch_url(
        module, "%s/api/frontend/settings" % url, headers=headers, method="GET"
    )
    if info["status"] == 200:
        try:
            settings = json.loads(to_text(r.read()))
            grafana_version = parse_grafana_version(settings["buildInfo"]["version"])
        except UnicodeError:
            raise GrafanaAPIException("Unable to decode version string to Unicode")
        except Exception as e:
            raise GrafanaAPIException(e)
    else:
        raise GrafanaAPIException("Unable to get grafana version: %s" % info)

    return grafana_version.get("major")


def grafana_folder_exists(module, url, folder_name, parent_folder, headers):
    # the 'General' folder is a special case, it's UID is 'general'
    if folder_name == "General":
        return True, 0

    try:
        folder_url = "%s/api/folders" % url
        if parent_folder:
            folder_url = "%s?parentUid=%s" % (folder_url, parent_folder)

        r, info = fetch_url(module, folder_url, headers=headers, method="GET")

        if info["status"] != 200:
            raise GrafanaAPIException(
                "Unable to query Grafana API for folders (name: %s): %d"
                % (folder_name, info["status"])
            )

        folders = json.loads(r.read())

        for folder in folders:
            if folder_name in (folder["title"], folder["uid"]):
                return True, folder["uid"]
    except Exception as e:
        raise GrafanaAPIException(e)

    return False, 0


def grafana_alert_rule_exists(module, url, uid, headers):
    alert_rule_exists = False
    alert_rule = {}

    # Grafana unified alerting (v8+) uses /api/v1/provisioning/alert-rules/{uid}
    grafana_version = get_grafana_version(module, url, headers)
    if grafana_version >= 8:
        uri = "%s/api/v1/provisioning/alert-rules/%s" % (url, uid)
    else:
        raise GrafanaAPIException(
            "Alert rules require Grafana 8 or higher (unified alerting). Current version: %d" % grafana_version
        )

    r, info = fetch_url(module, uri, headers=headers, method="GET")

    if info["status"] == 200:
        alert_rule_exists = True
        try:
            alert_rule = json.loads(r.read())
        except Exception as e:
            raise GrafanaAPIException(e)
    elif info["status"] == 404:
        alert_rule_exists = False
    else:
        raise GrafanaAPIException("Unable to get alert rule %s : %s" % (uid, info))

    return alert_rule_exists, alert_rule


def grafana_alert_rule_search(module, url, folder_uid, title, headers):
    # search alert rules by title
    uri = "%s/api/v1/provisioning/alert-rules" % (
        url
    )
    r, info = fetch_url(module, uri, headers=headers, method="GET")

    if info["status"] == 200:
        try:
            alert_rules = json.loads(r.read())
            for r in alert_rules:
                if r["title"] == title and r["folderUID"] == folder_uid:
                    return grafana_alert_rule_exists(
                        module, url, r["uid"], headers
                    )
        except Exception as e:
            raise GrafanaAPIException(e)
    else:
        raise GrafanaAPIException("Unable to search alert rule %s : %s" % (title, info))

    return False, None


# for comparison, we sometimes need to ignore a few keys
def is_grafana_alert_rule_changed(payload, alert_rule):
    # Compare the payload with the existing alert rule
    # Ignore certain fields that are auto-generated or managed by Grafana

    # Create copies to avoid modifying the originals
    payload_copy = copy.deepcopy(payload)
    alert_rule_copy = copy.deepcopy(alert_rule)

    # Remove auto-generated/managed fields for comparison
    fields_to_ignore = ["id", "updated", "provenance"]

    for field in fields_to_ignore:
        payload_copy.pop(field, None)
        alert_rule_copy.pop(field, None)

    if payload_copy == alert_rule_copy:
        return False
    return True


def grafana_import_alert_rule_groups(module, data, payload, headers):
    # Check that the groups JSON is nested under the 'groups' key
    if "groups" not in payload:
        raise GrafanaAPIException("Invalid alert rule groups format: missing 'groups' key")

    # Get the folder UID using the helper function
    folder_exists, folder_uid = grafana_folder_exists(
        module, data["url"], data["folder"], data.get("parent_folder"), headers
    )
    if not folder_exists:
        raise GrafanaAPIException(
            "Alert rule folder '%s' does not exist." % data["folder"]
        )

    # Process each group and its rules
    for group in payload["groups"]:
        if "rules" not in group:
            continue

        for rule in group["rules"]:
            try:
                # Create individual alert rule payload
                alert_rule_payload = {
                    "uid": rule.get("uid"),
                    "title": rule.get("title"),
                    "condition": rule.get("condition"),
                    "data": rule.get("data", []),
                    "noDataState": rule.get("noDataState", "NoData"),
                    "execErrState": rule.get("execErrState", "Alerting"),
                    "for": rule.get("for", "5m"),
                    "annotations": rule.get("annotations", {}),
                    "labels": rule.get("labels", {}),
                    "folderUID": folder_uid,
                }

                # Remove None values
                alert_rule_payload = {k: v for k, v in alert_rule_payload.items() if v is not None}

                uid = alert_rule_payload.get("uid")
                title = alert_rule_payload.get("title")

                if not uid and not title:
                    raise GrafanaAPIException("Alert rule missing both uid and title - at least one is required")

                # Check if alert rule already exists
                alert_rule_exists = False
                existing_rule = None

                if uid:
                    alert_rule_exists, existing_rule = grafana_alert_rule_exists(
                        module, data["url"], uid, headers=headers
                    )
                elif title:
                    alert_rule_exists, existing_rule = grafana_alert_rule_search(
                        module, data["url"], folder_uid, title, headers=headers
                    )

                if alert_rule_exists:
                    # Check if update is needed
                    if is_grafana_alert_rule_changed(alert_rule_payload, existing_rule):
                        if data.get("overwrite"):
                            # Update existing rule
                            r, info = fetch_url(
                                module,
                                "%s/api/v1/provisioning/alert-rules/%s" % (data["url"], uid),
                                data=json.dumps(alert_rule_payload),
                                headers=headers,
                                method="PUT",
                            )
                            if info["status"] != 200:
                                body = info.get("body", "")
                                raise GrafanaAPIException("Failed to update rule '%s': HTTP %s - %s" % (title or uid, info["status"], body))
                        else:
                            # Rule exists but overwrite is False, skip
                            continue
                    # else: rule unchanged, continue to next
                else:
                    # Create new rule
                    r, info = fetch_url(
                        module,
                        "%s/api/v1/provisioning/alert-rules" % data["url"],
                        data=json.dumps(alert_rule_payload),
                        headers=headers,
                        method="POST",
                    )
                    if info["status"] != 201:
                        body = info.get("body", "")
                        raise GrafanaAPIException("Failed to create rule '%s': HTTP %s - %s" % (title or uid, info["status"], body))

            except Exception as e:
                raise GrafanaAPIException("Error processing rule '%s': %s" % (rule.get("title", "unknown"), str(e)))

    # Return simple result
    result = {
        "msg": "Alert rule groups processed successfully",
        "changed": True,
    }

    return result


def grafana_create_alert_rule(module, data):
    # define data payload for grafana API
    payload = {}
    try:
        with open(data["path"], "r", encoding="utf-8") as json_file:
            payload = json.load(json_file)
    except Exception as e:
        raise GrafanaAPIException("Can't load json file %s" % to_native(e))

    # define http header
    headers = grafana_headers(module, data)

    grafana_version = get_grafana_version(module, data["url"], headers)

    # Alert rules require Grafana 8+ (unified alerting)
    if grafana_version < 8:
        raise GrafanaAPIException(
            "Alert rules require Grafana 8 or higher (unified alerting). Current version: %d" % grafana_version
        )

    # Check if this is an alert rule groups format (provisioning format)
    if "apiVersion" in payload and "groups" in payload:
        # Delegate to the groups import function to handle all rules
        return grafana_import_alert_rule_groups(module, data, payload, headers)

    # Extract UID and title from data or payload (for individual alert rules)
    uid = data.get("uid") or payload.get("uid")
    title = data.get("title") or payload.get("title")

    # Set UID in payload if provided in data
    if data.get("uid"):
        payload["uid"] = data["uid"]

    result = {}

    # test if the folder exists
    if data.get("parent_folder") and grafana_version < 11:
        module.fail_json(
            failed=True, msg="Subfolder API is available starting Grafana v11"
        )

    # Verify folder exists and get folder UID
    folder_exists, folder_uid = grafana_folder_exists(
        module, data["url"], data["folder"], data.get("parent_folder"), headers
    )
    if folder_exists is False:
        raise GrafanaAPIException(
            "Alert rule folder '%s' does not exist." % data["folder"]
        )

    payload["folderUID"] = folder_uid

    # test if alert rule already exists
    if uid:
        alert_rule_exists, alert_rule = grafana_alert_rule_exists(
            module, data["url"], uid, headers=headers
        )
    elif title:
        alert_rule_exists, alert_rule = grafana_alert_rule_search(
            module,
            data["url"],
            folder_uid,
            title,
            headers=headers,
        )
    else:
        raise GrafanaAPIException("Either uid or title must be provided for individual alert rules")

    if alert_rule_exists is True:
        grafana_alert_rule_changed = is_grafana_alert_rule_changed(payload, alert_rule)

        if grafana_alert_rule_changed:
            if module.check_mode:
                module.exit_json(
                    uid=uid,
                    failed=False,
                    changed=True,
                    msg="Alert rule %s will be updated" % payload.get("title", uid),
                )
            # update
            if not data.get("overwrite"):
                raise GrafanaAPIException(
                    "Alert rule %s already exists. Use overwrite=true to update." % uid
                )

            r, info = fetch_url(
                module,
                "%s/api/v1/provisioning/alert-rules/%s" % (data["url"], uid),
                data=json.dumps(payload),
                headers=headers,
                method="PUT",
            )
            if info["status"] == 200:
                try:
                    alert_rule = json.loads(r.read())
                    uid = alert_rule["uid"]
                except Exception as e:
                    raise GrafanaAPIException(e)
                result["uid"] = uid
                result["msg"] = "Alert rule %s updated" % payload.get("title", uid)
                result["changed"] = True
            else:
                body = json.loads(info["body"]) if info.get("body") else {}
                raise GrafanaAPIException(
                    "Unable to update the alert rule %s : %s (HTTP: %d)"
                    % (uid, body.get("message", info), info["status"])
                )
        else:
            # unchanged
            result["uid"] = uid
            result["msg"] = "Alert rule %s unchanged." % payload.get("title", uid)
            result["changed"] = False
    else:
        if module.check_mode:
            module.exit_json(
                failed=False,
                changed=True,
                msg="Alert rule %s will be created" % payload.get("title", ""),
            )

        r, info = fetch_url(
            module,
            "%s/api/v1/provisioning/alert-rules" % data["url"],
            data=json.dumps(payload),
            headers=headers,
            method="POST",
        )
        if info["status"] == 201:
            result["msg"] = "Alert rule %s created" % payload.get("title", "")
            result["changed"] = True
            try:
                alert_rule = json.loads(r.read())
                uid = alert_rule["uid"]
            except Exception as e:
                raise GrafanaAPIException(e)
            result["uid"] = uid
        else:
            body = json.loads(info["body"]) if info.get("body") else {}
            raise GrafanaAPIException(
                "Unable to create the new alert rule %s : %s (HTTP: %d)"
                % (payload.get("title", ""), body.get("message", info), info["status"])
            )

    return result


def grafana_delete_alert_rule(module, data):
    # define http headers
    headers = grafana_headers(module, data)

    grafana_version = get_grafana_version(module, data["url"], headers)

    # Alert rules require Grafana 8+ (unified alerting)
    if grafana_version < 8:
        raise GrafanaAPIException(
            "Alert rules require Grafana 8 or higher (unified alerting). Current version: %d" % grafana_version
        )
    else:
        if data.get("uid"):
            uid = data["uid"]
        else:
            raise GrafanaDeleteException("No uid specified %s")

    # test if alert rule already exists
    alert_rule_exists, alert_rule = grafana_alert_rule_exists(
        module, data["url"], uid, headers=headers
    )

    result = {}
    if alert_rule_exists is True:
        if module.check_mode:
            module.exit_json(
                uid=uid,
                failed=False,
                changed=True,
                msg="Alert rule %s will be deleted" % uid,
            )

        # delete
        r, info = fetch_url(
            module,
            "%s/api/v1/provisioning/alert-rules/%s" % (data["url"], uid),
            headers=headers,
            method="DELETE",
        )
        if info["status"] == 204:
            result["msg"] = "Alert rule %s deleted" % uid
            result["changed"] = True
            result["uid"] = uid
        else:
            raise GrafanaAPIException(
                "Unable to delete the alert rule %s : %s" % (uid, info)
            )
    else:
        # alert rule does not exist, do nothing
        result = {
            "msg": "Alert rule %s does not exist." % uid,
            "changed": False,
            "uid": uid,
        }

    return result


def grafana_export_alert_rule(module, data):
    # define http headers
    headers = grafana_headers(module, data)

    grafana_version = get_grafana_version(module, data["url"], headers)
    if grafana_version < 8:
        raise GrafanaAPIException(
            "Alert rules require Grafana 8 or higher (unified alerting). Current version: %d" % grafana_version
        )
    if data.get("uid"):
        uid = data["uid"]
    else:
        raise GrafanaExportException("No uid specified")

    # test if alert rule already exists
    alert_rule_exists, alert_rule = grafana_alert_rule_exists(
        module, data["url"], uid, headers=headers
    )

    if alert_rule_exists is True:
        if module.check_mode:
            module.exit_json(
                uid=uid,
                failed=False,
                changed=True,
                msg="Alert rule %s will be exported to %s" % (uid, data["path"]),
            )
        try:
            with open(data["path"], "w", encoding="utf-8") as f:
                f.write(json.dumps(alert_rule, indent=2))
        except Exception as e:
            raise GrafanaExportException("Can't write json file : %s" % to_native(e))
        result = {
            "msg": "Alert rule %s exported to %s" % (uid, data["path"]),
            "uid": uid,
            "changed": True,
        }
    else:
        result = {
            "msg": "Alert rule %s does not exist." % uid,
            "uid": uid,
            "changed": False,
        }

    return result


def main():
    # use the predefined argument spec for url
    argument_spec = grafana_argument_spec()
    argument_spec.update(
        state=dict(choices=["present", "absent", "export"], default="present"),
        org_id=dict(default=1, type="int"),
        org_name=dict(type="str"),
        folder=dict(type="str", default="General"),
        parent_folder=dict(type="str"),
        uid=dict(type="str"),
        path=dict(aliases=["alert_rule_url"], type="str"),
        overwrite=dict(type="bool", default=False),
        title=dict(type="str"),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[
            ["state", "export", ["path", "uid"]],
            ["state", "present", ["path"]],
        ],
        required_together=[["url_username", "url_password", "org_id"]],
        mutually_exclusive=[
            ["url_username", "grafana_api_key"],
            ["org_id", "org_name"],
        ],
    )

    module.params["url"] = clean_url(module.params["url"])

    try:
        if module.params["state"] == "present":
            result = grafana_create_alert_rule(module, module.params)
        elif module.params["state"] == "absent":
            result = grafana_delete_alert_rule(module, module.params)
        else:
            result = grafana_export_alert_rule(module, module.params)
    except GrafanaAPIException as e:
        module.fail_json(failed=True, msg="error : %s" % to_native(e))
        return
    except GrafanaMalformedJson as e:
        module.fail_json(failed=True, msg="error : %s" % to_native(e))
        return
    except GrafanaDeleteException as e:
        module.fail_json(
            failed=True, msg="error : Can't delete alert rule : %s" % to_native(e)
        )
        return
    except GrafanaExportException as e:
        module.fail_json(
            failed=True, msg="error : Can't export alert rule : %s" % to_native(e)
        )
        return

    module.exit_json(failed=False, **result)
    return


if __name__ == "__main__":
    main()
