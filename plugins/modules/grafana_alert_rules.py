#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2017, Thierry Sallé (@seuf)
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

DOCUMENTATION = r"""
---
module: grafana_alert_rules
author:
  - Artis Cevers @Artis_Code
version_added: "1.0.0"
short_description: Manage Grafana unified alert rules through the API
description:
  - Create, update, delete, or export Grafana unified alert rules using the provisioning API.
  - This module supports Grafana versions 8 and higher, where the unified alerting system was introduced.
  - It can also interact with subfolders starting from Grafana v11.
  - Authentication can be provided using either a Grafana API key or basic authentication credentials.

options:
  grafana_url:
    description:
      - URL to the Grafana server.
      - Example: http://grafana.example.com
    required: true
    type: str

  grafana_api_key:
    description:
      - Grafana API key for bearer authentication.
      - When provided, C(org_id) and C(org_name) are ignored because an API key belongs to one organization.
    type: str

  url_username:
    description:
      - Username for HTTP basic authentication.
      - Mutually exclusive with C(grafana_api_key).
    type: str

  url_password:
    description:
      - Password for HTTP basic authentication.
      - Required when C(url_username) is set.
    type: str

  org_id:
    description:
      - Grafana organization ID where the alert rule will be managed.
      - Not used when C(grafana_api_key) is provided.
      - Mutually exclusive with C(org_name).
    type: int
    default: 1

  org_name:
    description:
      - Grafana organization name where the alert rule will be managed.
      - Not used when C(grafana_api_key) is provided.
      - Mutually exclusive with C(org_id).
    type: str

  folder:
    description:
      - UID of the folder where the alert rule resides or will be created.
      - Default folder is C(General).
    type: str
    default: General
    version_added: "1.0.0"

  parent_folder:
    description:
      - UID of the parent folder used to scope subfolder searches.
      - Only available starting with Grafana v11 (subfolder feature).
    type: str
    version_added: "2.2.0"

  state:
    description:
      - Desired state of the alert rule.
      - C(present) → create or update an alert rule.
      - C(absent) → delete an existing alert rule.
      - C(export) → export an existing alert rule to a file.
    type: str
    default: present
    choices: [present, absent, export]

  uid:
    description:
      - Unique identifier (UID) of the alert rule.
      - Used for lookup or update when C(state) is C(export) or C(absent).
      - Can also be explicitly provided during creation when C(state) is C(present).
    type: str
    version_added: "1.0.0"

  path:
    description:
      - Local filesystem path or remote HTTP URL pointing to the alert rule JSON document.
      - Required when C(state) is C(present) or C(export).
      - Alias: C(alert_rule_url).
    type: str
    aliases:
      - alert_rule_url

  overwrite:
    description:
      - Whether to override an existing alert rule when C(state) is C(present).
      - If false and the rule already exists, the operation will fail unless the rule differs.
    type: bool
    default: false

  title:
    description:
      - Title of the alert rule.
      - Used as a human-readable identifier in messages.
    type: str

extends_documentation_fragment:
  - community.grafana.basic_auth
  - community.grafana.api_key

requirements:
  - Grafana >= 8.0.0
  - Python >= 3.6
  - Ansible >= 2.10
notes:
  - Unified alert rules and provisioning endpoints were introduced in Grafana 8.
  - Subfolder operations require Grafana 11 or newer.
  - The module supports Ansible check mode for dry-run operations.
"""

EXAMPLES = r"""
- name: Create or update a Grafana alert rule
  community.grafana.grafana_alert_rules:
    grafana_url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    state: present
    overwrite: true
    path: /path/to/alert_rules/cpu_alert.json

- name: Import Grafana alert rule from URL
  community.grafana.grafana_alert_rules:
    grafana_url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    folder: alerts
    alert_rule_url: https://example.com/alert_rules/cpu_alert.json

- name: Import Grafana alert rule into a subfolder
  community.grafana.grafana_alert_rules:
    grafana_url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    parent_folder: alerts
    folder: system
    path: /path/to/alert_rules/system_alert.json

- name: Export existing alert rule to a local JSON file
  community.grafana.grafana_alert_rules:
    grafana_url: http://grafana.company.com
    url_username: "admin"
    url_password: "{{ grafana_password }}"
    org_id: 1
    state: export
    uid: "cpu_alert_001"
    path: "/exports/cpu_alert.json"

- name: Delete an existing alert rule
  community.grafana.grafana_alert_rules:
    grafana_url: http://grafana.company.com
    grafana_api_key: "{{ grafana_api_key }}"
    state: absent
    uid: "cpu_alert_001"
"""

RETURN = r"""
---
uid:
  description:
    - UID of the alert rule that was created, updated, deleted, or exported.
  returned: always
  type: str
  sample: cpu_alert_001

msg:
  description:
    - Human-readable message describing the operation result.
  returned: always
  type: str
  sample: Alert rule cpu_alert_001 updated.

changed:
  description:
    - Indicates whether the alert rule was changed (created, updated, deleted, or exported).
  returned: always
  type: bool
  sample: true
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


def grafana_organization_id_by_name(module, grafana_url, org_name, headers):

    # --- Send GET request -------------------------------
    r, info = fetch_url(
        module,
        "%s/api/user/orgs" % grafana_url,
        headers=headers,
        method="GET"
    )

    # --- Check response status -----------------------------------------------
    status = info.get("status", 0)
    if status != 200:
        raise GrafanaAPIException(
            "Unable to retrieve users organizations: %s" % info
        )

    # --- Parse response content ----------------------------------------------
    organizations = json.loads(to_text(r.read()))
    for org in organizations:
        if org["name"] == org_name:
            return org["orgId"]

    # --- If not found, raise an explicit exception ----------------------------
    raise GrafanaAPIException(
        "Current user isn't member of organization: %s" % org_name
    )


def grafana_switch_organization(module, grafana_url, org_id, headers):

    # --- Send POST request -------------------------------
    r, info = fetch_url(
        module,
        "%s/api/user/using/%s" % (grafana_url, org_id),
        headers=headers,
        method="POST",
    )

    # --- Check response status -----------------------------------------------
    status = info.get("status", 0)
    if status != 200:
        raise GrafanaAPIException(
            "Unable to switch to organization %s : %s" % (org_id, info)
        )


def grafana_headers(module, data):

    headers = {"content-type": "application/json; charset=utf8"}

    api_key = data.get("grafana_api_key")
    grafana_url = data.get("url")

    # --- Use API key authentication if available -----------------------------
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key

    # --- Otherwise fall back to basic auth and org switch ---------------------
    else:
        # Ask Ansible to use basic authentication
        module.params["force_basic_auth"] = True

        # Optional organization handling
        org_name = module.params["org_name"]
        if org_name:
            org_id = grafana_organization_id_by_name(
                module,
                grafana_url,
                org_name,
                headers
            )

            data["org_id"] = org_id

            grafana_switch_organization(module, grafana_url, org_id, headers)

    return headers


def get_grafana_version(module, grafana_url, headers):

    # --- Perform GET request --------------------------------------------------
    r, info = fetch_url(
        module,
        "%s/api/frontend/settings" % grafana_url,
        headers=headers,
        method="GET"
    )
    status = info.get("status", 0)
    if status == 200:

    # --- Extract and parse version -------------------------------------------
        try:
            settings = json.loads(to_text(r.read()))
            grafana_version = parse_grafana_version(settings["buildInfo"]["version"])
        except UnicodeError:
            raise GrafanaAPIException(
                "Unable to decode version string to Unicode"
            )
        except Exception as e:
            raise GrafanaAPIException(e)
    else:
        raise GrafanaAPIException(
            "Unable to get grafana version: %s" % info
        )

    # --- Return major version -------------------------------------------------
    return grafana_version.get("major")


def grafana_alert_rule_exists(module, grafana_url, uid, headers):

    alert_rule = {}

    # --- Determine Grafana major version -------------------------------------
    grafana_version = get_grafana_version(module, grafana_url, headers)
    if grafana_version < 8:
        raise GrafanaAPIException(
            f"Alert rules require Grafana 8 or higher (unified alerting). "
            f"Current version: %d" % grafana_version
        )

    # --- Build endpoint URI ---------------------------------------------------
    uri = "%s/api/v1/provisioning/alert-rules/%s" % (grafana_url, uid)

    # --- Perform GET request --------------------------------------------------
    r, info = fetch_url(module, uri, headers=headers, method="GET")
    status = info.get("status", 0)

    if status == 200:
        try:
            alert_rule = json.loads(r.read())
            alert_rule_exists = True
        except Exception as e:
            raise GrafanaAPIException("Failed to decode alert rule response: %s" % to_native(e))

    # --- HTTP 404: rule not found --------------------------------------------
    elif info["status"] == 404:
        alert_rule_exists = False

    # --- Any other error ------------------------------------------------------
    else:
        raise GrafanaAPIException("Unable to get alert rule %s : %s" % (uid, info))

    return alert_rule_exists, alert_rule


def grafana_alert_rule_search(module, grafana_url, uid, headers):

    # --- Build endpoint for unified alert rules -------------------------------
    uri = "%s/api/v1/provisioning/alert-rules"% grafana_url

    r, info = fetch_url(
        module,
        uri,
        headers=headers,
        method="GET"
    )
    status = info.get("status", 0)

    # --- Success: parse and search -------------------------------------------
    if status == 200:
        try:
            alert_rules = json.loads(r.read())
            for rule in alert_rules:
                if rule.get("uid") == uid:
                    return grafana_alert_rule_exists(module, grafana_url, uid, headers)
        except Exception as e:
            raise GrafanaAPIException("Failed to decode alert rules list: %s" % to_native(e))
    else:
        raise GrafanaAPIException("Unable to search alert rule %s : %s" % (uid, info))

    return False, None


def is_grafana_alert_rule_changed(payload, alert_rule):

    # --- Defensive copies to avoid modifying inputs --------------------------
    payload_copy = copy.deepcopy(payload or {})
    alert_rule_copy = copy.deepcopy(alert_rule or {})

    # --- Fields managed or auto-generated by Grafana or by module parameters --
    # These fields should not trigger a "changed" status
    fields_to_ignore = {
        "id",           # Auto-generated by Grafana
        "updated",      # Timestamp - always changes
        "provenance",   # Managed by Grafana
        "orgID",        # Org ID - can vary in format
        "orgId",        # Org ID - alternate format
    }

    for field in fields_to_ignore:
        payload_copy.pop(field, None)
        alert_rule_copy.pop(field, None)


def resolve_folder_name_to_uid(module, grafana_url, folder_name, headers):

    # Allow direct UID usage without API lookup
    if folder_name and ' ' not in folder_name and folder_name.islower():
        return folder_name

    # Fetch all folders from Grafana
    uri = "%s/api/folders" % grafana_url

    r, info = fetch_url(
        module,
        uri,
        headers=headers,
        method="GET"
    )
    status = info.get("status", 0)

    if status == 200:
        try:
            folders = json.loads(r.read())

            # Search for folder by title (name)
            for folder in folders:
                if folder.get("title") == folder_name:
                    return folder.get("uid")

            # If not found by name, return original value (might be a UID)
            return folder_name

        except Exception as e:
            # On parsing error, return original value
            module.warn("Unable to parse folders list, using folder value as-is: %s" % to_native(e))
            return folder_name
    else:
        # On API error, return original value
        module.warn("Unable to fetch folders (HTTP %s), using folder value as-is" % status)
        return folder_name


def grafana_create_alert_rule(module, data):

    # --- Load payload --------------------------------------------------------
    try:
        with open(data["path"], "r", encoding="utf-8") as json_file:
            payload = json.load(json_file)
    except Exception as e:
        raise GrafanaAPIException("Can't load json file %s" % to_native(e))

    # --- Prepare headers and check Grafana version ---------------------------
    headers = grafana_headers(module, data)
    grafana_version = get_grafana_version(module, data["url"], headers)

    if grafana_version < 8:
        raise GrafanaAPIException(
            f"Alert rules require Grafana 8 or higher (unified alerting). "
            f"Current version: {grafana_version}"
        )

    # --- Extract UID ---------------------------------------------------------
    uid = data.get("uid") or payload.get("uid")

    # --- Check if alert rule exists (only if UID given) ----------------------
    if uid:
        alert_rule_exists, alert_rule = grafana_alert_rule_exists(
            module,
            data["url"],
            uid,
            headers=headers,
        )
    else:
        alert_rule_exists = False
        alert_rule = {}

    # --- Ensure the UID in payload (if explicitly provided) ------------------
    if data.get("uid"):
        payload["uid"] = data["uid"]

    # --- Add folder UID to payload if specified ------------------------------
    if data.get("folder"):
        folder_uid = resolve_folder_name_to_uid(module, data["url"], data["folder"], headers)
        payload["folderUID"] = folder_uid

    # --- Validate folder support ---------------------------------------------
    if data.get("parent_folder") and grafana_version < 11:
        module.fail_json(msg="Subfolder API is available starting Grafana v11")

    # --- Initialize result structure ----------------------------------------
    result = {}

    # --- Update existing alert rule -----------------------------------------
    if alert_rule_exists:
        grafana_alert_rule_changed = is_grafana_alert_rule_changed(payload, alert_rule)

        if grafana_alert_rule_changed:
            # Support check mode
            if module.check_mode:
                module.exit_json(
                    uid=uid,
                    failed=False,
                    changed=True,
                    msg=f"Alert rule {payload.get('title', uid)} will be updated"
                )

            # Require overwrite flag
            if not data.get("overwrite"):
                raise GrafanaAPIException(
                    "Alert rule %s already exists. Use overwrite=true to update." % uid
                )

            # Perform PUT update
            r, info = fetch_url(
                module,
                "%s/api/v1/provisioning/alert-rules/%s" % (data["url"], uid),
                data=json.dumps(payload),
                headers=headers,
                method="PUT",
            )

            if info.get("status") == 200:
                try:
                    alert_rule = json.loads(r.read()) if r else {}
                    uid = alert_rule.get("uid", uid)
                except Exception as e:
                    raise GrafanaAPIException(f"Failed to parse alert rule response: {e}")

                result.update({
                    "uid": uid,
                    "msg": f"Alert rule {payload.get('title', uid)} updated.",
                    "changed": True
                })

            else:
                body = json.loads(info.get("body", "{}")) if info.get("body") else {}
                raise GrafanaAPIException(
                    f"Unable to update alert rule {uid}: "
                    f"{body.get('message', info)} (HTTP {info.get('status')})"
                )

        else:
            # Nothing changed
            result.update({
                "uid": uid,
                "msg": f"Alert rule {payload.get('title', uid)} unchanged.",
                "changed": False,
            })
    # --- Create new alert rule ----------------------------------------------
    else:
        if module.check_mode:
            module.exit_json(
                failed=False,
                changed=True,
                msg=f"Alert rule {payload.get('title', '')} will be created",
            )

        r, info = fetch_url(
            module,
            "%s/api/v1/provisioning/alert-rules" % data["url"],
            data=json.dumps(payload),
            headers=headers,
            method="POST",
        )

        if info.get("status") == 201:
            try:
                alert_rule = json.loads(r.read()) if r else {}
                uid = alert_rule.get("uid")
            except Exception as e:
                raise GrafanaAPIException(f"Failed to parse created alert rule response: {e}")

            result.update({
                "uid": uid,
                "msg": f"Alert rule {payload.get('title', '')} created.",
                "changed": True,
            })
        else:
            body = json.loads(info.get("body", "{}")) if info.get("body") else {}
            raise GrafanaAPIException(
                f"Unable to create the new alert rule {payload.get('title', '')}: "
                f"{body.get('message', info)} (HTTP {info.get('status')})"
            )

    return result


def grafana_delete_alert_rule(module, data):

    # --- Prepare headers and Grafana version ---------------------------------
    headers = grafana_headers(module, data)
    grafana_version = get_grafana_version(module, data["url"], headers)

    # Grafana's unified alerting starts from v8 — enforce requirement
    if grafana_version < 8:
        raise GrafanaAPIException(
            "Alert rules require Grafana 8 or higher (unified alerting). "
            "Current version: %d" % grafana_version
        )

    # --- Validate UID presence -----------------------------------------------
    uid = data.get("uid")
    if not uid:
        raise GrafanaDeleteException("No UID specified for alert rule deletion.")

    # --- Check if alert rule exists first ------------------------------------
    alert_rule_exists, alert_rule = grafana_alert_rule_exists(
        module, data["url"], uid, headers=headers
    )

    # --- Initialize result structure -----------------------------------------
    result = {}

    # --- Rule exists → proceed to delete -------------------------------------
    if alert_rule_exists:
        # Support check mode (dry-run)
        if module.check_mode:
            module.exit_json(
                uid=uid,
                failed=False,
                changed=True,
                msg="Alert rule %s will be deleted" % uid,
            )

        # Perform DELETE request.
        # For unified alerting (Grafana 8+), use /api/v1/provisioning/alert-rules/[uid]
        r, info = fetch_url(
            module,
            "%s/api/v1/provisioning/alert-rules/%s" % (data["url"], uid),
            headers=headers,
            method="DELETE",
        )

        status = info.get("status", 0)

        if status in [200, 204]:
            # 200 OK or 204 No Content = successful deletion
            result.update({
                "uid": uid,
                "msg": "Alert rule %s deleted" % uid,
                "changed": True,
            })
        elif status == 404:
        # The rule might have already been removed externally
            result.update({
                "uid": uid,
                "msg": "Alert rule %s not found (already deleted?)" % uid,
                "changed": True,
            })
        else:
            # Unexpected response → raise a detailed exception
            body = json.loads(info.get("body, {}")) if info.get("body") else {}
            message = body.get("message", str(info))
            raise GrafanaAPIException(
                "Unable to delete alert rule %s : %s (HTTP %s)" % (uid, message, status)
            )
    # --- Rule does not exist --------------------------------------------------
    else:
        result.update({
            "uid": uid,
            "msg": "Alert rule %s does not exist" % (uid),
            "changed": False,
        })

    return result


def grafana_export_alert_rule(module, data):

    # --- Prepare HTTP headers and Grafana version ----------------------------
    headers = grafana_headers(module, data)
    grafana_version = get_grafana_version(module, data["url"], headers)

    if grafana_version < 8:
        raise GrafanaAPIException(
            "Alert rules require Grafana 8 or higher (unified alerting). "
            "Current version: %d" % grafana_version
        )

    # --- Validate UID presence -----------------------------------------------
    uid = data.get("uid")
    if not uid:
        raise GrafanaDeleteException("No UID specified for alert rule deletion.")

    # --- Check if alert rule exists ------------------------------------------
    alert_rule_exists, alert_rule = grafana_alert_rule_exists(
        module, data["url"], uid, headers=headers
    )

    # --- Initialize result dictionary ----------------------------------------
    result = {}

    if alert_rule_exists:
        if module.check_mode:
            module.exit_json(
                uid=uid,
                failed=False,
                changed=True,
                msg="Alert rule %s will be exported to %s" % (uid, data["path"]),
            )

        export_path = data.get("path")
        if not export_path:
            raise GrafanaExportException("No export path provided for alert rule export.")

        try:
            # Write alert rule to file, formatted for readability
            with open(data["path"], "w", encoding="utf-8") as f:
                f.write(json.dumps(alert_rule, indent=2))
        except Exception as e:
            raise GrafanaExportException("Can't write json file : %s" % to_native(e))

        result.update({
            "uid": uid,
            "msg": "Alert rule %s exported to %s" % (uid, data["path"]),
            "changed": True,
        })

    # --- If the rule does not exist ------------------------------------------
    else:
        result.update({
            "uid": uid,
            "msg": "Alert rule %s does not exist." % uid,
            "changed": False,
        })

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
