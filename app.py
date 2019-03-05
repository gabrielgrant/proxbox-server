#!/usr/bin/env python

"""
Proxbox Server

Allows registration of pach clusters to enable connecting 
"""
from collections import namedtuple
from uuid import uuid4
import os
import shutil
from cStringIO import StringIO
import subprocess
import tempfile
from urlparse import urlparse

from authorized_keys import PublicKey
from haikunator import Haikunator
import toml


from flask import Flask, request, jsonify
app = Flask(__name__)


ROOT_DOMAIN='proxbox.gabrielgrant.ca'
AUTHORIZED_KEYS_FILE = 'host_ssh/authorized_keys'
#AUTHORIZED_KEYS_FILE = os.path.expanduser("~/.ssh/authorized_keys")


# from https://nickjanetakis.com/blog/docker-tip-65-get-your-docker-hosts-ip-address-from-in-a-container#comment-4323314739
# other alternatives: https://github.com/moby/moby/issues/1143
DOCKER_HOST_IP = subprocess.check_output('ip route show default'.split()).split()[2]


# API error handling pattern from http://flask.pocoo.org/docs/0.12/patterns/apierrors/
class InvalidUsage(Exception):
    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        return rv

@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


def parse_authorized_keys(keyfile=None):
    if keyfile is None:
        #keyfile = os.path.expanduser("~/.ssh/authorized_keys")
        keyfile = AUTHORIZED_KEYS_FILE
        
    if os.path.exists(keyfile):
        for line in open(keyfile):
            line = line.strip()
            if line and not line.startswith("#"):
                yield PublicKey(line)

def generate_id():
    haikunator = Haikunator()
    ids = get_cluster_ids_in_authorized_keys()
    while True:
        cluster_id = haikunator.haikunate(token_length=6, token_hex=True)
        if cluster_id not in ids:
            break
    return cluster_id

def get_cluster_ids_in_authorized_keys(keyfile=None):
    return set(pk.comment for pk in parse_authorized_keys(keyfile))
    

def is_key_registered(pubkey):
    keys = set(pk.blob for pk in parse_authorized_keys())
    return pubkey.blob in keys

def get_rules(rules_file='rules.toml'):
    if os.path.exists(rules_file):
        return toml.load(rules_file)
    return {'backends':{}, 'frontends':{}}

# ssh settings from http://askubuntu.com/questions/48129/how-to-create-a-restricted-ssh-user-for-port-forwarding
OPTIONS_TMPL = '''command="echo 'This account can only port forward to {cluster_id}'",no-agent-forwarding,no-X11-forwarding,permitopen="0.0.0.0:{grpc_port}",permitopen="0.0.0.0:{ui_port}"'''
BACKEND_URL_TMPL = 'http://' + DOCKER_HOST_IP + ':{}'
FRONTEND_RULE_TMPL = 'Host: {}-{}.' + ROOT_DOMAIN

@app.route('/cluster/', methods=['POST'])
def register_cluster():
    pubkey_raw = request.form['pubkey']
    if not pubkey_raw:
        raise InvalidUsage('Missing pubkey parameter', status_code=400)
    pubkey = PublicKey(pubkey_raw)
    if is_key_registered(pubkey):
        raise InvalidUsage('Given pubkey is already registered', status_code=409)
    cluster_id = generate_id()
    # save key as submitted
    if not os.path.exists('pubkeys'):
        os.makedirs('pubkeys')
    pubkey_path = 'pubkeys/' + cluster_id + '.pub'
    open(pubkey_path, 'w').write(pubkey_raw)
    # convert key to openssl format
    openssl_key_path = 'pubkeys/' + cluster_id + '.openssl.pub'
    openssl_key = subprocess.check_output(['ssh-keygen', '-f', pubkey_path, '-e', '-m', 'PKCS8'])
    open(openssl_key_path, 'w').write(openssl_key)

    ports = allocate_ports()
    ports['cluster_id'] = cluster_id
    # add to authorized_hosts
    pubkey.options = OPTIONS_TMPL.format(**ports)
    pubkey.comment = cluster_id
    #keyfile = os.path.expanduser("~/.ssh/authorized_keys")
    keyfile = AUTHORIZED_KEYS_FILE
    keydir = os.path.dirname(keyfile)
    if not os.path.exists(keydir):
        os.makedirs(keydir)
    open(keyfile, 'a').write('\n' + str(pubkey) + '\n')
    # add to traefik rules
    rules = get_rules()
    
    rules['backends'][cluster_id + '-grpc'] = {'servers': {'server': {'url': BACKEND_URL_TMPL.format(ports['grpc_port'])}}}
    rules['backends'][cluster_id + '-ui'] = {'servers': {'server': {'url': BACKEND_URL_TMPL.format(ports['ui_port'])}}}
    rules['frontends'][cluster_id + '-ui'] = {
        'routes': {
            'route': {
                'rule': FRONTEND_RULE_TMPL.format(cluster_id, 'ui')
            }
        },
        'backend': cluster_id + '-ui'
    }
    rules['frontends'][cluster_id + '-grpc'] = {
        'routes': {
            'route': {
                'rule': FRONTEND_RULE_TMPL.format(cluster_id, 'grpc')
            }
        },
        'backend': cluster_id + '-grpc'
    }
    
    # write new rules to new location then atomically move into place
    new_rules_fd, new_rules_path = tempfile.mkstemp()
    new_rules_file = os.fdopen(new_rules_fd, 'w')
    toml.dump(rules, new_rules_file)
    new_rules_file.close()
    shutil.move(new_rules_path, 'rules.toml')
    return jsonify(ports)

def public_key_for_cluster(cluster_id):
    return [pk for pk in parse_authorized_keys() if pk.comment == cluster_id][0]

def decode_signature(cluster_id, signature):
    args = 'openssl rsautl -verify -inkey'.split() + ['pubkeys/' + cluster_id + '.openssl.pub', '-pubin']
    subprocess.check_output(args, stdin=StringIO(signature))
    

def allocate_ports():
    rules = get_rules()
    netlocs = [backend['servers']['server']['url'] for backend in rules['backends'].values()]
    ports = [urlparse(netloc).port for netloc in netlocs]
    top_port = max(ports) if ports else 8889
    return {
        'grpc_port': top_port + 1,
        'ui_port': top_port + 2,
        # possibly add hosts in the future?
    }

def get_ports(cluster_id, rules=None):
    if rules is None:
        rules = get_rules()
    grpc_netloc = rules['backends'][cluster_id + '-grpc']['servers']['server']
    ui_netloc = rules['backends'][cluster_id + '-ui']['servers']['server']
    return {
        'grpc_port': urlparse(grpc_netloc).port,
        'ui_port': urlparse(ui_netloc).port,
        'cluster_id': cluster_id
    }

@app.route('/cluster/<cluster_id>', methods=['POST'])
def fetch_cluster(cluster_id):
    if cluster_id not in get_cluster_ids_in_authorized_keys():
        raise InvalidUsage('Given cluster_id is not registered', status_code=404)
    # this is effectively basic auth, with cluster_id as username and sig as password
    signature = request.form['signature']
    if decode_signature(cluster_id, signature) != cluster_id:
        raise InvalidUsage('Given signature does not match pubkey registered for this cluster_id', status_code=401)
    return jsonify(get_ports(cluster_id))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8000)

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
