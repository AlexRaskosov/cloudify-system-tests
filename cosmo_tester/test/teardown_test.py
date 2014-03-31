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


__author__ = 'ran'


from cosmo_tester.framework.testenv import TestCase
from cosmo_tester.framework.openstack_api import openstack_clients


class NeutronGaloreTest(TestCase):

    def test_teardown(self):
        nova, neutron = openstack_clients(self.env.cloudify_config)

        self.post_bootstrap_assertions(nova, neutron)

        self.cfy.teardown(f=True,
                          v=True).wait()

        self.post_teardown_assertions(nova, neutron)

    def post_bootstrap_assertions(self, nova, neutron):
        pass

    def post_teardown_assertions(self, nova, neutron):
        pass
