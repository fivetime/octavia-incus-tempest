#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Statistics must move after traffic, not merely be reportable.

The API test `_test_listener_show_stats` asserts every counter is zero on a
fresh listener. That is worth having -- it covers the endpoint and its RBAC --
but it cannot tell "no traffic yet" from "this pipeline is broken and always
answers zero". Both look identical to it.

That distinction is not academic here. On the incus provider the counters do
not come from the load balancer's own process: each worker reads HAProxy's
`show stat` and pushes the numbers up with its heartbeat, and the control
plane sums them per (worker, listener). DESIGN.md 6.5 records a period where
that channel reported zero permanently. A test that only ever asserts zero
would have passed throughout.

So: drive real traffic, then require the counters to have moved.
"""

import time

from oslo_log import log as logging
from tempest import config
from tempest.lib.common.utils import data_utils
from tempest.lib import decorators

from octavia_tempest_plugin.common import constants as const
from octavia_tempest_plugin.tests import test_base
from octavia_tempest_plugin.tests import waiters

CONF = config.CONF
LOG = logging.getLogger(__name__)

# Counters are pushed with the heartbeat and summed on arrival, so they trail
# the traffic. Poll rather than sleep once: a fixed sleep either flakes or
# wastes the difference.
STATS_TIMEOUT = 120
STATS_INTERVAL = 5


class StatisticsScenarioTest(test_base.LoadBalancerBaseTestWithCompute):

    @classmethod
    def resource_setup(cls):
        super().resource_setup()

        lb_name = data_utils.rand_name("lb_member_lb1-stats")
        lb_kwargs = {const.PROVIDER: CONF.load_balancer.provider,
                     const.NAME: lb_name}
        cls._setup_lb_network_kwargs(lb_kwargs)

        lb = cls.mem_lb_client.create_loadbalancer(**lb_kwargs)
        cls.lb_id = lb[const.ID]
        cls.addClassResourceCleanup(
            cls.mem_lb_client.cleanup_loadbalancer, cls.lb_id)

        if CONF.validation.connect_method == 'floating':
            port_id = lb[const.VIP_PORT_ID]
            subnet_id = cls.lb_member_vip_subnet[const.ID]
            floating_ip = cls._create_floating_ip(port_id, subnet_id=subnet_id)
            cls.lb_vip_address = floating_ip['floating_ip_address']
        else:
            cls.lb_vip_address = lb[const.VIP_ADDRESS]

        waiters.wait_for_status(
            cls.mem_lb_client.show_loadbalancer, cls.lb_id,
            const.PROVISIONING_STATUS, const.ACTIVE,
            CONF.load_balancer.lb_build_interval,
            CONF.load_balancer.lb_build_timeout)

    def _wait_for_moving_stats(self, fetch, what):
        """Poll `fetch` until the counters are non-zero, and return them.

        Fails with the last reading rather than a bare timeout: "bytes_in
        stayed 0 for 120s" tells the reader which counter never moved, which
        is the whole question when this fails.
        """
        deadline = time.time() + STATS_TIMEOUT
        stats = {}
        while time.time() < deadline:
            stats = fetch()
            if stats.get(const.TOTAL_CONNECTIONS) and stats.get(const.BYTES_IN):
                return stats
            time.sleep(STATS_INTERVAL)
        self.fail(
            '%s statistics never moved after traffic. Last reading: %s. '
            'A load balancer that serves traffic while its counters stay at '
            'zero means the reporting path is broken, not that the traffic '
            'did not happen -- check the worker heartbeat channel.'
            % (what, stats))

    @decorators.idempotent_id('c9c2f0d3-2b53-4a6a-9b8f-6f0e07f7a5cb')
    def test_statistics_move_after_traffic(self):
        """Counters must reflect traffic that actually flowed.

        * Build a listener and pool with two members.
        * Confirm the counters start at zero.
        * Send traffic.
        * Require listener and load balancer counters to have moved, and to
          agree with each other.
        """
        listener_name = data_utils.rand_name("lb_member_listener1-stats")
        listener = self.mem_listener_client.create_listener(
            **{const.NAME: listener_name,
               const.PROTOCOL: const.HTTP,
               const.PROTOCOL_PORT: 8080,
               const.LOADBALANCER_ID: self.lb_id})
        listener_id = listener[const.ID]
        self.addCleanup(self.mem_listener_client.cleanup_listener, listener_id,
                        lb_client=self.mem_lb_client, lb_id=self.lb_id)
        waiters.wait_for_status(
            self.mem_lb_client.show_loadbalancer, self.lb_id,
            const.PROVISIONING_STATUS, const.ACTIVE,
            CONF.load_balancer.build_interval,
            CONF.load_balancer.build_timeout)

        pool = self.mem_pool_client.create_pool(
            **{const.NAME: data_utils.rand_name("lb_member_pool1-stats"),
               const.PROTOCOL: const.HTTP,
               const.LB_ALGORITHM: const.LB_ALGORITHM_ROUND_ROBIN,
               const.LISTENER_ID: listener_id})
        pool_id = pool[const.ID]
        self.addCleanup(self.mem_pool_client.cleanup_pool, pool_id,
                        lb_client=self.mem_lb_client, lb_id=self.lb_id)
        waiters.wait_for_status(
            self.mem_lb_client.show_loadbalancer, self.lb_id,
            const.PROVISIONING_STATUS, const.ACTIVE,
            CONF.load_balancer.build_interval,
            CONF.load_balancer.build_timeout)

        for index, (address, subnet) in enumerate(
                ((self.webserver1_ip, self.lb_member_1_subnet),
                 (self.webserver2_ip, self.lb_member_2_subnet)), start=1):
            member_kwargs = {
                const.POOL_ID: pool_id,
                const.NAME: data_utils.rand_name(
                    "lb_member_member%d-stats" % index),
                const.ADMIN_STATE_UP: True,
                const.ADDRESS: address,
                const.PROTOCOL_PORT: 80,
            }
            if subnet:
                member_kwargs[const.SUBNET_ID] = subnet[const.ID]
            member = self.mem_member_client.create_member(**member_kwargs)
            self.addCleanup(
                self.mem_member_client.cleanup_member, member[const.ID],
                pool_id=pool_id, lb_client=self.mem_lb_client,
                lb_id=self.lb_id)
            waiters.wait_for_status(
                self.mem_lb_client.show_loadbalancer, self.lb_id,
                const.PROVISIONING_STATUS, const.ACTIVE,
                CONF.load_balancer.check_interval,
                CONF.load_balancer.check_timeout)

        # Zero before traffic. Without this the assertion after traffic could
        # be satisfied by counters left over from something else.
        before = self.mem_listener_client.get_listener_stats(listener_id)
        self.assertEqual(0, before[const.TOTAL_CONNECTIONS],
                         'the listener had connections before any were sent')
        self.assertEqual(0, before[const.BYTES_IN])

        self.check_members_balanced(self.lb_vip_address, protocol_port=8080,
                                    protocol=const.HTTP)

        listener_stats = self._wait_for_moving_stats(
            lambda: self.mem_listener_client.get_listener_stats(listener_id),
            'Listener')
        LOG.info('Listener statistics after traffic: %s', listener_stats)
        self.assertGreater(listener_stats[const.BYTES_OUT], 0,
                           'bytes_in moved but bytes_out did not, so replies '
                           'are not being counted')
        self.assertEqual(0, listener_stats[const.REQUEST_ERRORS],
                         'the balanced traffic produced request errors')

        lb_stats = self._wait_for_moving_stats(
            lambda: self.mem_lb_client.get_loadbalancer_stats(self.lb_id),
            'Load balancer')
        LOG.info('Load balancer statistics after traffic: %s', lb_stats)

        # This load balancer has exactly one listener, so the two views have
        # to agree. They are summed separately, and a per-listener total that
        # the load balancer does not carry means the aggregation dropped a
        # worker.
        self.assertGreaterEqual(
            lb_stats[const.TOTAL_CONNECTIONS],
            listener_stats[const.TOTAL_CONNECTIONS],
            'the load balancer reports fewer connections than its only '
            'listener, so the per-worker sums are not all arriving')
