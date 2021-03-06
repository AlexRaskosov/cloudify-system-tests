########
# Copyright (c) 2014 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.

""" Assumes fabric environment already set up """

__author__ = 'ilyash'

import subprocess
import tempfile
import time
import os
from os.path import dirname, expanduser

import requests
import fabric.api
import fabric.context_managers
from path import path

from cosmo_tester.framework.testenv import TestCase
from cosmo_tester.framework.util import YamlPatcher


IMAGE_NAME = 'Ubuntu Server 12.04 LTS (amd64 20140606) - Partner Image'
FLAVOR_NAME = 'standard.small'

PUPPET_MASTER_VERSION = '3.5.1-1puppetlabs1'

MANIFESTS_URL = ('https://github.com/Fewbytes/cosmo-tester-puppet-downloadable'
                 '/archive/master.tar.gz')

MANIFESTS_FILE_NAME = 'manifests.tar.gz'


def find_node_state(node_name, nodes_state):
    pfx = node_name + '_'
    matches = [v for k, v in nodes_state.items() if k.startswith(pfx)]
    if len(matches) != 1:
        raise RuntimeError("Failed to find node {0}".format(node_name))
    return matches[0]


def get_nodes_of_type(blueprint, type_):
    return [n for n in blueprint.obj['blueprint']['nodes']
            if n['type'] == type_]


def get_agent_key_file(env):
    with env.cloudify_config_path.dirname():
        agent_key_file = path(expanduser(env.agent_key_path)).abspath()
        if not agent_key_file.exists():
            raise RuntimeError("Agent key file {0} does not exist".format(
                agent_key_file))
    return agent_key_file


def update_blueprint(env, blueprint, hostname, userdata_vars=None):
    hostname_base = 'system-test-{0}-{1}'.format(
        time.strftime("%Y%m%d-%H%M"), hostname)
    vms = get_nodes_of_type(blueprint, 'cloudify.openstack.server')
    if len(vms) > 1:
        hostnames = ['{0}-{1:2}'.format(hostname_base, i)
                     for i in range(0, len(vms))]
    else:
        hostnames = [hostname_base]

    users = []
    for vm_idx, vm in enumerate(vms):
        vm_hostname = hostnames[vm_idx]

        # vm['properties']['server'] does not exist when using existing one
        if 'server' in vm['properties']:
            vm['properties']['server'].update({
                'flavor_name': FLAVOR_NAME,
                'image_name': IMAGE_NAME,
                'key_name': env.agent_keypair_name,
                'name': vm_hostname,
            })
            vm['properties']['management_network_name'] = (
                env.management_network_name)
            vm['properties']['server']['security_groups'].append(
                env.agents_security_group)
            props = vm['properties']['server']
            if 'userdata' in props:
                props['userdata'] = props['userdata'].format(
                    hostname=vm_hostname, **(userdata_vars or {}))
        users.append('ubuntu')

    fips = get_nodes_of_type(blueprint, 'cloudify.openstack.floatingip')
    for fip in fips:
            fip_fip = fip['properties']['floatingip']
            fip_fip['floating_network_name'] = env.external_network_name

    return {'hostnames': hostnames, 'users': users}


def setup_puppet_server(local_dir):
    # import pdb; pdb.set_trace()
    for p in 'puppetmaster-common', 'puppetmaster':
        cmd = 'apt-get install -y {0}={1}'.format(p, PUPPET_MASTER_VERSION)
        fabric.api.sudo(cmd)
    remote_path = '/etc/puppet'
    local_path = (
        path(dirname(dirname(dirname(os.path.realpath(__file__))))) /
        'resources' /
        'puppet' + remote_path)
    fabric.api.put(local_path=local_path,
                   remote_path=dirname(remote_path),
                   use_sudo=True)
    fabric.api.sudo('chown puppet -R ' + remote_path)

    t = remote_path + '/puppet.conf'
    fabric.api.sudo('grep -q autosign ' + t + ' || ' +
                    '(echo "[master]"; echo "autosign = true") >> ' + t)
    # fabric.api.sudo('service puppetmaster restart') # Does not work !!!
    fabric.api.sudo('fuser -k 8140/tcp')
    fabric.api.sudo('service puppetmaster start')


class PuppetPluginAgentTest(TestCase):

    def setUp(self, *args, **kwargs):

        super(PuppetPluginAgentTest, self).setUp(*args, **kwargs)

        blueprint_dir = self.copy_blueprint('puppet-plugin')
        self.blueprint_dir = blueprint_dir
        if 'CLOUDIFY_TEST_PUPPET_IP' in os.environ:
            self.logger.info('Using existing Puppet server at {0}'.format(
                os.environ['CLOUDIFY_TEST_PUPPET_IP']))
            self.puppet_server_ip = os.environ['CLOUDIFY_TEST_PUPPET_IP']
            self.puppet_server_id = None
            return

        self.logger.info('Setting up Puppet master')

        self.blueprint_yaml = blueprint_dir / 'puppet-server-by-puppet.yaml'

        with YamlPatcher(self.blueprint_yaml) as blueprint:
            bp_info = update_blueprint(self.env, blueprint, 'puppet-master')

        self.puppet_server_hostname = bp_info['hostnames'][0]

        self.puppet_server_id = self.test_id + '-puppet-master'
        id_ = self.puppet_server_id
        before, after = self.upload_deploy_and_execute_install(id_, id_)

        fip_node = find_node_state('ip', after['node_state'][id_])
        self.puppet_server_ip = \
            fip_node['runtime_properties']['floating_ip_address']

        fabric_env = fabric.api.env
        fabric_env.update({
            'timeout': 30,
            'user': bp_info['users'][0],
            'key_filename': get_agent_key_file(self.env),
            'host_string': self.puppet_server_ip,
        })

        setup_puppet_server(blueprint_dir)

    def tearDown(self, *args, **kwargs):
        if self.puppet_server_id:
            self.execute_uninstall(self.puppet_server_id)
        super(PuppetPluginAgentTest, self).tearDown(*args, **kwargs)

    def test_puppet_agent(self):
        blueprint_dir = self.blueprint_dir
        self.blueprint_yaml = blueprint_dir / 'puppet-agent-test.yaml'
        with YamlPatcher(self.blueprint_yaml) as blueprint:
            bp_info = update_blueprint(self.env, blueprint, 'puppet-agent', {
                'puppet_server_ip': self.puppet_server_ip,
            })

        id_ = self.test_id + '-puppet-agent-' + str(int(time.time()))
        before, after = self.upload_deploy_and_execute_install(id_, id_)

        # import pdb; pdb.set_trace()

        fip_node = find_node_state('ip', after['node_state'][id_])
        puppet_agent_ip = fip_node['runtime_properties']['floating_ip_address']

        fabric_env = fabric.api.env
        fabric_env.update({
            'timeout': 30,
            'user': bp_info['users'][0],
            'key_filename': get_agent_key_file(self.env),
            'host_string': puppet_agent_ip,
        })

        f = '/tmp/cloudify_operation_create'

        out = fabric.api.run('[ -f {0} ]; echo $?'.format(f))
        self.assertEquals(out, '0')

        out = fabric.api.run('cat {0}'.format(f))
        self.assertEquals(out, id_)

        self.execute_uninstall(id_)


class PuppetPluginStandaloneTest(TestCase):

    def execute_and_check(self, id_):
        before, after = self.upload_deploy_and_execute_install(id_, id_)

        fip_node = find_node_state('ip', after['node_state'][id_])
        puppet_standalone_ip = \
            fip_node['runtime_properties']['floating_ip_address']

        page = requests.get('http://{0}:8080'.format(puppet_standalone_ip))
        self.assertIn('Cloudify Hello World', page.text,
                      'Expected text not found in response')

    def test_puppet_standalone_without_download(self):
        id_ = "{0}-puppet-standalone-{1}-{2}".format(self.test_id,
                                                     'nodl',
                                                     str(int(time.time())))
        blueprint_dir = self.copy_blueprint('puppet-plugin')

        self.blueprint_yaml = blueprint_dir / 'puppet-standalone-test.yaml'
        with YamlPatcher(self.blueprint_yaml) as blueprint:
            update_blueprint(self.env, blueprint, 'puppet-standalone-nodl')
        self.execute_and_check(id_)

    def _test_puppet_standalone_with_download(self, manifests_are_from_url):
        """ Tests standalone Puppet.
        manifests_are_from_url True ->
            puppet_config:
                download: http://....
                execute: -- removed
                manifest: site.pp
        manifests_are_from_url False ->
                download: /....
                execute:
                    configure: -- removed
        """

        mode = ['resource', 'url'][manifests_are_from_url]
        id_ = "{0}-puppet-standalone-{1}-{2}".format(self.test_id,
                                                     mode,
                                                     str(int(time.time())))
        _url = ('http://' +
                self.env.management_ip +
                '/resources/blueprints/' +
                id_ +
                '/' +
                MANIFESTS_FILE_NAME)

        download_from = ['/' + MANIFESTS_FILE_NAME, _url][
            manifests_are_from_url]

        def call(cmd):
            print("Executing: {0}".format(' '.join(cmd)))
            # subprocess.check_call(cmd, stdout=sys.stdout, stderr=sys.stderr)
            # Trying without piping since this caused the following problem:
            # Traceback (most recent call last):
            # File "/usr/lib/python2.7/subprocess.py", line 506, in check_call
            # retcode = call(*popenargs, **kwargs)
            # File "/usr/lib/python2.7/subprocess.py", line 493, in call
            # return Popen(*popenargs, **kwargs).wait()
            # File "/usr/lib/python2.7/subprocess.py", line 672, in __init__
            # errread, errwrite) = self._get_handles(stdin, stdout, stderr)
            # File "/usr/lib/python2.7/subprocess.py", line 1053, in _get_handles  # noqa
            # c2pwrite = stdout.fileno()
            # AttributeError: 'Tee' object has no attribute 'fileno'
            subprocess.check_call(cmd)

        blueprint_dir = self.copy_blueprint('puppet-plugin')

        # Download manifests
        file_name = os.path.join(tempfile.gettempdir(),
                                 self.test_id + '.manifests.tar.gz')
        temp_dir = tempfile.mkdtemp('.manifests', self.test_id + '.')
        call(['wget', '-O', file_name, MANIFESTS_URL])
        call(['tar', '-vxzf', file_name, '-C', temp_dir,
              '--xform', 's/^[^\/]\+\///'])
        call(['tar', '-vczf', os.path.join(blueprint_dir, MANIFESTS_FILE_NAME),
              '-C', temp_dir, '.'])

        self.blueprint_yaml = blueprint_dir / 'puppet-standalone-test.yaml'
        with YamlPatcher(self.blueprint_yaml) as blueprint:
            update_blueprint(self.env, blueprint, 'puppet-standalone-' + mode)
            conf = blueprint.obj['blueprint']['nodes'][1]['properties'][
                'puppet_config']
            conf['download'] = download_from
            if manifests_are_from_url:
                del conf['execute']
                conf['manifest'] = {'start': 'manifests/site.pp'}
            else:
                del conf['execute']['configure']
        self.execute_and_check(id_)

    def test_puppet_standalone_with_resource(self):
        self._test_puppet_standalone_with_download(False)

    def test_puppet_standalone_with_url(self):
        self._test_puppet_standalone_with_download(True)
