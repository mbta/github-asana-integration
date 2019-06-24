#! /usr/bin/env python3

import configparser
import hashlib
import hmac
import json
import logging
import os
import re
import requests

config = configparser.ConfigParser()
config.read('config.ini')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

asana_url = 'https://app.asana.com/api/1.0/tasks'
token = os.environ['ASANA_API_TOKEN']
hook_secret_key = os.environb[b'GITHUB_SECRET']

not_started_id = config.get('DEFAULT', 'not_started_id')
in_dev_id = config.get('DEFAULT', 'in_dev_id')
in_pr_id = config.get('DEFAULT', 'in_pr_id')
merged_done_id = config.get('DEFAULT', 'merged_done_id')


def handler(event, context):  # pylint:disable=unused-argument
    if not validate_signature(event):
        raise Exception('Signature sha does not match')
    event = json.loads(event['body'])
    if 'DEBUG_INTEGRATION' in os.environ:
        logger.debug('## EVENT BODY')
        logger.debug(event)
    # https://developer.github.com/v3/activity/events/types/#pullrequestevent
    asana_ids = find_asana_ids(event['pull_request'])
    if asana_ids:
        get_and_update_task(event['action'], event['pull_request'], asana_ids)
    else:
        raise Exception(
            "Asana id not found in the PR at {}".format(event['pull_request']['html_url']))


def validate_signature(event):
    sha_name, signature = event['headers']['X-Hub-Signature'].split('=')
    if sha_name != 'sha1':
        return False

    mac = hmac.new(hook_secret_key, msg=event['body'].encode(
        'utf-8'), digestmod=hashlib.sha1)
    return hmac.compare_digest(mac.hexdigest(), signature)


def find_asana_ids(pr_object):
    regex = re.compile(
        r"(https?:\/\/app.asana.com\/0\/([0-9]*)\/([0-9]*))")
    match = regex.search(pr_object['body'])
    if match:
        return {'task_id': match.group(3), 'project_id': match.group(2)}
    return None


def json_headers():
    return {'content-type': 'application/json',
            'Authorization': "Bearer {}".format(os.environ['ASANA_API_TOKEN'])}


def url_headers():
    return {'content-type': 'application/x-www-form-urlencoded',
            'Authorization': "Bearer {}".format(os.environ['ASANA_API_TOKEN'])}


def get_and_update_task(action='closed', pr={'merged': 'true', 'html_url': 'http://testing.com'},
                        ids={'task_id': os.environ['ASANA_TEST_TASK_ID'],
                             'project_id': config.get('TEST', 'project_id')},):
    project_id = ids['project_id']
    task_id = ids['task_id']
    r = requests.get("{}/{}".format(asana_url, task_id),
                     headers=json_headers())
    if r.status_code == 200:
        task = r.json()['data']
        add_github_link(task, pr['html_url'])
        confirm_project(task, project_id)
        update_project(task, project_id, action, pr)

    else:
        raise Exception(
            "Received bad status code from asana, {}".format(r.status_code))


def find(f, array):
    for item in array:
        if f(item):
            return item


def add_github_link(task, url):
    github_field = find(
        lambda field: field['name'] == 'GitHub PR', task['custom_fields'])
    if github_field and github_field['text_value'] != url:
        data = {'data': {'custom_fields': {}}}
        data['data']['custom_fields'][github_field['gid']] = url
        requests.put("{}/{}".format(asana_url,
                                    task['gid']), headers=json_headers(), json=data)
        logger.info("updating github field %s with %s",
                    github_field['gid'], url)


def confirm_project(task, project_id):
    if any(confirm_member(member, project_id) for member in task['memberships']):
        return True
    raise Exception(
        "Task {} is not on the project board {} in Not Started, in Dev, or in PR"
        .format(task['gid'], project_id))


def confirm_member(member, project_id):
    if member['project']['gid'] == project_id:
        if (member['section']['gid'] in [not_started_id, in_dev_id, in_pr_id]):
            return True
    return False


def update_project(task, project_id, action, pr):
    try:
        add_section(task, project_id, action, pr)
    except Exception as e:
        raise Exception("Updating project failed, {}".format(e))


def add_section(task, project_id, action, pr):
    task_id = task['gid']
    if action in ('opened', 'edited'):
        do_add_section(task_id, project_id, in_pr_id)
    elif action == 'closed' and pr['merged']:
        do_add_section(task_id, project_id, merged_done_id)
        mark_completed(task_id)


def do_add_section(task_id, project_id, section):
    data = {'project': "{}".format(
        project_id), 'section': "{}".format(section), 'insert_after': 'null'}
    r = requests.post("{}/{}/addProject".format(asana_url, task_id),
                      headers=url_headers(), data=data)
    logger.info("add section %s status code %s",
                section, r.status_code)


def mark_completed(task_id):
    data = {'completed': 'true'}
    r = requests.put("{}/{}".format(asana_url, task_id),
                     headers=url_headers(), data=data)
    logger.info("marking complete task %s status code %s",
                task_id, r.status_code)
